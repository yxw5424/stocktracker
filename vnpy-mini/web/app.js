// vnpy-mini 前端：REST 发起动作，WebSocket 接收所有实时事件。

const $ = (id) => document.getElementById(id);
const EXCHANGES = ["SHFE", "DCE", "CZCE", "CFFEX", "INE", "SSE", "SZSE"];

const state = {
  quotes: new Map(),       // symbol -> tick
  orders: new Map(),       // orderid -> order
  positions: new Map(),    // symbol.direction -> position
  strategies: new Map(),   // id -> strategy
  trades: [],              // 最近成交（倒序）
  series: new Map(),       // symbol -> [{price}]  价格序列，画图用
  signals: [],             // 量化今日信号
  chartSymbol: null,
  dir: "LONG",
  off: "OPEN",
};

const exOf = (s) => (s.endsWith(".SH") ? "SSE" : s.endsWith(".SZ") ? "SZSE" : "SSE");
const SERIES_MAX = 120;

// ---------- 工具 ----------
const fmt = (n, d = 2) =>
  (n === undefined || n === null || n === "") ? "—"
  : Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
const signClass = (n) => (Number(n) > 0 ? "up" : Number(n) < 0 ? "down" : "");
const signed = (n, d = 2) => (Number(n) > 0 ? "+" : "") + fmt(n, d);

function log(msg, level = "info") {
  const el = $("log");
  const t = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const line = document.createElement("div");
  if (level === "error") line.className = "err";
  line.innerHTML = `<span class="t">${t}</span>${msg}`;
  el.prepend(line);
  while (el.childNodes.length > 200) el.removeChild(el.lastChild);
}
function fillSelect(sel) {
  sel.innerHTML = EXCHANGES.map((e) => `<option value="${e}">${e}</option>`).join("");
}
function dirTag(d) {
  const long = d === "LONG" || d === "多";
  return `<span class="tag ${long ? "long" : "short"}">${long ? "多" : "空"}</span>`;
}

// ---------- 渲染：账户 / 行情 / 委托 / 持仓 / 成交 / 策略 ----------
function renderAccount(a) {
  if (a.balance !== undefined) $("balance").textContent = fmt(a.balance);
  if (a.available !== undefined) $("available").textContent = fmt(a.available);
  if (a.pnl !== undefined) {
    const el = $("pnl");
    el.textContent = signed(a.pnl);
    el.className = "num " + signClass(a.pnl);
  }
}

function renderQuotes() {
  const body = $("quoteTbl").querySelector("tbody");
  if (state.quotes.size === 0) {
    body.innerHTML = `<tr class="empty"><td colspan="4">暂无订阅</td></tr>`;
    return;
  }
  body.innerHTML = [...state.quotes.values()].map((t) => `
    <tr class="click" data-sym="${t.symbol}" data-ex="${t.exchange}">
      <td>${t.symbol}</td>
      <td class="r">${fmt(t.last_price)}</td>
      <td class="r">${fmt(t.bid_price_1)}</td>
      <td class="r">${fmt(t.ask_price_1)}</td>
    </tr>`).join("");
}

function renderOrders() {
  const body = $("orderTbl").querySelector("tbody");
  const list = [...state.orders.values()].reverse();
  $("orderCount").textContent = list.length ? `(${list.length})` : "";
  if (!list.length) {
    body.innerHTML = `<tr class="empty"><td colspan="8">暂无委托</td></tr>`;
    return;
  }
  const done = (s) => s === "全部成交" || s === "已撤销" || s === "拒单";
  body.innerHTML = list.map((o) => `
    <tr>
      <td>${o.orderid}</td><td>${o.symbol}</td><td>${dirTag(o.direction)}</td>
      <td class="r">${fmt(o.price)}</td><td class="r">${fmt(o.volume, 0)}</td>
      <td class="r">${fmt(o.traded, 0)}</td><td>${o.status}</td>
      <td>${done(o.status) ? "" : `<button class="x" data-cancel="${o.orderid}" data-sym="${o.symbol}" data-ex="${o.exchange}">撤</button>`}</td>
    </tr>`).join("");
}

function renderPositions() {
  const body = $("posTbl").querySelector("tbody");
  const list = [...state.positions.values()].filter((p) => p.volume);
  if (!list.length) {
    body.innerHTML = `<tr class="empty"><td colspan="6">暂无持仓</td></tr>`;
    return;
  }
  body.innerHTML = list.map((p) => `
    <tr>
      <td>${p.symbol}</td><td>${dirTag(p.direction)}</td>
      <td class="r">${fmt(p.volume, 0)}</td><td class="r">${fmt(p.price)}</td>
      <td class="r ${signClass(p.pnl)}">${signed(p.pnl)}</td>
      <td><button class="x" data-close="${p.symbol}" data-ex="${p.exchange}" data-dir="${p.direction}" data-vol="${p.volume}">平</button></td>
    </tr>`).join("");
}

