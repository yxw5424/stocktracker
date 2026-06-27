"""
日频决策上下文 —— 把"今天市场发生了什么 + 我现在的状态 + 量化信号"打包，喂给 AI 顾问。

刻意做成解耦：各部分（持仓/账户、量化信号、大盘、新闻、个股走势）都可由外部传入，
也提供 akshare 抓取大盘/新闻的可选实现（离线时自动跳过）。这样既能离线演示，
联网后又能接真实数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_context(
    *,
    session: str,                                  # "open" / "close"
    date: str,
    account: Optional[Dict[str, Any]] = None,
    positions: Optional[List[Dict[str, Any]]] = None,
    signals: Optional[List[Dict[str, Any]]] = None,   # 量化选股 top picks
    market: Optional[Dict[str, Any]] = None,          # 指数涨跌等
    news: Optional[List[str]] = None,                 # 当日新闻标题
    price_action: Optional[Dict[str, Any]] = None,    # 个股近期走势摘要
) -> Dict[str, Any]:
    """组装成一个结构化字典；缺的部分留空，AI 会据有的信息决策。"""
    return {
        "date": date,
        "session": session,            # 开盘前 / 收盘后
        "account": account or {},
        "positions": positions or [],
        "quant_signals": signals or [],
        "market": market or {},
        "news": news or [],
        "price_action": price_action or {},
    }


# --------------------------------------------------------------------------- #
# 可选：用 akshare 抓大盘与新闻（离线/未装则返回空，不报错）
# --------------------------------------------------------------------------- #
def fetch_market_snapshot() -> Dict[str, Any]:
    """主要指数当日涨跌幅（上证/深成/创业板）。akshare 不可用时返回 {}。"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_spot_em(symbol="上证系列指数")
        out = {}
        for name in ["上证指数", "深证成指", "创业板指"]:
            row = df[df["名称"] == name]
            if not row.empty:
                out[name] = {"price": float(row["最新价"].iloc[0]),
                             "pct": float(row["涨跌幅"].iloc[0])}
        return out
    except Exception:
        return {}


def fetch_news(limit: int = 10) -> List[str]:
    """当日财经快讯标题。akshare 不可用时返回 []。"""
    try:
        import akshare as ak
        df = ak.stock_info_global_cls()           # 财联社电报
        return [str(t) for t in df["标题"].head(limit).tolist()]
    except Exception:
        return []
