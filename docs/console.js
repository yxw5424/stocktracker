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
async function testNotify() {
  try {
    const r = await api("/notify/test", { method: "POST" });
    toast(r.sent.length ? "已发测试到:" + r.sent.join("、") : "没有已配置的渠道", r.sent.length > 0);
  } catch (e) { toast("发送失败:" + e.message, false); }
}

// ───────── 启动 ─────────
async function initConsole() {
  try {
    const r = await fetch("/api/ping");
    if (!r.ok) return;
  } catch (e) { return; }   // 非本地(Pages)→ 不启用控制台

  document.getElementById("nav").hidden = false;
  document.querySelectorAll("#nav button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.view)));
  document.getElementById("wl-add").addEventListener("click", addWatch);
  document.getElementById("rule-parse").addEventListener("click", parseRule);
  document.getElementById("notify-test").addEventListener("click", testNotify);
  loadWatchlist(); loadRules(); loadNotify();
}

initConsole();
