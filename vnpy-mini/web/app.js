// vnpy-mini 前端：与 FastAPI 桥接层通信。
// REST 发起动作（连接/订阅/下单/撤单），WebSocket 接收所有实时事件。

const $ = (id) => document.getElementById(id);

// 常用交易所（CTP/SimNow 覆盖的几个 + A股）
const EXCHANGES = ["SHFE", "DCE", "CZCE", "CFFEX", "INE", "SSE", "SZSE"];

// 本地状态
const state = {
  quotes: new Map(),     // symbol -> tick
  orders: new Map(),     // orderid -> order
  positions: new Map(),  // symbol.direction -> position
  trades: [],            // 最近成交（倒序）
  dir: "LONG",
  off: "OPEN",
};

// ---------- 工具 ----------
const fmt = (n, d = 2) =>
  (n === undefined || n === null || n === "") ? "—"
  : Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });

const signClass = (n) => (Number(n) > 0 ? "up" : Number(n) < 0 ? "down" : "");

function log(msg, level = "info") {
  const el = $("log");
  const t = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const line = document.createElement("div");
  line.innerHTML = `<span class="t">${t}</span>${msg}`;
  el.prepend(line);
  while (el.childNodes.length > 200) el.removeChild(el.lastChild);
}

function fillSelect(sel) {
  sel.innerHTML = EXCHANGES.map((e) => `<option value="${e}">${e}</option>`).join("");
}

// ---------- 渲染 ----------
function renderAccount(a) {
  if (a.balance !== undefined) $("balance").textContent = fmt(a.balance);
  if (a.available !== undefined) $("available").textContent = fmt(a.available);
  if (a.pnl !== undefined) {
    const el = $("pnl");
    el.textContent = (a.pnl > 0 ? "+" : "") + fmt(a.pnl);
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
    <tr>
      <td>${t.symbol}</td>
      <td class="r">${fmt(t.last_price)}</td>
      <td class="r">${fmt(t.bid_price_1)}</td>
      <td class="r">${fmt(t.ask_price_1)}</td>
    </tr>`).join("");
}

function dirTag(d) {
  const long = d === "LONG" || d === "多";
  return `<span class="tag ${long ? "long" : "short"}">${long ? "多" : "空"}</span>`;
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
      <td>${o.orderid}</td>
      <td>${o.symbol}</td>
      <td>${dirTag(o.direction)}</td>
      <td class="r">${fmt(o.price)}</td>
      <td class="r">${fmt(o.volume, 0)}</td>
      <td class="r">${fmt(o.traded, 0)}</td>
      <td>${o.status}</td>
      <td>${done(o.status) ? "" : `<button class="x" data-cancel="${o.orderid}" data-sym="${o.symbol}" data-ex="${o.exchange}">撤</button>`}</td>
    </tr>`).join("");
}

function renderPositions() {
  const body = $("posTbl").querySelector("tbody");
  const list = [...state.positions.values()].filter((p) => p.volume);
  if (!list.length) {
    body.innerHTML = `<tr class="empty"><td colspan="5">暂无持仓</td></tr>`;
    return;
  }
  body.innerHTML = list.map((p) => `
    <tr>
      <td>${p.symbol}</td>
      <td>${dirTag(p.direction)}</td>
      <td class="r">${fmt(p.volume, 0)}</td>
      <td class="r">${fmt(p.price)}</td>
      <td class="r ${signClass(p.pnl)}">${(p.pnl > 0 ? "+" : "") + fmt(p.pnl)}</td>
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
      <td>${t.symbol}</td>
      <td>${dirTag(t.direction)}</td>
      <td class="r">${fmt(t.price)}</td>
      <td class="r">${fmt(t.volume, 0)}</td>
    </tr>`).join("");
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
      state.quotes.set(data.symbol, data); renderQuotes(); break;
    case "order":
      state.orders.set(data.orderid, data); renderOrders(); break;
    case "trade":
      state.trades.unshift(data); renderTrades(); break;
    case "position":
      state.positions.set(`${data.symbol}.${data.direction}`, data); renderPositions(); break;
    case "account":
      renderAccount(data); break;
    case "log":
      log(data.msg, data.level); break;
  }
}

function applySnapshot(s) {
  if (s.account) renderAccount(s.account);
  (s.ticks || []).forEach((t) => state.quotes.set(t.symbol, t));
  (s.orders || []).forEach((o) => state.orders.set(o.orderid, o));
  (s.positions || []).forEach((p) => state.positions.set(`${p.symbol}.${p.direction}`, p));
  state.trades = s.trades || [];
  renderQuotes(); renderOrders(); renderPositions(); renderTrades();
}

// ---------- REST 动作 ----------
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
  fillSelect($("subExchange"));
  fillSelect($("oExchange"));

  $("connectBtn").onclick = async () => {
    await post("/api/connect", { setting: {} });
    log("已触发引擎连接");
  };

  $("subForm").onsubmit = async (e) => {
    e.preventDefault();
    const symbol = $("subSymbol").value.trim();
    if (!symbol) return;
    await post("/api/subscribe", { symbol, exchange: $("subExchange").value });
    $("subSymbol").value = "";
  };

  // 方向 / 开平 分段按钮
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

  $("orderForm").onsubmit = async (e) => {
    e.preventDefault();
    const symbol = $("oSymbol").value.trim();
    if (!symbol) { log("请填写下单合约", "error"); return; }
    const r = await post("/api/order", {
      symbol,
      exchange: $("oExchange").value,
      direction: state.dir,
      offset: state.off,
      type: "LIMIT",
      price: Number($("oPrice").value || 0),
      volume: Number($("oVolume").value || 1),
    });
    if (r && r.ok) log(`委托已发送 → ${r.orderid}`);
  };

  // 撤单（事件委托）
  $("orderTbl").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-cancel]");
    if (!btn) return;
    post("/api/cancel", { orderid: btn.dataset.cancel, symbol: btn.dataset.sym, exchange: btn.dataset.ex });
  });
}

// ---------- 启动 ----------
async function init() {
  bind();
  renderQuotes(); renderOrders(); renderPositions(); renderTrades();
  try {
    const st = await (await fetch("/api/status")).json();
    $("mode").textContent = st.mode;
  } catch (_) {}
  connectWS();
}

init();
