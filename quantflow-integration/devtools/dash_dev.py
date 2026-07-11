# -*- coding: utf-8 -*-
"""
看板 E2E 开发平台 —— 不需要 Docker/Mongo/华泰,直接在本机跑真实的 dashboard.py:
  · FakeMongo:内存假库,实现 dashboard 用到的最小 API(find/sort/limit/distinct/
    find_one/estimated_document_count/count_documents/update_one/delete_*)
  · 逼真夹具:61只标的的几何随机游走行情、权益快照、决策/对账/经验、华泰假响应
  · 假 market_data:add_symbol/incremental_update 离线可用(生成假行情)
用法:
  python devtools/dash_dev.py serve [端口]   # 起服务,浏览器手看
  python devtools/e2e_dash.py               # 全标签走查+截图(见同目录)
"""
import os
import sys
import json
import math
import types
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# ---------------- FakeMongo ----------------
class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction=1):
        if isinstance(key, list):
            key, direction = key[0]
        self.rows = sorted(self.rows, key=lambda d: d.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def __iter__(self):
        return iter([dict(r) for r in self.rows])


def _match(doc, flt):
    for k, v in (flt or {}).items():
        if isinstance(v, dict):
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
            if "$lt" in v and not (doc.get(k) or "") < v["$lt"]:
                return False
            if "$gt" in v and not (doc.get(k) or "") > v["$gt"]:
                return False
            if "$regex" in v:
                import re
                if not re.search(v["$regex"], str(doc.get(k, ""))):
                    return False
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _Coll:
    def __init__(self):
        self.docs = []

    def find(self, flt=None, proj=None):
        rows = [d for d in self.docs if _match(d, flt)]
        if proj:
            keep = {k for k, v in proj.items() if v} - {"_id"}
            drop = {k for k, v in proj.items() if not v}
            if keep:
                rows = [{k: d.get(k) for k in keep if k in d} for d in rows]
            else:
                rows = [{k: v for k, v in d.items() if k not in drop} for d in rows]
        return _Cursor(rows)

    def find_one(self, flt=None, proj=None, sort=None):
        cur = self.find(flt, proj)
        if sort:
            cur.sort(sort)
        rows = list(cur)
        return rows[0] if rows else None

    def distinct(self, key):
        return sorted({d.get(key) for d in self.docs if d.get(key)})

    def estimated_document_count(self):
        return len(self.docs)

    def count_documents(self, flt, limit=None):
        n = sum(1 for d in self.docs if _match(d, flt))
        return min(n, limit) if limit else n

    def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if _match(d, flt):
                d.update(update.get("$set", {}))
                return
        if upsert:
            doc = dict(flt)
            doc.update(update.get("$set", {}))
            self.docs.append(doc)

    def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if _match(d, flt):
                del self.docs[i]
                return
    def delete_many(self, flt):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, flt)]
        return types.SimpleNamespace(deleted_count=before - len(self.docs))

    def bulk_write(self, ops, ordered=False):
        for op in ops:
            self.update_one(op._filter, op._doc, upsert=True)


class FakeDB(dict):
    def __getitem__(self, k):
        if k not in self:
            dict.__setitem__(self, k, _Coll())
        return dict.get(self, k)


# ---------------- 夹具数据 ----------------
NAMES = {"510300.SH": "沪深300ETF", "511260.SH": "十年国债ETF", "518880.SH": "黄金ETF",
         "512760.SH": "芯片ETF", "588000.SH": "科创50ETF", "159915.SZ": "创业板ETF",
         "513100.SH": "纳指ETF", "510880.SH": "红利ETF", "600519.SH": "贵州茅台",
         "688347.SH": "华虹公司", "300308.SZ": "中际旭创", "512890.SH": "红利低波ETF"}


def _dates(n=250, end=None):
    end = end or datetime.date(2026, 7, 10)
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= datetime.timedelta(days=1)
    return list(reversed(out))


def _walk(seed, n, base, drift, vol):
    rnd = __import__("random").Random(seed)
    h, p = [], base
    for _ in range(n):
        p *= (1 + drift + rnd.gauss(0, vol))
        h.append(round(p, 3))
    return h