function renderTrades() {
  const body = $("tradeTbl").querySelector("tbody");
  if (!state.trades.length) {
    body.innerHTML = `<tr class="empty"><td colspan="5">暂无成交</td></tr>`;
    return;
  }
  body.innerHTML = state.trades.slice(0, 30).map((t) => `
    <tr>
      <td>${(t.datetime || "").split("T")[1] || t.datetime || ""}</td>
      <td>${t.symbol}</td><td>${dirTag(t.direction)}</td>
      <td class="r">${fmt(t.price)}</td><td class="r">${fmt(t.volume, 0)}</td>
    </tr>`).join("");
}

function renderStrategies() {
  const body = $("stratTbl").querySelector("tbody");
  const list = [...state.strategies.values()];
  if (!list.length) {
    body.innerHTML = `<tr class="empty"><td colspan="5">暂无运行中的策略</td></tr>`;
    return;
  }
  body.innerHTML = list.map((s) => `
    <tr class="${s.active ? "" : "off"}">
      <td>${s.name}<div class="sub">${s.symbol} · MA${s.fast}/${s.slow} · ${s.volume}手</div></td>
      <td class="r ${signClass(s.net)}">${signed(s.net, 0)}</td>
      <td class="r">${s.trades}</td>
      <td>${s.active ? `<span class="tag long">运行</span>` : `<span class="tag">已停</span>`}</td>
      <td>${s.active ? `<button class="x" data-stop="${s.id}">停</button>` : ""}</td>
    </tr>`).join("");
}

// ---------- 今日信号 + AI 顾问 ----------
function renderSignals() {
  const body = $("signalTbl").querySelector("tbody");
  if (!state.signals.length) {
    body.innerHTML = `<tr class="empty"><td colspan="4">点击「生成」运行量化选股</td></tr>`;
    return;
  }
  body.innerHTML = state.signals.map((s) => `
    <tr>
      <td>${s.symbol}</td>
      <td class="r">${(s.weight * 100).toFixed(1)}%</td>
      <td class="r">${fmt(s.score)}</td>
      <td><button class="x" data-build="${s.symbol}">建仓</button></td>
    </tr>`).join("");
}

function renderAdvice(d) {
  $("adviceEngine").textContent = d._engine ? `· ${d._engine}` : "";
  const acts = (d.actions || []).map((a) => `
    <tr>
      <td>${a.symbol}</td>
      <td><span class="act ${a.action}">${a.action}</span></td>
      <td class="r">${(a.target_weight * 100).toFixed(1)}%</td>
      <td class="r">${(a.confidence * 100).toFixed(0)}%</td>
      <td>${["buy", "add"].includes(a.action) ? `<button class="x" data-build="${a.symbol}">执行</button>` : ""}</td>
    </tr>`).join("");
  const swings = (d.swing_ideas || []).map((s) => `
    <div class="swing"><b>${s.symbol}</b> ${s.setup}：进 ${s.entry} / 损 ${s.stop} / 盈 ${s.take_profit}
      <div class="muted">${s.reason}</div></div>`).join("");
  $("adviceBody").innerHTML = `
    <div><span class="stance ${d.stance}">${({risk_on:"偏多",neutral:"中性",risk_off:"偏空"})[d.stance] || d.stance}</span>
      <span class="muted">信心 ${(d.confidence * 100).toFixed(0)}%</span></div>
    <div class="view">${d.market_view || ""}</div>
    ${acts ? `<table class="tbl"><thead><tr><th>合约</th><th>动作</th><th class="r">目标权重</th><th class="r">信心</th><th></th></tr></thead><tbody>${acts}</tbody></table>` : ""}
    ${swings ? `<div><div class="muted" style="margin-bottom:4px">波段想法</div>${swings}</div>` : ""}
    ${d.risk_notes ? `<div class="risk">⚠ ${d.risk_notes}</div>` : ""}`;
}

function buildPosition(symbol) {
  // 建仓/执行：订阅后挂高价限价单（mock 即时成交；live 走真实撮合）
  const ex = exOf(symbol);
  post("/api/subscribe", { symbol, exchange: ex });
  post("/api/order", { symbol, exchange: ex, direction: "LONG", offset: "OPEN",
                       type: "LIMIT", price: 99999, volume: 100 });
  log(`按信号建仓 ${symbol}`);
}

// ---------- 图表（原生 canvas，无依赖）----------
function pushSeries(tick) {
  let arr = state.series.get(tick.symbol);
  if (!arr) { arr = []; state.series.set(tick.symbol, arr); }
  arr.push(tick.last_price);
  if (arr.length > SERIES_MAX) arr.shift();
  if (!state.chartSymbol) selectSymbol(tick.symbol, tick.exchange);
  if (tick.symbol === state.chartSymbol) drawChart();
}

