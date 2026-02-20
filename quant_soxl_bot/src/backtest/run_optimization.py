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

# ---------------------------------------------------------------------------
# 确保 src.* 的 import 在任何工作目录下都能正常运行
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.strategies.volatility_trend import VolatilityTrendStrategy
from src.utils.alpaca_loader import download_alpaca_data

# ---------------------------------------------------------------------------
# 回测基础配置（与 run_backtest.py 保持一致）
# ---------------------------------------------------------------------------
INITIAL_CASH: float = 10_000.0
COMMISSION: float = 0.0
SIZER_PERCENTS: float = 95.0

# ---------------------------------------------------------------------------
# 固定参数（基于上轮搜索结论锁定，节省算力）
# ---------------------------------------------------------------------------
FIXED_PARAMS: dict = dict(
    ema_period=40,
    trailing_stop_atr_dist=3.5,
)

# ---------------------------------------------------------------------------
# 参数搜索网格
# ---------------------------------------------------------------------------
# range() 不支持浮点步长，因此用列表显式声明候选值。
# 总组合数 = 3 × 3 × 2 × 3 = 54
PARAM_GRID: dict = dict(
    stop_loss_atr_dist=[1.5, 2.0, 2.5],
    break_even_atr_dist=[1.5, 2.0, 3.0],
    enable_break_even=[True, False],
    rsi_ceiling=[70, 75, 80],
)


def main() -> None:
    # ==================================================================
    # 1. 加载数据（只下载一次，所有参数组合共享同一份 DataFrame）
    # ==================================================================
    print("=" * 72)
    print("  SOXL VolatilityTrendStrategy — 参数网格寻优 (Exp-005)")
    print("=" * 72)
    print(f"  固定参数: EMA={FIXED_PARAMS['ema_period']}, "
          f"TrailATR={FIXED_PARAMS['trailing_stop_atr_dist']}")

    print("\n[1/4] 加载 SOXL 5-min 数据 …")
    df = download_alpaca_data("SOXL", timeframe_minutes=5, days=60)
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
    cerebro.adddata(data_feed, name="SOXL_5m")

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
                enable_be=strat.p.enable_break_even,
                be_dist=strat.p.break_even_atr_dist,
                stop_atr=strat.p.stop_loss_atr_dist,
                rsi_ceil=strat.p.rsi_ceiling,
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
        f"{'EnableBE':>10}"
        f"{'BEDist':>8}"
        f"{'StopATR':>9}"
        f"{'RSICeil':>9}"
        f"{'Final($)':>12}"
        f"{'Profit($)':>12}"
        f"{'Return(%)':>11}"
    )
    print("-" * len(hdr))
    print(hdr)
    print("-" * len(hdr))

    for i, rec in enumerate(records[:10], start=1):
        be_label = "ON" if rec["enable_be"] else "OFF"
        be_dist_str = f"{rec['be_dist']:.1f}" if rec["enable_be"] else "  —"
        print(
            f"{i:<5}"
            f"{be_label:>10}"
            f"{be_dist_str:>8}"
            f"{rec['stop_atr']:>9.1f}"
            f"{rec['rsi_ceil']:>9}"
            f"{rec['final_value']:>12,.2f}"
            f"{rec['profit']:>12,.2f}"
            f"{rec['return_pct']:>10.2f}%"
        )

    print("-" * len(hdr))

    worst = records[-1]
    w_be = "ON" if worst["enable_be"] else "OFF"
    print(
        f"\n  最差: EnableBE={w_be}, BEDist={worst['be_dist']:.1f}, "
        f"StopATR={worst['stop_atr']:.1f}, RSICeil={worst['rsi_ceil']} "
        f"→ {worst['profit']:+,.2f} ({worst['return_pct']:+.2f}%)"
    )

    best = records[0]
    b_be = "ON" if best["enable_be"] else "OFF"
    print(
        f"  最优: EnableBE={b_be}, BEDist={best['be_dist']:.1f}, "
        f"StopATR={best['stop_atr']:.1f}, RSICeil={best['rsi_ceil']} "
        f"→ {best['profit']:+,.2f} ({best['return_pct']:+.2f}%)"
    )
    print()


# 多进程运行保护 — Windows / macOS spawn 模式要求入口在此块内
if __name__ == "__main__":
    main()
