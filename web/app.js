/* Ledger web app. Served by serve_for_aptiq.py; talks only to that server.
 * No build step, no dependencies. All text from SMS goes through esc(). */
"use strict";

// ---------------------------------------------------------------- categories (mirrors app/ledger/categories.py)
const CATS = [
  ["Food & Dining", "restaurant", "#FF6B6B"], ["Groceries", "local_grocery_store", "#4ECDC4"],
  ["Shopping", "shopping_bag", "#FF3EA5"], ["Transport", "directions_car", "#00F0FF"],
  ["Travel", "flight_takeoff", "#6EC1FF"], ["Utilities", "bolt", "#FFB020"],
  ["Entertainment", "movie", "#B98CFF"], ["Health", "medical_services", "#00FFA3"],
  ["Rent", "home", "#8B5CF6"], ["Insurance", "shield", "#4FC3FF"],
  ["Home Loan EMI", "account_balance", "#FF5C7A"], ["Home Loan Prepayment", "savings", "#FF8FA3"],
  ["Home Loan Insurance EMI", "health_and_safety", "#FFB3C1"], ["Home Loan Insurance Prepayment", "health_and_safety", "#FFC9D4"],
  ["Investments", "trending_up", "#00E5B3"], ["FnF Transfer", "family_restroom", "#7EB0FF"],
  ["UPI Spend", "qr_code_scanner", "#A0C4FF"], ["Cash Withdrawal", "local_atm", "#C8E6A0"],
  ["Fees & Tax", "receipt_long", "#E0A96D"], ["Salary", "payments", "#9DFFB0"],
  ["Interest / Dividend", "percent", "#9DFFB0"], ["Refund", "undo", "#9DFFB0"],
  ["Other Income", "call_received", "#9DFFB0"], ["Other", "category", "#9B9BA6"],
  ["Needs Review", "help", "#FFB020"],
].map(([name, icon, color]) => ({ name, icon, color }));
const LEGACY = { "Friends Transfer": "FnF Transfer", "Family": "FnF Transfer", "Personal Transfer": "FnF Transfer" };
const INCOME_CATS = new Set(["Salary", "FnF Transfer", "Interest / Dividend", "Refund", "Investments", "Other Income"]);
const INCOME_ONLY = new Set(["Salary", "Interest / Dividend", "Refund", "Other Income"]);
const NEEDS_REVIEW = "Needs Review";

