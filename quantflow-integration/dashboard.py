# -*- coding: utf-8 -*-
"""
统一量化工作台(只读)v3 —— 一个地址、一套导航看全部:http://127.0.0.1:8000/dash

七个标签(信息架构参考成熟终端的交易/行情/研究/资讯分区):
  总览        KPI卡(可点击跳转)+ 权益曲线 + 今日报告
  实盘        华泰账户/持仓盈亏/挂单/成交/滑点对账 —— 真金白银一屏管住
  行情·选股   全库标的筛选器:涨跌/波动/均线位置/回撤/成交额,可排序过滤,
             点行展开250日大图(价格+MA20/60/120)
  资讯        东财个股新闻按标的分类,服务端缓存10分钟
  决策·对账   AI决策记录(含复盘)/成交滑点/沉淀经验
  报告        回测HTML报告与每日决策报告列表
  系统        引擎状态(现役引擎/权重/实盘开关)/数据库概况/命令速查
全部只读:不放任何会下单、撤单、改数据的接口。真实操作仍走命令行。

由 run_local_secure.py 挂到平台 app 上(不改平台源码):
  /dash 页面 · /dash/data 总览 · /dash/screen 选股 · /dash/detail 个股详情
  · /dash/news 资讯 · /dash/quotes(旧,保留兼容)
"""
import os
import glob
import time
import datetime

REPORT_DIR = os.getenv("REPORT_DIR", "/reports")


def _mongo():
    import pymongo
    import urllib.parse
    host = os.getenv("MONGO_URI", "mongo:27017")
    user, pwd = os.getenv("MONGO_USER", ""), os.getenv("MONGO_PASSWORD", "")
    authdb = os.getenv("MONGO_AUTH_DB", "admin")
    auth = f"{user}:{urllib.parse.quote_plus(pwd)}@" if user else ""
    cli = pymongo.MongoClient(f"mongodb://{auth}{host}/{authdb}", serverSelectionTimeoutMS=4000)
    return cli[os.getenv("MONGO_DB", "panda")]


def _broker():
    """从可能的位置导入自研华泰客户端(只读调用)。"""
    import sys
    for p in ("/app/panda_quantflow/src",
              "/app/panda_quantflow/src/panda_plugins/custom",
              os.path.dirname(os.path.abspath(__file__))):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    from htsc_broker import HTSCBroker
    return HTSCBroker()


def collect():
    """总览数据,任何一块失败都不影响其它块。"""
    out = {"ok": True, "ledger": [], "huatai": {}, "report": "", "reports": [],
           "reports_html": [], "equity": [], "recon": [], "lessons": [], "errors": {}}

    try:
        db = _mongo()
        out["ledger"] = list(db["ai_review_positions"].find({}, {"_id": 0}))
        out["decisions_recent"] = list(
            db["ai_review_decisions"].find({}, {"_id": 0, "context": 0}).sort("date", -1).limit(30))
        out["equity"] = list(db["ai_review_equity"].find({}, {"_id": 0}).sort("date", 1).limit(180))
        out["recon"] = list(db["ai_review_recon"].find({}, {"_id": 0}).sort("date", -1).limit(50))
        for doc in db["ai_review_lessons"].find({}, {"_id": 0}).sort("date", -1).limit(5):
            out["lessons"] += doc.get("lessons", [])
        last_bar = db["stock_market"].find_one({}, {"_id": 0, "date": 1}, sort=[("date", -1)])
        out["asof"] = last_bar["date"] if last_bar else ""
    except Exception as e:
        out["errors"]["ledger"] = f"{type(e).__name__}: {e}"

    if os.getenv("HT_APIKEY"):
        try:
            b = _broker()
            out["huatai"] = {
                "pending": b.pending_orders(),
                "positions": b.positions(),
                "account": b.account_balance(),
                "trades": b.trade_history(),
            }
        except Exception as e:
            out["errors"]["huatai"] = f"{type(e).__name__}: {e}"
    else:
        out["errors"]["huatai"] = "quantflow 容器未配 HT_APIKEY(只影响看板显示,不影响下单)"

    try:
        mds = sorted(glob.glob(os.path.join(REPORT_DIR, "review_*.md")), reverse=True)
        out["reports"] = [os.path.basename(m) for m in mds[:30]]
        if mds:
            out["report"] = open(mds[0], encoding="utf-8").read()
            out["report_name"] = os.path.basename(mds[0])
        htmls = sorted(glob.glob(os.path.join(REPORT_DIR, "*.html")), reverse=True)
        out["reports_html"] = [os.path.basename(h) for h in htmls[:30]]
    except Exception as e:
        out["errors"]["report"] = f"{type(e).__name__}: {e}"

    # 系统状态(引擎配置来自环境变量;数据库概况来自 Mongo)
    weights = []
    for part in os.getenv("ALLOC_WEIGHTS",
                          "510300.SH:0.40,511260.SH:0.40,518880.SH:0.20").split(","):
        try:
            s, w = part.rsplit(":", 1)
            weights.append({"symbol": s.strip(), "weight": float(w)})
        except ValueError:
            continue
    out["sys"] = {
        "engine": os.getenv("SIGNAL_ENGINE", "alloc_p1"),
        "weights": weights,
        "paper_total": os.getenv("PAPER_TOTAL", "1000000"),
        "htsc_live": os.getenv("HTSC_LIVE", "0") == "1",
        "provider": os.getenv("PROVIDER", "deepseek"),
        "full_auth": os.getenv("FULL_AUTH", "0") == "1",
        "has_ht_key": bool(os.getenv("HT_APIKEY")),
    }
    try:
        db = _mongo()
        first = db["stock_market"].find_one({}, {"_id": 0, "date": 1}, sort=[("date", 1)])
        out["sys"]["db"] = {
            "symbols": len(db["stock_market"].distinct("symbol")),
            "bars": db["stock_market"].estimated_document_count(),
            "first_date": first["date"] if first else "",
            "last_date": out.get("asof", ""),
            "decisions": db["ai_review_decisions"].estimated_document_count(),
            "equity_days": db["ai_review_equity"].estimated_document_count(),
            "recon_rows": db["ai_review_recon"].estimated_document_count(),
        }
    except Exception as e:
        out["errors"]["sysdb"] = f"{type(e).__name__}: {e}"

    return out


