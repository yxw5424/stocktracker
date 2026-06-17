"""本地交互控制台:FastAPI 绑 127.0.0.1,给静态看板补"写"能力。

启动:  python -m server.app        →  http://127.0.0.1:8777
- 同时把 docs/ 作为静态站点托管(看板本体不变);
- /api/* 提供 自选/规则/通知 的读写,写 config.yaml / rules.yaml(写前自动备份)。
- GitHub Pages 上没有这个服务,所有写操作只发生在你本地 → 零对外攻击面。
- 红线:不做多用户/云端写/自动下单。
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ruamel.yaml import YAML
from starlette.middleware.sessions import SessionMiddleware

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
CONFIG = os.path.join(ROOT, "config.yaml")
RULES = os.path.join(ROOT, "rules.yaml")

# 允许的指标(对齐 analyzer/rules.py),做写入校验白名单
_IND_ALL = {"pct_change", "amount", "amplitude", "price"}
_IND_WATCH = {"pct_change", "volume_ratio", "slope", "price"}
_OPS = {">=", ">", "<=", "<", "=="}

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return _yaml.load(f)


def _save(path, data):
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")   # 写前备份,改坏能恢复
    with open(path, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "rule"


app = FastAPI(title="stocktracker console")

# ── 登录鉴权 ──
# 设了环境变量 STK_PASSWORD 才启用登录(用于把控制台安全地暴露给小范围的人)。
# 不设 = 本地无密码模式(只绑 127.0.0.1,仅自己用)。读/看板不拦,只拦"写操作"。
PASSWORD = os.getenv("STK_PASSWORD", "")
SECRET = os.getenv("STK_SECRET") or secrets.token_hex(16)


@app.middleware("http")
async def _gate_writes(request: Request, call_next):
    if PASSWORD and request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path != "/api/login":
        if not request.session.get("auth"):
            return JSONResponse({"detail": "需要登录后才能操作"}, status_code=401)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="lax", max_age=7 * 24 * 3600)


@app.get("/api/me")
def me(request: Request):
    return {"auth_required": bool(PASSWORD), "authed": (not PASSWORD) or bool(request.session.get("auth"))}


@app.post("/api/login")
def login(request: Request, payload: dict):
    if not PASSWORD:
        return {"ok": True}
    if secrets.compare_digest(str(payload.get("password", "")), PASSWORD):
        request.session["auth"] = True
        return {"ok": True}
    raise HTTPException(401, "密码错误")


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/ping")
def ping():
    return {"ok": True, "service": "stocktracker-console"}


# ──────────────── 自选管理 ────────────────
class WatchItem(BaseModel):
    code: str
    name: str = ""
    levels: list[float] = []


@app.get("/api/watchlist")
def get_watchlist():
    cfg = _load(CONFIG)
    return {"targets": [dict(t) for t in (cfg.get("targets") or [])]}


@app.post("/api/watchlist")
def add_watchlist(item: WatchItem):
    code = item.code.strip()
    if not (code.isdigit() and len(code) == 6):
        raise HTTPException(400, "代码须为 6 位数字")
    cfg = _load(CONFIG)
    targets = cfg.setdefault("targets", [])
    if any(str(t.get("code")) == code for t in targets):
        raise HTTPException(409, "已在自选中")
    targets.append({"code": code, "name": item.name.strip() or code, "levels": list(item.levels)})
    _save(CONFIG, cfg)
    return {"ok": True, "code": code}


@app.delete("/api/watchlist/{code}")
def del_watchlist(code: str):
    cfg = _load(CONFIG)
    targets = cfg.get("targets") or []
    kept = [t for t in targets if str(t.get("code")) != code]
    if len(kept) == len(targets):
        raise HTTPException(404, "不在自选中")
    cfg["targets"] = kept
    _save(CONFIG, cfg)
    return {"ok": True}


# ──────────────── 规则 ────────────────
from analyzer import backtest as btmod  # noqa: E402
from analyzer import rules as rulesmod  # noqa: E402


@app.get("/api/signals")
def list_signals():
    return {"signals": [{"key": k, "desc": v[1]} for k, v in btmod.SIGNALS.items()]}


class BacktestReq(BaseModel):
    code: str
    signal: str = "big_up_volume"
    days: int = 800


@app.post("/api/backtest")
def run_backtest(req: BacktestReq):
    if req.signal not in btmod.SIGNALS:
        raise HTTPException(400, "未知信号")
    code = req.code.strip()
    if not (code.isdigit() and len(code) == 6):
        raise HTTPException(400, "代码须为 6 位数字")
    return btmod.backtest(code, req.signal, days=req.days)


class RuleModel(BaseModel):
    id: str = ""
    name: str
    scope: str = "watchlist"
    logic: str = "AND"
    conditions: list[dict]
    cooldown_min: int = 30


@app.get("/api/rules")
def get_rules():
    return {"rules": rulesmod.load_rules()}


@app.post("/api/rule/parse")
def parse_rule(payload: dict):
    return rulesmod.parse_nl(payload.get("text", ""))


def _validate_rule(rule: "RuleModel"):
    if rule.scope not in ("all", "watchlist"):
        raise HTTPException(400, "scope 只能是 all / watchlist")
    if rule.logic.upper() not in ("AND", "OR"):
        raise HTTPException(400, "logic 只能是 AND / OR")
    if not rule.conditions:
        raise HTTPException(400, "至少一个条件")
    allowed = _IND_WATCH if rule.scope == "watchlist" else _IND_ALL
    for c in rule.conditions:
        if c.get("indicator") not in allowed:
            raise HTTPException(400, f"{rule.scope} 不支持指标 {c.get('indicator')}(可用:{sorted(allowed)})")
        if c.get("op") not in _OPS:
            raise HTTPException(400, f"非法运算符 {c.get('op')}")
        try:
            float(c.get("value"))
        except (TypeError, ValueError):
            raise HTTPException(400, "阈值须为数字")


@app.post("/api/rules")
def add_rule(rule: RuleModel):
    _validate_rule(rule)
    data = _load(RULES) or {}
    rules = data.setdefault("rules", [])
    rid = rule.id or _slug(rule.name)
    entry = {
        "id": rid, "name": rule.name, "scope": rule.scope, "logic": rule.logic.upper(),
        "conditions": [{"indicator": c["indicator"], "op": c["op"], "value": float(c["value"])}
                       for c in rule.conditions],
        "cooldown_min": int(rule.cooldown_min),
    }
    rules[:] = [x for x in rules if x.get("id") != rid] + [entry]   # 同 id 覆盖,否则追加
    _save(RULES, data)
    return {"ok": True, "id": rid}


@app.delete("/api/rules/{rid}")
def del_rule(rid: str):
    data = _load(RULES) or {}
    rules = data.get("rules") or []
    kept = [x for x in rules if x.get("id") != rid]
    if len(kept) == len(rules):
        raise HTTPException(404, "无此规则")
    data["rules"] = kept
    _save(RULES, data)
    return {"ok": True}


# ──────────────── 通知 ────────────────
_NOTIFY_ENV = {"pushplus": "PUSHPLUS_TOKEN", "serverchan": "SERVERCHAN_KEY", "bark": "BARK_URL",
               "telegram": "TELEGRAM_BOT_TOKEN", "email": "SMTP_HOST"}


@app.get("/api/notify")
def get_notify():
    return {"channels": [{"name": k, "env": v, "configured": bool(os.getenv(v))}
                         for k, v in _NOTIFY_ENV.items()]}


@app.post("/api/notify/test")
def test_notify():
    from analyzer import notify as notifymod
    sent = notifymod.send("stocktracker 测试", "这是一条测试推送,收到说明渠道配置正常。(不构成投资建议)")
    return {"sent": sent, "ok": bool(sent)}


# 静态看板(必须放最后:/api 路由优先,其余交给静态站点)
app.mount("/", StaticFiles(directory=DOCS, html=True), name="docs")


def main():
    import uvicorn
    print("本地控制台: http://127.0.0.1:8777   (Ctrl+C 退出)")
    uvicorn.run(app, host="127.0.0.1", port=8777, log_level="warning")


if __name__ == "__main__":
    main()
