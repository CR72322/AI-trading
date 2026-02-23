# Experiment 006 Report — 15m + Overnight Holding

## 1) Objective

针对「SOXL 单边上涨但策略亏损」的问题，验证以下改动是否改善收益表现：
- 从 5m 切换到 15m 数据，过滤日内噪音。
- 去除 EOD 强制平仓，允许隔夜持仓。
- 调整参数搜索空间以匹配更长周期。

## 2) Implemented Changes

### A. Data Loader (`src/utils/alpaca_loader.py`)
- 默认下载周期保持为 `timeframe_minutes=15`。
- 数据文件命名使用 `SOXL_Alpaca_15m.csv`。
- 本次已下载并保存：
  - 路径：`data/raw/SOXL_Alpaca_15m.csv`
  - Bars：`1206`
  - 时间范围：`2025-12-26 13:00:00` → `2026-02-23 15:45:00`

### B. Strategy (`src/strategies/volatility_trend.py`)
- 默认参数更新：
  - `enable_break_even=False`
  - `stop_loss_atr_dist=2.0`
- 保留开盘过滤：`09:30-09:45` 不开新仓（通过默认 `entry_start=09:45` 实现）。
- 当前无 EOD 15:55 强制平仓逻辑，仓位可跨日持有。

### C. Optimization Script (`src/backtest/run_optimization.py`)
- 数据源改为读取：`data/raw/SOXL_Alpaca_15m.csv`
- 固定参数：
  - `enable_break_even=False`
- 新参数网格（27 组合）：
  - `ema_period: [20, 30, 40]`
  - `stop_loss_atr_dist: [2.0, 2.5, 3.0]`
  - `trailing_stop_atr_dist: [3.5, 4.0, 5.0]`

## 3) Optimization Run

执行命令：

```bash
../venv/bin/python -m src.backtest.run_optimization
```

运行结果（Exp-006）：
- 组合数：`27`
- 最优：
  - `EMA=20, StopATR=2.0, TrailATR=3.5`
  - Final Value: `11,288.21`
  - Net PnL: `+1,288.21`
  - Return: `+12.88%`
- 最差：
  - `EMA=40, StopATR=3.0, TrailATR=5.0`
  - Net PnL: `+377.82`
  - Return: `+3.78%`

## 4) Conclusion

Exp-006 在当前样本中实现全组合正收益，且最优组合显著优于此前「5m + 日内强平」设定，说明：
- 15m 周期有效降低了噪音交易；
- 允许隔夜持仓后，趋势段利润保留明显改善；
- Break-Even 关闭后，策略更能承受正常波动并延长持仓。

## 5) Recommended Baseline (next run)

建议将以下参数作为下一轮基线配置：
- `ema_period=20`
- `stop_loss_atr_dist=2.0`
- `trailing_stop_atr_dist=3.5`
- `enable_break_even=False`
