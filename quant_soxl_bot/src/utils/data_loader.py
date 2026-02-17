import time
from pathlib import Path
from typing import Union

import pandas as pd
import yfinance as yf


def load_or_download_soxl_5m_data(
    csv_path: Union[str, Path, None] = None,
    *,
    lowercase_columns: bool = False,
) -> pd.DataFrame:
    """
    Load SOXL 5-minute data from CSV if available; otherwise download and cache it.

    Returns a DataFrame indexed by Datetime with Backtrader-compatible OHLCV columns:
    Open, High, Low, Close, Volume.
    """
    project_root = Path(__file__).resolve().parents[2]
    raw_dir = project_root / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if csv_path:
        p = Path(csv_path)
        target_csv = p if p.is_absolute() else (project_root / p)
    else:
        target_csv = raw_dir / "SOXL_5m_60d.csv"

    df: pd.DataFrame
    if target_csv.exists():
        try:
            df = pd.read_csv(target_csv, parse_dates=["Datetime"], index_col="Datetime")
        except Exception:
            # Cache file exists but is unreadable/corrupt -> fall back to re-download.
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()

    if df.empty:
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            print(f"  Downloading SOXL 5m data (attempt {attempt}/{max_retries}) …")
            df = yf.download(
                tickers="SOXL",
                period="60d",
                interval="5m",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if not df.empty:
                break
            if attempt < max_retries:
                wait = 2 ** attempt  # 2, 4, 8, 16, 32 seconds
                print(f"  Rate-limited — retrying in {wait}s …")
                time.sleep(wait)
        if df.empty:
            raise ValueError(
                "Failed to download SOXL 5-minute data after "
                f"{max_retries} attempts. Try again later."
            )

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        required_columns = ["Open", "High", "Low", "Close", "Volume"]
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(
                f"Downloaded data is missing required columns: {missing_columns}"
            )

        df = df[required_columns].copy()
        df.index = pd.to_datetime(df.index)
        # Backtrader works best with tz-naive datetimes.
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
        df.index.name = "Datetime"
        df = df.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
        df = df.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
        df.to_csv(target_csv, index=True, index_label="Datetime")

    required_columns = ["Open", "High", "Low", "Close", "Volume"]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"CSV data is missing required columns: {missing_columns}")

    df = df[required_columns].copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "Datetime"
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    if lowercase_columns:
        df = df.rename(
            columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        )

    return df