def _is_etf(sym):
    b = sym.split(".")[0]
    return b.startswith("5") or b[:2] in ("15", "16")


def screen():
    """选股器:全库标的的关键指标(涨跌/波动/均线位置/回撤/成交额)+迷你走势。"""
    out = {"asof": "", "rows": [], "error": ""}
    try:
        db = _mongo()
        names = {d["symbol"]: d.get("name", "") for d in
                 db["stock_info_new"].find({"type": {"$ne": 1}}, {"_id": 0, "symbol": 1, "name": 1})}
        for sym in db["stock_market"].distinct("symbol"):
            docs = list(db["stock_market"].find(
                {"symbol": sym}, {"_id": 0, "date": 1, "close": 1, "turnover": 1})
                .sort("date", -1).limit(130))
            if len(docs) < 21:
                continue
            docs.reverse()
            h = [float(d["close"]) for d in docs]
            last = h[-1]

            def ret(n):
                return round((last / h[-n - 1] - 1) * 100, 2) if len(h) > n and h[-n - 1] else None

            def ma_dist(n):
                if len(h) < n:
                    return None
                ma = sum(h[-n:]) / n
                return round((last / ma - 1) * 100, 2) if ma else None

            vol60 = None
            if len(h) >= 61:
                import math
                rets = [(h[i] / h[i - 1] - 1) for i in range(len(h) - 60, len(h)) if h[i - 1]]
                if rets:
                    mean = sum(rets) / len(rets)
                    vol60 = round(math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
                                  * math.sqrt(244) * 100, 1)
            hi60 = max(h[-60:]) if len(h) >= 60 else max(h)
            tv20 = [float(d.get("turnover", 0) or 0) for d in docs[-20:]]
            out["rows"].append({
                "symbol": sym, "name": names.get(sym, ""),
                "type": "ETF" if _is_etf(sym) else "股票",
                "price": last, "r1": ret(1), "r5": ret(5), "r20": ret(20), "r60": ret(60),
                "vol60": vol60, "ma20": ma_dist(20), "ma60": ma_dist(60), "ma120": ma_dist(120),
                "dd60": round((last / hi60 - 1) * 100, 2) if hi60 else None,
                "tv20_yi": round(sum(tv20) / max(len(tv20), 1) / 1e8, 2),
                "spark": h[-60:], "date": docs[-1]["date"],
            })
            out["asof"] = max(out["asof"], docs[-1]["date"])
        out["rows"].sort(key=lambda r: r["r20"] if r["r20"] is not None else -999, reverse=True)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def detail(symbol):
    """个股详情:250日收盘 + MA20/60/120(对齐日期,不足处为 None)。"""
    out = {"symbol": symbol, "name": "", "dates": [], "closes": [],
           "ma20": [], "ma60": [], "ma120": [], "error": ""}
    try:
        db = _mongo()
        info = db["stock_info_new"].find_one({"symbol": symbol}, {"_id": 0, "name": 1})
        out["name"] = (info or {}).get("name", "")
        docs = list(db["stock_market"].find(
            {"symbol": symbol}, {"_id": 0, "date": 1, "close": 1})
            .sort("date", -1).limit(250))
        docs.reverse()
        h = [float(d["close"]) for d in docs]
        out["dates"] = [d["date"] for d in docs]
        out["closes"] = h
        for n, key in ((20, "ma20"), (60, "ma60"), (120, "ma120")):
            out[key] = [round(sum(h[i - n + 1:i + 1]) / n, 4) if i >= n - 1 else None
                        for i in range(len(h))]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def quotes():
    """自选行情:对库里每只标的取最近 60 根日线 → 现价/涨跌/迷你走势。"""
    out = {"asof": "", "rows": [], "error": ""}
    try:
        db = _mongo()
        names = {d["symbol"]: d.get("name", "") for d in
                 db["stock_info_new"].find({"type": {"$ne": 1}}, {"_id": 0, "symbol": 1, "name": 1})}
        symbols = db["stock_market"].distinct("symbol")
        for sym in symbols:
            docs = list(db["stock_market"].find(
                {"symbol": sym}, {"_id": 0, "date": 1, "close": 1, "turnover": 1})
                .sort("date", -1).limit(60))
            if len(docs) < 2:
                continue
            docs.reverse()
            closes = [float(d["close"]) for d in docs]
            last, prev = closes[-1], closes[-2]
            def pct(n):
                return round((last / closes[-n - 1] - 1) * 100, 2) if len(closes) > n else None
            out["rows"].append({
                "symbol": sym, "name": names.get(sym, ""),
                "price": last, "chg1": round((last / prev - 1) * 100, 2),
                "chg5": pct(5), "chg20": pct(20),
                "turnover_yi": round(float(docs[-1].get("turnover", 0) or 0) / 1e8, 2),
                "spark": closes, "date": docs[-1]["date"],
            })
            out["asof"] = max(out["asof"], docs[-1]["date"])
        out["rows"].sort(key=lambda r: r["chg1"], reverse=True)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


