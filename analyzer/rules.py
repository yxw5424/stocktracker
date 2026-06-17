"""硬指标提示词规则引擎(PRD P0 核心:第一段「确定性」运行期)。

设计原则(来自 PRD 2.4):**把"聪明"放配置期,把"确定"放运行期。**
- 配置期(prompt-rule 技能 + /rule):LLM 把大白话翻成下面的封闭词表 DSL,并回译列阈值。
- 运行期(本模块):**纯硬指标、无 LLM、毫秒级、可复算**地判断每条规则是否命中。
- 降噪是一等公民:每条规则自带冷却 + 每日上限,误报是这类工具的生死线。
- 影子模式:`shadow: true` 的规则"只记录不推送",先观察 1~2 天再放量。
- 语义判断(见光死/假突破…)默认**不做**:方向预测准确率仅 45~53%,且属投顾红线。

规则只输出**客观事实**(放量 X 倍、突破近 N 日箱体、高开低走),
**绝不输出方向性结论**(会涨/见光死/该买/目标价)。

封闭词表(只能从中选,不发明新指标):
    slope            分时斜率 %/小时
    accel            斜率加速度
    vol_ratio        量比(当前 bar 量 / 近窗口均量)
    pct_window       区间涨跌幅 %(analysis.pct_change.window_minutes 内)
    pct_day          当日涨跌幅 %(对昨收)
    gap              跳空 %(今开 vs 昨收)
    amplitude        振幅 %((高-低)/昨收)
    price            现价(一般配合 price_breakout 用)
    price_breakout   突破:price 与 ref(box_high_20d / box_low_20d / box_high_30d…)比较
    intraday_shape   分时形态:high_open_low_close(高开低走)/ surge(加速拉升)/ dump(跳水)
"""
from __future__ import annotations

import datetime as dt
import os

import numpy as np
import pandas as pd

# ─────────────────────────── 词表与算子 ───────────────────────────

_OPS = {
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}

_LABEL = {
    "slope": "斜率", "accel": "斜率加速度", "vol_ratio": "量比",
    "pct_window": "区间涨跌", "pct_day": "当日涨跌", "price": "现价",
    "gap": "跳空", "amplitude": "振幅",
    "price_breakout": "突破", "intraday_shape": "分时形态",
}

# 哪些指标可用【日线】历史回放复现(其余如 slope/intraday_shape 是分时级,日线无法复现)
_DAILY_REPLAYABLE = {"pct_day", "pct_window", "vol_ratio", "gap", "amplitude", "price_breakout"}


# ─────────────────────────── 特征构建 ───────────────────────────

def _intraday_shape(df: pd.DataFrame | None) -> str:
    """从当日分时收盘序列分类形态(纯客观,不预测方向)。"""
    if df is None or getattr(df, "empty", True) or len(df) < 5:
        return "unknown"
    c = df["close"].astype(float).to_numpy()
    o, last = float(c[0]), float(c[-1])
    hi, lo = float(c.max()), float(c.min())
    rng = hi - lo
    if rng <= 0:
        return "flat"
    ret = (last / o - 1) * 100 if o else 0.0
    close_pos = (last - lo) / rng       # 0=收在最低,1=收在最高
    open_pos = (o - lo) / rng
    if open_pos >= 0.6 and close_pos <= 0.35 and ret < -0.5:
        return "high_open_low_close"
    if ret >= 1.5 and close_pos >= 0.7:
        return "surge"
    if ret <= -1.5 and close_pos <= 0.3:
        return "dump"
    return "normal"


def build_features(metrics: dict, daily_df: pd.DataFrame | None,
                   intraday_df: pd.DataFrame | None) -> dict:
    """把已算好的 metrics + 日K + 分时,汇成一个供 DSL 求值的特征字典。

    数据缺失的指标返回 None —— 用到它的条件会判定"无法求值",规则不会静默误命中。
    """
    feat: dict = {}
    for k in ("price", "pct_window", "pct_day", "slope", "slope_prev", "accel", "vol_ratio"):
        feat[k] = metrics.get(k)

    if daily_df is not None and not daily_df.empty and len(daily_df) >= 2:
        c = daily_df["close"].astype(float)
        last = daily_df.iloc[-1]
        prev_close = float(c.iloc[-2])
        if prev_close:
            feat["gap"] = round((float(last["open"]) - prev_close) / prev_close * 100, 2)
            feat["amplitude"] = round((float(last["high"]) - float(last["low"])) / prev_close * 100, 2)
            if feat.get("pct_day") is None:
                feat["pct_day"] = round((float(last["close"]) / prev_close - 1) * 100, 2)
        for n in (20, 30, 60):
            if len(c) > n:
                feat[f"_box_high_{n}d"] = round(float(c.iloc[-(n + 1):-1].max()), 3)
                feat[f"_box_low_{n}d"] = round(float(c.iloc[-(n + 1):-1].min()), 3)

    feat["intraday_shape"] = _intraday_shape(intraday_df)
    return feat