function selectSymbol(sym, ex) {
  state.chartSymbol = sym;
  $("chartSym").textContent = sym;
  $("oSymbol").value = sym;
  $("stSymbol").value = sym;
  if (ex) { $("oExchange").value = ex; $("stExchange").value = ex; }
  drawChart();
}

function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

function drawChart() {
  const c = $("chart");
  if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const ctx = c.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const accent = cssVar("--accent"), muted = cssVar("--muted"), line = cssVar("--line");
  const data = (state.chartSymbol && state.series.get(state.chartSymbol)) || [];

  if (data.length < 2) {
    ctx.fillStyle = muted; ctx.font = "13px system-ui";
    ctx.fillText(state.chartSymbol ? "等待行情数据…" : "点击「行情」中的合约查看走势", 12, h / 2);
    return;
  }

  let min = Math.min(...data), max = Math.max(...data);
  if (min === max) { min -= 1; max += 1; }
  const padL = 8, padR = 58, padT = 12, padB = 16;
  const X = (i) => padL + (w - padL - padR) * (i / (data.length - 1));
  const Y = (v) => padT + (h - padT - padB) * (1 - (v - min) / (max - min));

  // 横向网格 + 价格刻度
  ctx.font = "11px ui-monospace, monospace"; ctx.lineWidth = 1;
  [max, (max + min) / 2, min].forEach((v) => {
    const y = Y(v);
    ctx.strokeStyle = line; ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillStyle = muted; ctx.fillText(v.toFixed(2), w - padR + 6, y + 3);
  });

  // 面积
  ctx.beginPath();
  data.forEach((v, i) => { const x = X(i), y = Y(v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.lineTo(X(data.length - 1), h - padB); ctx.lineTo(X(0), h - padB); ctx.closePath();
  ctx.fillStyle = accent + "1f";
  ctx.fill();

  // 折线
  ctx.beginPath();
  data.forEach((v, i) => { const x = X(i), y = Y(v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.strokeStyle = accent; ctx.lineWidth = 1.6; ctx.stroke();

  // 最新点
  const lx = X(data.length - 1), ly = Y(data[data.length - 1]);
  ctx.beginPath(); ctx.arc(lx, ly, 3, 0, Math.PI * 2); ctx.fillStyle = accent; ctx.fill();
}

// ---------- WebSocket ----------
let ws;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { $("dot").classList.add("on"); $("conn").textContent = "已连接"; };
  ws.onclose = () => {
    $("dot").classList.remove("on"); $("conn").textContent = "断开，重连中…";
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

function handle({ type, data }) {
  switch (type) {
    case "snapshot": applySnapshot(data); break;
    case "tick":
      state.quotes.set(data.symbol, data); renderQuotes(); pushSeries(data); break;
    case "order":
      state.orders.set(data.orderid, data); renderOrders(); break;
    case "trade":
      state.trades.unshift(data); renderTrades(); break;
    case "position": {
      const key = `${data.symbol}.${data.direction}`;
      if (!data.volume) state.positions.delete(key);
      else state.positions.set(key, data);
      renderPositions(); break;
    }
    case "account": renderAccount(data); break;
    case "strategy": state.strategies.set(data.id, data); renderStrategies(); break;
    case "signals": state.signals = data; renderSignals(); break;
    case "advisor": renderAdvice(data); break;
    case "log": log(data.msg, data.level); break;
  }
}

function applySnapshot(s) {
  if (s.account) renderAccount(s.account);
  (s.ticks || []).forEach((t) => { state.quotes.set(t.symbol, t); });
  (s.orders || []).forEach((o) => state.orders.set(o.orderid, o));
  (s.positions || []).forEach((p) => state.positions.set(`${p.symbol}.${p.direction}`, p));
  (s.strategies || []).forEach((st) => state.strategies.set(st.id, st));
  state.trades = s.trades || [];
  renderQuotes(); renderOrders(); renderPositions(); renderTrades(); renderStrategies();
}

// ---------- REST ----------
async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) { log(`请求失败 ${path}: ${r.status}`, "error"); return null; }
  return r.json();
}

// ---------- 事件绑定 ----------
function bind() {
  fillSelect($("subExchange")); fillSelect($("oExchange")); fillSelect($("stExchange"));

  $("connectBtn").onclick = async () => { await post("/api/connect", { setting: {} }); };

  $("subForm").onsubmit = async (e) => {
    e.preventDefault();
    const symbol = $("subSymbol").value.trim();
    if (!symbol) return;
    await post("/api/subscribe", { symbol, exchange: $("subExchange").value });
    selectSymbol(symbol, $("subExchange").value);
    $("subSymbol").value = "";
  };

  // 点击行情某行 -> 选中到图表/下单/策略
  $("quoteTbl").addEventListener("click", (e) => {
    const tr = e.target.closest("tr.click");
    if (tr) selectSymbol(tr.dataset.sym, tr.dataset.ex);
  });

  // 方向 / 开平 分段
  document.querySelectorAll("#dirSeg button").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll("#dirSeg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); state.dir = b.dataset.v;
    };
  });
  document.querySelectorAll("#offSeg button").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll("#offSeg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); state.off = b.dataset.v;
    };
  });

  // 下单
  $("orderForm").onsubmit = async (e) => {
    e.preventDefault();
    const symbol = $("oSymbol").value.trim();
    if (!symbol) { log("请填写下单合约", "error"); return; }
    const r = await post("/api/order", {
      symbol, exchange: $("oExchange").value,
      direction: state.dir, offset: state.off, type: "LIMIT",
      price: Number($("oPrice").value || 0), volume: Number($("oVolume").value || 1),
    });
    if (r && r.ok) log(`委托已发送 → ${r.orderid}`);
  };

  // 撤单（事件委托）
  $("orderTbl").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-cancel]");
    if (!btn) return;
    post("/api/cancel", { orderid: btn.dataset.cancel, symbol: btn.dataset.sym, exchange: btn.dataset.ex });
  });

  // 全撤
  $("cancelAll").onclick = () => {
    state.orders.forEach((o) => {
      if (["全部成交", "已撤销", "拒单"].includes(o.status)) return;
      post("/api/cancel", { orderid: o.orderid, symbol: o.symbol, exchange: o.exchange });
    });
  };

  // 一键平仓
  $("posTbl").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-close]");
    if (!btn) return;
    const sym = btn.dataset.close;
    const q = state.quotes.get(sym);
    const price = q ? q.last_price : 0;
    post("/api/order", {
      symbol: sym, exchange: btn.dataset.ex,
      direction: btn.dataset.dir === "LONG" ? "SHORT" : "LONG",
      offset: "CLOSE", type: "LIMIT", price, volume: Number(btn.dataset.vol),
    });
  });

  // 启动策略
  $("stratForm").onsubmit = async (e) => {
    e.preventDefault();
    const symbol = $("stSymbol").value.trim();
    if (!symbol) { log("请填写策略合约", "error"); return; }
    const r = await post("/api/strategy/start", {
      name: "双均线", symbol, exchange: $("stExchange").value,
      fast: Number($("stFast").value || 5), slow: Number($("stSlow").value || 20),
      volume: Number($("stVol").value || 1),
    });
    if (r && r.ok) log(`策略已启动 → ${r.id}`);
  };

  // 停止策略
  $("stratTbl").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-stop]");
    if (btn) post("/api/strategy/stop", { id: btn.dataset.stop });
  });

  // 今日信号：生成 + 建仓
  $("genSignals").onclick = async () => {
    const r = await post("/api/signals/generate", { top_n: 10 });
    if (r && r.ok) { state.signals = r.signals; renderSignals(); log(`已生成 ${r.signals.length} 条量化信号`); }
    else if (r) log(r.error || "信号生成失败", "error");
  };
  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-build]");
    if (btn) buildPosition(btn.dataset.build);
  });

  // AI 顾问：开盘/收盘例程
  $("runOpen").onclick = async () => {
    log("正在运行 AI 顾问·开盘例程…");
    const r = await post("/api/advisor/run", { session: "open" });
    if (r && r.ok) renderAdvice(r.decision);
  };
  $("runClose").onclick = async () => {
    log("正在运行 AI 顾问·收盘例程…");
    const r = await post("/api/advisor/run", { session: "close" });
    if (r && r.ok) renderAdvice(r.decision);
  };

  window.addEventListener("resize", drawChart);
}

// ---------- 启动 ----------
async function init() {
  bind();
  renderQuotes(); renderOrders(); renderPositions(); renderTrades(); renderStrategies();
  renderSignals(); drawChart();
  try { $("mode").textContent = (await (await fetch("/api/status")).json()).mode; } catch (_) {}
  // 载入已有信号 / 最近决策 / 调度计划
  try { state.signals = await (await fetch("/api/signals")).json(); renderSignals(); } catch (_) {}
  try { const d = await (await fetch("/api/advisor/latest")).json(); if (d && d.stance) renderAdvice(d); } catch (_) {}
  try {
    const sch = await (await fetch("/api/schedule")).json();
    if (sch.length) $("scheduleLine").textContent =
      "下次自动运行：" + sch.slice(0, 2).map((s) => `${s.at}（${s.session === "open" ? "开盘" : "收盘"}）`).join(" · ");
  } catch (_) {}
  connectWS();
}
init();
