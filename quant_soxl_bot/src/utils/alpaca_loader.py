"""
alpaca_loader.py
================
Download historical bar data from Alpaca Markets and return a
Backtrader-compatible DataFrame.

The module reads Alpaca credentials from environment variables
(loaded via ``python-dotenv`` from the project-level ``.env`` file).

Usage
-----
    from src.utils.alpaca_loader import download_alpaca_data
    df = download_alpaca_data("SOXL", timeframe_minutes=5, days=60)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the project root (quant_soxl_bot/.env)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Alpaca SDK imports
# ---------------------------------------------------------------------------
from alpaca.data.historical.stock import StockHistoricalDataClient  # noqa: E402
from alpaca.data.requests import StockBarsRequest                   # noqa: E402
from alpaca.data.enums import DataFeed                              # noqa: E402
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit          # noqa: E402


def _get_alpaca_client() -> StockHistoricalDataClient:
    """Build an authenticated Alpaca historical-data client.

    Reads ``ALPACA_API_KEY`` and ``ALPACA_SECRET_KEY`` from the
    environment.  Exits with a clear message if they are missing.

    Returns
    -------
    StockHistoricalDataClient
        Ready-to-use SDK client for historical bar requests.
    """
    api_key: Optional[str] = os.getenv("ALPACA_API_KEY")
    secret_key: Optional[str] = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        print(
            "ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not found.\n"
            "       Please set them in quant_soxl_bot/.env\n"
            "       (see .env.example for the template).",
            file=sys.stderr,
        )
        raise EnvironmentError("Missing Alpaca API credentials in environment.")

    return StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)


def download_alpaca_data(
    symbol: str = "SOXL",
    *,
    timeframe_minutes: int = 5,
    days: int = 60,
    cache: bool = True,
) -> pd.DataFrame:
    """Download intraday bar data from Alpaca and return a Backtrader-ready DF.

    Parameters
    ----------
    symbol : str
        Ticker symbol to fetch (default ``"SOXL"``).
    timeframe_minutes : int
        Bar aggregation in minutes (default ``5``).
    days : int
        How many calendar days of history to request (default ``60``).
    cache : bool
        If True, save to / read from ``data/raw/<SYMBOL>_<TF>m_<DAYS>d.csv``
        to avoid redundant API calls.

    Returns
    -------
    pd.DataFrame
        Index  = ``Datetime`` (tz-naive, US/Eastern wall-clock time)
        Columns = ``Open, High, Low, Close, Volume``

    Raises
    ------
    ValueError
        If the API returns no data.
    EnvironmentError
        If Alpaca credentials are missing.
    """
    # ------------------------------------------------------------------
    # 1. CSV cache path
    # ------------------------------------------------------------------
    raw_dir = _PROJECT_ROOT / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_csv = raw_dir / f"{symbol}_{timeframe_minutes}m_{days}d.csv"

    # ------------------------------------------------------------------
    # 2. Try reading from cache first
    # ------------------------------------------------------------------
    if cache and cache_csv.exists():
        try:
            df = pd.read_csv(
                cache_csv, parse_dates=["Datetime"], index_col="Datetime"
            )
            if not df.empty:
                print(f"  Loaded {len(df)} bars from cache: {cache_csv.name}")
                return _sanitize_for_backtrader(df)
        except Exception:
            pass  # corrupt cache → fall through to download

    # ------------------------------------------------------------------
    # 3. Download from Alpaca
    # ------------------------------------------------------------------
    print(f"  Downloading {symbol} {timeframe_minutes}m bars ({days}d) from Alpaca …")
    client = _get_alpaca_client()

    # Build the request.
    # Alpaca expects tz-aware or tz-naive-UTC datetimes for start/end.
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(amount=timeframe_minutes, unit=TimeFrameUnit.Minute),
        start=start_dt,
        end=end_dt,
        # 使用 IEX 数据源 — 免费账户可用，且与 STRATEGY_SPECS.md 一致。
        # SIP 数据源需要付费订阅。
        feed=DataFeed.IEX,
    )

    bars = client.get_stock_bars(request)

    # ------------------------------------------------------------------
    # 4. Convert to DataFrame
    # ------------------------------------------------------------------
    # The SDK's BarSet exposes a .df() helper that returns a MultiIndex
    # DataFrame (symbol, timestamp).  We flatten it.
    df: pd.DataFrame = bars.df
    if df.empty:
        raise ValueError(
            f"Alpaca returned no data for {symbol} "
            f"({start_dt.date()} → {end_dt.date()})."
        )

    # Drop the symbol level if present (MultiIndex → single-level timestamp).
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index(level="symbol", drop=True)

    # ------------------------------------------------------------------
    # 5. Rename columns to Backtrader convention (capitalized OHLCV)
    # ------------------------------------------------------------------
    column_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        # Alpaca also returns trade_count, vwap — we ignore them.
    }
    df = df.rename(columns=column_map)

    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Alpaca data missing columns: {missing}")
    df = df[required].copy()

    # ------------------------------------------------------------------
    # 6. Sanitize timestamps for Backtrader
    # ------------------------------------------------------------------
    df = _sanitize_for_backtrader(df)

    # ------------------------------------------------------------------
    # 7. Cache to CSV for next run
    # ------------------------------------------------------------------
    if cache:
        df.to_csv(cache_csv, index=True, index_label="Datetime")
        print(f"  Cached {len(df)} bars → {cache_csv.name}")

    return df


def _sanitize_for_backtrader(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the DataFrame is clean and tz-naive for Backtrader.

    Steps
    -----
    1. Convert index to DatetimeIndex.
    2. Strip timezone info (Backtrader cannot handle tz-aware datetimes).
    3. Remove duplicate timestamps and NaN rows.
    4. Sort ascending by time.
    5. Cast OHLCV to correct numeric types.

    Parameters
    ----------
    df : pd.DataFrame
        Raw or cached DataFrame.

    Returns
    -------
    pd.DataFrame
        Cleaned DataFrame ready for ``bt.feeds.PandasData``.
    """
    df.index = pd.to_datetime(df.index)

    # Backtrader 与带时区的 DatetimeIndex 不兼容 → 统一去掉时区
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)

    df.index.name = "Datetime"

    # 去重（保留最后出现的 bar）
    df = df[~df.index.duplicated(keep="last")]

    # 数值类型强制转换
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0).astype(int)

    # 去掉 OHLC 中任何一列为 NaN 的行
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    # 时间升序
    df = df.sort_index()

    return df
