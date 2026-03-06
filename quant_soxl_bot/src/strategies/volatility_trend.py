"""
VolatilityTrendStrategy
=======================
Swing/volatility-expansion strategy for SOXL (3x Leveraged ETF).

Design reference: docs/STRATEGY_SPECS.md
- Enter on volatility expansion + trend confirmation.
- Exit via Hard Stop → Trailing Stop (no EOD forced close — positions may run overnight).
- Circuit breakers for daily drawdown and consecutive losses.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

import backtrader as bt


class VolatilityTrendStrategy(bt.Strategy):
    """Intraday volatility-trend strategy for SOXL.

    All tuneable numbers are exposed as ``params`` so that Backtrader's
    built-in ``cerebro.optstrategy()`` can sweep them automatically.

    Parameters
    ----------
    atr_period : int
        Look-back window for ATR.
    ema_period : int
        Look-back window for the EMA trend filter.
    adx_period : int
        Look-back window for the ADX trend-strength indicator.
    adx_threshold : float
        Minimum ADX to confirm a trending market before entry.
    rsi_period : int
        Look-back window for RSI.
    rsi_ceiling : float
        Max RSI at *signal bar* — no entry when RSI >= this value.
    rsi_confirm_upper : float
        Max RSI at *confirmation bar* — rejects overbought confirmations.
    vol_expansion : float
        Bar-range / ATR ratio that qualifies as volatility expansion.
    vol_multiplier : float
        Volume / AvgVolume ratio that qualifies as volume spike.
    sma_period : int
        SMA period used for average-volume baseline.
    stop_loss_atr_dist : float
        ATR multiplier for the initial hard stop distance.
    enable_break_even : bool
        Whether the break-even stop mechanism is active.
    break_even_atr_dist : float
        ATR multiplier for the break-even profit threshold.
    trailing_stop_atr_dist : float
        ATR multiplier for the trailing stop distance from highest high.
    max_stop_loss_pct : float
        Absolute cap on hard stop distance as a decimal (0.03 = 3 %).
    trade_cash : float
        Fixed dollar amount allocated per trade.
    entry_start_hour / entry_start_minute : int
        Earliest time (ET) at which new entries are allowed (default 09:45).
    entry_end_hour / entry_end_minute : int
        Latest time (ET) at which new entries are allowed (default 15:30).
    daily_loss_limit : float
        Maximum daily dollar loss before circuit breaker halts trading.
    max_consec_losses : int
        Consecutive losing trades before a cooldown pause.
    cooldown_minutes : int
        Minutes to pause after hitting consecutive loss limit.
    exit_cooldown_bars : int
        Minimum bars to wait after an exit before re-entering.
    """

    params: dict = dict(
        # --- Indicators ---
        atr_period=14,
        ema_period=50,
        adx_period=14,
        adx_threshold=25.0,
        rsi_period=14,
        rsi_ceiling=70.0,
        rsi_confirm_upper=75.0,
        vol_expansion=1.5,
        vol_multiplier=2.0,
        sma_period=20,
        # --- Risk / Stops ---
        stop_loss_atr_dist=2.0,
        enable_break_even=False,
        break_even_atr_dist=1.0,
        trailing_stop_atr_dist=3.5,
        max_stop_loss_pct=0.03,
        trade_cash=10_000.0,
        # --- Time windows (ET) ---
        entry_start_hour=9,
        entry_start_minute=45,
        entry_end_hour=15,
        entry_end_minute=30,
        # --- Circuit breakers ---
        daily_loss_limit=500.0,
        max_consec_losses=3,
        cooldown_minutes=30,
        # --- Trade cooldown ---
        exit_cooldown_bars=6,
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
        # ADX — trend-strength filter to avoid entries in choppy / range-bound markets
        self.adx = bt.indicators.AverageDirectionalMovementIndex(
            self.data, period=self.p.adx_period
        )
        # EMA(50) — longer-term trend filter (replaces SMA(20) for entry decisions)
        self.ema50 = bt.indicators.ExponentialMovingAverage(
            self.data.close, period=self.p.ema_period
        )

        # Order / position tracking
        self.order: Optional[bt.Order] = None
        self.entry_price: float = 0.0
        self.entry_size: float = 0.0
        self.entry_atr: float = 0.0           # ATR at the moment of entry (for stop)
        self.hard_stop_price: float = 0.0     # computed at entry, may be raised to BE
        self.highest_price: float = 0.0
        self.trailing_stop_level: float = 0.0
        self.breakeven_triggered: bool = False  # True once stop has been moved to BE
        self.last_exit_price: Optional[float] = None
        self.trade_log: list[dict] = []

        # Confirmation bar — delay entry by one bar to filter whipsaws
        self.waiting_confirmation: bool = False
        self.signal_bar_high: float = 0.0

        # Trade cooldown — prevent re-entry too soon after an exit
        self.bars_since_exit: int = 0

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
                self.entry_size = abs(order.executed.size)
                self.entry_atr = self.atr[0]
                atr_dist = self.p.stop_loss_atr_dist * self.entry_atr
                pct_cap = self.entry_price * self.p.max_stop_loss_pct
                stop_dist = min(atr_dist, pct_cap)
                self.hard_stop_price = self.entry_price - stop_dist
                self.highest_price = order.executed.price
                self.trailing_stop_level = 0.0
                self.breakeven_triggered = False
                self.log(
                    f"BUY EXECUTED | Price: {order.executed.price:.2f}, "
                    f"Size: {order.executed.size:.0f}, "
                    f"Cost: {order.executed.value:.2f}, "
                    f"Comm: {order.executed.comm:.2f}, "
                    f"HardStop: {self.hard_stop_price:.2f} "
                    f"(ATR@entry={self.entry_atr:.4f}, "
                    f"dist={stop_dist:.4f}, cap={pct_cap:.4f})"
                )
            elif order.issell():
                # Reset cooldown counter — the wait starts NOW.
                self.bars_since_exit = 0
                self.last_exit_price = order.executed.price
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

        direction = "Long" if trade.long else "Short"
        entry_price = float(trade.price)
        close_price = (
            float(self.last_exit_price)
            if self.last_exit_price is not None
            else float(trade.price)
        )
        # Backtrader marks closed trades with size=0; use cached entry size.
        position_size = abs(self.entry_size) if self.entry_size else 0.0
        notional = abs(entry_price * position_size)
        return_pct = (pnl / notional) if notional else 0.0

        self.trade_log.append(
            {
                "open_time": bt.num2date(trade.dtopen),
                "close_time": bt.num2date(trade.dtclose),
                "direction": direction,
                "entry_price": round(entry_price, 4),
                "exit_price": round(close_price, 4),
                "pnl_net": round(float(pnl), 4),
                "return_pct": round(return_pct * 100.0, 4),
            }
        )

        if pnl < 0:
            self.consec_losses += 1
            self.log(
                f"TRADE CLOSED (LOSS) | PnL: {pnl:.2f}, "
                f"Consecutive losses: {self.consec_losses}"
            )
        else:
            self.consec_losses = 0
            self.log(f"TRADE CLOSED (WIN)  | PnL: {pnl:.2f}")

    def stop(self) -> None:
        """Print a trade report table at strategy end."""
        if not self.trade_log:
            return

        import pandas as pd

        df = pd.DataFrame(self.trade_log)
        print("\n=== Trade Log Report ===")
        try:
            print(df.to_markdown(index=False))
        except Exception:
            print(df.to_string(index=False))

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
        """Return True if the current bar falls within the allowed entry window.

        Window: [entry_start_hour:entry_start_minute, entry_end_hour:entry_end_minute)
        Default 09:45–15:30 ET — avoids the first 15 min open auction.
        """
        bar_time = self._bar_dt().time()
        start = dt.time(self.p.entry_start_hour, self.p.entry_start_minute)
        end = dt.time(self.p.entry_end_hour, self.p.entry_end_minute)
        return start <= bar_time < end

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

        # Tick the post-exit cooldown counter every bar.
        if not self.position:
            self.bars_since_exit += 1

        # Skip if an order is still pending.
        if self.order is not None:
            return

        current_close: float = self.data.close[0]

        # ==============================================================
        # A) We ARE in a position → check exits (hierarchical order)
        # ==============================================================
        if self.position:
            # --- 0. Break-Even Trigger (optional) ---
            if (
                self.p.enable_break_even
                and not self.breakeven_triggered
                and (self.data.high[0] - self.entry_price)
                > self.p.break_even_atr_dist * self.entry_atr
            ):
                be_price = self.entry_price * 1.001
                if be_price > self.hard_stop_price:
                    self.hard_stop_price = be_price
                    self.breakeven_triggered = True
                    self.log(
                        f"BREAK-EVEN | Profit > {self.p.break_even_atr_dist}×ATR, "
                        f"stop raised to {self.hard_stop_price:.2f}"
                    )

            # --- 1. ATR Dynamic Hard Stop (with BE upgrade & max-% cap) ---
            if current_close <= self.hard_stop_price:
                self.log(
                    f"HARD STOP triggered | Close {current_close:.2f} "
                    f"<= Stop {self.hard_stop_price:.2f}"
                )
                self.order = self.close()
                return

            # --- 2. Dynamic Trailing Stop (spec §3.3.2) ---
            if current_close > self.highest_price:
                self.highest_price = current_close

            atr_val: float = self.atr[0]
            new_trail = self.highest_price - self.p.trailing_stop_atr_dist * atr_val
            if new_trail > self.trailing_stop_level:
                self.trailing_stop_level = new_trail

            if current_close < self.trailing_stop_level:
                self.log(
                    f"TRAILING STOP triggered | Close {current_close:.2f} "
                    f"< Trail {self.trailing_stop_level:.2f}"
                )
                self.order = self.close()
                return

        # ==============================================================
        # B) We are NOT in a position → check entry conditions
        # ==============================================================
        else:
            # Gate checks: time window, circuit breakers, post-exit cooldown
            if not self._in_entry_window():
                self.waiting_confirmation = False
                return
            if self._circuit_breaker_active():
                self.waiting_confirmation = False
                return
            if self.bars_since_exit <= self.p.exit_cooldown_bars:
                return  # still cooling down after previous exit

            # ---- Confirmation bar: execute deferred buy if confirmed ----
            if self.waiting_confirmation:
                if current_close > self.signal_bar_high:
                    # RSI ceiling check at confirmation — reject if severely overbought
                    if self.rsi[0] >= self.p.rsi_confirm_upper:
                        self.log(
                            f"SIGNAL CANCELLED (RSI) | RSI {self.rsi[0]:.1f} "
                            f">= {self.p.rsi_confirm_upper} at confirmation"
                        )
                        self.waiting_confirmation = False
                        return
                    # Confirmation passed — price broke above signal bar's high
                    self.log(
                        f"BUY CONFIRMED | Close {current_close:.2f} "
                        f"> SignalHigh {self.signal_bar_high:.2f}, "
                        f"RSI {self.rsi[0]:.1f}"
                    )
                    self.waiting_confirmation = False
                    self.order = self.buy()
                elif current_close < self.ema50[0]:
                    # Invalidated — price fell back below EMA(50), cancel signal
                    self.log(
                        f"SIGNAL CANCELLED | Close {current_close:.2f} "
                        f"< EMA50 {self.ema50[0]:.2f}"
                    )
                    self.waiting_confirmation = False
                # else: still waiting, do nothing this bar
                return

            # ---- Primary entry signal (spec §3.2 + ADX + EMA50) --------
            bar_range: float = self.data.high[0] - self.data.low[0]
            atr_val = self.atr[0]
            vol_expansion = bar_range > self.p.vol_expansion * atr_val
            vol_spike = self.data.volume[0] > self.p.vol_multiplier * self.avg_volume[0]
            volatility_ok: bool = vol_expansion or vol_spike

            trend_ok: bool = current_close > self.ema50[0]   # ← EMA(50) replaces SMA(20)
            rsi_ok: bool = self.rsi[0] < self.p.rsi_ceiling
            adx_ok: bool = self.adx[0] > self.p.adx_threshold

            if volatility_ok and trend_ok and rsi_ok and adx_ok:
                # Don't buy immediately — record signal and wait one bar
                self.signal_bar_high = self.data.high[0]
                self.waiting_confirmation = True
                self.log(
                    f"BUY SIGNAL (pending) | Close {current_close:.2f}, "
                    f"ATR {atr_val:.4f}, RSI {self.rsi[0]:.1f}, "
                    f"ADX {self.adx[0]:.1f}, "
                    f"Range/ATR {bar_range / atr_val:.2f}, "
                    f"Vol/Avg {self.data.volume[0] / self.avg_volume[0]:.2f}, "
                    f"SignalHigh {self.signal_bar_high:.2f}"
                )