_NEWS_CACHE = {}          # sym -> (ts, rows)
_NEWS_TTL = 600           # 服务端缓存 10 分钟,别把东财打成风控


def news(symbol="all"):
    """资讯:akshare 个股新闻(stock_news_em),按自选标的抓取并缓存。"""
    out = {"rows": [], "cached_at": "", "error": ""}
    try:
        db = _mongo()
        symbols = db["stock_market"].distinct("symbol")
    except Exception as e:
        out["error"] = f"Mongo: {e}"
        return out
    targets = symbols if symbol in ("", "all") else [symbol]
    targets = targets[:20]
    now = time.time()
    rows = []
    err = ""
    for sym in targets:
        bare = sym.split(".")[0]
        hit = _NEWS_CACHE.get(sym)
        if hit and now - hit[0] < _NEWS_TTL:
            rows += hit[1]
            continue
        try:
            import akshare as ak
            df = ak.stock_news_em(symbol=bare)
            got = []
            for _, r in df.head(12).iterrows():
                got.append({"symbol": sym,
                            "time": str(r.get("发布时间", "")),
                            "title": str(r.get("新闻标题", "")),
                            "source": str(r.get("文章来源", "")),
                            "url": str(r.get("新闻链接", ""))})
            _NEWS_CACHE[sym] = (now, got)
            rows += got
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            _NEWS_CACHE[sym] = (now, [])   # 失败也缓存,避免每次刷新都撞墙
    rows.sort(key=lambda r: r["time"], reverse=True)
    out["rows"] = rows[:80]
    out["cached_at"] = datetime.datetime.now().strftime("%H:%M:%S")
    if err and not rows:
        out["error"] = err
    return out



