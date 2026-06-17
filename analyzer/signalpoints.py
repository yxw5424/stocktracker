"""买卖点信号层(C):用横截面记分卡,判断某只票"今天触发了哪些信号",
并附上"这类信号在沪深300历史上的真实 EDGE/胜率"。

分级(诚实):
- edge_pos(EDGE≥0.3%)→ 候选买点(有历史超额)
- edge_flat(−0.15<EDGE<0.3)→ 无超额(别当买卖点)
- edge_neg(EDGE≤−0.15)→ 反指(历史上是负超额,别追)
- unknown → 未回测

不预测涨跌,只说"你触发了哪种历史信号、它历史上是赚是亏"。历史≠未来。
"""
from __future__ import annotations

import json
import os

from .backtest import SIGNALS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORECARD_PATH = os.path.join(ROOT, "docs", "data", "signal_scorecard.json")


def load_scorecard() -> dict:
    try:
        with open(SCORECARD_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"signals": {}}


def _grade(edge):
    if edge is None:
        return "unknown", "未回测"
    if edge >= 0.3:
        return "edge_pos", "候选买点"
    if edge <= -0.15:
        return "edge_neg", "反指·别当买点"
    return "edge_flat", "无超额"


def signal_points(daily_df, scorecard: dict, horizon: str = "10d") -> list[dict]:
    """该票最新一根 K 触发的信号点(含 EDGE 分级)。daily_df 为后/前复权日线均可。"""
    if daily_df is None or len(daily_df) < 30:
        return []
    out = []
    for sig, (fn, desc) in SIGNALS.items():
        try:
            if not bool(fn(daily_df).fillna(False).iloc[-1]):
                continue
        except Exception:
            continue
        h = scorecard.get("signals", {}).get(sig, {}).get("horizons", {}).get(horizon, {})
        edge, wr = h.get("edge"), h.get("win_rate")
        grade, kind = _grade(edge)
        out.append({"sig": sig, "desc": desc, "grade": grade, "kind": kind,
                    "edge": edge, "win_rate": wr, "horizon": horizon})
    # 候选买点排前面
    order = {"edge_pos": 0, "unknown": 1, "edge_flat": 2, "edge_neg": 3}
    out.sort(key=lambda p: (order.get(p["grade"], 9), -(p["edge"] or 0)))
    return out
