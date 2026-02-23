"""
run_optimization.py
===================
参数网格寻优脚本 — 利用 Backtrader 内置的 ``optstrategy`` 接口，
对 VolatilityTrendStrategy 的关键参数进行穷举搜索，找出在历史数据上
表现最优的参数组合。

工作原理
--------
1. ``cerebro.optstrategy()`` 会为参数网格中的每一种组合各创建一个
   独立的策略实例，类似 sklearn 的 GridSearchCV。
2. ``cerebro.run(maxcpus=None)`` 会自动使用所有 CPU 核心并行回测，
   大幅缩短总耗时。
3. ``optreturn=False`` 确保返回完整的策略对象（而非轻量摘要），
   这样我们才能通过 ``strat.broker.get_value()`` 读取最终资金。

Usage
-----
    cd quant_soxl_bot
    python -m src.backtest.run_optimization
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import backtrader as bt
import pandas as pd

# ---------------------------------------------------------------------------
# 确保 src.* 的 import 在任何工作目录下都能正常运行
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.strategies.volatility_trend import VolatilityTrendStrategy

# ---------------------------------------------------------------------------
# 回测基础配置（与 run_backtest.py 保持一致）
# ---------------------------------------------------------------------------
INITIAL_CASH: float = 10_000.0
COMMISSION: float = 0.0
SIZER_PERCENTS: float = 95.0

# ---------------------------------------------------------------------------
# 固定参数（Exp-006: 关闭 Break-Even，允许纯 Hard+Trail 风控）
# ---------------------------------------------------------------------------
FIXED_PARAMS: dict = dict(
    enable_break_even=False,
)

DATA_CSV: Path = _PROJECT_ROOT / "data" / "raw" / "SOXL_Alpaca_15m.csv"

# ---------------------------------------------------------------------------
# 参数搜索网格
# ---------------------------------------------------------------------------
# 总组合数 = 3 × 3 × 3 = 27
PARAM_GRID: dict = dict(
    ema_period=[20, 30, 40],
    stop_loss_atr_dist=[2.0, 2.5, 3.0],
    trailing_stop_atr_dist=[3.5, 4.0, 5.0],
)


def main() -> None:
    # ==================================================================
    # 1. 加载数据（只下载一次，所有参数组合共享同一份 DataFrame）
    # ==================================================================
    print("=" * 72)
    print("  SOXL VolatilityTrendStrategy — 参数网格寻优 (Exp-006)")
    print("=" * 72)
    print(f"  固定参数: EnableBE={FIXED_PARAMS['enable_break_even']}")

    print("\n[1/4] 加载 SOXL 15-min 数据 …")
    if not DATA_CSV.exists():
        raise FileNotFoundError(
            f"缺少数据文件: {DATA_CSV}\n"
            "请先运行 src.utils.alpaca_loader 下载 15m 数据。"
        )
    df = pd.read_csv(DATA_CSV, parse_dates=["Datetime"], index_col="Datetime")
    df = df.sort_index()
    print(f"      {len(df)} bars  |  {df.index.min()} → {df.index.max()}")

    # ==================================================================
    # 2. 初始化 Cerebro
    # ==================================================================
    cerebro = bt.Cerebro(optreturn=False)

    data_feed = bt.feeds.PandasData(
        dataname=df,
        datetime=None,
        open="Open",
        high="High",
        low="Low",
        close="Close",
        volume="Volume",
        openinterest=-1,
    )
    cerebro.adddata(data_feed, name="SOXL_15m")

    # ==================================================================
    # 3. 添加优化策略
    # ==================================================================
    # 固定参数以标量传入，搜索参数以列表传入；Backtrader 会自动
    # 对列表值计算笛卡尔积，标量值在所有组合中保持不变。
    cerebro.optstrategy(
        VolatilityTrendStrategy,
        **FIXED_PARAMS,
        **PARAM_GRID,
    )

    # ==================================================================
    # 4. Broker & Sizer
    # ==================================================================
    cerebro.broker.setcash(INITIAL_CASH)
    cerebro.broker.setcommission(commission=COMMISSION)
    cerebro.addsizer(bt.sizers.PercentSizer, percents=SIZER_PERCENTS)

    # ==================================================================
    # 5. 运行优化（多进程）
    # ==================================================================
    total = 1
    for v in PARAM_GRID.values():
        total *= len(v)
    print(f"\n[2/4] 启动优化 — {total} 种参数组合, maxcpus=ALL …")

    t0 = time.perf_counter()
    opt_results = cerebro.run(maxcpus=None)
    elapsed = time.perf_counter() - t0
    print(f"      完成！耗时 {elapsed:.1f}s")

    # ==================================================================
    # 6. 解析结果
    # ==================================================================
    print(f"\n[3/4] 解析 {len(opt_results)} 组回测结果 …")

    records: list[dict] = []
    for run in opt_results:
        strat = run[0]
        final_value = strat.broker.get_value()
        profit = final_value - INITIAL_CASH

        records.append(
            dict(
                ema_period=strat.p.ema_period,
                stop_atr=strat.p.stop_loss_atr_dist,
                trail_atr=strat.p.trailing_stop_atr_dist,
                final_value=final_value,
                profit=profit,
                return_pct=profit / INITIAL_CASH * 100,
            )
        )

    records.sort(key=lambda r: r["profit"], reverse=True)

    # ==================================================================
    # 7. 打印 Top 10 + 最差
    # ==================================================================
    print(f"\n[4/4] Top 10 参数组合（共 {len(records)} 组）")
    hdr = (
        f"{'Rank':<5}"
        f"{'EMA':>8}"
        f"{'StopATR':>9}"
        f"{'TrailATR':>10}"
        f"{'Final($)':>12}"
        f"{'Profit($)':>12}"
        f"{'Return(%)':>11}"
    )
    print("-" * len(hdr))
    print(hdr)
    print("-" * len(hdr))

    for i, rec in enumerate(records[:10], start=1):
        print(
            f"{i:<5}"
            f"{rec['ema_period']:>8}"
            f"{rec['stop_atr']:>9.1f}"
            f"{rec['trail_atr']:>10.1f}"
            f"{rec['final_value']:>12,.2f}"
            f"{rec['profit']:>12,.2f}"
            f"{rec['return_pct']:>10.2f}%"
        )

    print("-" * len(hdr))

    worst = records[-1]
    print(
        f"\n  最差: EMA={worst['ema_period']}, StopATR={worst['stop_atr']:.1f}, "
        f"TrailATR={worst['trail_atr']:.1f} "
        f"→ {worst['profit']:+,.2f} ({worst['return_pct']:+.2f}%)"
    )

    best = records[0]
    print(
        f"  最优: EMA={best['ema_period']}, StopATR={best['stop_atr']:.1f}, "
        f"TrailATR={best['trail_atr']:.1f} "
        f"→ {best['profit']:+,.2f} ({best['return_pct']:+.2f}%)"
    )
    print()


# 多进程运行保护 — Windows / macOS spawn 模式要求入口在此块内
if __name__ == "__main__":
    main()
