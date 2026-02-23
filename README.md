# AI-trading (SOXL Quant Backtest) 📈

本仓库当前主要包含一个基于 Alpaca API 的美股量化回测项目（重点标的：SOXL），用于在本地下载历史 K 线、运行 Backtrader 回测、做参数网格寻优，并输出回测图表。

## Quick Start 🚀

### 1) 环境要求 🧰

- macOS/Linux
- Python `3.10+`

仓库根目录下已有一个 `venv/`（如不可用可自行重建）。

### 2) 安装依赖 📦

```bash
python -m venv venv
./venv/bin/pip install -r quant_soxl_bot/requirements.txt
```

### 3) 配置 Alpaca Key 🔑

在 `quant_soxl_bot/` 下创建 `.env`：

```bash
cd quant_soxl_bot
cp .env.example .env
```

编辑 `quant_soxl_bot/.env`，填入：

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

注意：`.env` 不要提交到 git。

## How To Run ▶️

所有命令都建议在 `quant_soxl_bot/` 目录执行。

### A) 下载/更新 15m 数据 ⏱️

数据加载器会缓存到 `quant_soxl_bot/data/raw/`，15 分钟数据默认文件名为 `SOXL_Alpaca_15m.csv`。

```bash
cd quant_soxl_bot
../venv/bin/python -c "from src.utils.alpaca_loader import download_alpaca_data; download_alpaca_data('SOXL', timeframe_minutes=15, days=60, cache=True)"
```

### B) 运行主回测并生成图像 🧪🖼️

主回测已默认采用 Experiment 006 的最优参数组合，并使用 15m 数据：

- `ema_period=20`
- `stop_loss_atr_dist=2.0`
- `trailing_stop_atr_dist=3.5`
- `enable_break_even=False`

运行并输出图像到 `output/`：

```bash
cd quant_soxl_bot
../venv/bin/python -m src.backtest.run_backtest --save-plot output/backtest_result_exp006.png
```

默认也会写入/覆盖：`quant_soxl_bot/output/backtest_result.png`。

### C) 运行参数网格寻优 (Exp-006) 🧭

优化脚本从 `data/raw/SOXL_Alpaca_15m.csv` 读取数据：

```bash
cd quant_soxl_bot
../venv/bin/python -m src.backtest.run_optimization
```

## Project Structure 🗂️

项目代码位于 `quant_soxl_bot/`：

- `quant_soxl_bot/src/strategies/volatility_trend.py`: 核心策略 `VolatilityTrendStrategy`
- `quant_soxl_bot/src/utils/alpaca_loader.py`: Alpaca 历史数据下载与 CSV 缓存
- `quant_soxl_bot/src/backtest/run_backtest.py`: 主回测入口，生成 `output/*.png`
- `quant_soxl_bot/src/backtest/run_optimization.py`: 参数网格寻优入口
- `quant_soxl_bot/data/raw/`: 原始数据缓存（例如 `SOXL_Alpaca_15m.csv`）
- `quant_soxl_bot/output/`: 回测图像输出
- `quant_soxl_bot/docs/`:
  - `CHANGELOG.md`: 实验记录与策略演进
  - `experiment_006_report.md`: Exp-006 报告
  - `project_handover.md`: 交接与业务痛点说明
  - `STRATEGY_SPECS.md` / `STRATEGY_MATH.md`: 策略设计文档
