"""提示词规则引擎:把(由自然语言解析来的)硬指标 DSL 规则,在实时数据上执行。

规则存 `rules.yaml`,每条:
  {id, name, scope(all|watchlist), logic(AND|OR), conditions:[{indicator, op, value}], cooldown_min}

- scope=all       → digest 在【全市场快照】上向量化评估(可用指标:pct_change/amount/amplitude/price)。
- scope=watchlist → run.py 在【自选 + 分钟级】上评估(可用更细:slope/volume_ratio/pct_change/price)。

某指标在当前上下文拿不到(如全市场没有 slope)→ 该规则在此上下文"不适用",不误报。
这就是 PRD 的"硬指标段":确定、便宜、可解释。语义判断(见光死等)留给插件侧/LLM,默认不在引擎里跑。
"""
from __future__ import annotations

import os

import yaml

_OPS = {
    ">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b, "<": lambda a, b: a < b, "==": lambda a, b: a == b,
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_nl(text: str) -> dict:
    """把大白话规则做【基础关键词解析】成硬指标 DSL 草稿(供前端 chips 编辑确认)。

    这是离线、确定性的关键词解析,覆盖常见盯盘话术;复杂/否定/时序/组合规则
    请用 Claude Code 的 /rule(完整 LLM 解析)。解析结果【必须】回前端逐条可改。
    """
    import re

    t = text or ""

    def _num(anchor: str, default: float) -> float:
        m = re.search(anchor + r"[^0-9]{0,6}(\d+(?:\.\d+)?)", t)
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
        return float(m.group(1)) if m else default

    conds: list[dict] = []
    unsupported: list[str] = []
    if any(k in t for k in ["放量", "量比", "巨量", "天量", "爆量"]):
        conds.append({"indicator": "volume_ratio", "op": ">=", "value": 2.0})
    if any(k in t for k in ["斜率", "变陡", "激增", "加速", "陡", "拉升", "急拉"]):
        conds.append({"indicator": "slope", "op": ">=", "value": 4.0})
    if "涨停" in t:
        conds.append({"indicator": "pct_change", "op": ">=", "value": 9.7})
    elif any(k in t for k in ["大涨", "涨幅", "涨超", "猛涨", "暴涨"]) or ("涨" in t and "%" in t):
        conds.append({"indicator": "pct_change", "op": ">=", "value": _num("涨", 5.0)})
    if any(k in t for k in ["跳水", "大跌", "跌超", "急跌", "闪崩", "跳水"]) or ("跌" in t and "%" in t):
        conds.append({"indicator": "pct_change", "op": "<=", "value": -_num("跌", 3.0)})
    if "振幅" in t:
        conds.append({"indicator": "amplitude", "op": ">=", "value": 5.0})
    if any(k in t for k in ["成交额", "成交超", "放出"]):
        conds.append({"indicator": "amount", "op": ">=", "value": 300000000})
    if any(k in t for k in ["突破", "新高", "破位", "破前高"]):
        unsupported.append("突破/新高:引擎暂未实现该硬指标,可用 Claude /rule 或等后续版本")

    seen, uniq = set(), []
    for c in conds:
        if c["indicator"] in seen:
            continue
        seen.add(c["indicator"])
        uniq.append(c)
    conds = uniq

    uses_intraday = any(c["indicator"] in ("slope", "volume_ratio") for c in conds)
    scope = "watchlist" if (uses_intraday or "自选" in t) else "all"
    return {
        "raw_nl": text,
        "name": (text.strip()[:14] or "新规则"),
        "scope": scope,
        "logic": "AND",
        "conditions": conds,
        "cooldown_min": 30,
        "unsupported": unsupported,
    }


def load_rules(path: str | None = None) -> list[dict]:
    path = path or os.path.join(ROOT, "rules.yaml")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("rules", [])


def eval_rule(rule: dict, features: dict):
    """对单只票的特征 dict 评估。True/False,或 None(有条件不可评估 → 本上下文不适用)。"""
    results = []
    for c in rule.get("conditions", []):
        v = features.get(c["indicator"])
        if v is None:
            return None
        op = _OPS.get(c["op"])
        if op is None:
            return None
        results.append(op(v, c["value"]))
    if not results:
        return None
    return all(results) if rule.get("logic", "AND").upper() == "AND" else any(results)


def _series(df, indicator):
    """把指标名映射到全市场 df 的列/计算列(scope=all 上下文)。"""
    if indicator == "pct_change":
        return df["pct"]
    if indicator == "amount":
        return df["amount"]
    if indicator == "price":
        return df["price"]
    if indicator == "amplitude":
        return (df["high"] - df["low"]) / df["prev_close"].replace(0, float("nan")) * 100
    return None


def match_dataframe(rule: dict, df):
    """向量化匹配 scope=all 规则,返回命中子集 DataFrame;None=指标不可得(不适用)。"""
    mask = None
    and_logic = rule.get("logic", "AND").upper() == "AND"
    for c in rule.get("conditions", []):
        s = _series(df, c["indicator"])
        op = _OPS.get(c["op"])
        if s is None or op is None:
            return None
        m = op(s, c["value"])
        mask = m if mask is None else (mask & m if and_logic else mask | m)
    if mask is None:
        return None
    return df[mask.fillna(False)]