# ─────────────────────────── 条件 / 规则求值 ───────────────────────────

def _value_str(cond: dict, feat: dict) -> str:
    """该条件左值的"实际观测值",用于回译/命中消息。"""
    ind = cond.get("indicator")
    if ind == "price_breakout":
        ref = cond.get("ref", "box_high_20d")
        return f"现价{feat.get('price')}/{ref}={feat.get('_' + ref)}"
    if ind == "intraday_shape":
        return str(feat.get("intraday_shape"))
    v = feat.get(ind)
    if v is None:
        return "—"
    return f"{v:+.2f}" if isinstance(v, float) else str(v)


def eval_condition(cond: dict, feat: dict):
    """返回 True / False / None(数据缺失,无法求值)。"""
    ind = cond.get("indicator")
    op = cond.get("op", ">=")
    # 注意:用 bool() 强转——numpy 比较返回 np.bool_,且 `np.True_ is True` 为 False,
    # 直接用会让下游 `s is True` 判定失效。
    if ind == "price_breakout":
        rhs = feat.get("_" + cond.get("ref", "box_high_20d"))
        lhs = feat.get("price")
        if rhs is None or lhs is None:
            return None
        fn = _OPS.get(op)
        return bool(fn(lhs, rhs)) if fn else None
    if ind == "intraday_shape":
        lhs = feat.get("intraday_shape")
        if lhs in (None, "unknown"):
            return None
        return bool(lhs == cond.get("value")) if op != "!=" else bool(lhs != cond.get("value"))
    lhs = feat.get(ind)
    if lhs is None:
        return None
    if cond.get("abs"):
        lhs = abs(lhs)
    val = cond.get("value")
    fn = _OPS.get(op)
    if fn is None or val is None:
        return None
    return bool(fn(lhs, val))


def eval_rule(rule: dict, feat: dict) -> dict:
    """对一条规则求值。返回 {hit, rows:[(cond, sat, valstr)], unknown, logic}。"""
    rows = [(c, eval_condition(c, feat), _value_str(c, feat)) for c in rule.get("conditions", [])]
    logic = (rule.get("logic", "AND") or "AND").upper()
    sats = [r[1] for r in rows]
    if logic == "OR":
        hit = any(s is True for s in sats)
    else:
        hit = len(sats) > 0 and all(s is True for s in sats)
    return {"hit": hit, "rows": rows, "unknown": sum(1 for s in sats if s is None), "logic": logic}


def cond_text(cond: dict) -> str:
    """把一个条件渲染成人话(用于看板/回译展示阈值)。"""
    ind = cond.get("indicator")
    op = cond.get("op", "")
    if ind == "price_breakout":
        return f"突破 {cond.get('ref', 'box_high_20d')}"
    if ind == "intraday_shape":
        return f"分时形态 {op or '=='} {cond.get('value')}"
    absmark = "|·| " if cond.get("abs") else ""
    return f"{absmark}{_LABEL.get(ind, ind)} {op} {cond.get('value')}"


def hit_message(rule: dict, ev: dict) -> str:
    """命中消息:只列满足的客观条件 + 实际值。绝不含方向性结论。"""
    sat = [f"{cond_text(c)}(实际 {vs})" for c, s, vs in ev["rows"] if s is True]
    body = "；".join(sat) if sat else "条件命中"
    return f"规则『{rule.get('name', rule['id'])}』命中:{body}"


# ─────────────────────────── 加载 / 作用域 ───────────────────────────

def _norm_code(s) -> str:
    s = str(s).strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            return s[2:]
    return s


def scope_match(rule: dict, code: str) -> bool:
    sc = rule.get("scope") or ["*"]
    if "*" in sc:
        return True
    n = _norm_code(code)
    return any(_norm_code(x) == n for x in sc)


def load_rules(path: str) -> list[dict]:
    """读取 rules.yaml;不存在则返回空列表(规则引擎可选,不影响其余流程)。"""
    if not os.path.exists(path):
        return []
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for i, r in enumerate(data.get("rules", []) or []):
        if not isinstance(r, dict):
            continue
        r.setdefault("id", f"r_{i}")
        r.setdefault("name", r["id"])
        r.setdefault("enabled", True)
        r.setdefault("shadow", False)
        r.setdefault("logic", "AND")
        r.setdefault("level", "high")
        r.setdefault("scope", ["*"])
        r.setdefault("conditions", [])
        r.setdefault("noise", {})
        out.append(r)
    return out