def build_db():
    db = FakeDB()
    dates = _dates(250)
    for i, (sym, name) in enumerate(NAMES.items()):
        vol = 0.001 if "国债" in name else (0.025 if ("科创" in name or "华虹" in name or "旭创" in name) else 0.012)
        drift = 0.0005 if i % 3 else -0.0002
        closes = _walk(i, 250, 1.0 + i, drift, vol)
        for d, c in zip(dates, closes):
            db["stock_market"].docs.append({
                "symbol": sym, "date": d, "close": c, "open": round(c * 0.995, 3),
                "high": round(c * 1.01, 3), "low": round(c * 0.99, 3),
                "volume": 1e7, "turnover": (3 + i) * 1e8})
        db["stock_info_new"].docs.append({"symbol": sym, "name": name, "type": 0})
    eq_dates = dates[-40:]
    v = 1000000.0
    rnd = __import__("random").Random(7)
    for d in eq_dates:
        v *= (1 + rnd.gauss(0.0006, 0.004))
        db["ai_review_equity"].docs.append({"date": d, "total_asset": round(v)})
    db["ai_review_positions"].docs += [
        {"symbol": "510300.SH", "qty": 97500, "entry_price": 4.1, "entry_date": "20260701",
         "high_since": 4.2, "weight": 0.4, "alloc": True},
        {"symbol": "511260.SH", "qty": 3900, "entry_price": 100.1, "entry_date": "20260701",
         "high_since": 100.4, "weight": 0.4, "alloc": True},
        {"symbol": "518880.SH", "qty": 28100, "entry_price": 7.05, "entry_date": "20260701",
         "high_since": 7.3, "weight": 0.2, "alloc": True}]
    db["ai_review_decisions"].docs += [
        {"date": "20260701", "symbol": s, "side": "买入", "price": p, "decision": dec,
         "reason": r, "evaluated": ev, "outcome_pct": op, "verdict": vd}
        for s, p, dec, r, ev, op, vd in [
            ("510300.SH", 4.1, "执行", "月度再平衡:目标40%,偏离+40%(首次建仓),市场无极端状态", True, 2.4, "放行买入后涨"),
            ("511260.SH", 100.1, "执行", "月度再平衡:目标40%,债市平稳", True, 0.3, "放行买入后涨"),
            ("518880.SH", 7.05, "半仓", "月度再平衡:目标20%;金价近日波动放大,建议先半仓,下月补齐", False, None, None)]]
    db["ai_review_recon"].docs += [
        {"date": "20260702", "symbol": "510300.SH", "side": "买入", "decision_price": 4.10,
         "fill_price": 4.105, "slippage_pct": 0.12, "qty": 97500},
        {"date": "20260702", "symbol": "511260.SH", "side": "买入", "decision_price": 100.10,
         "fill_price": 100.09, "slippage_pct": -0.01, "qty": 3900}]
    db["ai_review_lessons"].docs.append({"date": "20260706", "lessons": [
        "再平衡日若遇跌停潮,推迟执行是对的", "黄金波动放大时半仓入场减少了回撤"]})
    db["user_watchlist"].docs += [
        {"symbol": "600519.SH", "name": "贵州茅台", "asset_type": "stock",
         "source": "manual", "added_at": "2026-07-08 10:00"},
        {"symbol": "512890.SH", "name": "红利低波ETF", "asset_type": "etf",
         "source": "manual", "added_at": "2026-07-09 14:00"}]
    return db


class FakeBroker:
    def pending_orders(self):
        return {"data": {"orders": [
            {"stockName": "黄金ETF", "stockCode": "518880", "exchange": "SH", "direction": "buy",
             "price": 7.05, "quantity": 14000, "filledQuantity": 0, "status": "已报待成交"}]}}

    def positions(self):
        return {"data": {"positions": [
            {"stockName": "沪深300ETF", "stockCode": "510300", "quantity": 97500,
             "costPrice": 4.105, "marketValue": 405600, "profit": 5360},
            {"stockName": "十年国债ETF", "stockCode": "511260", "quantity": 3900,
             "costPrice": 100.09, "marketValue": 391150, "profit": 800}]}}

    def account_balance(self):
        return {"data": {"totalAsset": 1021300, "cash": 224550, "marketValue": 796750}}

    def trade_history(self, start=None, end=None):
        return {"data": {"trades": [
            {"stockName": "沪深300ETF", "stockCode": "510300", "exchange": "SH", "direction": "buy",
             "price": 4.105, "quantity": 97500, "filledQuantity": 97500, "status": "全部成交"}]}}


