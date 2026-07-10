# -*- coding: utf-8 -*-
"""
统一量化工作台(只读)v2 —— 一个地址看全部:http://127.0.0.1:8000/dash

布局照抄成熟平台的通用模式(TradingView/雪球/东财的共性):
  · 顶部固定导航,单页多标签,不用再在地址栏里换 URL;
  · 自选行情表 + 迷你走势图(A股习惯:红涨绿跌);
  · 资讯流按标的分类(chips),服务端缓存 10 分钟;
  · 策略运营页 = 决策记录 + 对账(滑点) + 权益曲线。
全部只读:不放任何会下单、撤单、改数据的接口。真实操作仍走命令行。

由 run_local_secure.py 在启动时挂到平台 app 上(不改平台源码):
  /dash 页面 · /dash/data 总览 · /dash/quotes 行情 · /dash/news 资讯
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
    background:#101725;border-bottom:1px solid var(--line);padding:0 16px}
nav .brand{font-weight:700;font-size:15px;margin-right:14px;padding:12px 0;color:#cfe0ff}
nav a.tab{color:var(--mut);text-decoration:none;padding:12px 13px;font-size:14px;border-bottom:2px solid transparent}
nav a.tab.on{color:#fff;border-bottom-color:var(--acc)}
nav .right{margin-left:auto;display:flex;gap:12px;align-items:center}
nav .right a{color:var(--acc);font-size:13px;text-decoration:none}
main{max-width:1240px;margin:18px auto;padding:0 16px}
section{display:none}section.on{display:block}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid3{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow:auto}
.card.full{grid-column:1/-1}
h2{font-size:13px;color:#9daecd;margin:0 0 10px;font-weight:600}
.kpi{font-size:22px;font-weight:700}.kpi small{font-size:12px;color:var(--mut);font-weight:400;display:block;margin-top:4px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:#8fa0bd;font-weight:500}td.l,th.l{text-align:left}
.up{color:var(--up)}.dn{color:var(--dn)}.pend{color:#f6c343}
pre{white-space:pre-wrap;font-size:13px;line-height:1.55;color:#c6d0e2;margin:0}
a{color:var(--acc)}.err{color:#ff7b72;font-size:12px;margin-top:6px}
.sub{color:var(--mut);font-size:12px}
.pill{background:#1c2740;border-radius:6px;padding:2px 8px;font-size:12px;color:#9db2d8}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.chip{background:#1c2740;border:1px solid var(--line);border-radius:14px;padding:3px 11px;
      font-size:12px;color:#9db2d8;cursor:pointer}
.chip.on{background:#274068;color:#fff;border-color:#3d5c8c}
.newsitem{padding:9px 2px;border-bottom:1px solid var(--line);font-size:13px}
.newsitem .t{color:var(--mut);font-size:12px;margin-right:8px}
.newsitem .tag{background:#1c2740;border-radius:4px;padding:1px 6px;font-size:11px;color:#9db2d8;margin-right:8px}
button{background:#233;color:#9db2d8;border:1px solid #33405c;border-radius:6px;padding:4px 10px;cursor:pointer}
@media(max-width:900px){.grid{grid-template-columns:1fr}.grid3{grid-template-columns:1fr 1fr}}
</style></head><body>
<nav>
  <span class=brand>📊 量化工作台 <span class=pill>只读</span></span>
  <a class="tab on" data-t=overview href=#>总览</a>
  <a class=tab data-t=quotes href=#>自选行情</a>
  <a class=tab data-t=news href=#>资讯</a>
  <a class=tab data-t=ops href=#>决策·对账</a>
  <a class=tab data-t=reports href=#>报告</a>
  <span class=right>
    <a href="/reports/latest.html" target=_blank>回测汇总</a>
    <a href="/quantflow/" target=_blank>QuantFlow ↗</a>
    <span id=ts class=sub></span>
  </span>
</nav>
<main>

<section id=overview class=on>
  <div class=grid3>
    <div class=card><h2>华泰总资产</h2><div class=kpi id=k_asset>—<small id=k_asset_chg></small></div></div>
    <div class=card><h2>华泰持仓 / 挂单</h2><div class=kpi id=k_pos>—</div></div>
    <div class=card><h2>纸面账本持仓</h2><div class=kpi id=k_ledger>—</div></div>
    <div class=card><h2>行情数据截止</h2><div class=kpi id=k_asof>—</div></div>
  </div>
  <div class=grid>
    <div class=card><h2>权益曲线(每日快照,唯一无偏的成绩单)</h2><div id=equity>暂无快照(跑一次 reviewer 生成)</div></div>
    <div class=card><h2>华泰挂单</h2><div id=pending>加载中…</div></div>
    <div class=card><h2>华泰持仓</h2><div id=positions></div></div>
    <div class=card><h2>纸面账本(策略持有)</h2><div id=ledger></div></div>
    <div class=card full><h2>今日决策报告 <span id=rname class=pill></span></h2><pre id=report></pre></div>
  </div>
</section>

<section id=quotes>
  <div class=card full>
    <h2>自选行情 <span class=sub>(库内日线,红涨绿跌 · 数据截止 <span id=q_asof></span>)</span></h2>
    <div id=qtable>加载中…</div>
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

</main>
<script>
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function dig(o,ks){for(const k of ks){o=o&&o[k]}return o}
function pc(v){if(v===null||v===undefined)return '—';
  const c=v>0?'up':(v<0?'dn':'');return `<span class=${c}>${v>0?'+':''}${Number(v).toFixed(2)}%</span>`}
function spark(a,w=110,h=26){
  if(!a||a.length<2)return '';
  const mn=Math.min(...a),mx=Math.max(...a),sp=mx-mn||1;
  const pts=a.map((v,i)=>`${(i/(a.length-1)*w).toFixed(1)},${(h-2-(v-mn)/sp*(h-4)).toFixed(1)}`).join(' ');
  const up=a[a.length-1]>=a[0];
  return `<svg width=${w} height=${h}><polyline points="${pts}" fill=none stroke="${up?'#ff6b6b':'#4fd18b'}" stroke-width=1.4 /></svg>`}
function line(a,w,h){ // 权益大图
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
  let h='<table><tr><th class=l>标的</th><th>数量</th><th>市值</th><th>盈亏</th></tr>';
  for(const p of list){const pnl=p.profit||p.floatProfit||0;
    h+=`<tr><td class=l>${esc(p.stockName||'')} ${esc(p.stockCode||'')}</td><td>${esc(p.quantity||p.volume)}</td>`+
       `<td>${esc(p.marketValue||'')}</td><td class=${pnl>=0?'up':'dn'}>${esc(pnl)}</td></tr>`}
  return h+'</table>'}

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
  document.getElementById('equity').innerHTML=line(eq,760,180);
  document.getElementById('pending').innerHTML=ordersTable(po)+err('huatai');
  document.getElementById('positions').innerHTML=posTable(hp);
  const led=(d.ledger||[]).map(p=>`<tr><td class=l>${esc(p.symbol)}</td><td>${esc(p.entry_price)}</td><td>${esc(p.entry_date)}</td><td>${p.half?'半仓':''}${p.by_agent?' 🤖':''}</td></tr>`).join('');
  document.getElementById('ledger').innerHTML=led?`<table><tr><th class=l>标的</th><th>成本</th><th>入场日</th><th></th></tr>${led}</table>`:'<div class=sub>空仓</div>';
  document.getElementById('report').textContent=d.report||'(还没有决策报告 —— 跑一次 reviewer 就有了)';
  document.getElementById('rname').textContent=d.report_name||'';
  renderOps(d);renderReports(d);
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
  const rec=(d.recon||[]).map(x=>`<tr><td class=l>${esc(x.date)}</td><td class=l>${esc(x.symbol)}</td>`+
    `<td class=l>${esc(x.side)}</td><td>${esc(x.decision_price??'—')}</td><td>${esc(x.fill_price)}</td>`+
    `<td>${x.slippage_pct!==undefined?pc(x.slippage_pct):'—'}</td></tr>`).join('');
  document.getElementById('o_recon').innerHTML=rec?
    `<table><tr><th class=l>日期</th><th class=l>标的</th><th class=l>方向</th><th>决策价</th><th>成交价</th><th>滑点</th></tr>${rec}</table>`
    :'<div class=sub>暂无成交对账(周一开盘成交后就有了)</div>';
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
let qLoaded=false;
async function loadQuotes(force){
  if(qLoaded&&!force)return;qLoaded=true;
  const r=await fetch('/dash/quotes');const q=await r.json();
  document.getElementById('q_asof').textContent=q.asof||'—';
  if(q.error){document.getElementById('qtable').innerHTML=`<div class=err>${esc(q.error)}</div>`;return}
  const rows=(q.rows||[]).map(x=>`<tr><td class=l><b>${esc(x.name||x.symbol)}</b> <span class=sub>${esc(x.symbol)}</span></td>`+
    `<td>${Number(x.price).toFixed(3)}</td><td>${pc(x.chg1)}</td><td>${pc(x.chg5)}</td><td>${pc(x.chg20)}</td>`+
    `<td class=l>${spark(x.spark)}</td><td>${esc(x.turnover_yi)}亿</td></tr>`).join('');
  document.getElementById('qtable').innerHTML=rows?
    `<table><tr><th class=l>标的</th><th>现价</th><th>日涨跌</th><th>5日</th><th>20日</th><th class=l>60日走势</th><th>成交额</th></tr>${rows}</table>`
    :'<div class=sub>库里还没有行情,先跑 loader</div>';
}
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
      nSym=s;document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('on',c.dataset.s===s));
      renderNews(n)};
  }
  window._news=n;renderNews(n);
  if(n.error)document.getElementById('n_list').innerHTML=`<div class=err>${esc(n.error)}</div>`;
}
function renderNews(n){
  const rows=(n.rows||[]).filter(x=>nSym==='all'||x.symbol===nSym);
  document.getElementById('n_list').innerHTML=rows.map(x=>
    `<div class=newsitem><span class=t>${esc(x.time)}</span><span class=tag>${esc(x.symbol)}</span>`+
    `${x.url?`<a href="${esc(x.url)}" target=_blank>${esc(x.title)}</a>`:esc(x.title)}`+
    `<span class=sub> · ${esc(x.source)}</span></div>`).join('')||'<div class=sub>暂无资讯</div>';
}
document.querySelectorAll('nav a.tab').forEach(a=>a.onclick=e=>{
  e.preventDefault();
  document.querySelectorAll('nav a.tab').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('main section').forEach(x=>x.classList.remove('on'));
  a.classList.add('on');document.getElementById(a.dataset.t).classList.add('on');
  if(a.dataset.t==='quotes')loadQuotes();
  if(a.dataset.t==='news')loadNews();
});
loadOverview();setInterval(loadOverview,60000);setInterval(()=>{if(qLoaded)loadQuotes(true)},60000);
</script></body></html>"""