def rule_summary(rules: list[dict]) -> list[dict]:
    """供看板展示的规则清单(含阈值,即"回译"的静态版)。"""
    return [{
        "id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]),
        "shadow": bool(r["shadow"]), "scope": r["scope"], "logic": r["logic"],
        "raw_nl": r.get("raw_nl", ""),
        "conditions": [cond_text(c) for c in r["conditions"]],
        "noise": {"cooldown_sec": int((r["noise"] or {}).get("cooldown_sec", 1800)),
                  "max_alerts_per_day": int((r["noise"] or {}).get("max_alerts_per_day", 5))},
    } for r in rules]


# ─────────────────────────── 降噪状态(冷却 / 每日上限)───────────────────────────

def _rs(state: dict, rule_id: str, code: str) -> dict:
    return state.setdefault("rules", {}).setdefault(rule_id, {}).setdefault(code, {})


def rule_gate(state: dict, rule: dict, code: str, now: dt.datetime) -> tuple[bool, str]:
    """是否允许本次推送:受冷却 + 每日上限约束。返回 (ok, why)。"""
    nc = rule.get("noise") or {}
    cooldown = int(nc.get("cooldown_sec", 1800))
    max_day = int(nc.get("max_alerts_per_day", 5))
    rs = _rs(state, rule["id"], code)
    today = now.date().isoformat()
    if rs.get("day") != today:
        rs["day"], rs["count"] = today, 0
    last = rs.get("last_fire")
    if last:
        try:
            if (now - dt.datetime.fromisoformat(last)).total_seconds() < cooldown:
                return False, "cooldown"
        except Exception:
            pass
    if int(rs.get("count", 0)) >= max_day:
        return False, "daily_cap"
    return True, "ok"


def rule_record_fire(state: dict, rule: dict, code: str, now: dt.datetime) -> None:
    rs = _rs(state, rule["id"], code)
    rs["last_fire"] = now.isoformat()
    rs["day"] = now.date().isoformat()
    rs["count"] = int(rs.get("count", 0)) + 1


def rule_record_shadow(state: dict, rule: dict, code: str, now: dt.datetime) -> None:
    """影子命中:只计数不推送,用于"如果开了会推几条"的观察。"""
    rs = _rs(state, rule["id"], code)
    rs["last_shadow"] = now.isoformat()
    rs["shadow_count"] = int(rs.get("shadow_count", 0)) + 1


# ─────────────────────────── 历史回放(lite, PIT 对齐) ───────────────────────────

