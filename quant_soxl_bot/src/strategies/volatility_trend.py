"""
VolatilityTrendStrategy
=======================
Intraday volatility-expansion strategy for SOXL (3x Leveraged ETF).

Design reference: docs/STRATEGY_SPECS.md
- Enter on volatility expansion + trend confirmation.
- Exit via hierarchical stop system: Hard Stop → Trailing Stop → EOD Close.
- Circuit breakers for daily drawdown and consecutive losses.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

import backtrader as bt


class VolatilityTrendStrategy(bt.Strategy):
    """Intraday volatility-trend strategy for SOXL.

    Parameters
    ----------
    atr_period : int
        Look-back period for ATR indicator.
    atr_multiplier : float
        Multiplier applied to ATR for the trailing stop distance.
    sma_period : int
        Look-back period for the Simple Moving Average trend filter.
    rsi_period : int
        Look-back period for RSI.
    rsi_upper : float
        RSI threshold — no entry when RSI >= this value.
    vol_expansion : float
        Bar-range / ATR ratio that qualifies as volatility expansion.
    vol_multiplier : float
        Volume / AvgVolume ratio that qualifies as volume spike.
    hard_stop_pct : float
        Maximum allowed loss (%) from entry price before a hard stop fires.
    trade_cash : float
        Fixed dollar amount allocated per trade.
    market_open_hour : int
        Market open hour (ET / data timezone).
    market_open_minute : int
        Market open minute.
    entry_blackout_minutes : int
        Minutes after open during which new entries are blocked.
    eod_close_hour : int
        Hour at which all positions are force-closed (ET).
    eod_close_minute : int
        Minute at which all positions are force-closed.
    last_entry_hour : int
        Hour after which no new entries are allowed.
    last_entry_minute : int
        Minute after which no new entries are allowed.
    daily_loss_limit : float
        Maximum daily dollar loss before circuit breaker halts trading.
    max_consec_losses : int
        Consecutive losing trades before a cooldown pause.
    cooldown_minutes : int
        Minutes to pause after hitting consecutive loss limit.
    """

    params: dict = dict(
        # --- Indicators ---
        atr_period=14,
        atr_multiplier=2.5,
        sma_period=20,
        rsi_period=14,
        rsi_upper=70.0,
        vol_expansion=1.5,
        vol_multiplier=2.0,
        # --- Risk ---
        hard_stop_pct=2.0,
        trade_cash=10_000.0,
        # --- Time windows (ET) ---
        market_open_hour=9,
        market_open_minute=30,
        entry_blackout_minutes=15,
        eod_close_hour=15,
        eod_close_minute=55,
        last_entry_hour=15,
        last_entry_minute=30,
        # --- Circuit breakers ---
        daily_loss_limit=500.0,
        max_consec_losses=3,
        cooldown_minutes=30,
    )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    def __init__(self) -> None:
        # Indicators
        self.atr: bt.indicators.ATR = bt.indicators.ATR(
            self.data, period=self.p.atr_period
        )
        self.sma: bt.indicators.SMA = bt.indicators.SMA(
            self.data.close, period=self.p.sma_period
        )
        self.rsi: bt.indicators.RSI = bt.indicators.RSI(
            self.data.close, period=self.p.rsi_period
        )
        self.avg_volume: bt.indicators.SMA = bt.indicators.SMA(
            self.data.volume, period=self.p.sma_period
        )

        # Order / position tracking
        self.order: Optional[bt.Order] = None
        self.entry_price: float = 0.0
        self.highest_price: float = 0.0
        self.trailing_stop_level: float = 0.0

        # Circuit breaker state
        self.daily_pnl: float = 0.0
        self.consec_losses: int = 0
        self.cooldown_until: Optional[dt.datetime] = None
        self._current_date: Optional[dt.date] = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log(self, txt: str, dt_override: Optional[dt.datetime] = None) -> None:
        """Print a timestamped log line.

        Parameters
        ----------
        txt : str
            Message body.
        dt_override : datetime, optional
            Override the bar datetime for the log line.
        """
        bar_dt = dt_override or self.data.datetime.datetime(0)
        print(f"[{bar_dt:%Y-%m-%d %H:%M}] {txt}")

    # ------------------------------------------------------------------
    # Order notifications
    # ------------------------------------------------------------------
    def notify_order(self, order: bt.Order) -> None:
        """Handle order state changes and reset ``self.order``.

        Parameters
        ----------
        order : bt.Order
            The order object whose state changed.
        """
        if order.status in (order.Submitted, order.Accepted):
            return  # nothing to do yet

        if order.status == order.Completed:
            if order.isbuy():
                self.entry_price = order.executed.price
                self.highest_price = order.executed.price
                self.trailing_stop_level = 0.0
                self.log(
                    f"BUY EXECUTED | Price: {order.executed.price:.2f}, "
                    f"Size: {order.executed.size:.0f}, "
                    f"Cost: {order.executed.value:.2f}, "
                    f"Comm: {order.executed.comm:.2f}"
                )
            elif order.issell():
                self.log(
                    f"SELL EXECUTED | Price: {order.executed.price:.2f}, "
                    f"Size: {order.executed.size:.0f}, "
                    f"Comm: {order.executed.comm:.2f}"
                )
        elif order.status in (order.Canceled, order.Margin, order.Rejected):
            status_name = {
                order.Canceled: "Canceled",
                order.Margin: "Margin",
                order.Rejected: "Rejected",
            }.get(order.status, "Unknown")
            self.log(f"ORDER {status_name}")

        self.order = None

    # ------------------------------------------------------------------
    # Trade notifications (PnL tracking for circuit breakers)
    # ------------------------------------------------------------------
    def notify_trade(self, trade: bt.Trade) -> None:
        """Track closed-trade PnL for circuit breaker logic.

        Parameters
        ----------
        trade : bt.Trade
            The trade object.
        """
        if not trade.isclosed:
            return

        pnl = trade.pnlcomm
        self.daily_pnl += pnl

        if pnl < 0:
            self.consec_losses += 1
            self.log(
                f"TRADE CLOSED (LOSS) | PnL: {pnl:.2f}, "
                f"Consecutive losses: {self.consec_losses}"
            )
        else:
            self.consec_losses = 0
            self.log(f"TRADE CLOSED (WIN)  | PnL: {pnl:.2f}")

    # ------------------------------------------------------------------
    # Helpers — time filters
    # ------------------------------------------------------------------
    def _bar_dt(self) -> dt.datetime:
        """Return the current bar's datetime."""
        return self.data.datetime.datetime(0)

    def _reset_daily_state_if_new_day(self) -> None:
        """Reset per-day accumulators when the calendar date changes."""
        today = self._bar_dt().date()
        if today != self._current_date:
            self._current_date = today
            self.daily_pnl = 0.0
            self.consec_losses = 0
            self.cooldown_until = None

    def _in_entry_window(self) -> bool:
        """Return True if the current bar is inside the allowed entry window.

        Blocked periods (per spec §3.2 rule 4):
        - First 15 min after open  (09:30 – 09:45)
        - Last 30 min before close (15:30 – 16:00)
        """
        bar_time = self._bar_dt().time()
        earliest = (
            dt.datetime.combine(
                dt.date.today(),
                dt.time(self.p.market_open_hour, self.p.market_open_minute),
            )
            + dt.timedelta(minutes=self.p.entry_blackout_minutes)
        ).time()
        latest = dt.time(self.p.last_entry_hour, self.p.last_entry_minute)
        return earliest <= bar_time < latest

    def _is_eod_close_time(self) -> bool:
        """Return True if the current bar is at or past the EOD close time."""
        bar_time = self._bar_dt().time()
        eod_time = dt.time(self.p.eod_close_hour, self.p.eod_close_minute)
        return bar_time >= eod_time

    # ------------------------------------------------------------------
    # Helpers — circuit breakers
    # ------------------------------------------------------------------
    def _circuit_breaker_active(self) -> bool:
        """Return True if any circuit breaker prohibits new entries.

        Rules (spec §3.4):
        - Daily PnL exceeds loss limit → stop for the day.
        - Consecutive losses hit threshold → cooldown pause.
        """
        if self.daily_pnl <= -abs(self.p.daily_loss_limit):
            self.log("CIRCUIT BREAKER | Daily loss limit reached — no new entries.")
            return True

        if self.consec_losses >= self.p.max_consec_losses:
            now = self._bar_dt()
            if self.cooldown_until is None:
                self.cooldown_until = now + dt.timedelta(
                    minutes=self.p.cooldown_minutes
                )
                self.log(
                    f"CIRCUIT BREAKER | {self.consec_losses} consecutive losses — "
                    f"pausing until {self.cooldown_until:%H:%M}."
                )
            if now < self.cooldown_until:
                return True
            # Cooldown expired — reset and allow trading.
            self.consec_losses = 0
            self.cooldown_until = None

        return False

    # ------------------------------------------------------------------
    # Core logic executed every bar
    # ------------------------------------------------------------------
    def next(self) -> None:  # noqa: C901 — complexity justified by spec
        """Evaluate entry and exit rules on each new bar."""
        self._reset_daily_state_if_new_day()

        # Skip if an order is still pending.
        if self.order is not None:
            return

        current_close: float = self.data.close[0]

        # ==============================================================
        # A) We ARE in a position → check exits (hierarchical order)
        # ==============================================================
        if self.position:
            # --- 1. Hard Stop Loss (spec §3.3.1) ---
            hard_stop_price = self.entry_price * (1.0 - self.p.hard_stop_pct / 100.0)
            if current_close <= hard_stop_price:
                self.log(
                    f"HARD STOP triggered | Close {current_close:.2f} "
                    f"<= Stop {hard_stop_price:.2f}"
                )
                self.order = self.close()
                return

            # --- 2. Dynamic Trailing Stop (spec §3.3.2) ---
            if current_close > self.highest_price:
                self.highest_price = current_close

            atr_val: float = self.atr[0]
            new_trail = self.highest_price - self.p.atr_multiplier * atr_val
            if new_trail > self.trailing_stop_level:
                self.trailing_stop_level = new_trail

            if current_close < self.trailing_stop_level:
                self.log(
                    f"TRAILING STOP triggered | Close {current_close:.2f} "
                    f"< Trail {self.trailing_stop_level:.2f}"
                )
                self.order = self.close()
                return

            # --- 3. End-of-Day forced close (spec §3.3.3) ---
            if self._is_eod_close_time():
                self.log("EOD CLOSE | Forcing position close before market end.")
                self.order = self.close()
                return

        # ==============================================================
        # B) We are NOT in a position → check entry conditions
        # ==============================================================
        else:
            # Gate checks: time window & circuit breakers
            if not self._in_entry_window():
                return
            if self._circuit_breaker_active():
                return

            # --- Entry signal (spec §3.2) ---
            bar_range: float = self.data.high[0] - self.data.low[0]
            atr_val = self.atr[0]
            vol_expansion = bar_range > self.p.vol_expansion * atr_val
            vol_spike = self.data.volume[0] > self.p.vol_multiplier * self.avg_volume[0]
            volatility_ok: bool = vol_expansion or vol_spike

            trend_ok: bool = current_close > self.sma[0]
            rsi_ok: bool = self.rsi[0] < self.p.rsi_upper

            if volatility_ok and trend_ok and rsi_ok:
                size = int(self.p.trade_cash // current_close)
                if size <= 0:
                    return

                self.log(
                    f"BUY SIGNAL | Close {current_close:.2f}, "
                    f"ATR {atr_val:.4f}, RSI {self.rsi[0]:.1f}, "
                    f"Range/ATR {bar_range / atr_val:.2f}, "
                    f"Vol/Avg {self.data.volume[0] / self.avg_volume[0]:.2f}"
                )
                self.order = self.buy(size=size)
