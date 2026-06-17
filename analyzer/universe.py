"""股票池(成分股)。"""
from __future__ import annotations

from . import fetch as fetchmod  # 触发 _force_direct(直连),并复用 _retry/_polite_pause


def hs300() -> list[str]:
    """沪深300 成分股 6 位代码列表(中证指数官网源)。"""
    import akshare as ak

    df = fetchmod._retry(lambda: ak.index_stock_cons_csindex(symbol="000300"))
    return df["成分券代码"].astype(str).str.zfill(6).tolist()
