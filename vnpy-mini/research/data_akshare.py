"""
真实 A股数据接入（akshare，免费）——把日线整理成与 data.make_synthetic_panel 相同的宽表。

用法：
    from research.data_akshare import load_panel
    panel = load_panel(["600000.SH", "000001.SZ", ...], "20210101", "20231231")

注意：
  * akshare 用后复权（adjust="hfq"），避免除权日假跳空；
  * 停牌日 akshare 通常无数据行 → 这里前向填充价格、并标记 suspended；
  * 真做研究还要补上**退市/ST**股票与 point-in-time 成分股，否则有幸存者偏差。
本模块只负责把"能取到的"整理成面板；数据完整性需你自行保证。
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def _require_akshare():
    try:
        import akshare as ak  # noqa
        return ak
    except Exception as exc:  # 未安装或装不上（本环境即如此）
        raise RuntimeError(
            "需要 akshare：pip install akshare。若安装失败可改用 tushare/qlib，"
            "只要最终整理成 {field: 日期×股票} 宽表即可。"
            f"（原始错误：{exc}）"
        )


def load_panel(symbols: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    """拉取 symbols 在 [start, end] 的后复权日线，返回宽表面板。

    symbols 形如 "600000.SH" / "000001.SZ"；start/end 形如 "20210101"。
    """
    ak = _require_akshare()
    closes, opens, highs, lows, vols = {}, {}, {}, {}, {}
    for sym in symbols:
        code = sym.split(".")[0]
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date=start, end_date=end, adjust="hfq")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                                "最高": "high", "最低": "low", "成交量": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        closes[sym], opens[sym] = df["close"], df["open"]
        highs[sym], lows[sym], vols[sym] = df["high"], df["low"], df["volume"]

    if not closes:
        raise RuntimeError("没有取到任何数据，检查网络/代码/日期。")

    close = pd.DataFrame(closes).sort_index()
    open_ = pd.DataFrame(opens).reindex_like(close)
    high = pd.DataFrame(highs).reindex_like(close)
    low = pd.DataFrame(lows).reindex_like(close)
    volume = pd.DataFrame(vols).reindex_like(close)

    suspended = volume.isna() | (volume.fillna(0) == 0)
    # 停牌/缺失日前向填充价格，保证价格序列连续（成交量保持 0/NaN）
    close = close.ffill()
    open_ = open_.fillna(close)
    high = high.fillna(close)
    low = low.fillna(close)
    prev_close = close.shift(1).fillna(close)

    return {
        "open": open_, "high": high, "low": low, "close": close,
        "prev_close": prev_close, "volume": volume.fillna(0.0),
        "suspended": suspended,
    }


# 一个常用的小股票池，方便快速试跑（沪深主板蓝筹示例）
SAMPLE_UNIVERSE = [
    "600000.SH", "600036.SH", "600519.SH", "601318.SH", "600276.SH",
    "000001.SZ", "000002.SZ", "000333.SZ", "000651.SZ", "000858.SZ",
    "002415.SZ", "002594.SZ", "600030.SH", "601166.SH", "600887.SH",
]
