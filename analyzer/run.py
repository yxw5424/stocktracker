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

import yaml

# Windows 控制台默认可能是 cp1252,打印中文会报错;统一切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# akshare 抓的是国内财经站点。若系统开了科学上网代理(Clash 等),请求会被代理
# 拦截而连不上(报 ProxyError)。这里默认让本进程"直连不走代理"。
# 若确需走代理,运行前把环境变量 NO_PROXY 设成具体域名即可覆盖此默认。
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from . import analyze as analyzemod
from . import fetch as fetchmod
from . import notify as notifymod
from . import state as statemod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")


def load_cfg() -> dict:
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def in_session(cfg: dict, now: dt.datetime) -> bool:
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
    tz = ZoneInfo(cfg["market"]["timezone"])
    now = dt.datetime.now(tz).replace(tzinfo=None)  # 用本地 naive 时间统一比较

    state_path = os.path.join(DATA_DIR, "state.json")
    state = statemod.load_state(state_path)

    market_open = args.force or args.demo or in_session(cfg, now)
    snapshot = {
        "updated_at": now.isoformat(timespec="seconds"),
        "market_open": market_open,
        "mode": "normal",
        "targets": [],
    }

    any_surge = False
    pending = []  # [(target, metrics, alerts)]

    for target in cfg["targets"]:
        code = target["code"]
        try:
            if args.demo:
                df = fetchmod.demo_minute(code, cfg["analysis"]["bar_period"])
                prev_close = float(df["close"].iloc[0])
            elif not market_open:
                df, prev_close = None, None  # 休市不取数
            else:
                df = fetchmod.fetch_minute(code, cfg["analysis"]["bar_period"])
                # 昨收价仅用于日内涨跌幅(看板未展示),而取它要下载全市场表、又慢又易失败,
                # 故默认跳过;核心的"区间涨跌幅/斜率"用分钟K线即可算。
                prev_close = None
        except Exception as e:
            print(f"[fetch] {code} failed: {e}")
            df, prev_close = None, None

        if df is None or df.empty:
            continue

        metrics = analyzemod.analyze(df, cfg, prev_close)
        alerts = analyzemod.evaluate_triggers(metrics, target, cfg)
        if any(a["type"] == "slope_surge" for a in alerts):
            any_surge = True

        series = [
            {"t": str(r["time"])[11:16], "p": round(float(r["close"]), 3)}
            for _, r in df.tail(60).iterrows()
        ]
        snapshot["targets"].append({
            "code": code, "name": target.get("name", code),
            "metrics": metrics, "alerts": alerts, "series": series,
        })
        if alerts:
            pending.append((target, metrics, alerts))

    # ── 自适应节奏:斜率激增 → 高频 ──
    mode, interval = statemod.decide_cadence(state, cfg, any_surge, now)
    snapshot["mode"] = mode
    snapshot["next_interval_minutes"] = interval

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

    # ── 写快照 ──
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "data.json")

    # 加固:本轮没取到任何数据(如取数失败/被代理拦/GitHub 海外跑不通),
    # 而上一次快照是有数据的 → 保留上一次,绝不用空数据覆盖好数据。
    write_snapshot = True
    if not snapshot["targets"] and os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("targets"):
                write_snapshot = False
                print("[skip] 本轮未取到数据,保留上一次有效快照(不覆盖)")
        except Exception:
            pass

    if write_snapshot:
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
    print(f"[done] mode={mode} interval={interval}m surge={any_surge} notified={len(notified)}")


if __name__ == "__main__":
    main()
