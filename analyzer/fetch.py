"""数据获取:优先 akshare(A股分钟K线),失败或 --demo 时用合成数据。

akshare 是抓公开网页的非官方接口:有秒~分钟级延迟、会限频,精确价以券商为准。
"""
from __future__ import annotations

import random
import time

import pandas as pd


def _force_direct() -> None:
    """国内数据源(新浪/腾讯)直连即可,绕过系统代理(Clash)。

    这样抓数据【不再依赖科学上网】:Clash 没开/挂了也能抓。
    (东财对直连不通,但我们已不用东财;若代理在 TUN 透明模式下仍可能被截,极少见。)
    """
    import os
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    for _mod in ("requests.utils", "urllib.request"):
        try:
            import importlib
            importlib.import_module(_mod).getproxies = lambda *a, **k: {}
        except Exception:
            pass


_force_direct()

# 疑似"被限频/连接被掐"的异常特征(连接重置、读超时、429)。
_RATELIMIT_HINTS = ("RemoteDisconnected", "Connection aborted", "ConnectionError",
                    "Read timed out", "ReadTimeout", "429", "Max retries")


def _is_ratelimit(err: Exception) -> bool:
    s = f"{type(err).__name__}: {err}"
    return any(h in s for h in _RATELIMIT_HINTS)


def _retry(call, tries: int = 3, base_delay: float = 1.0):
    """指数退避 + 抖动重试。撞到疑似限频信号时退避更久,避免把限流升级成封 IP。

    本机实测:行情走系统代理可达,直连反而被重置;故默认走系统代理 + 重试。
    注:'多少秒才安全'没有官方依据,这里只做温和退避,不当硬阈值。
    """
    last = None
    for i in range(tries):
        try:
            return call()
        except Exception as e:
            last = e
            if i < tries - 1:
                factor = 6.0 if _is_ratelimit(e) else 1.0  # 限频类退避更久(秒级)
                time.sleep(base_delay * (2 ** i) * factor * random.uniform(0.8, 1.2))
    raise last


def _polite_pause(lo: float = 0.8, hi: float = 1.8) -> None:
    """每次真实外部请求前的随机间隔——把'恒定心跳'打散成正常用户画像,降低被封概率。"""
    time.sleep(random.uniform(lo, hi))


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


def fetch_1min(code: str) -> pd.DataFrame:
    """1 分钟K线(新浪源,约覆盖近 9 个交易日),time/open/close/high/low/volume。

    一次请求即可同时支撑【当日分时切片】+【分钟K本地重采样分析】,避免重复打接口。
    """
    import akshare as ak

    _polite_pause()
    df = _retry(lambda: ak.stock_zh_a_minute(symbol=_sina_symbol(code), period="1", adjust=""))
    df = df.rename(columns={"day": "time"})
    df["time"] = pd.to_datetime(df["time"])
    for c in ["open", "close", "high", "low", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = [c for c in ["time", "open", "close", "high", "low", "volume"] if c in df.columns]
    return df[keep].dropna(subset=["close"]).sort_values("time").reset_index(drop=True)


def resample_bars(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """把 1 分钟K本地重采样成 N 分钟K(供分析用,省掉一次额外请求)。"""
    if df is None or df.empty:
        return df
    r = (df.set_index("time")
           .resample(f"{minutes}min")
           .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
           .dropna(subset=["close"]))
    return r.reset_index()


def today_slice(df: pd.DataFrame) -> pd.DataFrame:
    """取最新交易日的分钟数据(当日分时)。"""
    if df is None or df.empty:
        return df
    last_day = df["time"].dt.date.max()
    return df[df["time"].dt.date == last_day].reset_index(drop=True)


def fetch_daily(code: str, days: int = 120, adjust: str = "qfq") -> pd.DataFrame:
    """日K(新浪源),返回 date/open/close/high/low/volume 的最近 days 根。

    adjust: 看板展示用 'qfq'(前复权);**回测必须用 'hfq'(后复权)**——前复权会随新
    除权事件改写历史价,构成 look-ahead 泄漏(后复权不会改历史,回测才干净)。
    """
    import akshare as ak

    _polite_pause()
    df = _retry(lambda: ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust=adjust))
    keep = [c for c in ["date", "open", "close", "high", "low", "volume"] if c in df.columns]
    df = df[keep].copy()
    for c in ["open", "close", "high", "low", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"]).tail(days).reset_index(drop=True)


def fetch_daily_cached(code: str, days: int, cache_dir: str, today: str) -> pd.DataFrame:
    """日K 当天只抓一次:命中当天缓存直接读本地,否则抓一次并缓存。

    日K 盘中不变,这样把日K请求量砍掉约 99%。缓存目录在 .gitignore 内(不入库)。
    """
    import json
    import os

    path = os.path.join(cache_dir, f"daily_{code}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            if obj.get("date") == today and obj.get("rows"):
                return pd.DataFrame(obj["rows"])
        except Exception:
            pass
    df = fetch_daily(code, days)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": today, "rows": df.to_dict("records")}, f, ensure_ascii=False, default=str)
    except Exception:
        pass
    return df


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
