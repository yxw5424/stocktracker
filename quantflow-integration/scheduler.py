# -*- coding: utf-8 -*-
"""
Routine 调度器 —— 跑在常驻的 quantflow 容器里,定时执行平台任务,前端可管理。

任务(进程内执行,不需要 docker compose run):
  refresh      行情增量更新(market_data.incremental_update 全库)
  review       AI 复核官(进程内跑 daily_review.main:月初出再平衡信号,平时快照/复盘;
               ⚠ 它读同一套环境变量:HTSC_LIVE=1 时定时任务也会真下单,默认0干跑)
  backup_lite  轻量备份:把 ai_review_*(决策/权益/对账/经验)+自选导出为 gzip JSON
               到 /reports/backups/(完整 mongodump 仍用 docker compose run --rm backup)

任务定义存 Mongo(scheduler_jobs),默认三条全部【停用】——在看板「系统」页
手动开启,避免用户不知情的后台 LLM 调用/下单。仅交易日 = 查 trade_calendar,
日历缺失时退化为周一~周五。时区跟容器 TZ(Asia/Shanghai)。
"""
import os
import io
import sys
import json
import gzip
import time
import datetime
import threading
import traceback
import contextlib

REPORT_DIR = os.getenv("REPORT_DIR", "/reports")
JOBS_COL = "scheduler_jobs"

DEFAULT_JOBS = [
    {"id": "sentinel", "name": "异动哨兵(14:50查实时行情,大跌触发复核)", "task": "sentinel",
     "time": "14:50", "trading_days_only": True, "enabled": False},
    {"id": "refresh", "name": "行情增量更新", "task": "refresh",
     "time": "15:20", "trading_days_only": True, "enabled": False},
    {"id": "review", "name": "AI复核官(月初再平衡/每日快照)", "task": "review",
     "time": "15:40", "trading_days_only": True, "enabled": False},
    {"id": "backup", "name": "轻量备份(决策/权益/对账→/reports/backups)", "task": "backup_lite",
     "time": "20:00", "trading_days_only": False, "enabled": False},
]

_LOCK = threading.Lock()          # 同一时刻只跑一个任务
_STARTED = {"v": False}


def _mongo():
    import dashboard
    return dashboard._mongo()


def _is_trading_day(db, ymd):
    try:
        col = db["trade_calendar"]
        if col.estimated_document_count() > 0:
            return col.find_one({"nature_date": int(ymd), "exchange": "SH"}) is not None
    except Exception:
        pass
    return datetime.datetime.strptime(ymd, "%Y%m%d").weekday() < 5


# ---------------- 任务实现 ----------------
def _task_refresh(log):
    import market_data as md
    db = _mongo()
    syms = sorted(db["stock_market"].distinct("symbol"))
    added, fails = 0, 0
    for sym in syms:
        try:
            n, _note = md.incremental_update(db, sym)
            added += n
        except Exception as e:
            fails += 1
            log(f"[fail] {sym}: {e}")
    log(f"完成:{len(syms)}只,新增{added}条,失败{fails}只")


