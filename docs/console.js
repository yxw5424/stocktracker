// 本地控制台:仅当检测到本地 API(/api/ping)才启用;GitHub Pages 上静默隐身。
// 主路径:大白话 → 可编辑 DSL chips → 保存为常驻规则。表单是精修层,不是主角。

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.status;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, ok = true) {
  let box = document.getElementById("toast");
  if (!box) { box = document.createElement("div"); box.id = "toast"; document.body.appendChild(box); }
  const t = document.createElement("div");
  t.className = "toast " + (ok ? "ok" : "err");
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

const INDICATORS = {
  watchlist: [["pct_change", "涨跌幅%"], ["volume_ratio", "量比"], ["slope", "斜率%/h"], ["price", "价格"]],
  all: [["pct_change", "涨跌幅%"], ["amount", "成交额"], ["amplitude", "振幅%"], ["price", "价格"]],
};
const OPS = [[">=", "≥"], [">", ">"], ["<=", "≤"], ["<", "<"], ["==", "="]];

function switchTab(view) {
  document.getElementById("board").hidden = view !== "board";
  document.getElementById("console").hidden = view !== "console";
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("active", b.dataset.view === view));
}

// ───────── 自选管理 ─────────
async function loadWatchlist() {
  const d = await api("/watchlist");
  const ul = document.getElementById("wl-list");
  ul.innerHTML = "";
  d.targets.forEach(t => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${t.name} <small>${t.code}</small></span><button class="x">✕</button>`;
    li.querySelector(".x").addEventListener("click", () => delWatch(t.code));
    ul.appendChild(li);
  });
}
async function addWatch() {
  const code = document.getElementById("wl-code").value.trim();
  const name = document.getElementById("wl-name").value.trim();
  try {
    await api("/watchlist", { method: "POST", body: JSON.stringify({ code, name }) });
    toast("已加入自选 " + code);
    document.getElementById("wl-code").value = "";
    document.getElementById("wl-name").value = "";
    loadWatchlist();
  } catch (e) { toast("加入失败:" + e.message, false); }
}
async function delWatch(code) {
  try { await api("/watchlist/" + code, { method: "DELETE" }); toast("已移除 " + code); loadWatchlist(); }
  catch (e) { toast("移除失败:" + e.message, false); }
}
async function refreshBoard() {
  const btn = document.getElementById("wl-refresh");
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = "刷新中…(约20秒)";
  try {
    const r = await api("/refresh", { method: "POST" });
    toast(`已刷新,看板 ${r.n} 只 —— 去首页 Ctrl+F5`);
  } catch (e) { toast("刷新失败:" + e.message, false); }
  finally { btn.disabled = false; btn.textContent = old; }
}

// ───────── 规则制定器(NL → chips → 保存) ─────────
let draft = null;

async function parseRule() {
  const text = document.getElementById("rule-nl").value.trim();
  if (!text) return;
  try { draft = await api("/rule/parse", { method: "POST", body: JSON.stringify({ text }) }); renderDraft(); }
  catch (e) { toast("解析失败:" + e.message, false); }
}

function renderDraft() {
  const box = document.getElementById("rule-draft");
  box.hidden = false;
  box.innerHTML = `
    <div class="draft-head">我理解成了这些硬指标(可逐条改):</div>
    <div class="draft-row">
      范围 <select id="d-scope">${["watchlist", "all"].map(s => `<option ${draft.scope === s ? "selected" : ""}>${s}</option>`).join("")}</select>
      逻辑 <select id="d-logic">${["AND", "OR"].map(l => `<option ${draft.logic === l ? "selected" : ""}>${l}</option>`).join("")}</select>
    </div>
    <div id="d-chips" class="chips"></div>
    <button id="d-add" class="btn btn-ghost">+ 加条件</button>
    <div class="draft-row">名称 <input id="d-name" class="field" value="${draft.name}">
      冷却 <input id="d-cd" class="field num" type="number" value="${draft.cooldown_min}"> 分钟</div>
    ${(draft.unsupported && draft.unsupported.length) ? `<div class="warn">⚠️ 暂不支持:${draft.unsupported.join(";")}</div>` : ""}
    <div class="draft-row"><button id="d-save" class="btn btn-primary">保存规则</button>
      <span class="hint">保存后引擎下一轮即执行</span></div>`;
  renderChips();
  document.getElementById("d-scope").addEventListener("change", e => { draft.scope = e.target.value; fixIndicators(); renderChips(); });
  document.getElementById("d-logic").addEventListener("change", e => { draft.logic = e.target.value; });
  document.getElementById("d-add").addEventListener("click", () => {
    draft.conditions.push({ indicator: INDICATORS[draft.scope][0][0], op: ">=", value: 0 });
    renderChips();
  });
  document.getElementById("d-save").addEventListener("click", saveRule);
}

function fixIndicators() {
  const allowed = INDICATORS[draft.scope].map(x => x[0]);
  draft.conditions.forEach(c => { if (!allowed.includes(c.indicator)) c.indicator = allowed[0]; });
}

function renderChips() {
  const wrap = document.getElementById("d-chips");
  wrap.innerHTML = "";
  const inds = INDICATORS[draft.scope] || INDICATORS.watchlist;
  draft.conditions.forEach((c, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `
      <select class="c-ind">${inds.map(([v, l]) => `<option value="${v}" ${c.indicator === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <select class="c-op">${OPS.map(([v, l]) => `<option value="${v}" ${c.op === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <input class="c-val" type="number" value="${c.value}">
      <button class="c-x">✕</button>`;
    chip.querySelector(".c-ind").addEventListener("change", e => c.indicator = e.target.value);
    chip.querySelector(".c-op").addEventListener("change", e => c.op = e.target.value);
    chip.querySelector(".c-val").addEventListener("input", e => c.value = parseFloat(e.target.value));
    chip.querySelector(".c-x").addEventListener("click", () => { draft.conditions.splice(i, 1); renderChips(); });
    wrap.appendChild(chip);
  });
  if (!draft.conditions.length) wrap.innerHTML = '<span class="hint">没识别到硬指标,点「+ 加条件」手动加,或换个说法</span>';
}

async function saveRule() {
  const rule = {
    name: document.getElementById("d-name").value.trim() || "新规则",
    scope: document.getElementById("d-scope").value,
    logic: document.getElementById("d-logic").value,
    conditions: draft.conditions,
    cooldown_min: parseInt(document.getElementById("d-cd").value) || 30,
  };
  try {
    const r = await api("/rules", { method: "POST", body: JSON.stringify(rule) });
    toast("规则已保存:" + r.id);
    document.getElementById("rule-draft").hidden = true;
    document.getElementById("rule-nl").value = "";
    loadRules();
  } catch (e) { toast("保存失败:" + e.message, false); }
}

async function loadRules() {
  const d = await api("/rules");
  const ul = document.getElementById("rule-list");
  ul.innerHTML = "";
  (d.rules || []).forEach(r => {
    const conds = (r.conditions || []).map(c => `${c.indicator}${c.op}${c.value}`).join(`  ${r.logic}  `);
    const li = document.createElement("li");
    li.innerHTML = `<span><b>${r.name}</b> <small class="badge-scope">${r.scope}</small> ${conds}</span><button class="x">✕</button>`;
    li.querySelector(".x").addEventListener("click", () => delRule(r.id));
    ul.appendChild(li);
  });
  if (!(d.rules || []).length) ul.innerHTML = '<li class="hint">还没有规则。上面用大白话写一条试试。</li>';
}
async function delRule(id) {
  try { await api("/rules/" + id, { method: "DELETE" }); toast("已删除规则 " + id); loadRules(); }
  catch (e) { toast("删除失败:" + e.message, false); }
}

// ───────── 通知 ─────────
async function loadNotify() {
  const d = await api("/notify");
  const ul = document.getElementById("notify-list");
  ul.innerHTML = "";
  d.channels.forEach(c => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="dot ${c.configured ? "on" : "off"}"></span>${c.name} <small>${c.env}</small> — ${c.configured ? "已配置" : "未配置(设环境变量后重启)"}`;
    ul.appendChild(li);
  });
}
// ───────── 一键回测 ─────────
async function loadBtSignals() {
  try {
    const d = await api("/signals");
    document.getElementById("bt-signal").innerHTML =
      d.signals.map(s => `<option value="${s.key}">${s.desc}</option>`).join("");
  } catch (e) {}
}
async function runBacktest() {
  const code = document.getElementById("bt-code").value.trim();
  const signal = document.getElementById("bt-signal").value;
  const box = document.getElementById("bt-result");
  box.innerHTML = '<span class="hint">回测中…</span>';
  try {
    const r = await api("/backtest", { method: "POST", body: JSON.stringify({ code, signal }) });
    if (r.error) { box.innerHTML = `<span class="warn">${r.error}</span>`; return; }
    let html = `<div class="hint">${r.code} 「${r.signal_desc}」 近${r.sample_days}根·出现${r.occurrences}次</div>`;
    html += '<table class="scorecard"><thead><tr><th>持有</th><th>样本外N</th><th>胜率(区间)</th><th>均值</th><th>盈亏比</th></tr></thead><tbody>';
    for (const [h, hs] of Object.entries(r.horizons)) {
      const o = hs.oos;
      if (!o) { html += `<tr><td>${h}</td><td colspan="4" class="hint">样本外无样本</td></tr>`; continue; }
      const flag = o.reliable ? "" : " ⚠少";
      html += `<tr><td>${h}</td><td>${o.n}${flag}</td><td>${o.win_rate}% [${o.win_ci[0]}~${o.win_ci[1]}]</td><td>${o.avg}%</td><td>${o.profit_factor ?? "-"}</td></tr>`;
    }
    html += `</tbody></table><div class="hint">${(r.caveats || []).join(";")}</div>`;
    box.innerHTML = html;
  } catch (e) { box.innerHTML = `<span class="warn">回测失败:${e.message}</span>`; }
}

async function testNotify() {
  try {
    const r = await api("/notify/test", { method: "POST" });
    toast(r.sent.length ? "已发测试到:" + r.sent.join("、") : "没有已配置的渠道", r.sent.length > 0);
  } catch (e) { toast("发送失败:" + e.message, false); }
}

// ───────── 启动 ─────────
function mountConsole() {
  document.getElementById("nav").hidden = false;
  document.querySelectorAll("#nav button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.view)));
  document.getElementById("wl-add").addEventListener("click", addWatch);
  document.getElementById("wl-refresh").addEventListener("click", refreshBoard);
  document.getElementById("rule-parse").addEventListener("click", parseRule);
  document.getElementById("bt-run").addEventListener("click", runBacktest);
  document.getElementById("notify-test").addEventListener("click", testNotify);
  loadWatchlist(); loadRules(); loadNotify(); loadBtSignals();
}

function showLogin() {
  const d = document.createElement("div");
  d.id = "login-overlay";
  d.innerHTML = `<div class="login-box">
      <h3>📈 登录</h3>
      <input id="login-pw" type="password" class="field" placeholder="访问密码" autofocus>
      <button id="login-btn" class="btn btn-primary">进入控制台</button>
      <div id="login-err" class="warn"></div>
      <div class="hint">看板可直接浏览;操作需登录。</div>
    </div>`;
  document.body.appendChild(d);
  const go = async () => {
    try {
      await api("/login", { method: "POST", body: JSON.stringify({ password: document.getElementById("login-pw").value }) });
      d.remove();
      mountConsole();
    } catch (e) { document.getElementById("login-err").textContent = "密码错误"; }
  };
  document.getElementById("login-btn").addEventListener("click", go);
  document.getElementById("login-pw").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
}

async function initConsole() {
  try {
    const r = await fetch("/api/ping");
    if (!r.ok) return;
  } catch (e) { return; }   // 非本地/无后端(Pages)→ 不启用控制台

  let me = { auth_required: false, authed: true };
  try { me = await api("/me"); } catch (e) {}
  if (me.auth_required && !me.authed) showLogin();   // 需要登录 → 弹密码框
  else mountConsole();
}

initConsole();
