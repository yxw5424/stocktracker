// 看板:读取 data/data.json + data/alerts_history.json,渲染卡片(分时/日K 可切换),60 秒自动刷新。
const charts = {};      // index -> echarts 实例
const viewState = {};   // index -> 'intraday' | 'daily'
let lastData = null;

async function getJSON(url) {
  try {
    const r = await fetch(`${url}?_=${Date.now()}`, { cache: "no-store" });
    return await r.json();
  } catch (e) {
    return null;
  }
}

function renderChart(i, view) {
  const t = (lastData.targets || [])[i];
  if (!t) return;
  const el = document.getElementById(`chart-${i}`);
  if (!el) return;
  const chart = charts[i] || (charts[i] = echarts.init(el));
  const up = (t.metrics?.pct_window || 0) >= 0;
  const upColor = "#ff4d4f", downColor = "#18b89a";

  if (view === "daily") {
    const d = t.views?.daily || [];
    chart.setOption({
      grid: { left: 46, right: 12, top: 10, bottom: 22 },
      xAxis: { type: "category", data: d.map(x => x.d), axisLabel: { fontSize: 10, color: "#8b97b0" } },
      yAxis: { type: "value", scale: true, axisLabel: { fontSize: 10, color: "#8b97b0" } },
      tooltip: { trigger: "axis" },
      series: [{
        type: "candlestick",
        data: d.map(x => [x.o, x.c, x.l, x.h]),
        itemStyle: { color: upColor, color0: downColor, borderColor: upColor, borderColor0: downColor },
      }],
    }, true);
  } else {
    const s = t.views?.intraday || [];
    chart.setOption({
      grid: { left: 46, right: 12, top: 10, bottom: 22 },
      legend: { right: 8, top: 0, textStyle: { color: "#8b97b0", fontSize: 10 }, itemWidth: 14, itemHeight: 8 },
      xAxis: { type: "category", data: s.map(x => x.t), axisLabel: { fontSize: 10, color: "#8b97b0" } },
      yAxis: { type: "value", scale: true, axisLabel: { fontSize: 10, color: "#8b97b0" } },
      tooltip: { trigger: "axis" },
      series: [
        {
          name: "价格", type: "line", data: s.map(x => x.p), smooth: false, showSymbol: false,
          lineStyle: { width: 1.6, color: up ? upColor : downColor },
          areaStyle: { color: up ? "rgba(255,77,79,.08)" : "rgba(24,184,154,.08)" },
        },
        { name: "均价", type: "line", data: s.map(x => x.avg), smooth: false, showSymbol: false,
          lineStyle: { width: 1, color: "#f5a623" } },
      ],
    }, true);
  }
}

function switchView(i, view) {
  viewState[i] = view;
  document.querySelectorAll(`#card-${i} .view-toggle button`).forEach(b => {
    b.classList.toggle("active", b.dataset.view === view);
  });
  renderChart(i, view);
}

