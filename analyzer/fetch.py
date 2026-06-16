"""数据获取:优先 akshare(A股分钟K线),失败或 --demo 时用合成数据。

akshare 是抓公开网页的非官方接口:有秒~分钟级延迟、会限频,精确价以券商为准。
"""
from __future__ import annotations

import pandas as pd

# akshare 中文列名 → 英文
_CN_COLS = {
    "时间": "time", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount",
}


def fetch_minute(code: str, period: str = "15") -> pd.DataFrame:
    """返回列: time, open, close, high, low, volume(按时间升序)。"""
    import akshare as ak

    df = ak.stock_zh_a_hist_min_em(symbol=code, period=str(period), adjust="")
    df = df.rename(columns=_CN_COLS)
    keep = [c for c in ["time", "open", "close", "high", "low", "volume"] if c in df.columns]
    df = df[keep].copy()
    df["time"] = pd.to_datetime(df["time"])
    for c in ["open", "close", "high", "low", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"]).sort_values("time").reset_index(drop=True)


def fetch_prev_close(code: str) -> float | None:
    """昨收价,用于日内涨跌幅。取不到返回 None。"""
    import akshare as ak

    try:
        spot = ak.stock_zh_a_spot_em()
        row = spot.loc[spot["代码"] == code]
        if not row.empty:
            return float(row.iloc[0]["昨收"])
    except Exception:
        return None
    return None


def demo_minute(code: str, period: str = "15", n: int = 40, surge: bool = True) -> pd.DataFrame:
    """离线合成分钟K线,用于不联网验证整条流水线与网站。"""
    import numpy as np

    seed = sum(ord(ch) for ch in code)
    rng = np.random.default_rng(seed)
    base = 7.0 + (seed % 5)
    steps = rng.normal(0, 0.004, n).cumsum()
    prices = base * (1 + steps)
    if surge:  # 末段人为制造"斜率激增 + 放量",方便看到高频模式被触发
        prices[-6:] = prices[-7] * (1 + np.linspace(0.02, 0.13, 6))
    start = pd.Timestamp("2026-06-15 09:30:00")
    times = [start + pd.Timedelta(minutes=int(period) * i) for i in range(n)]
    vol = rng.integers(8_000, 20_000, n).astype(float)
    if surge:
        vol[-6:] *= 3
    close = pd.Series(prices)
    return pd.DataFrame({
        "time": times,
        "open": close.shift(1).fillna(close.iloc[0]).values,
        "close": close.values,
        "high": (close * 1.003).values,
        "low": (close * 0.997).values,
        "volume": vol,
    })
