"""告警去重 + 自适应节奏状态。状态存 docs/data/state.json,随仓库提交而持久化。"""
from __future__ import annotations

import datetime as dt
import json
import os


def load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"targets": {}, "fast_mode_until": None}


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _parse(ts):
    return dt.datetime.fromisoformat(ts) if ts else None


def decide_cadence(state: dict, cfg: dict, any_surge: bool, now: dt.datetime):
    """斜率激增 → 进入/续期高频模式。返回 (mode, interval_minutes)。"""
    cad = cfg["cadence"]
    fast_until = _parse(state.get("fast_mode_until"))
    if any_surge:
        fast_until = now + dt.timedelta(minutes=int(cad["fast_mode_cooldown_minutes"]))
        state["fast_mode_until"] = fast_until.isoformat()
    mode = "fast" if (fast_until and now < fast_until) else "normal"
    interval = int(cad["fast_interval_minutes"]) if mode == "fast" else int(cad["base_interval_minutes"])
    return mode, interval


def should_report(state: dict, code: str, now: dt.datetime, interval_minutes: int) -> bool:
    """距上次完整汇报是否已超过 interval。"""
    t = state["targets"].setdefault(code, {})
    last = _parse(t.get("last_report"))
    return last is None or (now - last).total_seconds() >= interval_minutes * 60


def dedup_alerts(state: dict, code: str, alerts: list[dict], now: dt.datetime, dedup_minutes: int) -> list[dict]:
    """同类告警在窗口内只发一次。"""
    t = state["targets"].setdefault(code, {})
    seen = t.setdefault("last_alert_by_type", {})
    fresh = []
    for al in alerts:
        last = _parse(seen.get(al["type"]))
        if last is None or (now - last).total_seconds() >= dedup_minutes * 60:
            fresh.append(al)
            seen[al["type"]] = now.isoformat()
    return fresh
