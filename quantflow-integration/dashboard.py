# -*- coding: utf-8 -*-
"""
只读看板 —— 挂在 QuantFlow 同一网页服务下,浏览器打开 /dash 就能看:
  今日决策报告 / 华泰挂单·持仓·成交 / 当前纸面账本 / 回测汇总链接。
全部只读:不放任何会下单、撤单、改数据的接口。真实操作仍走命令行。

由 run_local_secure.py 在启动时挂到平台 app 上(不改平台源码)。
"""
import os
import glob
import json

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
    """汇总只读数据,任何一块失败都不影响其它块。"""
    out = {"ok": True, "ledger": [], "huatai": {}, "report": "", "reports": [], "errors": {}}

    # 纸面账本
    try:
        db = _mongo()
        out["ledger"] = list(db["ai_review_positions"].find({}, {"_id": 0}))
        out["decisions_recent"] = list(
            db["ai_review_decisions"].find({}, {"_id": 0}).sort("date", -1).limit(20))
    except Exception as e:
        out["errors"]["ledger"] = f"{type(e).__name__}: {e}"

    # 华泰只读三查
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

    # 最新决策报告(markdown 原文)
    try:
        mds = sorted(glob.glob(os.path.join(REPORT_DIR, "review_*.md")), reverse=True)
        out["reports"] = [os.path.basename(m) for m in mds[:30]]
        if mds:
            out["report"] = open(mds[0], encoding="utf-8").read()
            out["report_name"] = os.path.basename(mds[0])
    except Exception as e:
        out["errors"]["report"] = f"{type(e).__name__}: {e}"

    return out


DASH_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>ETF 参赛看板</title><style>
body{font-family:system-ui,'Microsoft YaHei',sans-serif;background:#0f1420;color:#dde3ee;margin:0;padding:24px}
h1{font-size:19px;margin:0 0 4px}
.sub{color:#8892a6;font-size:13px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1200px;margin:auto}
.card{background:#141b2c;border:1px solid #232b3d;border-radius:10px;padding:16px 18px;overflow:auto}
.card.full{grid-column:1/3}
h2{font-size:14px;color:#9daecd;margin:0 0 10px;display:flex;justify-content:space-between}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 10px;border-bottom:1px solid #232b3d;text-align:right;white-space:nowrap}
th{color:#8fa0bd}td.l,th.l{text-align:left}
.buy{color:#7ee2a8}.sell{color:#ff7b72}.pend{color:#f6c343}
pre{white-space:pre-wrap;font-size:13px;line-height:1.5;color:#c6d0e2;margin:0}
a{color:#58a6ff}.err{color:#ff7b72;font-size:12px}
.pill{background:#1c2740;border-radius:6px;padding:2px 8px;font-size:12px;color:#9db2d8}
button{background:#233;color:#9db2d8;border:1px solid #33405c;border-radius:6px;padding:4px 10px;cursor:pointer}
</style></head><body>
<h1>ETF 参赛看板 <span class=pill>只读</span></h1>
<div class=sub>每天看这里就够;下单/撤单/灌数据仍在命令行(网页故意不放这些按钮,防误点)。
 · <a href="/reports/latest.html" target=_blank>回测汇总表</a>
 · <a href="/quantflow/" target=_blank>QuantFlow 工作台</a>
 · <button onclick=load()>刷新</button> <span id=ts class=sub></span></div>
<div class=grid>
  <div class=card><h2>华泰挂单</h2><div id=pending>加载中…</div></div>
  <div class=card><h2>华泰持仓</h2><div id=positions>加载中…</div></div>
  <div class=card><h2>纸面账本(策略持有)</h2><div id=ledger></div></div>
  <div class=card><h2>账户 / 成交</h2><div id=acct></div></div>
  <div class=card full><h2><span>今日决策报告 <span id=rname class=pill></span></span></h2><pre id=report></pre></div>
</div>
<script>
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function dig(o,ks){for(const k of ks){o=o&&o[k]}return o}
function ordersTable(list){
  if(!list||!list.length)return '<div class=sub>无</div>';
  let h='<table><tr><th class=l>标的</th><th>方向</th><th>价</th><th>数量</th><th>已成</th><th>状态</th></tr>';
  for(const o of list){const d=(o.direction||'').includes('buy');
    h+=`<tr><td class=l>${esc(o.stockName||'')} ${esc(o.stockCode||'')}.${esc(o.exchange||'')}</td>`+
       `<td class=${d?'buy':'sell'}>${d?'买':'卖'}</td><td>${esc(o.price)}</td>`+
       `<td>${esc(o.quantity)}</td><td>${esc(o.filledQuantity)}</td><td class=pend>${esc(o.status)}</td></tr>`}
  return h+'</table>'}
function posTable(list){
  if(!list||!list.length)return '<div class=sub>空仓</div>';
  let h='<table><tr><th class=l>标的</th><th>数量</th><th>市值</th><th>盈亏</th></tr>';
  for(const p of list){const pnl=p.profit||p.floatProfit||0;
    h+=`<tr><td class=l>${esc(p.stockName||'')} ${esc(p.stockCode||'')}</td><td>${esc(p.quantity||p.volume)}</td>`+
       `<td>${esc(p.marketValue||'')}</td><td class=${pnl>=0?'buy':'sell'}>${esc(pnl)}</td></tr>`}
  return h+'</table>'}
async function load(){
  document.getElementById('ts').textContent='加载中…';
  const r=await fetch('/dash/data');const d=await r.json();
  const err=k=>d.errors&&d.errors[k]?`<div class=err>${esc(d.errors[k])}</div>`:'';
  document.getElementById('pending').innerHTML=ordersTable(dig(d,['huatai','pending','data','orders']))+err('huatai');
  document.getElementById('positions').innerHTML=posTable(dig(d,['huatai','positions','data','positions']));
  const led=(d.ledger||[]).map(p=>`<tr><td class=l>${esc(p.symbol)}</td><td>${esc(p.entry_price)}</td><td>${esc(p.entry_date)}</td><td>${p.half?'半仓':''}</td></tr>`).join('');
  document.getElementById('ledger').innerHTML=led?`<table><tr><th class=l>标的</th><th>成本</th><th>入场日</th><th></th></tr>${led}</table>`:'<div class=sub>空仓</div>';
  const acc=dig(d,['huatai','account','data'])||{};
  const tr=dig(d,['huatai','trades','data','trades'])||dig(d,['huatai','trades','data','list'])||[];
  document.getElementById('acct').innerHTML=
    `<div class=sub>总资产 ${esc(acc.totalAsset||acc.netAsset||'—')} · 可用 ${esc(acc.cash||acc.availableCash||'—')}</div>`+
    `<div class=sub style=margin-top:8px>近7日成交 ${tr.length} 笔</div>`+ordersTable(tr);
  document.getElementById('report').textContent=d.report||'(还没有决策报告 —— 跑一次 reviewer 就有了)';
  document.getElementById('rname').textContent=d.report_name||'';
  document.getElementById('ts').textContent='更新于 '+new Date().toLocaleTimeString();
}
load();setInterval(load,60000);
</script></body></html>"""
