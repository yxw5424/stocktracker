"""数据获取:优先 akshare(A股分钟K线),失败或 --demo 时用合成数据。

akshare 是抓公开网页的非官方接口:有秒~分钟级延迟、会限频,精确价以券商为准。
"""
from __future__ import annotations

import time

import pandas as pd


def _retry(call, tries: int = 3, base_delay: float = 0.8):
    """对网络抖动(系统代理/Clash 偶发断连)做重试,吸收偶发失败。

    本机实测:行情走系统代理可达,直连反而被重置;故保持默认走系统代理 + 重试。
    """
    last = None
    for i in range(tries):
        try:
            return call()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(base_delay * (i + 1))
    raise last


def _sina_symbol(code: str) -> str:
    """6 位代码 → 新浪/腾讯前缀格式:沪 sh / 深 sz / 北 bj。"""
    code = str(code).strip()
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    if code.startswith(("4", "8", "9")):
        return "bj" + code
    return "sh" + code


# akshare 中文列名 → 英文
_CN_COLS = {
    "时间": "time", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount",
}


def fetch_minute(code: str, period: str = "15") -> pd.DataFrame:
    """分钟K线(新浪源),返回 time/open/close/high/low/volume(按时间升序)。"""
    import akshare as ak

    df = _retry(lambda: ak.stock_zh_a_minute(symbol=_sina_symbol(code), period=str(period), adjust=""))
    df = df.rename(columns={"day": "time"})
    df["time"] = pd.to_datetime(df["time"])
    for c in ["open", "close", "high", "low", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = [c for c in ["time", "open", "close", "high", "low", "volume"] if c in df.columns]
    return df[keep].dropna(subset=["close"]).sort_values("time").reset_index(drop=True)


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


def fetch_intraday(code: str) -> pd.DataFrame:
    """当日分时(1 分钟),返回 time/close/volume,只保留最新交易日。"""
    import akshare as ak

    df = _retry(lambda: ak.stock_zh_a_minute(symbol=_sina_symbol(code), period="1", adjust=""))
    df = df.rename(columns={"day": "time"})
    df["time"] = pd.to_datetime(df["time"])
    for c in ["close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("time")
    last_day = df["time"].dt.date.max()
    return df[df["time"].dt.date == last_day].reset_index(drop=True)


def fetch_daily(code: str, days: int = 120) -> pd.DataFrame:
    """日K(前复权),返回 date/open/close/high/low/volume 的最近 days 根。"""
    import akshare as ak

    df = _retry(lambda: ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust="qfq"))
    keep = [c for c in ["date", "open", "close", "high", "low", "volume"] if c in df.columns]
    df = df[keep].copy()
    for c in ["open", "close", "high", "low", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"]).tail(days).reset_index(drop=True)


def demo_daily(code: str, n: int = 120) -> pd.DataFrame:
    """离线合成日K,用于不联网验证日K视图。"""
    import numpy as np

    seed = sum(ord(ch) for ch in code)
    rng = np.random.default_rng(seed + 1)
    base = 7.0 + (seed % 5)
    close = base * (1 + rng.normal(0.001, 0.02, n).cumsum())
    close_s = pd.Series(close)
    open_s = close_s.shift(1).fillna(close_s.iloc[0])
    high = np.maximum(open_s, close_s) * (1 + np.abs(rng.normal(0, 0.012, n)))
    low = np.minimum(open_s, close_s) * (1 - np.abs(rng.normal(0, 0.012, n)))
    start = pd.Timestamp("2026-01-02")
    dates = [(start + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]
    return pd.DataFrame({
        "date": dates, "open": open_s.values, "close": close_s.values,
        "high": high, "low": low, "volume": rng.integers(100000, 500000, n).astype(float),
    })


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