def patched_dashboard(tmp_reports):
    """导入真实 dashboard.py,替换外部依赖为夹具。返回 (dashboard模块, fake_db)。"""
    os.environ.setdefault("HT_APIKEY", "fake-for-dev")
    os.environ["REPORT_DIR"] = tmp_reports
    os.makedirs(tmp_reports, exist_ok=True)
    open(os.path.join(tmp_reports, "review_20260710.md"), "w", encoding="utf-8").write(
        "# AI 信号复核报告 20260710\n\n信号引擎=alloc_p1,非月度首个交易日,今日无再平衡信号。\n\n"
        "## 对账:实际成交 vs 决策价\n- 账户总资产快照:1,021,300 元 (较上一快照 +0.21%)\n")
    open(os.path.join(tmp_reports, "latest.html"), "w", encoding="utf-8").write("<h1>回测汇总(夹具)</h1>")

    import importlib.util
    spec = importlib.util.spec_from_file_location("dashboard", os.path.join(ROOT, "dashboard.py"))
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)

    db = build_db()
    dash._mongo = lambda: db
    dash._broker = lambda: FakeBroker()
    dash.REPORT_DIR = tmp_reports

    # 假 market_data:离线生成行情(测 add/remove/refresh 全链路,不打真实数据源)
    md = types.ModuleType("market_data")
    md.WATCH_COL = "user_watchlist"

    def add_symbol(_db, code):
        code = str(code).strip()
        if not (len(code.split(".")[0]) == 6 and code.split(".")[0].isdigit()):
            return {"ok": False, "error": f"代码不合法:{code}"}
        sym = code if "." in code else code + (".SH" if code[0] in "569" else ".SZ")
        dates = _dates(120)
        closes = _walk(hash(sym) % 97, 120, 10.0, 0.0004, 0.015)
        for d, c in zip(dates, closes):
            _db["stock_market"].update_one({"symbol": sym, "date": d},
                {"$set": {"symbol": sym, "date": d, "close": c, "open": c, "high": c,
                          "low": c, "volume": 1e6, "turnover": 2e8}}, upsert=True)
        _db["stock_info_new"].update_one({"symbol": sym},
            {"$set": {"symbol": sym, "name": "新增测试标的", "type": 0}}, upsert=True)
        _db["user_watchlist"].update_one({"symbol": sym},
            {"$set": {"symbol": sym, "name": "新增测试标的", "asset_type": "stock",
                      "source": "manual", "added_at": "2026-07-11 09:00"}}, upsert=True)
        return {"ok": True, "symbol": sym, "name": "新增测试标的", "bars": 120, "note": "全量拉取 120 条(夹具)"}

    def remove_symbol(_db, sym, protected=()):
        _db["user_watchlist"].delete_one({"symbol": sym})
        if sym in protected:
            return {"ok": True, "symbol": sym, "note": "已移出自选;此标的被现役引擎使用,行情保留"}
        n = _db["stock_market"].delete_many({"symbol": sym}).deleted_count
        _db["stock_info_new"].delete_one({"symbol": sym})
        return {"ok": True, "symbol": sym, "note": f"已移除(连同 {n} 条行情)"}

    def incremental_update(_db, sym, etf=None, today=None):
        import time as _t
        _t.sleep(0.02)
        return 1, "增量 1 天(夹具)"

    md.add_symbol, md.remove_symbol, md.incremental_update = add_symbol, remove_symbol, incremental_update
    dash._market_data = lambda: md

    # Routine 定时任务夹具(内存态,支持开关/改时间/立即运行全链路)
    jobs = [
        {"id": "refresh", "name": "行情增量更新", "task": "refresh", "time": "15:20",
         "trading_days_only": True, "enabled": True, "last_run": "2026-07-10 15:20:03",
         "last_status": "ok", "last_duration_s": 42.5, "last_log": "15:20:45 完成:13只,新增13条,失败0只"},
        {"id": "review", "name": "AI复核官(月初再平衡/每日快照)", "task": "review", "time": "15:40",
         "trading_days_only": True, "enabled": False, "last_run": "", "last_status": "", "last_log": ""},
        {"id": "backup", "name": "轻量备份(决策/权益/对账→/reports/backups)", "task": "backup_lite",
         "time": "20:00", "trading_days_only": False, "enabled": True,
         "last_run": "2026-07-10 20:00:01", "last_status": "ok", "last_duration_s": 0.4,
         "last_log": "20:00:01 已备份 512 条记录 -> /reports/backups/core_20260710_2000.json.gz"}]
    dash._DEV_JOBS = jobs

    def jobs_list():
        return {"ok": True, "rows": jobs, "scheduler_on": True}

    def job_update(jid, enabled=None, time_str=None, tdo=None):
        for j in jobs:
            if j["id"] == jid:
                if enabled is not None:
                    j["enabled"] = enabled in (True, "1", "true")
                if time_str:
                    j["time"] = time_str
        return {"ok": True}

    def job_run_now(jid):
        for j in jobs:
            if j["id"] == jid:
                j["last_status"] = "ok"
                j["last_run"] = "2026-07-11 09:00:00"
                j["last_duration_s"] = 1.2
                j["last_log"] = "09:00:01 (夹具)立即运行完成"
        return {"ok": True, "id": jid, "note": "已触发"}

    def job_log(jid):
        for j in jobs:
            if j["id"] == jid:
                return {"ok": True, "id": jid, "log": j.get("last_log", ""), "status": j.get("last_status", "")}
        return {"ok": False, "error": "不存在"}

    dash._dev_jobs_api = (jobs_list, job_update, job_run_now, job_log)

    # 资讯:预填缓存,离线可用
    now = __import__("time").time()
    for sym, name in list(NAMES.items())[:6]:
        dash._NEWS_CACHE[sym] = (now + 9999, [
            {"symbol": sym, "time": "2026-07-10 09:0%d" % (hash(sym) % 10),
             "title": f"{name}:成交额环比放大,资金面观察", "source": "东财", "url": ""}])
    return dash, db