def _task_review(log):
    """进程内跑复核官。每次全新加载模块(它有进程级缓存,长驻必须刷新)。"""
    import importlib.util
    path = None
    for p in ("/app/panda_quantflow/src/daily_review.py",
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_review.py"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "review", "daily_review.py")):
        if os.path.exists(p):
            path = p
            break
    if not path:
        raise RuntimeError("找不到 daily_review.py(镜像需重新 build)")
    spec = importlib.util.spec_from_file_location(f"daily_review_{int(time.time())}", path)
    mod = importlib.util.module_from_spec(spec)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        spec.loader.exec_module(mod)
        mod.main()
    out = buf.getvalue()
    for line in out.splitlines()[-60:]:
        log(line)


def _task_backup_lite(log):
    db = _mongo()
    outdir = os.path.join(REPORT_DIR, "backups")
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    payload = {}
    for coll in ("ai_review_positions", "ai_review_decisions", "ai_review_lessons",
                 "ai_review_equity", "ai_review_recon", "user_watchlist", JOBS_COL):
        payload[coll] = list(db[coll].find({}, {"_id": 0}))
    path = os.path.join(outdir, f"core_{stamp}.json.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)
    keep = sorted(os.listdir(outdir))
    for old in keep[:-30]:                       # 只留最近30份
        try:
            os.remove(os.path.join(outdir, old))
        except OSError:
            pass
    log(f"已备份 {sum(len(v) for v in payload.values())} 条记录 -> {path}")


def _pick_num(obj, *keys):
    """在嵌套响应里递归找数值字段(键名匹配)。"""
    if isinstance(obj, dict):
        low = {str(k).lower(): v for k, v in obj.items()}
        for k in keys:
            v = low.get(k.lower())
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        for v in obj.values():
            got = _pick_num(v, *keys)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _pick_num(v, *keys)
            if got:
                return got
    return None


def _task_sentinel(log):
    """异动哨兵:盘中(默认14:50)查沪深300实时报价,单日跌幅超过 SENTINEL_PCT(默认3%)
    → 立即触发一次复核官运行(agent 收集事实并决定是否报警/建议推迟月初动作)。
    没有 HT_APIKEY(拿不到实时价)则跳过。这是事件驱动的 agentic 环节:平时沉默。"""
    if not os.getenv("HT_APIKEY"):
        log("未配 HT_APIKEY,拿不到实时报价,跳过")
        return
    import sys
    for p in ("/app/panda_quantflow/src",):
        if p not in sys.path:
            sys.path.insert(0, p)
    from htsc_broker import HTSCBroker
    b = HTSCBroker()
    resp = b.get_quote("510300", "SH")
    last = _pick_num(resp, "lastPrice", "last", "price", "currentPrice", "newPrice")
    pre = _pick_num(resp, "preClose", "preClosePrice", "prevClose", "yesterdayClose")
    if not last or not pre:
        log(f"报价解析失败,原始响应前200字:{str(resp)[:200]}")
        return
    chg = (last / pre - 1) * 100
    thresh = float(os.getenv("SENTINEL_PCT", "3"))
    log(f"沪深300 现价 {last} / 昨收 {pre},日内 {chg:+.2f}%(阈值 -{thresh}%)")
    if chg <= -thresh:
        log(f"⚠ 触发异动:跌幅超过 {thresh}%,立即运行复核官收集事实并决策")
        _task_review(log)
    else:
        log("正常,不动")


_TASKS = {"refresh": _task_refresh, "review": _task_review,
          "backup_lite": _task_backup_lite, "sentinel": _task_sentinel}


# ---------------- 任务库(Mongo) ----------------
def _seed(db):
    for j in DEFAULT_JOBS:
        if not db[JOBS_COL].find_one({"id": j["id"]}):
            db[JOBS_COL].update_one({"id": j["id"]}, {"$set": j}, upsert=True)


def jobs_list():
    try:
        db = _mongo()
        _seed(db)
        rows = list(db[JOBS_COL].find({}, {"_id": 0}))
        rows.sort(key=lambda j: j.get("time", ""))
        return {"ok": True, "rows": rows,
                "scheduler_on": os.getenv("SCHEDULER", "1") == "1"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "rows": []}


def job_update(jid, enabled=None, time_str=None, trading_days_only=None):
    try:
        db = _mongo()
        upd = {}
        if enabled is not None:
            upd["enabled"] = enabled in (True, "1", "true")
        if time_str:
            hh, mm = time_str.strip().split(":")
            upd["time"] = f"{int(hh):02d}:{int(mm):02d}"
        if trading_days_only is not None:
            upd["trading_days_only"] = trading_days_only in (True, "1", "true")
        if not upd:
            return {"ok": False, "error": "没有要更新的字段"}
        db[JOBS_COL].update_one({"id": jid}, {"$set": upd})
        return {"ok": True, "id": jid, **upd}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _run_job(job):
    db = _mongo()
    jid = job["id"]
    logs = []

    def log(msg):
        logs.append(f"{datetime.datetime.now().strftime('%H:%M:%S')} {msg}")

    db[JOBS_COL].update_one({"id": jid}, {"$set": {"last_status": "running",
        "last_run": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}})
    t0 = time.time()
    try:
        with _LOCK:
            _TASKS[job["task"]](log)
        status = "ok"
    except Exception as e:
        status = "fail"
        log(f"异常:{e}")
        logs.append(traceback.format_exc()[-1500:])
    db[JOBS_COL].update_one({"id": jid}, {"$set": {
        "last_status": status,
        "last_duration_s": round(time.time() - t0, 1),
        "last_log": "\n".join(logs)[-6000:]}})
    return status


def job_run_now(jid):
    try:
        db = _mongo()
        job = db[JOBS_COL].find_one({"id": jid}, {"_id": 0})
        if not job:
            return {"ok": False, "error": f"任务不存在:{jid}"}
        threading.Thread(target=_run_job, args=(job,), daemon=True).start()
        return {"ok": True, "id": jid, "note": "已触发,几秒后刷新看结果"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def job_log(jid):
    try:
        db = _mongo()
        job = db[JOBS_COL].find_one({"id": jid}, {"_id": 0}) or {}
        return {"ok": True, "id": jid, "log": job.get("last_log", "(还没跑过)"),
                "status": job.get("last_status", "")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------- 调度循环 ----------------
def _loop():
    while True:
        try:
            now = datetime.datetime.now()
            hhmm, today = now.strftime("%H:%M"), now.strftime("%Y%m%d")
            db = _mongo()
            _seed(db)
            for job in db[JOBS_COL].find({"enabled": True}):
                if job.get("time") != hhmm:
                    continue
                if job.get("last_fired") == f"{today} {hhmm}":
                    continue                     # 这一分钟已触发过
                if job.get("trading_days_only") and not _is_trading_day(db, today):
                    continue
                db[JOBS_COL].update_one({"id": job["id"]},
                                        {"$set": {"last_fired": f"{today} {hhmm}"}})
                threading.Thread(target=_run_job, args=(dict(job),), daemon=True).start()
        except Exception:
            pass                                  # 调度循环永不死
        time.sleep(20)


def start():
    """由 run_local_secure 在启动时调用;SCHEDULER=0 可整体停用。"""
    if _STARTED["v"] or os.getenv("SCHEDULER", "1") != "1":
        return False
    _STARTED["v"] = True
    threading.Thread(target=_loop, daemon=True).start()
    print("  Routine调度器已启动(任务默认停用,在 /dash 系统页开启)")
    return True
