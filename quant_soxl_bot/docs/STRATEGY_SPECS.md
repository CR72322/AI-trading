# SOXL Intraday Volatility Trading Bot - Design Specifications

## 1. Overview
A high-frequency/intraday trading bot designed for 3x Leveraged ETFs (specifically SOXL). The strategy exploits short-term volatility expansion while strictly managing downside risk through dynamic trailing stops and hard circuit breakers.

**Core Philosophy:** "Enter on volatility expansion, exit on trend weakness. Do not hold losing positions."

## 2. Technical Architecture
- **Language:** Python 3.10+
- **Backtesting Engine:** `Backtrader`
- **Live Execution:** `ib_insync` (Interactive Brokers)
- **Data Source:** `yfinance` (Historical), IBKR (Real-time Tick/5s Bar)
- **Timeframe:** 1-Minute / 5-Minute Bars

## 3. Strategy Logic (The "Alpha")

### 3.1 Indicators
- **ATR (Average True Range):** Period = 14. Used to measure volatility.
- **SMA (Simple Moving Average):** Period = 20. Used for short-term trend baseline.
- **RSI (Relative Strength Index):** Period = 14. Used to avoid buying at extreme overbought levels (>75).

### 3.2 Entry Rules (Long Only)
Trigger a **BUY** signal if ALL conditions are met:
1.  **Volatility Expansion:** Current Bar Range > `1.5 * ATR(14)` OR Volume > `2.0 * AverageVolume(20)`.
2.  **Trend Filter:** Price > `SMA(20)`.
3.  **Not Overbought:** RSI < 70 (Prevent buying at the top).
4.  **Time Filter:** No new entries in the first 15 mins (9:30-9:45) or last 30 mins (15:30-16:00) of the market.

### 3.3 Exit Rules
The exit logic is hierarchical. Check in this order:

1.  **Hard Stop Loss (Safety Net):**
    - Fixed % loss from entry price.
    - Value: `2.0%` max loss.
    - Action: Market Sell immediately.

2.  **Dynamic Trailing Stop (Profit Locking):**
    - **Concept:** The stop price rises as the stock price rises, but never falls.
    - **Calculation:** `StopPrice = HighestPriceSinceEntry - (ATR_Multiplier * ATR)`
    - **ATR_Multiplier:** Dynamic based on volatility (default `2.5`).
    - Action: Market Sell if `CurrentPrice < StopPrice`.

3.  **End of Day (EOD):**
    - Force close all positions at 15:55 EST to avoid overnight gap risk.

### 3.4 Risk Management (Circuit Breakers)
- **Daily Drawdown Limit:** If daily PnL < -$500 (or -2% of account), **STOP TRADING** for the day.
- **Consecutive Loss Limit:** If 3 consecutive trades are losers, pause trading for 30 minutes.
- **Position Sizing:** Fixed cash amount per trade (e.g., $10,000) or % of equity.

## 4. Code Structure & Naming Conventions
- **Class Name:** `VolatilityTrendStrategy`
- **Key Variables:**
    - `self.highest_high`: Tracks highest price since entry.
    - `self.trailing_stop_level`: Current price level to trigger exit.
    - `self.order`: Current pending order object.

## 5. Development Roadmap
1.  **Phase 1 (Current):** Implement `Backtrader` strategy with `yfinance` data. Validate logic on historical data (2022 bear market & 2023 bull market).
2.  **Phase 2:** Implement logic visualization (matplotlib) to debug entry/exit points.
3.  **Phase 3:** Build `ib_insync` connector for paper trading.