def serve(port=18200):
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs

    tmp = os.path.join(HERE, "_fixture_reports")
    dash, db = patched_dashboard(tmp)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/dash":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(dash.DASH_HTML.encode())
            elif u.path == "/dash/data":
                self._json(dash.collect())
            elif u.path == "/dash/screen":
                self._json(dash.screen())
            elif u.path == "/dash/detail":
                self._json(dash.detail(q.get("symbol", "")))
            elif u.path == "/dash/news":
                self._json(dash.news(q.get("symbol", "all")))
            elif u.path == "/dash/watchlist":
                self._json(dash.watchlist())
            elif u.path == "/dash/refresh/status":
                self._json(dash.refresh_status())
            elif u.path == "/dash/jobs":
                self._json(dash._dev_jobs_api[0]())
            elif u.path == "/dash/jobs/log":
                self._json(dash._dev_jobs_api[3](q.get("id", "")))
            elif u.path.startswith("/reports/"):
                p = os.path.join(tmp, os.path.basename(u.path))
                if os.path.exists(p):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(open(p, "rb").read())
                else:
                    self.send_response(404); self.end_headers()
            else:
                self.send_response(404); self.end_headers()

        def do_POST(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/dash/watchlist/add":
                self._json(dash.watchlist_add(q.get("code", "")))
            elif u.path == "/dash/watchlist/remove":
                self._json(dash.watchlist_remove(q.get("symbol", "")))
            elif u.path == "/dash/refresh":
                self._json(dash.refresh_start())
            elif u.path == "/dash/jobs/update":
                self._json(dash._dev_jobs_api[1](q.get("id", ""), q.get("enabled"), q.get("time")))
            elif u.path == "/dash/jobs/run":
                self._json(dash._dev_jobs_api[2](q.get("id", "")))
            else:
                self.send_response(404); self.end_headers()

    srv = HTTPServer(("127.0.0.1", port), H)
    print(f"dash dev server -> http://127.0.0.1:{port}/dash")
    return srv


if __name__ == "__main__":
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 18200
    serve(port).serve_forever()
