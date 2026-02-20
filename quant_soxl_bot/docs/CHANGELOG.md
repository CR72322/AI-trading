# Strategy Changelog — VolatilityTrendStrategy (SOXL)

> 按时间倒序排列。每条记录包含：假设、改动、回测结果与观察。

---

## Experiment 005 — Break-Even 开关 + 时间滤网参数化

| Field | Detail |
|-------|--------|
| **Date** | 2026-02-16 |
| **File** | `src/strategies/volatility_trend.py` |
| **Backtest Period** | 2025-12-19 → 2026-02-13 (Alpaca IEX 5m) |

### Hypothesis

Experiment 004 的网格寻优（27 组合）发现：
- **保本机制触发过早**（`break_even_atr_dist=1.0`），止损被拉升至保本位后被日内正常波动扫出，无法捕捉主升浪。
- TrailATR 对结果几乎无影响 → 多数退出走的是硬止损/保本，移动止盈未被触发。
- 需要能在优化时**彻底关闭保本**以评估其实际价值。

同时旧的时间窗口由 `market_open_hour` + `entry_blackout_minutes` + `last_entry_hour/minute` 三个冗余参数组合计算，不直观且不便于在 `optstrategy` 中调节。

### Changes

1. **`enable_break_even` 开关**
   - 新增布尔参数 `enable_break_even=True`。
   - `next()` 中保本逻辑前置 `self.p.enable_break_even` 判断。
   - 优化器可传入 `enable_break_even=[True, False]` 对比开关效果。

2. **时间滤网参数化**
   - 移除旧参数 `market_open_hour/minute`、`entry_blackout_minutes`、`last_entry_hour/minute`。
   - 新增 `entry_start_hour=9, entry_start_minute=45` 和 `entry_end_hour=15, entry_end_minute=30`。
   - `_in_entry_window()` 简化为直接比较 `start <= bar_time < end`。
   - 默认窗口 09:45–15:30 ET（避开开盘 15 分钟拍卖和收盘前 30 分钟强平区间）。

3. **保留不变**
   - EOD 强制平仓 15:55 (`eod_close_hour/minute`) 不变。
   - EMA50 趋势过滤、Confirmation Bar、RSI Ceiling、ADX 门控、冷却期等全部保留。

### Results

| Metric | Value |
|--------|-------|
| Net PnL | *待回测* |
| Trades | *待回测* |

**预期：** 关闭保本后策略应能持有更久、捕捉更大波段；时间参数化为后续优化（如提前到 10:00 开始入场）提供便利。

---

## Experiment 004 — Break-Even + RSI Ceiling + Stop Cap

| Field | Detail |
|-------|--------|
| **Date** | 2026-02-17 |
| **File** | `src/strategies/volatility_trend.py` |
| **Backtest Period** | 2025-12-19 → 2026-02-13 (Alpaca IEX 5m) |

### Hypothesis

策略在趋势末端回撤较大，且存在高位追高风险。需要更早锁定利润并限制最大亏损。

### Changes

1. **保本止损 (Break-Even Trigger)**
   - 浮盈 > `1.0 × entry_ATR` 时，`hard_stop_price` 上移至 `entry_price × 1.001`。
   - 一旦触发不可逆，保证至少保本出场。

2. **RSI 入场天花板 (RSI Ceiling)**
   - 在 Confirmation Bar 阶段增加 `RSI < 75` 检查。
   - 防止在严重超买区域开新仓。

3. **ATR 止损加盖 (Max Stop Loss Cap)**
   - `stop_dist = min(ATR × 2.0, entry_price × 3%)`
   - 即使 ATR 极大，单笔最大亏损也不超过 3%。

### Results

| Metric | Value |
|--------|-------|
| Net PnL | **-$795** |
| Trades | 8 |
| Max Single Loss | **-$264** (vs V3 的 -$384) |
| Break-Even 触发 | 3 次 |
| RSI 天花板拦截 | 2 笔 |

**观察：**
- 保本止损效果显著：2/5 那笔潜在亏损从 -$384 压缩到仅 -$14。
- RSI 天花板成功拦截了 1/21 (RSI=76.9) 和 1/27 (RSI=75.3) 两笔高位追涨。
- 但 RSI 天花板也过滤掉了两笔盈利交易 (+$430, +$177)，导致总 PnL 低于 V3。
- 3% Cap 在 2/5 高波动期间生效，限制了下行风险。

---

## Experiment 003 — ATR Dynamic Stop + EMA(50) + Confirmation Bar

| Field | Detail |
|-------|--------|
| **Date** | 2026-02-17 |
| **File** | `src/strategies/volatility_trend.py` |
| **Backtest Period** | 2025-12-19 → 2026-02-13 (Alpaca IEX 5m) |

### Hypothesis

固定 2% 的硬止损在 SOXL 高波动下容易被噪音震出；SMA(20) 均线过于敏感，在均线纠缠时产生大量假信号。

### Changes

1. **ATR 动态硬止损**
   - 废弃固定 2% 止损。
   - 改为 `entry_price - 2.0 × ATR_at_entry`，根据波动率环境自适应。

