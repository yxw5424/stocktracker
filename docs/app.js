// 看板:读取 data/data.json + data/alerts_history.json,渲染卡片与折线,60 秒自动刷新。
const charts = {};

async function getJSON(url) {
  try {
    const r = await fetch(`${url}?_=${Date.now()}`, { cache: "no-store" });
    return await r.json();
  } catch (e) {
    return null;
  }
}

function render(data, hist) {
  const meta = document.getElementById("meta");
  if (!data) { meta.textContent = "暂无数据,等待第一次运行…"; return; }

  const modeTxt = data.mode === "fast" ? "⚡ 高频模式" : "🟢 正常模式";
  const openTxt = data.market_open ? "开盘中" : "休市";
  meta.innerHTML =
    `更新 ${data.updated_at} ｜ ${openTxt} ｜ ${modeTxt} ｜ 汇报间隔 ${data.next_interval_minutes ?? "-"} 分钟`;

  const cards = document.getElementById("cards");
  cards.innerHTML = "";
  (data.targets || []).forEach((t, i) => {
    const m = t.metrics || {};
    const up = (m.pct_window || 0) >= 0;
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `
      <div class="card-head">
        <span class="name">${t.name} <small>${t.code}</small></span>
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
      <div class="chart" id="chart-${i}"></div>`;
    cards.appendChild(div);

    const el = document.getElementById(`chart-${i}`);
    const chart = charts[i] || (charts[i] = echarts.init(el));
    const xs = (t.series || []).map(p => p.t);
    const ys = (t.series || []).map(p => p.p);
    const color = up ? "#ff4d4f" : "#18b89a";
    chart.setOption({
      grid: { left: 44, right: 12, top: 10, bottom: 22 },
      xAxis: { type: "category", data: xs, axisLabel: { fontSize: 10, color: "#8b97b0" } },
      yAxis: { type: "value", scale: true, axisLabel: { fontSize: 10, color: "#8b97b0" } },
      tooltip: { trigger: "axis" },
      series: [{
        type: "line", data: ys, smooth: true, showSymbol: false,
        lineStyle: { width: 2, color },
        areaStyle: { color: up ? "rgba(255,77,79,.10)" : "rgba(24,184,154,.10)" },
      }],
    });
  });

  const ul = document.getElementById("history");
  ul.innerHTML = "";
  (hist || []).slice().reverse().slice(0, 30).forEach(h => {
    const li = document.createElement("li");
    li.textContent = `${h.time}  ${h.code}  ${h.message}`;
    ul.appendChild(li);
  });
}

async function load() {
  const [data, hist] = await Promise.all([
    getJSON("data/data.json"),
    getJSON("data/alerts_history.json"),
  ]);
  render(data, hist || []);
}

window.addEventListener("resize", () => Object.values(charts).forEach(c => c.resize()));
load();
setInterval(load, 60000);
