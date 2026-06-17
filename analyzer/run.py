"""主入口:取数 → 分析 → 触发判断 → 写 data.json → 去重推送 → 更新状态。

用法:
    python -m analyzer.run            # 正常(联网,仅交易时段取数)
    python -m analyzer.run --demo     # 离线合成数据,验证整条流水线 + 网站
    python -m analyzer.run --force    # 忽略交易时段判断(联网)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

# Windows 控制台默认可能是 cp1252,打印中文会报错;统一切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from . import analyze as analyzemod
from . import fetch as fetchmod
from . import notify as notifymod
from . import rules as rulesmod
from . import state as statemod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")
CACHE_DIR = os.path.join(ROOT, ".cache")  # 日K 等本地缓存(不入库)


def load_cfg() -> dict:
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def in_session(cfg: dict, now: dt.datetime) -> bool:
    if now.weekday() >= 5:  # 周末不是交易日,直接不取数(法定节假日可后续补)
        return False
    for start, end in cfg["market"]["sessions"]:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        s = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        e = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if s <= now <= e:
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="用合成数据离线跑(不联网)")
    ap.add_argument("--force", action="store_true", help="忽略交易时段判断")
    args = ap.parse_args()

    cfg = load_cfg()
    rules_cfg = rulesmod.load_rules(os.path.join(ROOT, "rules.yaml"))
    tz = ZoneInfo(cfg["market"]["timezone"])
    now = dt.datetime.now(tz).replace(tzinfo=None)  # 用本地 naive 时间统一比较

    state_path = os.path.join(DATA_DIR, "state.json")
    state = statemod.load_state(state_path)

    market_open = args.force or args.demo or in_session(cfg, now)
    # 失败熔断:疑似被限频时冷却退避,本轮不取数、沿用旧数据(--force/--demo 可强制绕过)
    cooling = statemod.in_cooldown(state, now) and not args.force and not args.demo
    if cooling:
        print(f"[cooldown] 取数冷却中(疑似被限频),沿用上次数据,到 {state.get('fetch_cooldown_until')} 自动恢复")
    snapshot = {
        "updated_at": now.isoformat(timespec="seconds"),
        "market_open": market_open,
        "mode": "normal",
        "demo": args.demo,
        "targets": [],
    }

    any_surge = False
    pending = []  # [(target, metrics, alerts)]
    live_rule_hits = []  # [(target, rule, message, hit_dict)] —— 非影子规则,待按规则冷却推送

    for target in cfg["targets"]:
        code = target["code"]
        analysis_df = intraday_df = daily_df = None
        try:
            if args.demo:
                analysis_df = fetchmod.demo_minute(code, cfg["analysis"]["bar_period"], n=40)
                intraday_df = fetchmod.demo_minute(code, "1", n=240)
                daily_df = fetchmod.demo_daily(code)
            elif market_open and not cooling:
                # 防封关键:一轮只打一次 1 分钟请求 —— 分时切片 + 重采样出分钟K分析,共用这份数据;
                # 日K 当天只抓一次(缓存)。把每只票每轮请求数从 3 砍到约 1~2。
                raw = fetchmod.fetch_1min(code)
                analysis_df = fetchmod.resample_bars(raw, int(cfg["analysis"]["bar_period"]))
                intraday_df = fetchmod.today_slice(raw)
                daily_df = fetchmod.fetch_daily_cached(code, 120, CACHE_DIR, str(now.date()))
        except Exception as e:
            print(f"[fetch] {code} failed: {e}")

        if analysis_df is None or analysis_df.empty:
            continue

        metrics = analyzemod.analyze(analysis_df, cfg, None)
        alerts = analyzemod.evaluate_triggers(metrics, target, cfg)
        if any(a["type"] == "slope_surge" for a in alerts):
            any_surge = True

        # ── 视图:分时(含均价线)+ 日K(蜡烛) ──
        views = {"intraday": [], "daily": []}
        if intraday_df is not None and not intraday_df.empty:
            c = intraday_df["close"].to_numpy(dtype=float)
            v = intraday_df["volume"].fillna(0).to_numpy(dtype=float) if "volume" in intraday_df else None
            if v is not None and v.sum() > 0:
                avg = (c * v).cumsum() / v.cumsum().clip(min=1e-9)
            else:
                avg = pd.Series(c).expanding().mean().to_numpy()
            views["intraday"] = [
                {"t": str(t)[11:16], "p": round(float(p), 3), "avg": round(float(a), 3)}
                for t, p, a in zip(intraday_df["time"], c, avg)
            ]
        if daily_df is not None and not daily_df.empty:
            views["daily"] = [
                {"d": str(r["date"])[5:], "o": round(float(r["open"]), 3), "c": round(float(r["close"]), 3),
                 "h": round(float(r["high"]), 3), "l": round(float(r["low"]), 3)}
                for _, r in daily_df.iterrows()
            ]

        # ── 提示词规则引擎(硬指标 DSL):确定性命中判断 ──
        feat = rulesmod.build_features(metrics, daily_df, intraday_df)
        rule_hits = []
        for rule in rules_cfg:
            if not rule.get("enabled", True) or not rulesmod.scope_match(rule, code):
                continue
            ev = rulesmod.eval_rule(rule, feat)
            if not ev["hit"]:
                continue
            shadow = bool(rule.get("shadow", False))
            hit = {"rule_id": rule["id"], "name": rule.get("name", rule["id"]),
                   "level": rule.get("level", "high"), "shadow": shadow,
                   "message": rulesmod.hit_message(rule, ev), "fired": False}
            rule_hits.append(hit)
            if shadow:
                rulesmod.rule_record_shadow(state, rule, code, now)  # 只记录不推送
            else:
                live_rule_hits.append((target, rule, hit["message"], hit))

        snapshot["targets"].append({
            "code": code, "name": target.get("name", code),
            "metrics": metrics, "alerts": alerts, "views": views, "rule_hits": rule_hits,
        })
        if alerts:
            pending.append((target, metrics, alerts))

    # ── 失败熔断:本轮真实取数全失败则计数,达阈值进入冷却 ──
    if market_open and not cooling and not args.demo:
        fresh_count = len(snapshot["targets"])  # 此时尚未 backfill,均为本轮新取
        cd = statemod.record_fetch_result(state, fresh_count > 0, now, cfg)
        if fresh_count == 0:
            print(f"[fetch] 本轮全部失败 streak={state.get('fetch_fail_streak')}"
                  + (f" → 冷却至 {cd}" if cd else ""))

    # ── 自适应节奏:斜率激增 → 高频 ──
    mode, interval = statemod.decide_cadence(state, cfg, any_surge, now)
    snapshot["mode"] = mode
    snapshot["next_interval_minutes"] = interval
    snapshot["rules"] = rulesmod.rule_summary(rules_cfg)  # 看板展示规则清单(含阈值)

    # ── 去重 + 按节奏决定是否推送 ──
    dedup_min = int(cfg["cadence"]["alert_dedup_minutes"])
    notified = []
    for target, metrics, alerts in pending:
        code = target["code"]
        # 正常节奏未到点且非高频 → 本轮只更新网站、不推送
        if mode == "normal" and not statemod.should_report(state, code, now, interval):
            continue
        fresh = statemod.dedup_alerts(state, code, alerts, now, dedup_min)
        if not fresh:
            continue
        state["targets"][code]["last_report"] = now.isoformat()
        title = f"📈 {target.get('name', code)} {code} 异动 [{mode}]"
        lines = [f"价格 {metrics['price']}  斜率 {metrics['slope']:+.2f}%/h  量比 {metrics['vol_ratio']:.2f}"]
        lines += [f"• {a['message']}" for a in fresh]
        lines += ["", "(信息提醒,不构成投资建议,不自动下单)"]
        content = "\n".join(lines)
        sent = notifymod.send(title, content)
        notified.append({"code": code, "alerts": [a["message"] for a in fresh], "sent": sent})
        print(f"[alert] {code}: {[a['message'] for a in fresh]} -> {sent}")

    # ── 规则命中推送:走每条规则自己的冷却 / 每日上限,独立于全局汇报节奏 ──
    for target, rule, msg, hit in live_rule_hits:
        code = target["code"]
        ok, why = rulesmod.rule_gate(state, rule, code, now)
        if not ok:
            hit["suppressed"] = why  # cooldown / daily_cap —— 看板可显示"已抑制"
            continue
        rulesmod.rule_record_fire(state, rule, code, now)
        hit["fired"] = True
        title = f"📐 {target.get('name', code)} {code} 规则命中"
        content = "\n".join([f"【规则命中·非投资建议】{msg}", "",
                             "(仅客观盘面,不构成投资建议,不自动下单)"])
        sent = notifymod.send(title, content)
        notified.append({"code": code, "alerts": [msg], "sent": sent})
        print(f"[rule] {code} {rule['id']}: {msg} -> {sent}")

    # ── 写快照 ──
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "data.json")

    # 加固:对本轮没取到的标的,沿用上一次的好数据(标记 stale),避免卡片消失、
    # 或被部分失败的结果覆盖。海外 Actions 全失败时也能靠仓库里的旧数据兜底。
    got = {t["code"] for t in snapshot["targets"]}
    prev_targets = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                prev_targets = {t["code"]: t for t in json.load(f).get("targets", [])}
        except Exception:
            pass
    for target in cfg["targets"]:
        code = target["code"]
        if code not in got and code in prev_targets:
            stale = dict(prev_targets[code])
            stale["stale"] = True
            snapshot["targets"].append(stale)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # ── 追加告警历史(最多保留 200 条)──
    hist_path = os.path.join(DATA_DIR, "alerts_history.json")
    history = []
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    for n in notified:
        for m in n["alerts"]:
            history.append({"time": now.isoformat(timespec="seconds"), "code": n["code"], "message": m})
    history = history[-200:]
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    statemod.save_state(state_path, state)
    rule_fired = sum(1 for _, _, _, h in live_rule_hits if h.get("fired"))
    print(f"[done] mode={mode} interval={interval}m surge={any_surge} "
          f"notified={len(notified)} rules_fired={rule_fired}")


if __name__ == "__main__":
    main()
