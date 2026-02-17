# VolatilityTrendStrategy — Mathematical Specification

> **Instrument:** SOXL (Direxion Daily Semiconductor Bull 3x Shares)
> **Timeframe:** 5-Minute Bars (Intraday)
> **Strategy Class:** Long-Only Momentum / Volatility-Expansion

---

## 1. Variable Definitions

### 1.1 Price Data (Per Bar)

| Symbol | Definition |
|--------|-----------|
| $O_t$ | Open price at bar $t$ |
| $H_t$ | High price at bar $t$ |
| $L_t$ | Low price at bar $t$ |
| $C_t$ | Close price at bar $t$ |
| $V_t$ | Volume at bar $t$ |

### 1.2 Technical Indicators

#### Average True Range (ATR)

Measures volatility as the exponential moving average of the True Range.

$$
\text{TR}_t = \max\bigl(H_t - L_t,\;\lvert H_t - C_{t-1}\rvert,\;\lvert L_t - C_{t-1}\rvert\bigr)
$$

$$
\text{ATR}_t = \frac{1}{n}\sum_{i=0}^{n-1} \text{TR}_{t-i}
\quad\text{(initial seed)},
\qquad
\text{ATR}_t = \frac{(n-1)\cdot\text{ATR}_{t-1} + \text{TR}_t}{n}
\quad\text{(subsequent)}
$$

where $n = 14$ (parameter `atr_period`).

**Role in strategy:** Normalises all distance-based thresholds (stops, entry filters) to the current volatility regime. A higher ATR widens stops; a lower ATR tightens them.

#### Exponential Moving Average (EMA)

$$
\text{EMA}_t = \alpha \cdot C_t + (1 - \alpha)\cdot\text{EMA}_{t-1},
\qquad \alpha = \frac{2}{n + 1}
$$

where $n = 50$ (parameter `ema_period`).

**Role in strategy:** Primary trend filter. Entry is permitted only when $C_t > \text{EMA}_{50,t}$, ensuring positions are opened in the direction of the intermediate trend.

#### Relative Strength Index (RSI)

$$
\text{RSI}_t = 100 - \frac{100}{1 + \text{RS}_t},
\qquad
\text{RS}_t = \frac{\text{AvgGain}_{t,n}}{\text{AvgLoss}_{t,n}}
$$

where $n = 14$ (parameter `rsi_period`). Gains and losses use Wilder's smoothing.

**Role in strategy:** Dual-purpose overbought filter.
- Signal bar: $\text{RSI}_t < 70$ (parameter `rsi_upper`).
- Confirmation bar: $\text{RSI}_t < 75$ (parameter `rsi_confirm_upper`).

#### Average Directional Index (ADX)

$$
\text{ADX}_t = \text{EMA}\bigl(\lvert \text{DI}^{+}_t - \text{DI}^{-}_t\rvert \;/\; (\text{DI}^{+}_t + \text{DI}^{-}_t),\; n\bigr) \times 100
$$

where $n = 14$ (parameter `adx_period`).

**Role in strategy:** Trend-strength gate. Entry is only permitted when $\text{ADX}_t > 25$ (parameter `adx_threshold`), filtering out range-bound / choppy markets.

#### Average Volume

$$
\overline{V}_t = \text{SMA}(V, 20) = \frac{1}{20}\sum_{i=0}^{19} V_{t-i}
$$

**Role in strategy:** Baseline for the volume-spike entry condition.

---

## 2. Parameter Matrix

