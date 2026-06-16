"""全市场异动筛选(新浪批量快照,1 请求,防封)。

诚实声明:这里输出的是【客观异动分 / 信号强度】,**不是"胜率",更不是"会涨"的预测**。
- 异动分 = 透明的多因子(涨跌幅绝对值 / 振幅 / 跳空 / 活跃度)线性组合,可解释、可复算。
- 真正的"历史胜率"要靠回测(见 backtest.py),且历史 ≠ 未来。
- 全市场 5000+ 只 **只用 1 个请求**(新浪 stock_zh_a_spot),符合防封原则。
"""
from __future__ import annotations

import pandas as pd

from . import fetch as fetchmod

_REN = {"代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "pct",
        "今开": "open", "昨收": "prev_close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount"}


def fetch_spot_all() -> pd.DataFrame:
    """全市场实时快照(新浪,1 请求)。返回标准化列。"""
    import akshare as ak

    fetchmod._polite_pause()
    df = fetchmod._retry(lambda: ak.stock_zh_a_spot())
    df = df.rename(columns=_REN)
    keep = [c for c in _REN.values() if c in df.columns]
    df = df[keep].copy()
    for c in ["price", "pct", "open", "prev_close", "high", "low", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["price", "prev_close"])


def anomaly_score(df: pd.DataFrame) -> pd.DataFrame:
    """透明多因子异动分(可解释)。各因子均为当下可算的客观量。"""
    d = df[df["prev_close"] > 0].copy()
    d["amplitude"] = (d["high"] - d["low"]) / d["prev_close"] * 100        # 振幅 %
    d["gap"] = (d["open"] - d["prev_close"]) / d["prev_close"] * 100       # 跳空 %
    d["liq_rank"] = d["amount"].rank(pct=True) * 100                       # 活跃度分位
    # 异动分:涨跌幅绝对值(主)+ 振幅 + |跳空| + 活跃度。权重透明、可调。
    d["score"] = (d["pct"].abs() * 4 + d["amplitude"] * 2
                  + d["gap"].abs() * 1 + d["liq_rank"] * 0.2).round(1)
    return d


def screen(top_n: int = 30, min_amount: float = 5e7, exclude_st: bool = True) -> pd.DataFrame:
    """全市场异动筛选,返回按异动分排序的前 top_n(已滤掉低成交/ST)。"""
    d = anomaly_score(fetch_spot_all())
    if exclude_st:
        d = d[~d["name"].astype(str).str.contains("ST", case=False, na=False)]
    d = d[d["amount"] >= min_amount]   # 滤掉几乎没成交、易被操纵的小票
    d = d.sort_values("score", ascending=False).head(top_n)
    cols = ["code", "name", "price", "pct", "amplitude", "gap", "amount", "score"]
    return d[cols].reset_index(drop=True)


def main() -> None:
    import argparse
    import os
    import sys

    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="全市场异动筛选(异动分,非胜率)")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--json", action="store_true", help="写 docs/data/screen.json 供看板用")
    args = ap.parse_args()

    res = screen(top_n=args.top)
    if args.json:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = os.path.join(root, "docs", "data", "screen.json")
        res.to_json(out, orient="records", force_ascii=False)
        print(f"wrote {out} ({len(res)} rows)")
    else:
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