DASH_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>量化工作台</title><style>
:root{--bg:#0f1420;--card:#141b2c;--line:#232b3d;--fg:#dde3ee;--mut:#8892a6;--acc:#58a6ff;
      --up:#ff6b6b;--dn:#4fd18b}
*{box-sizing:border-box}
body{font-family:system-ui,'Microsoft YaHei',sans-serif;background:var(--bg);color:var(--fg);margin:0}
nav{position:sticky;top:0;z-index:9;display:flex;align-items:center;gap:2px;
    background:#101725;border-bottom:1px solid var(--line);padding:0 16px;flex-wrap:wrap}
nav .brand{font-weight:700;font-size:15px;margin-right:14px;padding:12px 0;color:#cfe0ff}
nav a.tab{color:var(--mut);text-decoration:none;padding:12px 12px;font-size:14px;border-bottom:2px solid transparent}
nav a.tab.on{color:#fff;border-bottom-color:var(--acc)}
nav .right{margin-left:auto;display:flex;gap:12px;align-items:center}
nav .right a{color:var(--acc);font-size:13px;text-decoration:none}
main{max-width:1280px;margin:18px auto;padding:0 16px}
section{display:none}section.on{display:block}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow:auto}
.card.full{grid-column:1/-1}
.card.link{cursor:pointer}.card.link:hover{border-color:#3d5c8c}
h2{font-size:13px;color:#9daecd;margin:0 0 10px;font-weight:600}
.kpi{font-size:22px;font-weight:700}.kpi small{font-size:12px;color:var(--mut);font-weight:400;display:block;margin-top:4px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:#8fa0bd;font-weight:500}td.l,th.l{text-align:left}
th.sort{cursor:pointer}th.sort:hover{color:#fff}th.sort.on{color:var(--acc)}
tr.click{cursor:pointer}tr.click:hover td{background:#182238}
.up{color:var(--up)}.dn{color:var(--dn)}.pend{color:#f6c343}
pre{white-space:pre-wrap;font-size:13px;line-height:1.55;color:#c6d0e2;margin:0}
a{color:var(--acc)}.err{color:#ff7b72;font-size:12px;margin-top:6px}
.sub{color:var(--mut);font-size:12px}
.pill{background:#1c2740;border-radius:6px;padding:2px 8px;font-size:12px;color:#9db2d8}
.pill.live{background:#5a1e1e;color:#ffb3b3}.pill.dry{background:#1e3a26;color:#8fe0ac}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;align-items:center}
.chip{background:#1c2740;border:1px solid var(--line);border-radius:14px;padding:3px 11px;
      font-size:12px;color:#9db2d8;cursor:pointer;user-select:none}
.chip.on{background:#274068;color:#fff;border-color:#3d5c8c}
.newsitem{padding:9px 2px;border-bottom:1px solid var(--line);font-size:13px}
.newsitem .t{color:var(--mut);font-size:12px;margin-right:8px}
.newsitem .tag{background:#1c2740;border-radius:4px;padding:1px 6px;font-size:11px;color:#9db2d8;margin-right:8px}
input[type=text]{background:#0d1320;border:1px solid var(--line);border-radius:6px;color:var(--fg);
      padding:5px 10px;font-size:13px;width:160px}
code,kbd{background:#0d1320;border:1px solid var(--line);border-radius:4px;padding:1px 6px;font-size:12px}
.cmd{background:#0d1320;border:1px solid var(--line);border-radius:8px;padding:10px 12px;
     font-family:Consolas,monospace;font-size:12.5px;line-height:1.7;color:#a8c7fa;white-space:pre;overflow-x:auto}
.legend{display:inline-block;width:18px;height:3px;border-radius:2px;vertical-align:middle;margin:0 4px 0 10px}
@media(max-width:900px){.grid{grid-template-columns:1fr}.grid4{grid-template-columns:1fr 1fr}}
</style></head><body>
<nav>
  <span class=brand>📊 量化工作台 <span class=pill>只读</span></span>
  <a class="tab on" data-t=overview href=#>总览</a>
  <a class=tab data-t=live href=#>实盘</a>
  <a class=tab data-t=screen href=#>行情·选股</a>
  <a class=tab data-t=news href=#>资讯</a>
  <a class=tab data-t=ops href=#>决策·对账</a>
  <a class=tab data-t=reports href=#>报告</a>
  <a class=tab data-t=system href=#>系统</a>
  <span class=right>
    <a href="/reports/latest.html" target=_blank>回测汇总</a>
    <a href="/quantflow/" target=_blank>QuantFlow ↗</a>
    <span id=ts class=sub></span>
  </span>
</nav>
<main>

<section id=overview class=on>
  <div class=grid4>
    <div class="card link" data-go=live><h2>华泰总资产 →实盘</h2><div class=kpi id=k_asset>—<small id=k_asset_chg></small></div></div>
    <div class="card link" data-go=live><h2>华泰持仓 / 挂单</h2><div class=kpi id=k_pos>—</div></div>
    <div class="card link" data-go=ops><h2>纸面账本持仓 →决策</h2><div class=kpi id=k_ledger>—</div></div>
    <div class="card link" data-go=system><h2>行情数据截止 →系统</h2><div class=kpi id=k_asof>—</div></div>
  </div>
  <div class=grid>
    <div class=card full><h2>权益曲线(每日快照,唯一无偏的成绩单)</h2><div id=equity>暂无快照(跑一次 reviewer 生成)</div></div>
    <div class=card full><h2>今日决策报告 <span id=rname class=pill></span></h2><pre id=report></pre></div>
  </div>
</section>

<section id=live>
  <div class=grid4>
    <div class=card><h2>总资产</h2><div class=kpi id=l_total>—</div></div>
    <div class=card><h2>可用资金</h2><div class=kpi id=l_cash>—</div></div>
    <div class=card><h2>持仓市值</h2><div class=kpi id=l_mv>—</div></div>
    <div class=card><h2>模式</h2><div class=kpi id=l_mode>—</div></div>
  </div>
  <div class=grid>
    <div class=card><h2>持仓(华泰)</h2><div id=l_positions>加载中…</div></div>
    <div class=card><h2>挂单</h2><div id=l_pending></div></div>
    <div class=card><h2>近期成交</h2><div id=l_trades></div></div>
    <div class=card><h2>滑点对账 <span class=sub>正=吃亏</span></h2><div id=l_recon></div></div>
    <div class=card full><h2>纸面账本(策略应有持仓,与上面实盘对照)</h2><div id=l_ledger></div></div>
  </div>
</section>

<section id=screen>
  <div class=card full>
    <h2>行情·选股 <span class=sub>(库内 <span id=s_count>—</span> 只 · 数据截止 <span id=s_asof>—</span> · 点列头排序,点行看大图)</span></h2>
    <div class=chips id=s_chips>
      <span class="chip on" data-f=all>全部</span>
      <span class=chip data-f=ETF>只看ETF</span>
      <span class=chip data-f=股票>只看股票</span>
      <span class=chip data-f=ma120>站上MA120</span>
      <span class=chip data-f=lowvol>低波动(年化<25%)</span>
      <input type=text id=s_q placeholder="代码/名称过滤">
    </div>
    <div id=s_detail style="display:none;margin-bottom:14px"></div>
    <div id=s_table>加载中…</div>
  </div>
</section>

<section id=news>
  <div class=card full>
    <h2>资讯 <span class=sub>(东财个股新闻,10分钟缓存 · <span id=n_cached></span>)</span></h2>
    <div class=chips id=n_chips></div>
    <div id=n_list>加载中…</div>
  </div>
</section>

<section id=ops>
  <div class=grid>
    <div class=card full><h2>决策记录(最近30条)</h2><div id=o_dec></div></div>
    <div class=card><h2>对账:实际成交 vs 决策价 <span class=sub>正滑点=吃亏</span></h2><div id=o_recon></div></div>
    <div class=card><h2>沉淀的经验</h2><div id=o_lessons></div></div>
  </div>
</section>

<section id=reports>
  <div class=grid>
    <div class=card><h2>回测可视化报告(HTML)</h2><div id=r_html></div></div>
    <div class=card><h2>每日决策报告(Markdown)</h2><div id=r_md></div></div>
  </div>
</section>

<section id=system>
  <div class=grid>
    <div class=card><h2>现役引擎</h2><div id=y_engine></div></div>
    <div class=card><h2>数据库概况</h2><div id=y_db></div></div>
    <div class=card full><h2>日常命令速查 <span class=sub>(cmd 窗口,在 docker/ 目录下)</span></h2>
      <div class=cmd>:: ① 更新全部行情(收盘后;幂等,只补新交易日)
set LOAD_ALL=1
docker compose run --rm loader

:: ② 跑复核官(月初自动出再平衡信号;平时=快照权益+复盘)
docker compose run --rm reviewer

:: 改了 .py 代码才需要(改名单/.env 不需要):
docker compose build loader reviewer quantflow && docker compose up -d quantflow</div>
      <div class=sub style=margin-top:8px>方法论:任何新策略必须过「无前视写法 → 训练/测试分窗 → 两窗胜过P1」才可上岗。
证据档案见仓库 A-SHARE-STRATEGY-RESEARCH.md / BACKTEST-AUDIT.md。</div>
    </div>
  </div>
</section>

</main>
<script>
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function dig(o,ks){for(const k of ks){o=o&&o[k]}return o}
function num(v){return (v===null||v===undefined||v==='')?null:Number(v)}
function pc(v){v=num(v);if(v===null)return '—';
  const c=v>0?'up':(v<0?'dn':'');return `<span class=${c}>${v>0?'+':''}${v.toFixed(2)}%</span>`}
function money(v){v=num(v);return v===null?'—':v.toLocaleString()}
function spark(a,w=110,h=26){
  if(!a||a.length<2)return '';
  const mn=Math.min(...a),mx=Math.max(...a),sp=mx-mn||1;
  const pts=a.map((v,i)=>`${(i/(a.length-1)*w).toFixed(1)},${(h-2-(v-mn)/sp*(h-4)).toFixed(1)}`).join(' ');
  const up=a[a.length-1]>=a[0];
  return `<svg width=${w} height=${h}><polyline points="${pts}" fill=none stroke="${up?'#ff6b6b':'#4fd18b'}" stroke-width=1.4 /></svg>`}
function line(a,w,h){
  if(!a||a.length<2)return '<div class=sub>快照不足2个,再跑几天 reviewer 就有曲线了</div>';
  const vs=a.map(x=>x.total_asset),mn=Math.min(...vs),mx=Math.max(...vs),sp=mx-mn||1;
  const pts=vs.map((v,i)=>`${(i/(vs.length-1)*(w-8)+4).toFixed(1)},${(h-16-(v-mn)/sp*(h-30)).toFixed(1)}`).join(' ');
  const last=vs[vs.length-1],chg=(last/vs[0]-1)*100;
  return `<div class=sub>${a[0].date} → ${a[a.length-1].date} · ${last.toLocaleString()} 元 (${chg>=0?'+':''}${chg.toFixed(2)}%)</div>
   <svg width="100%" height=${h} viewBox="0 0 ${w} ${h}" preserveAspectRatio=none>
   <polyline points="${pts}" fill=none stroke="#58a6ff" stroke-width=1.6 /></svg>`}
function ordersTable(list){
  if(!list||!list.length)return '<div class=sub>无</div>';
  let h='<table><tr><th class=l>标的</th><th>方向</th><th>价</th><th>数量</th><th>已成</th><th>状态</th></tr>';
  for(const o of list){const d=(o.direction||'').includes('buy');
    h+=`<tr><td class=l>${esc(o.stockName||'')} ${esc(o.stockCode||'')}.${esc(o.exchange||'')}</td>`+
       `<td class=${d?'up':'dn'}>${d?'买':'卖'}</td><td>${esc(o.price)}</td>`+
       `<td>${esc(o.quantity)}</td><td>${esc(o.filledQuantity)}</td><td class=pend>${esc(o.status)}</td></tr>`}
  return h+'</table>'}
function posTable(list){
  if(!list||!list.length)return '<div class=sub>空仓</div>';
  let h='<table><tr><th class=l>标的</th><th>数量</th><th>成本</th><th>市值</th><th>盈亏</th></tr>';
  for(const p of list){const pnl=num(p.profit??p.floatProfit)??0;
    h+=`<tr><td class=l>${esc(p.stockName||'')} ${esc(p.stockCode||'')}</td><td>${esc(p.quantity??p.volume??'')}</td>`+
       `<td>${esc(p.costPrice??p.avgPrice??'—')}</td><td>${esc(p.marketValue||'')}</td>`+
       `<td class=${pnl>=0?'up':'dn'}>${esc(pnl)}</td></tr>`}
  return h+'</table>'}
function reconTable(list){
  const rec=(list||[]).map(x=>`<tr><td class=l>${esc(x.date)}</td><td class=l>${esc(x.symbol)}</td>`+
    `<td class=l>${esc(x.side)}</td><td>${esc(x.decision_price??'—')}</td><td>${esc(x.fill_price)}</td>`+
    `<td>${x.slippage_pct!==undefined?pc(x.slippage_pct):'—'}</td></tr>`).join('');
  return rec?`<table><tr><th class=l>日期</th><th class=l>标的</th><th class=l>方向</th><th>决策价</th><th>成交价</th><th>滑点</th></tr>${rec}</table>`
    :'<div class=sub>暂无成交对账</div>'}
function ledgerTable(list){
  const led=(list||[]).map(p=>`<tr><td class=l>${esc(p.symbol)}</td><td>${esc(p.qty??'—')}</td>`+
    `<td>${esc(p.entry_price)}</td><td>${p.weight?(p.weight*100).toFixed(0)+'%':''}</td>`+
    `<td>${esc(p.entry_date)}</td><td>${p.half?'半仓':''}${p.by_agent?' 🤖':''}${p.alloc?' 配置':''}</td></tr>`).join('');
  return led?`<table><tr><th class=l>标的</th><th>股数</th><th>成本参考价</th><th>目标权重</th><th>最近调整</th><th></th></tr>${led}</table>`
    :'<div class=sub>空仓(月初再平衡后入账)</div>'}

let D=null;
async function loadOverview(){
  document.getElementById('ts').textContent='加载中…';
  const r=await fetch('/dash/data');D=await r.json();const d=D;
  const err=k=>d.errors&&d.errors[k]?`<div class=err>${esc(d.errors[k])}</div>`:'';
  const acc=dig(d,['huatai','account','data'])||{};
  const asset=acc.totalAsset||acc.netAsset||null;
  document.getElementById('k_asset').firstChild.textContent=asset?Number(asset).toLocaleString():'—';
  const eq=d.equity||[];
  if(eq.length>1){const c=(eq[eq.length-1].total_asset/eq[0].total_asset-1)*100;
    document.getElementById('k_asset_chg').innerHTML='快照累计 '+pc(c)}
  const hp=dig(d,['huatai','positions','data','positions'])||[];
  const po=dig(d,['huatai','pending','data','orders'])||[];
  document.getElementById('k_pos').textContent=hp.length+' / '+po.length;
  document.getElementById('k_ledger').textContent=(d.ledger||[]).length+' 只';
  document.getElementById('k_asof').textContent=d.asof||'—';
  document.getElementById('equity').innerHTML=line(eq,900,190);
  document.getElementById('report').textContent=d.report||'(还没有决策报告 —— 跑一次 reviewer 就有了)';
  document.getElementById('rname').textContent=d.report_name||'';
  // 实盘页
  document.getElementById('l_total').textContent=money(acc.totalAsset||acc.netAsset);
  document.getElementById('l_cash').textContent=money(acc.cash||acc.availableCash);
  document.getElementById('l_mv').textContent=money(acc.marketValue||acc.positionValue);
  const sys=d.sys||{};
  document.getElementById('l_mode').innerHTML=sys.htsc_live?'<span class="pill live">实盘模拟·真下单</span>':'<span class="pill dry">干跑·不下单</span>';
  document.getElementById('l_positions').innerHTML=posTable(hp)+err('huatai');
  document.getElementById('l_pending').innerHTML=ordersTable(po);
  const tr=dig(d,['huatai','trades','data','trades'])||dig(d,['huatai','trades','data','list'])||[];
  document.getElementById('l_trades').innerHTML=ordersTable(tr);
  document.getElementById('l_recon').innerHTML=reconTable((d.recon||[]).slice(0,10));
  document.getElementById('l_ledger').innerHTML=ledgerTable(d.ledger);
  renderOps(d);renderReports(d);renderSystem(d);
  document.getElementById('ts').textContent='更新 '+new Date().toLocaleTimeString();
}
function renderOps(d){
  const dec=(d.decisions_recent||[]).map(x=>{
    const out=x.evaluated?pc(x.outcome_pct)+' '+esc(x.verdict||''):'<span class=sub>待评估</span>';
    return `<tr><td class=l>${esc(x.date)}</td><td class=l>${esc(x.symbol)}</td><td class=l>${esc(x.side)}</td>`+
      `<td>${esc(x.price)}</td><td class=l><b>${esc(x.decision)}</b></td>`+
      `<td class=l style="white-space:normal;max-width:280px">${esc((x.reason||'').slice(0,80))}</td><td class=l>${out}</td></tr>`}).join('');
  document.getElementById('o_dec').innerHTML=dec?
    `<table><tr><th class=l>日期</th><th class=l>标的</th><th class=l>信号</th><th>价</th><th class=l>决定</th><th class=l>理由</th><th class=l>结果</th></tr>${dec}</table>`
    :'<div class=sub>暂无决策记录</div>';
  document.getElementById('o_recon').innerHTML=reconTable(d.recon);
  document.getElementById('o_lessons').innerHTML=(d.lessons||[]).length?
    '<ul style="margin:0;padding-left:18px;line-height:1.7">'+(d.lessons||[]).map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>'
    :'<div class=sub>暂无</div>';
}
function renderReports(d){
  document.getElementById('r_html').innerHTML=(d.reports_html||[]).map(x=>
    `<div class=newsitem><a href="/reports/${esc(x)}" target=_blank>${esc(x)}</a></div>`).join('')||'<div class=sub>暂无</div>';
  document.getElementById('r_md').innerHTML=(d.reports||[]).map(x=>
    `<div class=newsitem><a href="/reports/${esc(x)}" target=_blank>${esc(x)}</a></div>`).join('')||'<div class=sub>暂无</div>';
}
function renderSystem(d){
  const s=d.sys||{},db=s.db||{};
  const w=(s.weights||[]).map(x=>`<tr><td class=l>${esc(x.symbol)}</td><td>${(x.weight*100).toFixed(0)}%</td></tr>`).join('');
  document.getElementById('y_engine').innerHTML=
    `<div style=margin-bottom:8px><b>${esc(s.engine||'—')}</b> `+
    (s.htsc_live?'<span class="pill live">HTSC_LIVE=1 真下单</span>':'<span class="pill dry">干跑</span>')+
    ` <span class=pill>复核:${esc(s.provider||'')}</span>`+
    (s.full_auth?' <span class=pill>AI全权</span>':' <span class=pill>AI只审偏离</span>')+`</div>`+
    (w?`<table><tr><th class=l>目标权重</th><th></th></tr>${w}</table>`:'')+
    `<div class=sub style=margin-top:6px>纸面名义资金 ${Number(s.paper_total||0).toLocaleString()} 元 · `+
    (s.has_ht_key?'华泰只读已连':'华泰未配APIKEY')+`</div>`;
  document.getElementById('y_db').innerHTML=
    `<table><tr><td class=l>标的数</td><td>${db.symbols??'—'}</td></tr>`+
    `<tr><td class=l>日线条数</td><td>${(db.bars??0).toLocaleString()}</td></tr>`+
    `<tr><td class=l>数据区间</td><td>${esc(db.first_date||'—')} ~ ${esc(db.last_date||'—')}</td></tr>`+
    `<tr><td class=l>决策记录</td><td>${db.decisions??'—'}</td></tr>`+
    `<tr><td class=l>权益快照天数</td><td>${db.equity_days??'—'}</td></tr>`+
    `<tr><td class=l>对账成交笔数</td><td>${db.recon_rows??'—'}</td></tr></table>`+
    (d.errors&&d.errors.sysdb?`<div class=err>${esc(d.errors.sysdb)}</div>`:'');
}

// ---- 行情·选股 ----
let S=null,sSort={key:'r20',desc:true},sFilter='all',sQ='',sLoaded=false;
const S_COLS=[['name','标的','l'],['price','现价'],['r1','日'],['r5','5日'],['r20','20日'],['r60','60日'],
  ['vol60','波动率'],['ma20','vs MA20'],['ma60','vs MA60'],['ma120','vs MA120'],['dd60','距60日高'],
  ['tv20_yi','日均额'],['spark','60日走势','l']];
async function loadScreen(force){
  if(sLoaded&&!force)return;sLoaded=true;
  const r=await fetch('/dash/screen');S=await r.json();
  document.getElementById('s_asof').textContent=S.asof||'—';
  renderScreen();
}
function renderScreen(){
  if(!S)return;
  if(S.error){document.getElementById('s_table').innerHTML=`<div class=err>${esc(S.error)}</div>`;return}
  let rows=(S.rows||[]).slice();
  if(sFilter==='ETF'||sFilter==='股票')rows=rows.filter(x=>x.type===sFilter);
  if(sFilter==='ma120')rows=rows.filter(x=>num(x.ma120)!==null&&x.ma120>0);
  if(sFilter==='lowvol')rows=rows.filter(x=>num(x.vol60)!==null&&x.vol60<25);
  if(sQ)rows=rows.filter(x=>(x.symbol+x.name).toLowerCase().includes(sQ.toLowerCase()));
  const k=sSort.key;
  rows.sort((a,b)=>{const va=num(a[k]),vb=num(b[k]);
    if(va===null&&vb===null)return 0;if(va===null)return 1;if(vb===null)return -1;
    return sSort.desc?vb-va:va-vb});
  document.getElementById('s_count').textContent=rows.length;
  const head=S_COLS.map(([key,label,l])=>{
    if(key==='spark'||key==='name')return `<th class="${l||''}">${label}</th>`;
    const on=sSort.key===key?' on':'';
    return `<th class="sort${on}" data-k=${key}>${label}${sSort.key===key?(sSort.desc?' ↓':' ↑'):''}</th>`}).join('');
  const body=rows.map(x=>`<tr class=click data-s="${esc(x.symbol)}">`+
    `<td class=l><b>${esc(x.name||x.symbol)}</b> <span class=sub>${esc(x.symbol)} ${esc(x.type)}</span></td>`+
    `<td>${Number(x.price).toFixed(3)}</td><td>${pc(x.r1)}</td><td>${pc(x.r5)}</td><td>${pc(x.r20)}</td><td>${pc(x.r60)}</td>`+
    `<td>${x.vol60===null?'—':esc(x.vol60)+'%'}</td><td>${pc(x.ma20)}</td><td>${pc(x.ma60)}</td><td>${pc(x.ma120)}</td>`+
    `<td>${pc(x.dd60)}</td><td>${esc(x.tv20_yi)}亿</td><td class=l>${spark(x.spark)}</td></tr>`).join('');
  document.getElementById('s_table').innerHTML=rows.length?
    `<div style="overflow-x:auto"><table><tr>${head}</tr>${body}</table></div>`
    :'<div class=sub>没有匹配的标的(先跑 LOAD_ALL loader)</div>';
  document.querySelectorAll('#s_table th.sort').forEach(th=>th.onclick=()=>{
    const key=th.dataset.k;
    if(sSort.key===key)sSort.desc=!sSort.desc;else sSort={key,desc:true};
    renderScreen()});
  document.querySelectorAll('#s_table tr.click').forEach(tr=>tr.onclick=()=>showDetail(tr.dataset.s));
}
async function showDetail(sym){
  const box=document.getElementById('s_detail');
  box.style.display='block';box.innerHTML='<div class=card>加载 '+esc(sym)+' …</div>';
  const r=await fetch('/dash/detail?symbol='+encodeURIComponent(sym));const d=await r.json();
  if(d.error||!(d.closes||[]).length){box.innerHTML=`<div class=card><div class=err>${esc(d.error||'无数据')}</div></div>`;return}
  const W=1100,H=260,n=d.closes.length;
  const all=d.closes.concat(d.ma20,d.ma60,d.ma120).filter(v=>v!==null&&v!==undefined);
  const mn=Math.min(...all),mx=Math.max(...all),sp=mx-mn||1;
  const xy=(v,i)=>`${(i/(n-1)*(W-10)+5).toFixed(1)},${(H-18-(v-mn)/sp*(H-36)).toFixed(1)}`;
  const poly=(arr,color,w)=>{
    const segs=[];let cur=[];
    arr.forEach((v,i)=>{if(v===null||v===undefined){if(cur.length>1)segs.push(cur);cur=[]}else cur.push(xy(v,i))});
    if(cur.length>1)segs.push(cur);
    return segs.map(s=>`<polyline points="${s.join(' ')}" fill=none stroke="${color}" stroke-width=${w} />`).join('')};
  const last=d.closes[n-1],first=d.closes[0],chg=(last/first-1)*100;
  box.innerHTML=`<div class=card><h2>${esc(d.name||'')} ${esc(d.symbol)}
    <span class=sub>${esc(d.dates[0])} ~ ${esc(d.dates[n-1])} · 现价 ${last} (区间 ${chg>=0?'+':''}${chg.toFixed(1)}%)</span>
    <span style=float:right class=sub>收盘<span class=legend style=background:#e8ecf4></span>
      MA20<span class=legend style=background:#f6c343></span>
      MA60<span class=legend style=background:#58a6ff></span>
      MA120<span class=legend style=background:#b17ee2></span>
      <a href=# onclick="this.closest('#s_detail').style.display='none';return false" style=margin-left:14px>✕关闭</a></span></h2>
    <svg width="100%" height=${H} viewBox="0 0 ${W} ${H}" preserveAspectRatio=none>
      ${poly(d.closes,'#e8ecf4',1.6)}${poly(d.ma20,'#f6c343',1.1)}${poly(d.ma60,'#58a6ff',1.1)}${poly(d.ma120,'#b17ee2',1.1)}
    </svg></div>`;
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
document.getElementById('s_chips').onclick=e=>{
  const f=e.target.dataset&&e.target.dataset.f;if(!f)return;
  sFilter=f;document.querySelectorAll('#s_chips .chip').forEach(c=>c.classList.toggle('on',c.dataset.f===f));
  renderScreen()};
document.getElementById('s_q').oninput=e=>{sQ=e.target.value;renderScreen()};

// ---- 资讯 ----
let nSym='all',nLoaded=false;
async function loadNews(force){
  if(nLoaded&&!force)return;nLoaded=true;
  document.getElementById('n_list').innerHTML='加载中…(首次抓取较慢)';
  const r=await fetch('/dash/news?symbol='+encodeURIComponent(nSym));const n=await r.json();
  document.getElementById('n_cached').textContent='更新于 '+(n.cached_at||'—');
  if(!document.getElementById('n_chips').childElementCount){
    const syms=['all',...new Set((n.rows||[]).map(x=>x.symbol))];
    document.getElementById('n_chips').innerHTML=syms.map(s=>
      `<span class="chip${s===nSym?' on':''}" data-s="${esc(s)}">${s==='all'?'全部':esc(s)}</span>`).join('');
    document.getElementById('n_chips').onclick=e=>{
      const s=e.target.dataset&&e.target.dataset.s;if(!s)return;
      nSym=s;document.querySelectorAll('#n_chips .chip').forEach(c=>c.classList.toggle('on',c.dataset.s===s));
      renderNews(window._news)};
  }
  window._news=n;renderNews(n);
  if(n.error)document.getElementById('n_list').innerHTML=`<div class=err>${esc(n.error)}</div>`;
}
function renderNews(n){
  const rows=((n||{}).rows||[]).filter(x=>nSym==='all'||x.symbol===nSym);
  document.getElementById('n_list').innerHTML=rows.map(x=>
    `<div class=newsitem><span class=t>${esc(x.time)}</span><span class=tag>${esc(x.symbol)}</span>`+
    `${x.url?`<a href="${esc(x.url)}" target=_blank>${esc(x.title)}</a>`:esc(x.title)}`+
    `<span class=sub> · ${esc(x.source)}</span></div>`).join('')||'<div class=sub>暂无资讯</div>';
}

// ---- 导航 ----
function go(tab){
  document.querySelectorAll('nav a.tab').forEach(x=>x.classList.toggle('on',x.dataset.t===tab));
  document.querySelectorAll('main section').forEach(x=>x.classList.toggle('on',x.id===tab));
  if(tab==='screen')loadScreen();
  if(tab==='news')loadNews();
  history.replaceState(null,'','#'+tab);
}
document.querySelectorAll('nav a.tab').forEach(a=>a.onclick=e=>{e.preventDefault();go(a.dataset.t)});
document.querySelectorAll('.card.link').forEach(c=>c.onclick=()=>go(c.dataset.go));
if(location.hash&&document.getElementById(location.hash.slice(1)))go(location.hash.slice(1));
loadOverview();setInterval(loadOverview,60000);setInterval(()=>{if(sLoaded)loadScreen(true)},120000);
</script></body></html>"""