def replay_rule(rule: dict, code: str, days: int = 250, demo: bool = False) -> dict:
    """在【日线】历史上回放一条规则的可复现条件,统计触发点与未来 N 日收益。

    诚实声明(务必随结果展示):
    - **PIT 对齐**:第 i 天只用前 i 天的数据算特征(箱体高/均量等),不偷看未来,防前视偏差。
    - 仅 slope/accel/intraday_shape 等**分时级指标无法在日线复现**,会被忽略并明确列出;
      若规则全靠这些指标,回放不具代表性。
    - 历史 ≠ 未来;样本可能小;未计成本/滑点/涨跌停无法成交。胜率高 ≠ 该买。
    """
    from . import fetch as fetchmod
    df = (fetchmod.demo_daily(code, max(days, 120)) if demo
          else fetchmod.fetch_daily(code, days)).reset_index(drop=True)
    if df is None or df.empty or len(df) < 30:
        return {"code": code, "rule": rule["id"], "error": "历史数据不足"}

    conds = rule.get("conditions", [])
    used = [c for c in conds if c.get("indicator") in _DAILY_REPLAYABLE]
    dropped = [cond_text(c) for c in conds if c.get("indicator") not in _DAILY_REPLAYABLE]
    if not used:
        return {"code": code, "rule": rule["id"],
                "error": "该规则无可在日线复现的条件(全为分时级)", "dropped": dropped}

    closes = df["close"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    vols = df["volume"].to_numpy(dtype=float)
    sub_rule = {**rule, "conditions": used}

    hits, dates = [], []
    start = 21  # 留足箱体/均量窗口
    for i in range(start, len(df)):
        pc = closes[i - 1]
        if pc <= 0:
            continue
        feat = {
            "pct_day": (closes[i] / pc - 1) * 100,
            "pct_window": (closes[i] / pc - 1) * 100,   # 日线近似(分钟级窗口无法复现)
            "gap": (opens[i] - pc) / pc * 100,
            "amplitude": (highs[i] - lows[i]) / pc * 100,
            "price": float(closes[i]),
        }
        ma20 = vols[i - 20:i].mean()
        feat["vol_ratio"] = float(vols[i] / ma20) if ma20 else None
        for n in (20, 30, 60):
            if i > n:
                feat[f"_box_high_{n}d"] = float(closes[i - n:i].max())
                feat[f"_box_low_{n}d"] = float(closes[i - n:i].min())
        if eval_rule(sub_rule, feat)["hit"]:
            hits.append(i)
            dates.append(str(df.iloc[i]["date"])[:10])

    horizon_stats = {}
    for h in (1, 3, 5, 10):
        rets = [float((closes[i + h] / closes[i] - 1) * 100) for i in hits if i + h < len(closes)]
        if rets:
            rs = sorted(rets)
            horizon_stats[f"{h}d"] = {
                "n": len(rets),
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                "avg": round(sum(rets) / len(rets), 2),
                "median": round(rs[len(rs) // 2], 2),
                "best": round(max(rets), 2), "worst": round(min(rets), 2),
            }
    return {
        "code": code, "rule": rule["id"], "rule_name": rule.get("name", rule["id"]),
        "sample_days": len(df), "occurrences": len(hits),
        "replayed_conditions": [cond_text(c) for c in used],
        "dropped_conditions": dropped,
        "recent_triggers": dates[-10:],
        "horizons": horizon_stats,
        "disclaimer": "PIT 对齐的历史统计;分时级条件已忽略;历史≠未来;未计成本/滑点/涨跌停;胜率高≠该买。",
    }


# ─────────────────────────── CLI ───────────────────────────

def main() -> None:
    import argparse
    import json
    import sys

    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="硬指标规则引擎:列出规则 / 历史回放(PIT)")
    ap.add_argument("--replay", metavar="RULE_ID", help="回放某条规则在历史上的触发点")
    ap.add_argument("--code", help="回放用的股票代码(默认取规则 scope 第一个或自选第一只)")
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--demo", action="store_true", help="用合成日线离线回放")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rules = load_rules(os.path.join(root, "rules.yaml"))
    by_id = {r["id"]: r for r in rules}

    if not args.replay:
        if args.json:
            print(json.dumps(rule_summary(rules), ensure_ascii=False, indent=2))
            return
        print(f"已加载 {len(rules)} 条规则:\n")
        for r in rules:
            tags = []
            if not r["enabled"]:
                tags.append("已停用")
            if r["shadow"]:
                tags.append("影子")
            tag = ("  [" + "/".join(tags) + "]") if tags else ""
            print(f"• {r['id']}  {r['name']}{tag}")
            print(f"    原话:{r.get('raw_nl', '')}")
            print(f"    条件({r['logic']}):" + " ; ".join(cond_text(c) for c in r["conditions"]))
            nc = r["noise"] or {}
            print(f"    降噪:冷却 {nc.get('cooldown_sec', 1800)}s,每日上限 {nc.get('max_alerts_per_day', 5)}")
            print(f"    作用域:{r['scope']}\n")
        print("回放:python -m analyzer.rules --replay <id> --code 600909 [--demo]")
        print("\n⚠️ 规则只报客观事实,不构成投资建议,不自动下单。")
        return

    rule = by_id.get(args.replay)
    if not rule:
        print(f"未找到规则 {args.replay};可用:{list(by_id)}")
        return
    code = args.code
    if not code:
        sc = [s for s in (rule.get("scope") or []) if s != "*"]
        code = _norm_code(sc[0]) if sc else "600909"
    res = replay_rule(rule, code, days=args.days, demo=args.demo)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if "error" in res:
        print(f"{code} / {args.replay}: {res['error']}")
        if res.get("dropped"):
            print("  无法在日线复现的条件:" + " ; ".join(res["dropped"]))
        return
    print(f"回放 {res['code']} 规则『{res['rule_name']}』 — 近 {res['sample_days']} 日触发 {res['occurrences']} 次")
    print("  已复现条件:" + " ; ".join(res["replayed_conditions"]))
    if res["dropped_conditions"]:
        print("  ⚠️ 已忽略(分时级,日线无法复现):" + " ; ".join(res["dropped_conditions"]))
    if res["recent_triggers"]:
        print("  最近触发日:" + ", ".join(res["recent_triggers"]))
    if res["horizons"]:
        print(f"\n  {'持有期':<6}{'样本':>5}{'胜率':>8}{'平均':>9}{'中位':>9}{'最好':>9}{'最差':>9}")
        for h, s in res["horizons"].items():
            print(f"  {h:<6}{s['n']:>5}{s['win_rate']:>7}%{s['avg']:>8}%{s['median']:>8}%{s['best']:>8}%{s['worst']:>8}%")
    print(f"\n⚠️ {res['disclaimer']}")


if __name__ == "__main__":
    main()