| Parameter | Symbol | Default | Description | Optimisation Range |
|-----------|--------|---------|-------------|-------------------|
| `atr_period` | $n_{\text{ATR}}$ | 14 | ATR look-back window | 10 – 20 |
| `atr_multiplier` | $k_{\text{trail}}$ | 3.5 | Trailing stop: ATR distance from highest high | 2.0 – 5.0 |
| `sma_period` | $n_{\text{SMA}}$ | 20 | SMA period (used for avg volume) | 10 – 30 |
| `rsi_period` | $n_{\text{RSI}}$ | 14 | RSI look-back window | 10 – 20 |
| `rsi_upper` | $\theta_{\text{RSI}}$ | 70 | Max RSI at signal bar for entry | 60 – 75 |
| `rsi_confirm_upper` | $\theta_{\text{RSI}}^{\text{conf}}$ | 75 | Max RSI at confirmation bar | 70 – 80 |
| `vol_expansion` | $k_{\text{range}}$ | 1.5 | Min bar-range / ATR ratio for volatility expansion | 1.0 – 2.5 |
| `vol_multiplier` | $k_{\text{vol}}$ | 2.0 | Min volume / avg-volume ratio for volume spike | 1.5 – 3.0 |
| `adx_period` | $n_{\text{ADX}}$ | 14 | ADX look-back window | 10 – 20 |
| `adx_threshold` | $\theta_{\text{ADX}}$ | 25 | Minimum ADX for trend confirmation | 20 – 35 |
| `ema_period` | $n_{\text{EMA}}$ | 50 | EMA trend filter period | 30 – 80 |
| `initial_stop_atr_dist` | $k_{\text{stop}}$ | 2.0 | ATR multiplier for initial hard stop | 1.5 – 3.0 |
| `max_stop_pct` | $\delta_{\max}$ | 3.0% | Absolute cap on hard stop distance | 2.0% – 5.0% |
| `breakeven_atr_mult` | $k_{\text{BE}}$ | 1.0 | ATR profit threshold to trigger break-even | 0.5 – 2.0 |
| `exit_cooldown_bars` | $n_{\text{cool}}$ | 6 | Min bars between exit and next entry | 4 – 12 |
| `trade_cash` | $Q$ | $10,000 | Fixed dollar amount per trade | Account-dependent |
| `daily_loss_limit` | $L_{\text{day}}$ | $500 | Daily PnL floor before circuit breaker | Account-dependent |
| `max_consec_losses` | $N_{\text{loss}}$ | 3 | Consecutive losses before cooldown pause | 2 – 5 |
| `cooldown_minutes` | $T_{\text{pause}}$ | 30 min | Duration of consecutive-loss pause | 15 – 60 min |

---

## 3. Core Formulas

### 3.1 Trend Definition

A bar $t$ is considered to be in an **uptrend** if and only if:

$$
C_t > \text{EMA}_{50,t}
$$

This replaces the earlier $C_t > \text{SMA}_{20,t}$ condition. The longer EMA window (250 minutes at 5m bars) provides a more stable trend baseline, reducing whipsaws during short-term mean-reversion.

### 3.2 Entry Signal (Two-Phase)

#### Phase 1 — Signal Bar (bar $t$)

All four conditions must hold simultaneously:

$$
\underbrace{
\Bigl[(H_t - L_t) > k_{\text{range}} \cdot \text{ATR}_t\Bigr]
\;\lor\;
\Bigl[V_t > k_{\text{vol}} \cdot \overline{V}_t\Bigr]
}_{\text{Volatility Expansion}}
\;\land\;
\underbrace{C_t > \text{EMA}_{50,t}}_{\text{Trend}}
\;\land\;
\underbrace{\text{RSI}_t < \theta_{\text{RSI}}}_{\text{Not Overbought}}
\;\land\;
\underbrace{\text{ADX}_t > \theta_{\text{ADX}}}_{\text{Trend Strength}}
$$

If true, record:

$$
H_t^{\text{sig}} \coloneqq H_t, \qquad \text{state} \leftarrow \texttt{WAITING\_CONFIRMATION}
$$

No order is placed on this bar.

#### Phase 2 — Confirmation Bar (bar $t+1$)

$$
\text{BUY if}\quad
C_{t+1} > H_t^{\text{sig}}
\;\land\;
\text{RSI}_{t+1} < \theta_{\text{RSI}}^{\text{conf}}
$$

$$
\text{CANCEL if}\quad C_{t+1} < \text{EMA}_{50,t+1}
$$

The confirmation requirement ensures that momentum persists beyond a single bar, filtering out spike-and-reverse (whipsaw) patterns.

### 3.3 ATR Dynamic Hard Stop (with Cap)

At the moment of entry (execution price $P_{\text{entry}}$, ATR at entry $\sigma_0$):

$$
d_{\text{ATR}} = k_{\text{stop}} \cdot \sigma_0
$$

$$
d_{\text{cap}} = P_{\text{entry}} \cdot \frac{\delta_{\max}}{100}
$$

$$
d^{*} = \min(d_{\text{ATR}},\; d_{\text{cap}})
$$

$$
\boxed{S_{\text{hard}} = P_{\text{entry}} - d^{*}}
$$

**Interpretation:** The stop distance adapts to current volatility via ATR, but is never wider than $\delta_{\max}$% of the entry price. This prevents outsized losses during volatility spikes (e.g., earnings, macro events).

**Exit trigger:**

$$
C_t \leq S_{\text{hard}} \implies \text{MARKET SELL}
$$

### 3.4 Break-Even Trigger

