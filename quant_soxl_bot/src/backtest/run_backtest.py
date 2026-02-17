"""
run_backtest.py
===============
Entry-point script for back-testing the VolatilityTrendStrategy on SOXL
5-minute historical data via Backtrader.

Usage
-----
    cd quant_soxl_bot
    python -m src.backtest.run_backtest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — render to file, no GUI needed
import matplotlib.pyplot as plt  # noqa: E402

import backtrader as bt

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so that ``src.*`` imports work
# regardless of the working directory.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.strategies.volatility_trend import VolatilityTrendStrategy
from src.utils.data_loader import load_or_download_soxl_5m_data

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
INITIAL_CASH: float = 10_000.0
COMMISSION: float = 0.0
SIZER_PERCENTS: float = 95.0


import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Custom plotting (avoids Backtrader's oversized figure issue)
# ---------------------------------------------------------------------------
def _save_backtest_chart(
    strat: bt.Strategy,
    df: pd.DataFrame,
    initial_cash: float,
    save_file: Path,
) -> None:
    """Render a 4-panel backtest summary and save to *save_file*.

    Panels: (1) Price + SMA + buy/sell markers,
            (2) Volume, (3) RSI, (4) Portfolio value.
    """
    # --- Extract indicator / trade data from the strategy object ----------
    dt_index = [bt.num2date(strat.data.datetime[i])
                for i in range(-len(strat.data) + 1, 1)]
    closes = np.array([strat.data.close[i]
                       for i in range(-len(strat.data) + 1, 1)])
    volumes = np.array([strat.data.volume[i]
                        for i in range(-len(strat.data) + 1, 1)])
    sma_vals = np.array([strat.sma[i]
                         for i in range(-len(strat.sma) + 1, 1)])
    rsi_vals = np.array([strat.rsi[i]
                         for i in range(-len(strat.rsi) + 1, 1)])

    # Pad shorter indicator arrays to align with dt_index.
    pad_sma = len(dt_index) - len(sma_vals)
    sma_padded = np.concatenate([np.full(pad_sma, np.nan), sma_vals])
    pad_rsi = len(dt_index) - len(rsi_vals)
    rsi_padded = np.concatenate([np.full(pad_rsi, np.nan), rsi_vals])

    # Collect buy / sell points from completed orders in the analyzers.
    buy_dates, buy_prices = [], []
    sell_dates, sell_prices = [], []
    for order in strat._orders:
        if order.status != order.Completed:
            continue
        exec_dt = bt.num2date(order.executed.dt)
        if order.isbuy():
            buy_dates.append(exec_dt)
            buy_prices.append(order.executed.price)
        else:
            sell_dates.append(exec_dt)
            sell_prices.append(order.executed.price)

    # --- Build figure -----------------------------------------------------
    fig, axes = plt.subplots(
        4, 1, figsize=(28, 16), sharex=True,
        gridspec_kw={"height_ratios": [3, 1, 1, 1.5]},
    )

    # Panel 1: Price + SMA + trades
    ax_price = axes[0]
    ax_price.plot(dt_index, closes, linewidth=0.7, color="steelblue", label="Close")
    ax_price.plot(dt_index, sma_padded, linewidth=0.7, color="orange",
                  label=f"SMA({strat.p.sma_period})")
    ax_price.scatter(buy_dates, buy_prices, marker="^", color="lime",
                     edgecolors="black", s=50, zorder=5, label="Buy")
    ax_price.scatter(sell_dates, sell_prices, marker="v", color="red",
                     edgecolors="black", s=50, zorder=5, label="Sell")
    ax_price.set_ylabel("Price ($)")
    ax_price.legend(loc="upper left", fontsize=8)
    ax_price.set_title("SOXL — VolatilityTrendStrategy Back-test", fontsize=13)
    ax_price.grid(True, alpha=0.3)

    # Panel 2: Volume
    ax_vol = axes[1]
    ax_vol.bar(dt_index, volumes, width=0.002, color="gray", alpha=0.6)
    ax_vol.set_ylabel("Volume")
    ax_vol.grid(True, alpha=0.3)

    # Panel 3: RSI
    ax_rsi = axes[2]
    ax_rsi.plot(dt_index, rsi_padded, linewidth=0.7, color="purple")
    ax_rsi.axhline(strat.p.rsi_upper, color="red", linestyle="--", linewidth=0.6,
                   label=f"RSI Upper ({strat.p.rsi_upper})")
    ax_rsi.axhline(30, color="green", linestyle="--", linewidth=0.6, label="RSI 30")
    ax_rsi.set_ylabel("RSI")
    ax_rsi.set_ylim(0, 100)
    ax_rsi.legend(loc="upper left", fontsize=8)
    ax_rsi.grid(True, alpha=0.3)

    # Panel 4: Portfolio value (reconstructed from trades)
    portfolio = np.full(len(dt_index), initial_cash, dtype=float)
    position_size = 0
    entry_cost = 0.0
    for i, dt_val in enumerate(dt_index):
        for bd, bp in zip(buy_dates, buy_prices):
            if bd == dt_val:
                size = int(portfolio[i - 1] * 0.95 / bp) if i > 0 else 0
                position_size += size
                entry_cost += size * bp
        for sd, sp in zip(sell_dates, sell_prices):
            if sd == dt_val:
                if position_size > 0:
                    portfolio[i] = portfolio[i - 1] + position_size * (sp - entry_cost / position_size)
                    position_size = 0
                    entry_cost = 0.0
        if i > 0 and position_size > 0:
            portfolio[i] = portfolio[i - 1] + position_size * (closes[i] - closes[i - 1])
        elif i > 0 and position_size == 0:
            portfolio[i] = portfolio[i - 1]

    ax_pv = axes[3]
    ax_pv.plot(dt_index, portfolio, linewidth=0.8, color="teal")
    ax_pv.axhline(initial_cash, color="gray", linestyle=":", linewidth=0.6)
    ax_pv.set_ylabel("Portfolio ($)")
    ax_pv.set_xlabel("Date")
    ax_pv.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(str(save_file), dpi=120)
    plt.close(fig)
    print(f"\nChart saved to: {save_file.resolve()}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SOXL VolatilityTrend back-test")
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip the matplotlib chart entirely.",
    )
    parser.add_argument(
        "--save-plot",
        type=str,
        default=None,
        metavar="PATH",
        help="Save chart to file instead of showing GUI (e.g. output/backtest.png).",
    )
    return parser.parse_args()


def main() -> None:
    """Build, configure, and run the Backtrader engine."""
    args = _parse_args()

    # 1. Cerebro ----------------------------------------------------------
    cerebro = bt.Cerebro()

    # 2. Data -------------------------------------------------------------
    print("Loading SOXL 5-min data …")
    df = load_or_download_soxl_5m_data()
    print(f"  Rows loaded: {len(df)}  |  "
          f"Range: {df.index.min()} → {df.index.max()}")

    data_feed = bt.feeds.PandasData(
        dataname=df,
        datetime=None,       # index is already the datetime
        open="Open",
        high="High",
        low="Low",
        close="Close",
        volume="Volume",
        openinterest=-1,     # not available
    )
    cerebro.adddata(data_feed, name="SOXL_5m")

    # 3. Strategy ---------------------------------------------------------
    cerebro.addstrategy(VolatilityTrendStrategy)

    # 4. Broker settings --------------------------------------------------
    cerebro.broker.setcash(INITIAL_CASH)
    cerebro.broker.setcommission(commission=COMMISSION)

    # 5. Sizer (95 % of equity per trade — stress-test max drawdown) ------
    cerebro.addsizer(bt.sizers.PercentSizer, percents=SIZER_PERCENTS)

    # 6. Run --------------------------------------------------------------
    print(f"\nStarting Portfolio Value: ${cerebro.broker.getvalue():,.2f}")
    results = cerebro.run()
    strat = results[0]
    final_value = cerebro.broker.getvalue()
    print(f"Final Portfolio Value:    ${final_value:,.2f}")
    print(f"Net PnL:                 ${final_value - INITIAL_CASH:,.2f}")

    # 7. Plot — custom figure for reliable file saving --------------------
    if not args.no_plot:
        save_path = args.save_plot or str(
            _PROJECT_ROOT / "output" / "backtest_result.png"
        )
        save_file = Path(save_path)
        save_file.parent.mkdir(parents=True, exist_ok=True)

        _save_backtest_chart(strat, df, INITIAL_CASH, save_file)


if __name__ == "__main__":
    main()