2. **EMA(50) 趋势过滤**
   - 入场条件从 `Close > SMA(20)` 改为 `Close > EMA(50)`。
   - 覆盖约半天窗口，过滤短期均线纠缠。

3. **确认 K 线机制 (Confirmation Bar)**
   - 信号触发当根 bar 不下单，只记录 `signal_bar_high`。
   - 下一根 bar：`Close > signal_bar_high` → 确认买入；`Close < EMA(50)` → 取消信号。

### Results

| Metric | Value |
|--------|-------|
| Net PnL | **-$214** |
| Trades | 10 |
| Win Rate | 40% (4W / 6L) |
| Max Single Loss | -$384 |

**观察：**
- 12 月整个震荡期零入场，confirmation bar 成功过滤了全部假突破。
- 交易次数从 25 笔降到 10 笔，资金曲线明显平滑。
- 2/2 和 2/3 两笔信号被 EMA50 取消，避免了约 -$600 的潜在亏损。
- 但 ATR 止损在高波动时偏宽，2/5 单笔亏损 -$384。
- 仍存在利润回吐问题（保本机制尚未引入）。

---

## Experiment 002 — Trailing Stop + ADX Filter + Cooldown

| Field | Detail |
|-------|--------|
| **Date** | 2026-02-16 |
| **File** | `src/strategies/volatility_trend.py` |
| **Backtest Period** | 2025-12-19 → 2026-02-13 (Alpaca IEX 5m) |

### Hypothesis

简单的波动率突破需要配合移动止盈才能拿住长线趋势；同时需要过滤震荡市的假信号。

### Changes

1. **放宽 Trailing Stop**
   - `atr_multiplier` 从 2.5 提高到 3.5，给趋势更大呼吸空间。

2. **ADX 趋势强度过滤**
   - 新增 `ADX(14) > 25` 作为入场条件。
   - ADX 低于 25 视为震荡市，拒绝开仓。

3. **交易冷却 (Cooldown)**
   - 卖出后等待至少 6 根 bar（30 分钟）才允许再次入场。
   - 避免被洗出后立即追回。

### Results

| Metric | Value |
|--------|-------|
| Net PnL | **+$149** |
| Trades | 25 |
| Win Rate | ~40% |
| Max Single Loss | -$341 |

**观察：**
- 相比 V1 扭亏为盈（-$1,243 → +$149），交易次数减少 40%。
- ADX 过滤器成功拦截了震荡市中的多数假信号。
- 放宽的 trailing stop 让 1/21 (+$605)、1/27 (+$385)、2/2 (+$504) 等大单跑到了 EOD。
- 冷却期避免了被洗盘后立即追高的恶性循环。
- 但 2% 固定硬止损仍然是主要亏损来源，频繁被 SOXL 日内波动触发。

---

## Experiment 001 — Initial Prototype (Baseline)

| Field | Detail |
|-------|--------|
| **Date** | 2026-02-16 |
| **File** | `src/strategies/volatility_trend.py` |
| **Backtest Period** | 2025-12-19 → 2026-02-13 (Alpaca IEX 5m) |

### Hypothesis

利用 SOXL 的高波动率进行日内波动率突破交易。ATR 突破 + SMA 趋势过滤 + RSI 超买过滤应该能捕捉日内动量。

### Changes

- 基础波动率突破策略：`Bar Range > 1.5 × ATR(14)` 或 `Volume > 2.0 × AvgVol(20)`。
- 趋势过滤：`Close > SMA(20)`。
- RSI 过滤：`RSI(14) < 70`。
- 时间窗口：排除开盘 15 分钟和收盘 30 分钟。
- 硬止损：固定 2%。
- 移动止盈：`HighestPrice - 2.5 × ATR`。
- EOD 强制平仓：15:55。
- 熔断器：日亏 $500 停、连亏 3 笔暂停 30 分钟。

### Results

| Metric | Value |
|--------|-------|
| Net PnL | **-$1,243** |
| Trades | 42 |
| Win Rate | ~33% |
| Max Single Loss | -$341 |

**观察：**
- 严重的过度交易 (Over-trading)：60 天产生 42 笔交易。
- 频繁被洗盘 (Whipsaw)：震荡期反复触发信号 → 入场 → 止损 → 再入场。
- SOXL 期间上涨约 55%，但策略亏损 12.4%，说明信号质量和止损设计存在根本问题。
- 2% 固定硬止损对 3x 杠杆 ETF 来说过于紧密。

---

## Summary — Evolution Tracker

| Version | Net PnL | Trades | Max Loss | Key Improvement |
|---------|---------|--------|----------|-----------------|
| **001** | -$1,243 | 42 | -$341 | Baseline |
| **002** | +$149 | 25 | -$341 | ADX + Cooldown → 扭亏为盈 |
| **003** | -$214 | 10 | -$384 | Confirmation Bar → 大幅减少交易 |
| **004** | -$795 | 8 | **-$264** | Break-Even → 最低单笔回撤 |
| **005** | *TBD* | *TBD* | *TBD* | BE 开关 + 时间滤网参数化 |