Once the position's intraday high exceeds the entry price by at least $k_{\text{BE}}$ times the entry ATR:

$$
H_t - P_{\text{entry}} > k_{\text{BE}} \cdot \sigma_0
\implies
S_{\text{hard}} \leftarrow P_{\text{entry}} \times 1.001
$$

The $\times 1.001$ factor provides a minimal buffer above exact break-even to cover commission slippage. This upgrade is **irreversible** — the stop can only rise from this point (via the trailing stop mechanism), never fall back.

**State machine:**

$$
\text{breakeven\_triggered}: \texttt{false} \xrightarrow{H_t - P_e > k_{\text{BE}}\sigma_0} \texttt{true} \quad\text{(one-way latch)}
$$

### 3.5 Dynamic Trailing Stop

The trailing stop is evaluated on every bar while in a position. It tracks the highest observed price and applies a volatility-scaled cushion:

$$
P_{\text{high},t} = \max(P_{\text{high},t-1},\; C_t)
$$

$$
S_{\text{trail},t}^{\text{new}} = P_{\text{high},t} - k_{\text{trail}} \cdot \text{ATR}_t
$$

$$
S_{\text{trail},t} = \max(S_{\text{trail},t-1},\; S_{\text{trail},t}^{\text{new}})
$$

The trailing stop **only ratchets upward**, never downward. This locks in progressively more profit as the position moves favourably.

**Exit trigger:**

$$
C_t < S_{\text{trail},t} \implies \text{MARKET SELL}
$$

### 3.6 End-of-Day Forced Close

$$
t_{\text{bar}} \geq 15{:}55\;\text{ET} \implies \text{CLOSE ALL POSITIONS}
$$

Eliminates overnight gap risk, which is especially severe for 3x leveraged ETFs.

---

## 4. Exit Priority Hierarchy

Exits are evaluated in strict order on each bar. The first condition that fires executes; subsequent checks are skipped.

$$
\text{Break-Even upgrade} \;\rightarrow\; \text{Hard Stop} \;\rightarrow\; \text{Trailing Stop} \;\rightarrow\; \text{EOD Close}
$$

| Priority | Mechanism | Condition | Purpose |
|----------|-----------|-----------|---------|
| 0 | Break-Even | $H_t - P_e > k_{\text{BE}} \sigma_0$ | Lock in cost basis |
| 1 | Hard Stop | $C_t \leq S_{\text{hard}}$ | Maximum loss cap |
| 2 | Trailing Stop | $C_t < S_{\text{trail},t}$ | Profit protection |
| 3 | EOD Close | $t \geq$ 15:55 ET | Overnight risk elimination |

Note: Priority 0 (Break-Even) is a stop **upgrade**, not an exit. It modifies $S_{\text{hard}}$ before the Hard Stop check at Priority 1.

---

## 5. Entry Gate Filters

Before any signal evaluation occurs, the following gates must all pass:

| Gate | Condition | Purpose |
|------|-----------|---------|
| Time Window | $09{:}45 \leq t < 15{:}30$ ET | Avoid open/close volatility |
| Circuit Breaker (Daily) | $\sum \text{PnL}_{\text{today}} > -L_{\text{day}}$ | Cap daily drawdown |
| Circuit Breaker (Streak) | Consecutive losses $< N_{\text{loss}}$ | Pause after losing streak |
| Post-Exit Cooldown | Bars since last exit $> n_{\text{cool}}$ | Prevent immediate re-entry |

---

## 6. Risk Metrics (Per Trade)

### Maximum Dollar Risk

$$
R_{\max} = \text{Size} \times d^{*} = \text{Size} \times \min\bigl(k_{\text{stop}}\cdot\sigma_0,\; P_e \cdot \tfrac{\delta_{\max}}{100}\bigr)
$$

### Risk-Reward at Break-Even Threshold

At the break-even trigger point, the position has earned at least:

$$
\text{Unrealised Profit} \geq \text{Size} \times k_{\text{BE}} \cdot \sigma_0
$$

After the trigger, downside is capped at approximately zero (the stop is at $P_e \times 1.001$), converting the remaining position into a **free carry** with upside optionality.

### Reward-to-Risk Ratio (Target)

For the trailing stop to be reached after break-even:

$$
\frac{\text{Profit at Trail Exit}}{\text{Risk after BE}} \approx \frac{k_{\text{trail}} \cdot \text{ATR}_t}{\epsilon} \gg 1
$$

where $\epsilon \approx P_e \times 0.001$ is the break-even buffer. This asymmetry is the core edge of the strategy.
