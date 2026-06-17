"""市场层分析:从【全市场快照 + 指数】榨出 宽度 / 情绪 / 形势(regime)。

一个全市场请求即可算出整张市场的"温度",远比只看个股信息量大。全部透明可解释。
"""
from __future__ import annotations

import pandas as pd

_KEY_INDEX = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sh000300": "沪深300", "bj899050": "北证50",
}


def _board_limit(code: str) -> float:
    """该板块的涨跌停幅度(用于估算涨停/跌停家数)。"""
    c = str(code)
    if c.startswith(("sz30", "sh68")):       # 创业板 / 科创板
        return 20.0
    if c.startswith(("bj", "sh92", "sz92")):  # 北交所
        return 30.0
    return 10.0


def breadth(spot: pd.DataFrame) -> dict:
    """市场宽度:涨跌家数、涨跌停、强弱分布、总成交。"""
    d = spot.dropna(subset=["pct"]).copy()
    n = len(d)
    adv = int((d["pct"] > 0).sum())
    dec = int((d["pct"] < 0).sum())
    d["limit"] = d["code"].map(_board_limit)
    limit_up = int((d["pct"] >= d["limit"] - 0.5).sum())
    limit_dn = int((d["pct"] <= -(d["limit"] - 0.5)).sum())
    return {
        "total": n, "adv": adv, "dec": dec, "flat": n - adv - dec,
        "up_ratio": round(adv / max(1, adv + dec), 3),
        "limit_up": limit_up, "limit_down": limit_dn,
        "up5": int((d["pct"] >= 5).sum()), "down5": int((d["pct"] <= -5).sum()),
        "median_pct": round(float(d["pct"].median()), 2),
        "mean_pct": round(float(d["pct"].mean()), 2),
        "total_amount_yi": round(float(d["amount"].sum(skipna=True)) / 1e8),  # 亿元
    }


def index_summary(idx: pd.DataFrame) -> list[dict]:
    """主要指数的现价与涨跌幅。"""
    out = []
    for code, name in _KEY_INDEX.items():
        row = idx.loc[idx["code"] == code]
        if not row.empty:
            r = row.iloc[0]
            out.append({"code": code, "name": name,
                        "price": round(float(r["price"]), 2), "pct": round(float(r["pct"]), 2)})
    return out


def regime(bd: dict, idx_list: list[dict]) -> dict:
    """市场形势:赚钱效应分(0-100)+ 标签 + 风险基调。透明、可解释、可调权重。"""
    up_ratio = bd["up_ratio"]
    net_limit = bd["limit_up"] - bd["limit_down"]
    idx_avg = round(sum(i["pct"] for i in idx_list) / max(1, len(idx_list)), 2) if idx_list else 0.0
    money = max(0, min(100, round(up_ratio * 100 + net_limit * 0.2 + idx_avg * 3)))

    if up_ratio >= 0.6 and net_limit > 0:
        label, tone = "普涨 · 赚钱效应强", "risk_on"
    elif up_ratio <= 0.35 or net_limit < -20:
        label, tone = "普跌 · 亏钱效应", "risk_off"
    elif bd["limit_up"] >= 30 and up_ratio < 0.5:
        label, tone = "分化 · 题材活跃但普跌", "mixed"
    else:
        label, tone = "震荡 · 结构性行情", "neutral"
    return {"money_effect": money, "label": label, "tone": tone,
            "up_ratio": up_ratio, "net_limit": net_limit, "index_avg_pct": idx_avg}