function render(data, hist) {
  lastData = data;
  const meta = document.getElementById("meta");
  if (!data) { meta.textContent = "暂无数据,等待第一次运行…"; return; }

  const modeTxt = data.mode === "fast" ? "⚡ 高频模式" : "🟢 正常模式";
  const openTxt = data.market_open ? "开盘中" : "休市";
  const demoTag = data.demo ? '<span style="color:#f5a623;font-weight:600">⚠️ 演示数据 ｜ </span>' : "";
  meta.innerHTML = demoTag + `更新 ${data.updated_at} ｜ ${openTxt} ｜ ${modeTxt} ｜ 汇报间隔 ${data.next_interval_minutes ?? "-"} 分钟`;

  const cards = document.getElementById("cards");
  // 关键:重建卡片前先销毁旧 ECharts 实例并清表,否则实例仍指向被删除的旧 DOM → 刷新后图消失
  Object.values(charts).forEach(c => { try { c.dispose(); } catch (e) {} });
  for (const k in charts) delete charts[k];
  cards.innerHTML = "";
  (data.targets || []).forEach((t, i) => {
    const m = t.metrics || {};
    const up = (m.pct_window || 0) >= 0;
    const hasIntraday = (t.views?.intraday || []).length > 0;
    const hasDaily = (t.views?.daily || []).length > 0;
    // 默认视图:有分时用分时,否则日K
    const def = viewState[i] || (hasIntraday ? "intraday" : "daily");
    viewState[i] = def;

    const div = document.createElement("div");
    div.className = "card";
    div.id = `card-${i}`;
    div.innerHTML = `
      <div class="card-head">
        <span class="name">${t.name} <small>${t.code}</small>${t.stale ? ' <small style="color:#f5a623">·旧</small>' : ""}</span>
        <span class="price ${up ? "up" : "down"}">${m.price ?? "-"}</span>
      </div>
      <div class="stats">
        <span>${m.pct_window_minutes ?? 60}分 <b class="${up ? "up" : "down"}">${m.pct_window >= 0 ? "+" : ""}${m.pct_window ?? "-"}%</b></span>
        <span>斜率 <b>${m.slope >= 0 ? "+" : ""}${m.slope ?? "-"}%/h</b></span>
        <span>量比 <b>${m.vol_ratio ?? "-"}</b></span>
      </div>
      <div class="alerts">${
        (t.alerts && t.alerts.length)
          ? t.alerts.map(a => `<span class="badge ${a.level}">${a.message}</span>`).join("")
          : '<span class="badge calm">无异动</span>'
      }</div>
      <div class="points">${
        (t.points || []).map(p => `<span class="pt pt-${p.grade}" title="沪深300历史·扣成本·非投资建议">${p.desc} · ${p.kind}${p.edge != null ? ` <b>${p.horizon}EDGE ${p.edge >= 0 ? "+" : ""}${p.edge}% 胜率${p.win_rate}%</b>` : ""}</span>`).join("")
      }</div>
      <div class="view-toggle">
        <button data-view="intraday" ${hasIntraday ? "" : "disabled"}>分时</button>
        <button data-view="daily" ${hasDaily ? "" : "disabled"}>日K</button>
      </div>
      <div class="chart" id="chart-${i}"></div>`;
    cards.appendChild(div);

    div.querySelectorAll(".view-toggle button").forEach(b => {
      b.classList.toggle("active", b.dataset.view === def);
      b.addEventListener("click", () => { if (!b.disabled) switchView(i, b.dataset.view); });
    });
    renderChart(i, def);
  });

  const ul = document.getElementById("history");
  ul.innerHTML = "";
  (hist || []).slice().reverse().slice(0, 30).forEach(h => {
    const li = document.createElement("li");
    li.textContent = `${h.time}  ${h.code}  ${h.message}`;
    ul.appendChild(li);
  });
}

function renderMarket(m) {
  const el = document.getElementById("market");
  if (!m || !m.regime) { el.innerHTML = ""; return; }
  const r = m.regime, b = m.breadth || {};
  const tone = r.tone === "risk_on" ? "up" : (r.tone === "risk_off" ? "down" : "");
  const idx = (m.indices || []).map(i =>
    `<span class="idx ${i.pct >= 0 ? "up" : "down"}">${i.name} ${i.pct >= 0 ? "+" : ""}${i.pct}%</span>`).join("");
  const sec = (m.sectors_top || []).map(s =>
    `<span class="idx ${s.pct >= 0 ? "up" : "down"}">${s.sector} ${s.pct >= 0 ? "+" : ""}${s.pct}%</span>`).join("");
  el.innerHTML = `
    <div class="regime">
      <span class="regime-label ${tone}">${r.label}</span>
      <span class="money">赚钱效应 <b>${r.money_effect}</b>/100</span>
      <span class="breadth">涨<b class="up">${b.adv}</b> 跌<b class="down">${b.dec}</b> ｜ 涨停<b class="up">${b.limit_up}</b> 跌停<b class="down">${b.limit_down}</b> ｜ 总额 ${b.total_amount_yi} 亿</span>
    </div>
    <div class="indices">${idx}</div>
    ${sec ? `<div class="indices"><span class="strip-label">领涨板块</span>${sec}</div>` : ""}`;
}

function renderSignals(sigs) {
  const ul = document.getElementById("signals");
  ul.innerHTML = "";
  const dimName = { market: "市场", stock: "个股", watchlist: "自选", sector: "板块", rule: "规则", news: "消息" };
  (sigs || []).forEach(s => {
    const li = document.createElement("li");
    li.className = `sig ${s.level}`;
    li.innerHTML = `<span class="sig-dim sig-${s.dim}">${dimName[s.dim] || s.dim}</span><span class="sig-msg">${s.message}</span>`;
    ul.appendChild(li);
  });
}

async function load() {
  const [data, hist, market] = await Promise.all([
    getJSON("data/data.json"),
    getJSON("data/alerts_history.json"),
    getJSON("data/market.json"),
  ]);
  render(data, hist || []);
  renderMarket(market);
  renderSignals(market ? market.signals : []);
}

window.addEventListener("resize", () => Object.values(charts).forEach(c => c.resize()));
load();
setInterval(load, 60000);