function cat(name) {
  const n = LEGACY[name] || name || NEEDS_REVIEW;
  return CATS.find(c => c.name === n) || { name: n, icon: "label", color: "#9B9BA6" };
}
function choicesFor(income) {
  return CATS.filter(c => c.name !== NEEDS_REVIEW && (income ? INCOME_CATS.has(c.name) : !INCOME_ONLY.has(c.name)));
}

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sym = (name, extra = "") => `<span class="sym" aria-hidden="true" ${extra}>${name}</span>`;
const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr = v => inr0.format(v || 0);
function compact(v) {
  const a = Math.abs(v);
  const s = a >= 1e7 ? (a / 1e7).toFixed(1) + "Cr" : a >= 1e5 ? (a / 1e5).toFixed(1) + "L"
    : a >= 1e3 ? (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "K" : a.toFixed(0);
  return (v < 0 ? "-" : "") + "₹" + s;
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthLabel(ym, long = false) {
  const [y, m] = ym.split("-").map(Number);
  return long ? new Date(y, m - 1).toLocaleString("en", { month: "long", year: "numeric" }) : `${MONTHS[m - 1]} ${y}`;
}
const monthShort = ym => MONTHS[Number(ym.split("-")[1]) - 1];
const ymOf = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
function relative(ts) {
  const s = (Date.now() - ts) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
const store = {
  get(k, d) { try { const v = localStorage.getItem("ledger." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ledger." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

// ---------------------------------------------------------------- API
async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* not json */ }
  if (!res.ok) throw new Error((data && data.error) || `Server returned ${res.status}`);
  return data;
}

// ---------------------------------------------------------------- state
const S = {
  txns: [], loaded: false, error: null, demo: false, syncedAt: null, syncing: false,
  month: store.get("month", null), catFilter: null, dirFilter: null,
  dev: store.get("devManualAdd", false),
};

async function loadTransactions() {
  const data = await api("GET", "/api/transactions");
  S.txns = data.transactions.map(t => ({ ...t, category: LEGACY[t.category] || t.category }))
    .sort((a, b) => b.txn_time.localeCompare(a.txn_time));
  S.demo = !!data.demo;
  S.syncedAt = Date.now();
  S.loaded = true;
  S.error = null;
}
const months = () => [...new Set(S.txns.map(t => t.month))].sort().reverse();
function currentMonth() {
  const ms = months();
  if (S.month && ms.includes(S.month)) return S.month;
  return ms[0] || ymOf(new Date());
}
const isIncome = t => t.direction === "income";

// ---------------------------------------------------------------- router
function route() {
  const [path, query] = (location.hash.slice(1) || "/expenses").split("?");
  const parts = path.split("/").filter(Boolean);
  return { name: parts[0] || "expenses", arg: parts[1] ? decodeURIComponent(parts[1]) : null, q: new URLSearchParams(query || "") };
}
const go = h => { location.hash = h; };
window.addEventListener("hashchange", () => { closeSheet(); render(); window.scrollTo(0, 0); });

function renderNav(active) {
  const tabs = [["expenses", "pie_chart", "Expenses"], ["savings", "savings", "Savings"], ["settings", "settings", "Settings"]];
  const main = tabs.some(t => t[0] === active);
  $("#nav").innerHTML = main ? `<div class="nav-inner">${tabs.map(([id, ic, label]) =>
    `<button class="tab ${id === active ? "on" : ""}" data-go="#/${id}" aria-label="${label}">${sym(ic)}<span class="t">${label}</span></button>`).join("")}</div>` : "";
}

function render() {
  const r = route();
  renderNav(r.name);
  const app = $("#app");
  if (!S.loaded && !["savings", "settings"].includes(r.name)) {
    app.innerHTML = S.error ? errorView(S.error) : `<div class="loading">Loading…</div>`;
    return;
  }
  const views = { expenses: viewExpenses, review: viewReview, analytics: r.arg ? viewCategoryAnalytics : viewOverview, savings: viewSavings, settings: viewSettings };
  (views[r.name] || viewExpenses)(app, r);
}
function errorView(msg) {
  return `<div class="pad"><div class="card"><div class="card-title">Can't reach the ledger server</div>
    <p class="hint" style="font-size:13px">${esc(msg)}</p>
    <button class="btn ghost" data-act="retry" style="width:100%">Retry</button></div></div>`;
}

// ---------------------------------------------------------------- Expenses
function monthSummary(txns) {
  let expense = 0, credit = 0;
  const out = {}, inn = {};
  for (const t of txns) {
    if (isIncome(t)) { credit += t.amount; inn[t.category] = (inn[t.category] || 0) + t.amount; }
    else { expense += t.amount; out[t.category] = (out[t.category] || 0) + t.amount; }
  }
  return { expense, credit, out, inn, count: txns.length };
}

function viewExpenses(app, r) {
  const linked = r && r.q.get("month");  // #/expenses?month=YYYY-MM opens that month
  if (linked && linked !== S.month && months().includes(linked)) { S.month = linked; store.set("month", linked); }
  const m = currentMonth();
  const all = S.txns.filter(t => t.month === m);
  const sum = monthSummary(all);
  const credits = S.dirFilter === "income";
  const breakdown = credits ? sum.inn : sum.out;
  const total = credits ? sum.credit : sum.expense;
  const shown = all.filter(t => (!S.dirFilter || t.direction === S.dirFilter) && (!S.catFilter || t.category === S.catFilter));
  const reviewCount = all.filter(t => t.category === NEEDS_REVIEW).length;

  app.innerHTML = `
    <div class="header">
      <div class="title"><h1>Expenses</h1><div class="sub">${S.syncedAt ? "Synced " + relative(S.syncedAt) : ""}</div></div>
      ${S.dev ? `<button class="sq mint" data-act="add" aria-label="Add transaction">${sym("add")}</button>` : ""}
      <button class="sq violet" data-go="#/analytics" aria-label="Analytics">${sym("insights")}</button>
      <button class="sq amber wide" data-act="review" aria-label="Review">${sym("fact_check")}${reviewCount ? `<span class="count">${reviewCount}</span>` : ""}</button>
      <button class="sq cyan" data-act="refresh" aria-label="Refresh from SMS">${S.syncing ? '<span class="sym spin" aria-hidden="true">progress_activity</span>' : sym("refresh")}</button>
    </div>
    ${S.demo ? `<div class="demo-badge">Demo data - every name and number here is made up.</div>` : ""}
    <div class="pills">${months().map(x => `<button class="pill ${x === m ? "on" : ""}" data-month="${x}">${monthLabel(x)}</button>`).join("")}</div>
    <div class="pad16" style="margin-top:14px">
      <div class="summary">
        <div class="eyebrow">${monthLabel(m, true)}</div>
        <button data-act="dir" data-dir="expense" style="display:block;text-align:left">
          <div class="big ${credits ? "dim" : ""}">${inr(sum.expense)}</div>
          <div class="below">total spent ${S.dirFilter === "expense" ? "✓" : "›"}</div>
        </button>
        <div class="row2">
          <button class="stat ${credits ? "on" : ""}" data-act="dir" data-dir="income">${sym("arrow_downward", 'style="color:var(--mint);font-size:16px"')}
            <div><div class="v">${inr(sum.credit)}</div><div class="k">received ${credits ? "✓" : "›"}</div></div></button>
          <div class="stat">${sym("receipt_long", 'style="color:var(--cyan);font-size:16px"')}
            <div><div class="v">${sum.count}</div><div class="k">transactions</div></div></div>
        </div>
      </div>
    </div>
    ${Object.keys(breakdown).length ? `<div class="pad16" style="margin-top:16px"><div class="card">
      <div style="display:flex;gap:8px;align-items:baseline;margin-bottom:14px">
        <div class="card-title">${credits ? "Where it came from" : "Where it went"}</div><div class="hint">· tap to filter</div></div>
      ${Object.entries(breakdown).sort((a, b) => b[1] - a[1]).map(([name, v]) => catRow(name, v, total)).join("")}
    </div></div>` : ""}
    <div class="list-head" id="list-head">
      <span class="lbl">TRANSACTIONS</span><span class="badge">${shown.length}</span><span class="grow"></span>
      ${S.dirFilter ? filterPill(S.dirFilter === "income" ? "Received" : "Spent", "dir", "#00FFA3", "south_west") : ""}
      ${S.catFilter ? filterPill(S.catFilter, "cat", cat(S.catFilter).color, cat(S.catFilter).icon) : ""}
      ${!S.dirFilter && !S.catFilter ? `<span class="hint">tap to recategorise</span>` : ""}
    </div>
    <div class="txns">${txList(shown)}</div>`;
}
function catRow(name, v, total) {
  const c = cat(name), share = total ? v / total : 0, on = S.catFilter === name;
  return `<div class="cat-row" role="button" tabindex="0" data-act="cat" data-cat="${esc(name)}">
    <div class="cat-top">
      <span class="ic" style="color:${c.color};background:${c.color}${on ? "3d" : "29"};border-color:${c.color}${on ? "bf" : "59"}">${sym(c.icon)}</span>
      <span class="cat-name" style="${on ? `color:${c.color}` : ""}">${esc(name)}</span>
      <span class="cat-amt">${inr(v)}</span><span class="cat-pct">${Math.round(share * 100)}%</span>
      <button class="cat-an" data-act="cat-analytics" data-cat="${esc(name)}" aria-label="Analytics for ${esc(name)}" style="color:${c.color}">${sym("insights")}</button>
    </div>
    <div class="bar"><i style="width:${Math.max(2, share * 100)}%;background:${c.color}"></i></div></div>`;
}
const filterPill = (label, kind, color, icon) =>
  `<button class="fpill" data-act="clear-${kind}" style="color:${color};background:${color}26;border-color:${color}80">${sym(icon)}<span>${esc(label)}</span>${sym("close")}</button>`;

function txList(txns) {
  if (!txns.length) return `<div class="empty">No transactions this month.</div>`;
  const today = new Date().toISOString().slice(0, 10);
  const yest = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
  let html = "", last = null;
  for (const t of txns) {
    if (t.txn_date !== last) {
      last = t.txn_date;
      const d = new Date(t.txn_date + "T00:00:00");
      const label = t.txn_date === today ? "Today" : t.txn_date === yest ? "Yesterday"
        : d.toLocaleDateString("en", { weekday: "short", month: "short", day: "numeric" });
      html += `<div class="day">${label}</div>`;
    }
    html += txRow(t);
  }
  return html;
}
function txRow(t) {
  const c = cat(t.category);
  const time = (t.txn_time || "").slice(11, 16);
  return `<button class="tx" data-act="edit" data-id="${esc(t.txn_id)}">
    <span class="ic lg" style="color:${c.color};background:${c.color}29;border-color:${c.color}59">${sym(c.icon)}</span>
    <span class="mid"><div class="name">${esc(t.merchant || "Unknown")}</div>
      <div class="meta"><span style="color:${c.color}">${esc(c.name)}</span><span class="muted">·</span><span class="muted">${esc(t.payment_mode || "")} ${time}</span></div></span>
    <span class="amt ${isIncome(t) ? "in" : ""}">${isIncome(t) ? "+" : "-"}${inr(t.amount)}</span></button>`;
}

// ---------------------------------------------------------------- sheets
function openSheet(html, onMount) {
  const root = $("#sheet");
  root.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><div class="handle"></div>${html}</div>`;
  root.hidden = false;
  document.body.style.overflow = "hidden";
  onMount && onMount($(".sheet", root));
}
function closeSheet() {
  const root = $("#sheet");
  root.hidden = true;
  root.innerHTML = "";
  document.body.style.overflow = "";
}
$("#sheet").addEventListener("click", e => { if (e.target.id === "sheet") closeSheet(); });

function chipList(choices, selected, attr) {
  return choices.map(c => `<button class="chip ${c.name === selected ? "on" : ""}" ${attr}="${esc(c.name)}"
    style="${c.name === selected ? `color:${c.color};border-color:${c.color}99;background:${c.color}2e` : ""}">
    <span class="sym" style="color:${c.color}">${c.icon}</span><span>${esc(c.name)}</span></button>`).join("");
}

function openEditor(t) {
  let category = t.category, direction = t.direction, applyAll = !!t.payee_key, showRaw = false;
  const manual = t.sender_id === "manual";
  const draw = sh => {
    const c = cat(category);
    sh.innerHTML = `<div class="handle"></div>
      <div class="sheet-head">
        <span class="ic lg" style="color:${c.color};background:${c.color}29;border-color:${c.color}80">${sym(c.icon)}</span>
        <div class="mid"><div class="t">Transaction</div><div class="s">${esc(new Date(t.txn_time.replace(" ", "T")).toLocaleString("en", { weekday: "long", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))}</div></div>
        <button class="icon-btn" data-x="delete" aria-label="Delete" style="color:var(--danger)">${sym("delete")}</button>
      </div>
      <div class="label">Merchant</div><input class="input" id="e-merchant" value="${esc(t.merchant)}" placeholder="Merchant / payee">
      <div class="label">Amount</div>
      <div class="amount-row"><span class="cur">₹</span><input class="input amount" id="e-amount" inputmode="decimal" value="${t.amount}"></div>
      <div class="label">Direction</div>
      <div class="segpick">
        ${[["expense", "arrow_upward", "Expense", "var(--rose)"], ["income", "arrow_downward", "Credit", "var(--mint)"]].map(([d, ic, l, col]) =>
          `<button data-dir="${d}" style="${direction === d ? `color:${col};border-color:${col};background:rgba(255,255,255,.06)` : ""}">${sym(ic)}${l}</button>`).join("")}
      </div>
      <div class="label">Category</div>
      <div class="chips">${chipList(CATS.filter(x => x.name !== NEEDS_REVIEW), category, "data-cat")}</div>
      ${t.payee_key && !manual ? `<div class="toggle-row"><div class="mid"><div class="t">Apply to all from this payee</div>
        <div class="s">Past and future: ${esc(t.payee_key)}</div></div>
        <button class="switch ${applyAll ? "on" : ""}" data-x="all" aria-label="Apply to all from this payee"></button></div>` : ""}
      <div class="label">Details</div>
      <div class="meta-row"><span>Sender</span><span>${esc(t.sender_id || "-")}</span></div>
      <div class="meta-row"><span>Payment mode</span><span>${esc(t.payment_mode || "-")}</span></div>
      <div class="meta-row"><span>Reference</span><span>${esc(t.reference_id || "-")}</span></div>
      <div class="meta-row"><span>Confidence</span><span>${Math.round((t.category_confidence || 0) * 100)}%</span></div>
      <button class="raw-toggle" data-x="raw">${sym(showRaw ? "expand_less" : "expand_more")}Raw message</button>
      ${showRaw ? `<div class="raw">${esc(t.message_body)}</div>` : ""}
      <div class="actions"><button class="btn ghost" data-x="close">Cancel</button>
        <button class="btn primary" data-x="save" style="background:${c.color}">Save</button></div>`;
  };
  openSheet("", sh => {
    draw(sh);
    sh.addEventListener("click", async e => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.cat) { category = b.dataset.cat; keep(sh, draw); }
      else if (b.dataset.dir) { direction = b.dataset.dir; keep(sh, draw); }
      else if (b.dataset.x === "all") { applyAll = !applyAll; keep(sh, draw); }
      else if (b.dataset.x === "raw") { showRaw = !showRaw; keep(sh, draw); }
      else if (b.dataset.x === "close") closeSheet();
      else if (b.dataset.x === "delete") {
        if (!confirm("Hide this transaction? It stays hidden after every rebuild.")) return;
        await act(() => api("DELETE", `/transactions/${encodeURIComponent(t.txn_id)}`), "Deleted");
      } else if (b.dataset.x === "save") {
        const merchant = $("#e-merchant", sh).value.trim(), amount = parseFloat($("#e-amount", sh).value);
        const body = {};
        if (merchant && merchant !== t.merchant) body.merchant = merchant;
        if (category !== t.category) body.category = category;
        if (Number.isFinite(amount) && amount > 0 && amount !== t.amount) body.amount = amount;
        if (direction !== t.direction) body.direction = direction;
        if (!Object.keys(body).length) return closeSheet();
        const all = applyAll && !manual && (body.category || body.merchant);
        body.apply_to = all ? "payee" : "txn";
        await act(() => api("PATCH", `/transactions/${encodeURIComponent(t.txn_id)}`, body),
          all ? `All from ${t.merchant} → ${category} (past and future)` : "Saved");
      }
    });
  });
}
// redraw a sheet but keep what was typed into its inputs
function keep(sh, draw) {
  const vals = [...sh.querySelectorAll("input")].map(i => [i.id, i.value]);
  draw(sh);
  vals.forEach(([id, v]) => { const i = sh.querySelector("#" + id); if (i) i.value = v; });
}
async function act(fn, ok) {
  try {
    await fn();
    closeSheet();
    await loadTransactions();
    render();
    toast(ok);
  } catch (err) { toast(err.message); }
}

function openAdd() {
  const m = currentMonth(), now = new Date();
  const last = new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)), 0);
  const def = ymOf(now) === m || last > now ? now : last;
  let direction = "expense", category = null, mode = "UPI";
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const draw = sh => {
    const income = direction === "income", accent = income ? "var(--mint)" : "var(--rose)";
    sh.innerHTML = `<div class="handle"></div>
      <div class="segpick">${[["expense", "arrow_upward", "Expense"], ["income", "arrow_downward", "Credit"]].map(([d, ic, l]) =>
        `<button data-dir="${d}" style="${direction === d ? `color:${accent};border-color:${accent};background:rgba(255,255,255,.06)` : ""}">${sym(ic)}${l}</button>`).join("")}</div>
      <div class="label">Amount</div><div class="amount-row"><span class="cur" style="color:${accent}">₹</span><input class="input amount" id="a-amount" inputmode="decimal" placeholder="0"></div>
      <div class="label">${income ? "From" : "Paid to"}</div><input class="input" id="a-merchant" placeholder="${income ? "Person / payer" : "Shop / person"}">
      <div class="label">Date</div><input class="input" id="a-date" type="date" max="${iso(now)}" value="${iso(def)}">
      <div class="label">Category</div><div class="chips">${chipList(choicesFor(income), category, "data-cat")}</div>
      <div class="label">Payment mode</div><div class="chips">${["UPI", "Card", "Cash", "Bank Transfer", "Wallet", "Other"].map(x =>
        `<button class="chip ${x === mode ? "on" : ""}" data-mode="${x}"><span>${x}</span></button>`).join("")}</div>
      <div class="label">Note</div><input class="input" id="a-note" placeholder="Optional">
      <div class="actions"><button class="btn primary" data-x="save" style="background:${accent}">${income ? "Add credit" : "Add expense"}</button></div>`;
  };
  openSheet("", sh => {
    draw(sh);
    sh.addEventListener("click", async e => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.dir) { direction = b.dataset.dir; if (category && !choicesFor(direction === "income").some(c => c.name === category)) category = null; keep(sh, draw); }
      else if (b.dataset.cat) { category = b.dataset.cat; keep(sh, draw); }
      else if (b.dataset.mode) { mode = mode === b.dataset.mode ? null : b.dataset.mode; keep(sh, draw); }
      else if (b.dataset.x === "save") {
        const amount = parseFloat($("#a-amount", sh).value), date = $("#a-date", sh).value;
        if (!(amount > 0)) return toast("Enter an amount");
        await act(async () => {
          await api("POST", "/transactions", { date, amount, direction, merchant: $("#a-merchant", sh).value.trim(),
            category: category || NEEDS_REVIEW, payment_mode: mode, note: $("#a-note", sh).value.trim() });
          S.month = date.slice(0, 7); store.set("month", S.month);
        }, `Added to ${new Date(date + "T00:00:00").toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" })}`);
      }
    });
  });
}

// ---------------------------------------------------------------- Review
const R = { month: undefined, payees: null, ai: {}, aiNote: null, aiLoading: false, error: null };
async function loadReview() {
  R.payees = null; R.error = null; R.ai = {}; R.aiNote = null;
  render();
  try {
    const q = R.month ? `?month=${R.month}&limit=80` : "?limit=80";
    R.payees = (await api("GET", "/review" + q)).payees;
  } catch (err) { R.error = err.message; }
  render();
  if (!R.payees || !R.payees.length) return;
  R.aiLoading = true; render();
  try {   // AI is optional: the server always answers 200 with a note on failure
    const res = await api("POST", "/review/suggest", { month: R.month || null });
    R.ai = Object.fromEntries((res.suggestions || []).map(s => [`${s.payee_key}|${s.kind}`, s]));
    R.aiNote = res.error;
  } catch { R.aiNote = "AI suggestions unavailable right now"; }
  R.aiLoading = false;
  render();
}
function viewReview(app, r) {
  if (R.month === undefined) { R.month = r.q.get("month") || null; loadReview(); return; }
  const ms = months();
  const conf = { high: "var(--mint)", medium: "var(--amber)" };
  app.innerHTML = `
    <div class="appbar"><button class="icon-btn" data-act="back" aria-label="Back">${sym("arrow_back_ios_new")}</button><h2><span>Review payees</span></h2>
      <button class="icon-btn" data-act="review-reload" aria-label="Reload">${sym("refresh")}</button></div>
    <div class="pad" style="padding-bottom:8px"><div class="hint" style="font-size:12px">Unknown payees of ₹500+ (smaller UPI payments go to UPI Spend). Tag a payee once - every past and future transaction from it follows.</div></div>
    <div class="chips scroll pad" style="padding-bottom:10px">${[null, ...ms].map(m =>
      `<button class="chip ${m === R.month ? "on" : ""}" data-rmonth="${m || ""}"><span>${m ? monthLabel(m) : "All months"}</span></button>`).join("")}</div>
    <div class="pad16">
      ${R.aiLoading || R.aiNote ? `<div class="note">${sym("auto_awesome", `style="color:${R.aiNote ? "var(--text-3)" : "var(--violet)"}"`)}<span>${esc(R.aiNote || "Claude is suggesting categories…")}</span></div>` : ""}
      ${R.error ? `<div class="empty">${esc(R.error)}</div>` : R.payees === null ? `<div class="loading">Loading…</div>`
        : !R.payees.length ? `<div class="empty">Nothing left to review</div>`
        : R.payees.map((p, i) => {
          const c = cat(p.category), s = R.ai[`${p.payee_key}|${p.kind}`], sc = s && cat(s.category);
          const showKey = p.payee_key.toLowerCase() !== (p.name || "").toLowerCase();
          return `<div class="card payee" role="button" tabindex="0" data-act="tag" data-i="${i}" style="margin-top:10px;padding:14px">
            <div class="top"><span class="ic lg" style="color:${c.color};background:${c.color}24;border-color:transparent">${sym(c.icon)}</span>
              <div class="mid"><div class="nm">${esc(p.name || p.payee_key)}</div>
                <div class="sub">${[showKey ? esc(p.payee_key) : "", `${p.n} txn${p.n === 1 ? "" : "s"}`, `last ${esc(p.last_date)}`,
                  p.kind === "income" ? "money in" : "", p.hint === "person" ? "looks like a person" : ""].filter(Boolean).join(" · ")}</div></div>
              <div class="right"><div class="v">${inr(p.total)}</div><div class="c" style="color:${c.color}">${esc(p.category)}</div></div></div>
            ${s ? `<div class="sugg">${sym("auto_awesome", 'style="color:var(--violet);font-size:14px"')}${sym(sc.icon, `style="color:${sc.color};font-size:14px"`)}
              <div class="mid"><div class="c" style="color:${sc.color}">${esc(s.category)}</div><div class="r" style="color:${conf[s.confidence] || "var(--text-3)"}">${esc(s.confidence)} · ${esc(s.reason)}</div></div>
              <button class="accept" data-act="accept" data-i="${i}">${sym("check")}Accept for all</button></div>` : ""}
          </div>`;
        }).join("")}
    </div>`;
}
async function tagPayee(p, category, name) {
  try {
    await api("POST", "/rules", { payee_key: p.payee_key, category, name: name || undefined });
    R.payees = R.payees.filter(x => x !== p);
    await loadTransactions();
    render();
    toast(`All from ${p.name || p.payee_key} → ${category} (past and future)`);
  } catch (err) { toast("Tag failed: " + err.message); }
}
function openTagSheet(p) {
  const s = R.ai[`${p.payee_key}|${p.kind}`];
  const choices = choicesFor(p.kind === "income").sort((a, b) => rank(a) - rank(b));
  function rank(c) { return c.name === (s && s.category) ? 0 : (p.hint === "person" && c.name === "FnF Transfer") ? 1 : 2; }
  openSheet(`<div class="hint" style="margin-bottom:10px">${esc(p.payee_key)}</div>
    <input class="input" id="t-name" value="${esc(p.name || "")}" placeholder="Display name (e.g. Tea stall)">
    <div class="chips" style="margin-top:16px">${chipList(choices, null, "data-cat")}</div>`, sh => {
    sh.addEventListener("click", e => {
      const b = e.target.closest("button[data-cat]");
      if (!b) return;
      const name = $("#t-name", sh).value.trim();
      closeSheet();
      tagPayee(p, b.dataset.cat, name && name !== p.name ? name : null);
    });
  });
}

// ---------------------------------------------------------------- charts (small SVG, no library)
const W = 340;
function axisMonths(ms, H, x) {
  const step = Math.max(1, Math.ceil(ms.length / 6));
  return ms.map((m, i) => i % step ? "" : `<text x="${x(i)}" y="${H - 4}" text-anchor="middle">${monthShort(m)}</text>`).join("");
}
function yTicks(max, y, H) {
  return [0.5, 1].map(f => `<line x1="40" x2="${W}" y1="${y(max * f)}" y2="${y(max * f)}" stroke="rgba(255,255,255,.08)"/>
    <text x="36" y="${y(max * f) + 3}" text-anchor="end">${compact(max * f)}</text>`).join("");
}
function barChart(ms, vals, color, selected, avg, id) {
  const H = 190, top = 22, bottom = H - 22, max = Math.max(1, ...vals);
  const bw = (W - 44) / ms.length, x = i => 44 + bw * i + bw / 2, y = v => bottom - (v / max) * (bottom - top);
  const width = Math.max(3, Math.min(18, bw * 0.6));
  const sel = ms.indexOf(selected);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" data-chart="${id}">${yTicks(max, y, H)}
    ${avg > 0 ? `<line x1="40" x2="${W}" y1="${y(avg)}" y2="${y(avg)}" stroke="${color}" stroke-opacity=".5" stroke-dasharray="4 4"/>` : ""}
    ${vals.map((v, i) => `<rect x="${x(i) - width / 2}" y="${y(v)}" width="${width}" height="${bottom - y(v)}" rx="3" fill="${color}" fill-opacity="${i === sel ? 1 : .45}"/>
      <rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${bottom - top}" fill="transparent" data-pick="${i}" style="cursor:pointer"/>`).join("")}
    ${sel >= 0 ? `<text class="tip" x="${Math.min(W - 30, Math.max(70, x(sel)))}" y="${Math.max(10, y(vals[sel]) - 6)}" text-anchor="middle">${inr(vals[sel])}</text>` : ""}
    ${axisMonths(ms, H, x)}</svg>`;
}
function lineChart(ms, vals, color, { area = false, dots = false, zero = false, pick = null, id = "" } = {}) {
  const H = 160, top = 22, bottom = H - 22;
  const max = Math.max(1, ...vals), min = Math.min(0, ...vals), span = max - min || 1;
  const x = i => 44 + (ms.length === 1 ? (W - 44) / 2 : (W - 50) * i / (ms.length - 1)), y = v => bottom - ((v - min) / span) * (bottom - top);
  const pts = vals.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const bw = (W - 44) / ms.length;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" data-chart="${id}">${yTicks(max, y, H)}
    ${zero ? `<line x1="40" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="rgba(255,255,255,.25)"/>` : ""}
    ${area ? `<polygon points="${x(0)},${bottom} ${pts} ${x(ms.length - 1)},${bottom}" fill="${color}" fill-opacity=".12"/>` : ""}
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
    ${dots ? vals.map((v, i) => `<circle cx="${x(i)}" cy="${y(v)}" r="2.8" fill="${v >= 0 ? "var(--mint)" : "var(--rose)"}"/>`).join("") : ""}
    ${vals.map((v, i) => `<rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${bottom - top}" fill="transparent" data-pick="${i}"/>`).join("")}
    ${pick !== null && pick >= 0 ? `<circle cx="${x(pick)}" cy="${y(vals[pick])}" r="4" fill="${color}"/>
      <text class="tip" x="${Math.min(W - 40, Math.max(80, x(pick)))}" y="${Math.max(10, y(vals[pick]) - 8)}" text-anchor="middle">${monthShort(ms[pick])} · ${inr(vals[pick])}</text>` : ""}
    ${axisMonths(ms, H, x)}</svg>`;
}
function pairBars(ms, a, b, pick, id) {
  const H = 190, top = 22, bottom = H - 22, max = Math.max(1, ...a, ...b);
  const bw = (W - 44) / ms.length, x = i => 44 + bw * i + bw / 2, y = v => bottom - (v / max) * (bottom - top);
  const w = Math.max(2.5, Math.min(9, bw * 0.3));
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" data-chart="${id}">${yTicks(max, y, H)}
    ${ms.map((_, i) => `<rect x="${x(i) - w - 1}" y="${y(a[i])}" width="${w}" height="${bottom - y(a[i])}" rx="2" fill="var(--rose)" fill-opacity="${pick === i || pick === null ? 1 : .5}"/>
      <rect x="${x(i) + 1}" y="${y(b[i])}" width="${w}" height="${bottom - y(b[i])}" rx="2" fill="var(--mint)" fill-opacity="${pick === i || pick === null ? 1 : .5}"/>
      <rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${bottom - top}" fill="transparent" data-pick="${i}"/>`).join("")}
    ${pick !== null ? `<text class="tip" x="${Math.min(W - 70, Math.max(110, x(pick)))}" y="12" text-anchor="middle">${monthShort(ms[pick])}: out ${compact(a[pick])} · in ${compact(b[pick])}</text>` : ""}
    ${axisMonths(ms, H, x)}</svg>`;
}
function donut(entries, touched) {
  const total = entries.reduce((s, e) => s + e[1], 0) || 1, cx = 100, cy = 100;
  let a = -Math.PI / 2;
  const arcs = entries.map(([name, v], i) => {
    const sweep = (v / total) * Math.PI * 2, r = i === touched ? 92 : 84, inner = 58;
    const a2 = a + Math.max(0.0001, sweep - 0.02);
    const p = (ang, rad) => `${cx + rad * Math.cos(ang)},${cy + rad * Math.sin(ang)}`;
    const large = sweep > Math.PI ? 1 : 0;
    const d = `M${p(a, r)} A${r},${r} 0 ${large} 1 ${p(a2, r)} L${p(a2, inner)} A${inner},${inner} 0 ${large} 0 ${p(a, inner)}Z`;
    a += sweep;
    return `<path d="${d}" fill="${cat(name).color}" data-slice="${i}" style="cursor:pointer"/>`;
  }).join("");
  return `<svg viewBox="0 0 200 200" style="width:200px;height:200px;display:block;margin:0 auto">${arcs}</svg>`;
}

// ---------------------------------------------------------------- Analytics
const A = { range: store.get("range", 12), side: "expense", slice: null, pick: null, catMonth: null, linePick: null };
function monthAxis(txns) {
  const now = new Date();
  let start = txns.length ? new Date(txns[txns.length - 1].txn_date + "T00:00:00") : now;
  for (const t of txns) { const d = new Date(t.txn_date + "T00:00:00"); if (d < start) start = d; }
  const out = [];
  for (let d = new Date(start.getFullYear(), start.getMonth()); d <= now; d = new Date(d.getFullYear(), d.getMonth() + 1)) out.push(ymOf(d));
  return A.range && out.length > A.range ? out.slice(-A.range) : out;
}
const rangePicker = color => `<div class="chips" style="margin-bottom:12px">${[[6, "6M"], [12, "1Y"], [0, "All"]].map(([v, l]) =>
  `<button class="chip ${A.range === v ? "on" : ""}" data-range="${v}" style="${A.range === v ? `color:${color};border-color:${color}99;background:${color}2e` : ""}"><span>${l}</span></button>`).join("")}</div>`;
const kpi = (k, v, d, upGood) => `<div class="kpi"><div class="v">${v}</div><div class="k">${esc(k)}${d === null || d === undefined ? "" :
  ` <span class="d" style="color:${(d >= 0) === upGood ? "var(--mint)" : "var(--rose)"}">${d >= 0 ? "▲" : "▼"}${Math.round(Math.abs(d) * 100)}%</span>`}</div></div>`;
const chartCard = (title, body, extra = "") => `<div class="card" style="margin-top:12px;padding:14px"><div class="chart-title"><span class="t">${title}</span>${extra}</div>${body}</div>`;

function viewOverview(app) {
  const ms = monthAxis(S.txns), inRange = S.txns.filter(t => ms.includes(t.month));
  const spend = Object.fromEntries(ms.map(m => [m, 0])), inc = Object.fromEntries(ms.map(m => [m, 0])), byCat = {};
  for (const t of inRange) {
    (isIncome(t) ? inc : spend)[t.month] += t.amount;
    if (t.direction === A.side) byCat[t.category] = (byCat[t.category] || 0) + t.amount;
  }
  const a = ms.map(m => spend[m]), b = ms.map(m => inc[m]), net = ms.map((m, i) => b[i] - a[i]);
  const cats = Object.entries(byCat).sort((x, y) => y[1] - x[1]), catTotal = cats.reduce((s, e) => s + e[1], 0);
  const touched = A.slice !== null && cats[A.slice] ? cats[A.slice] : null;
  const sum = arr => arr.reduce((s, v) => s + v, 0);
  app.innerHTML = `
    <div class="appbar"><button class="icon-btn" data-act="back" aria-label="Back">${sym("arrow_back_ios_new")}</button><h2><span>Analytics</span></h2></div>
    <div class="pad16">${rangePicker("#00F0FF")}
      <div class="kpis">${kpi("Spent", compact(sum(a)))}${kpi("Received", compact(sum(b)))}${kpi("Net", compact(sum(net)))}</div>
      ${chartCard("Spent vs received", pairBars(ms, a, b, A.pick, "pair"),
        `<span class="legend"><i class="legend-dot" style="background:var(--rose)"></i>spent</span><span class="legend"><i class="legend-dot" style="background:var(--mint)"></i>received</span>`)}
      ${chartCard("Net per month", lineChart(ms, net, "#00F0FF", { dots: true, zero: true, pick: A.linePick, id: "net" }))}
      ${chartCard("By category", `
        <div class="donut-wrap">${donut(cats, A.slice)}<div class="donut-center"><div class="v">${compact(touched ? touched[1] : catTotal)}</div><div class="k">${esc(touched ? touched[0] : "total")}</div></div></div>
        <div style="margin-top:8px">${cats.map(([name, v], i) => { const c = cat(name); return `<button class="legend-row ${i === A.slice ? "on" : ""}" data-go="#/analytics/${encodeURIComponent(name)}?dir=${A.side}">
          ${sym(c.icon, `style="color:${c.color};font-size:15px"`)}<span class="n">${esc(name)}</span><span class="v">${compact(v)}</span><span class="p">${Math.round(v / (catTotal || 1) * 100)}%</span>${sym("chevron_right", 'style="color:var(--text-3);font-size:16px"')}</button>`; }).join("")}</div>`,
        `<span class="seg"><button class="${A.side === "expense" ? "on" : ""}" data-side="expense">Out</button><button class="${A.side === "income" ? "on" : ""}" data-side="income">In</button></span>`)}
    </div>`;
}

function viewCategoryAnalytics(app, r) {
  const name = r.arg, dir = r.q.get("dir") || "expense", c = cat(name);
  const txns = S.txns.filter(t => t.category === name && t.direction === dir);
  const ms = monthAxis(txns), byM = Object.fromEntries(ms.map(m => [m, 0]));
  for (const t of txns) if (t.month in byM) byM[t.month] += t.amount;
  const vals = ms.map(m => byM[m]), total = vals.reduce((s, v) => s + v, 0);
  const active = vals.filter(v => v > 0).length, avg = active ? total / active : 0;
  const sel = A.catMonth && ms.includes(A.catMonth) ? A.catMonth : [...ms].reverse().find(m => byM[m] > 0) || ms[ms.length - 1];
  let run = 0;
  const cum = vals.map(v => (run += v));
  const payees = {};
  for (const t of txns) if (t.month in byM) payees[t.merchant || t.payee_key] = (payees[t.merchant || t.payee_key] || 0) + t.amount;
  const top = Object.entries(payees).sort((x, y) => y[1] - x[1]).slice(0, 6);
  const monthTx = txns.filter(t => t.month === sel);
  app.innerHTML = `
    <div class="appbar"><button class="icon-btn" data-act="back" aria-label="Back">${sym("arrow_back_ios_new")}</button>
      <h2>${sym(c.icon, `style="color:${c.color}"`)}<span>${esc(name)}</span></h2></div>
    ${!txns.length ? `<div class="empty">No transactions yet</div>` : `<div class="pad16">${rangePicker(c.color)}
      <div class="kpis">${kpi("Total", compact(total))}${kpi("Avg / month", compact(avg))}${kpi(monthLabel(sel), compact(byM[sel] || 0), avg ? ((byM[sel] || 0) - avg) / avg : null, dir === "income")}</div>
      ${chartCard("Monthly", barChart(ms, vals, c.color, sel, avg, "cat"), `<span class="hint">tap a bar</span>`)}
      ${chartCard("Cumulative", lineChart(ms, cum, c.color, { area: true, pick: A.linePick, id: "cum" }))}
      ${top.length > 1 ? chartCard("Top payees", top.map(([n, v]) => `<div class="top-row"><span class="n">${esc(n)}</span>
        <span class="b"><i style="width:${v / top[0][1] * 100}%;background:${c.color}"></i></span><span class="v">${compact(v)}</span></div>`).join("")) : ""}
    </div>
    <div class="section-label">${monthLabel(sel)} · ${monthTx.length}</div>
    ${monthTx.map(t => `<button class="mini" data-act="edit" data-id="${esc(t.txn_id)}"><span class="d">${new Date(t.txn_date + "T00:00:00").toLocaleDateString("en", { day: "numeric", month: "short" })}</span>
      <span class="n">${esc(t.merchant || "Unknown")}</span><span class="v" style="color:${c.color}">${inr(t.amount)}</span></button>`).join("")}`}`;
  app.dataset.catMonths = JSON.stringify(ms);
}

// ---------------------------------------------------------------- Savings
const SV = { data: undefined, error: null, cat: null };
async function loadSavings() {
  try { SV.data = await api("GET", "/holdings.json"); SV.error = null; }
  catch (err) { SV.data = null; SV.error = err.message; }
  render();
}
function viewSavings(app) {
  if (SV.data === undefined) { app.innerHTML = `<div class="header"><div class="title"><h1>Savings</h1></div></div><div class="loading">Loading…</div>`; loadSavings(); return; }
  const d = SV.data;
  if (!d) {
    app.innerHTML = `<div class="header"><div class="title"><h1>Savings</h1></div></div><div class="pad16" style="margin-top:16px"><div class="card">
      <div class="card-title">No portfolio yet</div><p class="hint" style="font-size:13px">The Savings tab shows a mutual-fund <code>holdings.json</code>
      built by a separate project. Point the server at it with <code>APTIQ_HOLDINGS_PATH</code>. (${esc(SV.error)})</p></div></div>`;
    return;
  }
  const t = d.totals, gain = t.pnl >= 0, catColors = ["#00F0FF", "#8B5CF6", "#FF3EA5", "#FFB020", "#00FFA3", "#4FC3FF"];
  const cats = (d.by_category || []).slice().sort((a, b) => b.current_value - a.current_value);
  const colorOf = name => catColors[Math.max(0, cats.findIndex(c => c.category === name)) % catColors.length];
  const funds = (d.schemes || []).filter(s => !SV.cat || s.category === SV.cat).sort((a, b) => b.current_value - a.current_value);
  app.innerHTML = `
    <div class="header"><div class="title"><h1>Savings</h1><div class="sub">Prices as of ${esc(d.as_of)}</div></div>
      <button class="sq cyan" data-act="savings-reload" aria-label="Reload">${sym("refresh")}</button></div>
    ${S.demo ? `<div class="demo-badge">Demo data - every name and number here is made up.</div>` : ""}
    <div class="pad16" style="margin-top:14px">
      <div class="summary" style="background:linear-gradient(135deg, rgba(0,255,163,.14), rgba(0,240,255,.06));box-shadow:0 0 24px -6px rgba(0,255,163,.14)">
        <div class="eyebrow">Portfolio value</div><div class="big">${inr(t.current_value)}</div>
        <div class="below ${gain ? "up" : "down"}" style="font-family:var(--mono);font-weight:700">${gain ? "+" : ""}${inr(t.pnl)}  (${gain ? "+" : ""}${t.pnl_pct.toFixed(2)}%)</div>
        <div class="row2"><div class="stat">${sym("account_balance_wallet", 'style="color:var(--cyan);font-size:16px"')}<div><div class="v">${inr(t.invested)}</div><div class="k">invested</div></div></div>
          <div class="stat">${sym("pie_chart", 'style="color:var(--violet);font-size:16px"')}<div><div class="v">${t.scheme_count}</div><div class="k">funds · ${t.folio_count} folios</div></div></div></div>
      </div>
      <div class="card" style="margin-top:16px"><div style="display:flex;gap:8px;align-items:baseline;margin-bottom:14px"><div class="card-title">Asset mix</div><div class="hint">· tap to filter</div></div>
        ${cats.map(c => { const col = colorOf(c.category), share = c.current_value / (t.current_value || 1), on = SV.cat === c.category;
          return `<button class="cat-row" data-scat="${esc(c.category)}"><div class="cat-top"><span class="ic" style="color:${col};background:${col}29;border-color:${col}${on ? "bf" : "59"}">${sym("donut_small")}</span>
            <span class="cat-name" style="${on ? `color:${col}` : ""}">${esc(c.category)}</span><span class="cat-amt">${inr(c.current_value)}</span>
            <span class="cat-pct ${c.pnl >= 0 ? "up" : "down"}" style="width:52px">${c.pnl >= 0 ? "+" : ""}${c.pnl_pct.toFixed(1)}%</span></div>
            <div class="bar"><i style="width:${Math.max(2, share * 100)}%;background:${col}"></i></div></button>`; }).join("")}
      </div>
    </div>
    <div class="list-head"><span class="lbl">FUNDS</span><span class="badge">${funds.length}</span><span class="grow"></span>
      ${SV.cat ? `<button class="fpill" data-scat="" style="color:var(--cyan);background:rgba(0,240,255,.15);border-color:rgba(0,240,255,.5)"><span>${esc(SV.cat)}</span>${sym("close")}</button>` : ""}</div>
    <div class="txns">${funds.map(s => { const p = s.current_value - s.invested_amount, pct = s.invested_amount ? p / s.invested_amount * 100 : 0, col = colorOf(s.category);
      return `<div class="fund"><span class="ic lg" style="color:${col};background:${col}24;border-color:transparent">${sym("show_chart")}</span>
        <div class="mid"><div class="nm">${esc(s.scheme_name)}</div><div class="sub">${esc(s.amc || "")} · ${esc(s.category)}</div></div>
        <div class="right"><div class="v">${inr(s.current_value)}</div><div class="${p >= 0 ? "up" : "down"}" style="font-size:11px;font-weight:700">${p >= 0 ? "+" : ""}${pct.toFixed(1)}%</div></div></div>`; }).join("")}</div>`;
}

// ---------------------------------------------------------------- Settings
function viewSettings(app) {
  const n = S.txns.length, ms = months();
  app.innerHTML = `
    <div class="header"><div class="title"><h1>Settings</h1></div></div>
    <div class="pad16" style="margin-top:16px">
      <div class="card"><div class="card-title" style="margin-bottom:6px">Ledger server</div>
        <div class="set-row"><div class="mid"><div class="k">Connected to</div><div class="s">This page is served by your Mac</div></div><span class="v">${esc(location.host)}</span></div>
        <div class="set-row"><div class="mid"><div class="k">Read new bank SMS</div><div class="s">${S.syncedAt ? "Synced " + relative(S.syncedAt) : "Not synced yet"}</div></div>
          <button class="sq cyan" data-act="refresh" aria-label="Refresh">${S.syncing ? '<span class="sym spin" aria-hidden="true">progress_activity</span>' : sym("refresh")}</button></div>
        ${S.demo ? `<div class="set-row"><div class="mid"><div class="k">Demo mode</div><div class="s">Made-up data from scripts/demo.py</div></div></div>` : ""}
      </div>
      <div class="card"><div class="card-title" style="margin-bottom:6px">Developer options</div>
        <div class="set-row"><div class="mid"><div class="k">Add transactions manually</div><div class="s">Shows a + button on Expenses for cash or missed SMS.</div></div>
          <button class="switch ${S.dev ? "on" : ""}" data-act="dev" aria-label="Add transactions manually"></button></div>
      </div>
      <div class="card"><div class="card-title" style="margin-bottom:6px">Your data</div>
        <div class="set-row"><div class="mid"><div class="k">Transactions</div></div><span class="v">${n}</span></div>
        <div class="set-row"><div class="mid"><div class="k">Months</div></div><span class="v">${ms.length ? `${monthLabel(ms[ms.length - 1])} – ${monthLabel(ms[0])}` : "-"}</span></div>
        <div class="set-row"><div class="mid"><div class="k">Storage</div><div class="s">Everything stays on your Mac (data/ledger.db). Nothing is stored in this browser except your preferences.</div></div></div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- events
async function refreshFromSms() {
  if (S.syncing) return;
  S.syncing = true; render();
  try {
    const res = await api("POST", "/refresh");
    await loadTransactions();
    toast(res.ingested ? `${res.ingested} new SMS · ${res.published} transactions` : `Up to date · ${res.published} transactions`);
  } catch (err) { toast(err.message); }
  S.syncing = false;
  render();
}
function scrollToList() {
  requestAnimationFrame(() => { const h = $("#list-head"); if (h) h.scrollIntoView({ behavior: "smooth", block: "start" }); });
}

document.addEventListener("click", e => {
  if (e.target.closest("#sheet")) return;
  const el = e.target.closest("[data-go],[data-act],[data-month],[data-rmonth],[data-range],[data-side],[data-pick],[data-slice],[data-scat]");
  if (!el) return;
  const r = route();
  if (el.dataset.go) return go(el.dataset.go);
  if (el.dataset.month) { S.month = el.dataset.month; S.catFilter = S.dirFilter = null; store.set("month", S.month); return render(); }
  if (el.dataset.rmonth !== undefined) { R.month = el.dataset.rmonth || null; return loadReview(); }
  if (el.dataset.range !== undefined) { A.range = Number(el.dataset.range); A.pick = A.linePick = null; store.set("range", A.range); return render(); }
  if (el.dataset.side) { A.side = el.dataset.side; A.slice = null; return render(); }
  if (el.dataset.scat !== undefined) { SV.cat = el.dataset.scat && el.dataset.scat !== SV.cat ? el.dataset.scat : null; return render(); }
  if (el.dataset.slice !== undefined) { const i = Number(el.dataset.slice); A.slice = A.slice === i ? null : i; return render(); }
  if (el.dataset.pick !== undefined) {
    const i = Number(el.dataset.pick), chart = el.closest("svg").dataset.chart;
    if (chart === "cat") A.catMonth = JSON.parse($("#app").dataset.catMonths || "[]")[i];
    else if (chart === "pair") A.pick = A.pick === i ? null : i;
    else A.linePick = A.linePick === i ? null : i;
    return render();
  }
  switch (el.dataset.act) {
    case "retry": return boot();
    case "back": return history.length > 1 ? history.back() : go("#/expenses");
    case "refresh": return refreshFromSms();
    case "add": return openAdd();
    case "review": { const m = currentMonth(); R.month = undefined;
      return go(S.txns.some(t => t.month === m && t.category === NEEDS_REVIEW) ? `#/review?month=${m}` : "#/review"); }
    case "review-reload": return loadReview();
    case "savings-reload": SV.data = undefined; return render();
    case "dev": S.dev = !S.dev; store.set("devManualAdd", S.dev); return render();
    case "dir": { const d = el.dataset.dir; S.dirFilter = S.dirFilter === d ? null : d; S.catFilter = null; render(); if (S.dirFilter) scrollToList(); return; }
    case "cat": if (e.target.closest(".cat-an")) return;
      S.catFilter = S.catFilter === el.dataset.cat ? null : el.dataset.cat; render(); if (S.catFilter) scrollToList(); return;
    case "cat-analytics": A.catMonth = null; A.linePick = null;
      return go(`#/analytics/${encodeURIComponent(el.dataset.cat)}?dir=${S.dirFilter === "income" ? "income" : "expense"}`);
    case "clear-dir": S.dirFilter = null; return render();
    case "clear-cat": S.catFilter = null; return render();
    case "edit": { const t = S.txns.find(x => x.txn_id === el.dataset.id); return t && openEditor(t); }
    case "tag": if (e.target.closest(".accept")) return; return openTagSheet(R.payees[Number(el.dataset.i)]);
    case "accept": { const p = R.payees[Number(el.dataset.i)], s = R.ai[`${p.payee_key}|${p.kind}`]; return tagPayee(p, s.category, null); }
  }
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeSheet();
  if ((e.key === "Enter" || e.key === " ") && e.target.matches('[role="button"]')) { e.preventDefault(); e.target.click(); }
});

async function boot() {
  S.error = null;
  render();
  try { await loadTransactions(); } catch (err) { S.error = err.message; }
  render();
}
boot();
