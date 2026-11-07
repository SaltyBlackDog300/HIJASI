const $ = (id) => document.getElementById(id);
const state = { csrf: "", merchant: null, page: "overview" };
const today = new Date();

function esc(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function setText(id, v) { $(id).textContent = v; }
function cur() { return (state.merchant?.currency || "eur").toUpperCase(); }
function money(minor) {
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency: cur() }).format((minor || 0) / 100); }
  catch (_) { return ((minor || 0) / 100).toFixed(2) + " " + cur(); }
}
function dateLabel(v) {
  if (!v) return "No activity";
  const d = new Date(String(v).replace(" ", "T"));
  return Number.isNaN(+d) ? "—" : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
function showBanner(msg) { const el = $("banner"); el.textContent = msg || ""; el.hidden = !msg; }

async function api(path, options = {}) {
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(state.csrf ? { "X-CSRF-Token": state.csrf } : {}) };
  const res = await fetch(path, { credentials: "same-origin", ...options, headers, body: options.body ? JSON.stringify(options.body) : undefined });
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) { const e = new Error(data?.error?.message || "Request failed."); e.status = res.status; e.fields = data?.error?.fields || {}; throw e; }
  return data;
}

// ---------------------------------------------------------------- screens

function showScreen(id) {
  for (const s of ["auth-screen", "pending-screen", "app-shell"]) $(s).hidden = s !== id;
}

function setAuthMode(mode) {
  const signup = mode === "signup";
  $("signup-fields").hidden = !signup;
  $("apply-note").hidden = !signup;
  for (const id of ["business-name", "website", "country", "currency", "description"]) $(id).required = signup;
  $("login-tab").classList.toggle("active", !signup);
  $("signup-tab").classList.toggle("active", signup);
  setText("auth-title", signup ? "Apply for a merchant account" : "Sign in to your workspace");
  setText("auth-subtitle", signup ? "Tell us about your business. We review every application." : "Your customers and wallet activity are waiting.");
  setText("auth-submit-label", signup ? "Submit application" : "Sign in");
  $("password").autocomplete = signup ? "new-password" : "current-password";
  $("auth-form").dataset.mode = mode;
  $("auth-error").hidden = true;
}

// ---------------------------------------------------------------- verification (teammate's module at /verification)

const FIELD_LABELS = {
  legal_name: "Legal business name", registration_number: "Business registration number",
  registration_country: "Registration country", contact_name: "Contact person", contact_email: "Contact email",
  website: "Website", product_category: "Product category", product_description: "What do you sell?",
  sample_products: "Example products (one per line)", selling_countries: "Countries you sell in (one per line)",
  regulated_products: "We sell regulated products (alcohol, medicine, tobacco, …)",
  licence_details: "Licence details (required if you sell regulated products)",
  declares_accurate_and_lawful: "I declare this information is accurate and my products are lawful",
  accepted_policy_version: "I accept the sandbox policy below",
};
const CATEGORY_LABELS = { food_and_drink: "Food and drink", physical_goods: "Physical goods", digital_services: "Digital services", other: "Other" };
const STATUS_TEXT = {
  none: ["Complete your application", "Fill in the declaration, save a draft, then submit it for review."],
  draft: ["Draft saved", "Submit it when you're ready. Reviewers only see submitted applications."],
  submitted: ["Submitted for review", "A reviewer will pick it up soon. You can't edit it while it's waiting."],
  under_review: ["Under review", "A reviewer is checking your business against the register and your catalogue."],
  needs_information: ["More information needed", "Update your declaration using the reviewer's note below, then submit again."],
  rejected: ["Application rejected", "See the reviewer's note below."],
  suspended: ["Account suspended", "API access is switched off. See the reviewer's note below."],
  approved: ["Approved", "Your account is approved."],
};
const verif = { schema: null, app: null, merchant: null };
const vPath = () => `/verification/merchants/${encodeURIComponent(String(verif.merchant.id))}/application`;
const vOrdered = () => verif.schema.field_order.map(n => [n, verif.schema.fields[n]]);

function renderVerificationFields() {
  const box = $("verification-fields");
  box.replaceChildren();
  for (const [name, spec] of vOrdered()) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    const isCheck = spec.type === "boolean" || name === "accepted_policy_version";
    let input;
    if (spec.type === "choice") {
      input = document.createElement("select");
      input.add(new Option("Choose a category", ""));
      spec.options.forEach(v => input.add(new Option(CATEGORY_LABELS[v] || v, v)));
    } else if (spec.type.endsWith("list") || name === "product_description" || name === "licence_details") {
      input = document.createElement("textarea");
      input.rows = spec.type.endsWith("list") ? 3 : 3;
    } else {
      input = document.createElement("input");
      input.type = isCheck ? "checkbox" : spec.type === "email" ? "email" : spec.type === "url" ? "url" : "text";
    }
    input.id = `vf-${name}`;
    if (spec.max_length && !isCheck) input.maxLength = spec.max_length;
    if (spec.type === "country_code") { input.maxLength = 2; input.placeholder = "NO"; }
    const label = document.createElement("label");
    label.htmlFor = input.id;
    label.textContent = FIELD_LABELS[name] + (spec.required || isCheck ? "" : " (optional)");
    const err = document.createElement("p");
    err.className = "field-error";
    err.id = `ve-${name}`;
    if (isCheck) { wrap.className = "field check-field"; label.prepend(input); wrap.append(label, err); }
    else wrap.append(label, input, err);
    box.append(wrap);
  }
  $("policy-box").innerHTML = `<strong>Sandbox policy (${esc(verif.schema.policy.version)})</strong><p>${esc(verif.schema.policy.text)}</p>`;
}

function fillVerification() {
  const m = verif.merchant;
  // Prefill a new application from what the merchant told us at sign-up.
  const d = verif.app?.details || {
    legal_name: m.business_name, registration_country: m.country, contact_email: m.email,
    website: m.website, product_description: m.description || "", selling_countries: [m.country],
  };
  for (const [name, spec] of vOrdered()) {
    const input = $(`vf-${name}`);
    const v = d[name];
    if (input.type === "checkbox") input.checked = name === "accepted_policy_version" ? v === verif.schema.policy.version : v === true;
    else input.value = Array.isArray(v) ? v.join("\n") : (v ?? "");
  }
  const status = verif.app?.status || "none";
  const [title, text] = STATUS_TEXT[status] || STATUS_TEXT.none;
  setText("pending-title", title);
  setText("pending-text", text);
  const pill = $("pending-pill");
  pill.textContent = status === "none" ? "not started" : status.replace("_", " ");
  pill.className = `status-pill status-${status === "none" ? "draft" : status}`;
  setText("pending-version", verif.app ? `Version ${verif.app.version}` : "");
  const reason = verif.app?.review_reason;
  $("review-reason").hidden = !reason;
  $("review-reason").textContent = reason ? `Reviewer's note: ${reason}` : "";
  const editable = !verif.app || ["draft", "needs_information"].includes(verif.app.status);
  $("verification-fields").disabled = !editable;
  $("save-draft").hidden = $("submit-application").hidden = !editable;
  clearFieldErrors();
}

function clearFieldErrors() { document.querySelectorAll(".field-error").forEach(e => e.textContent = ""); }

function collectVerification() {
  const details = {};
  for (const [name, spec] of vOrdered()) {
    const input = $(`vf-${name}`);
    if (name === "accepted_policy_version") details[name] = input.checked ? verif.schema.policy.version : "";
    else if (spec.type === "boolean") details[name] = input.checked;
    else if (spec.type.endsWith("list")) {
      const values = input.value.split("\n").map(v => v.trim()).filter(Boolean);
      if (values.length) details[name] = values;
    } else if (input.value.trim()) details[name] = input.value.trim();
  }
  if (verif.app) {  // PATCH merges, so explicitly clear fields the merchant emptied
    for (const [name, spec] of vOrdered()) {
      if (!(name in details) && name in verif.app.details) details[name] = spec.type.endsWith("list") ? [] : "";
    }
  }
  return details;
}

async function saveVerification(submit) {
  const msg = $("verification-message");
  clearFieldErrors();
  msg.textContent = "";
  for (const b of ["save-draft", "submit-application"]) $(b).disabled = true;
  try {
    const details = collectVerification();
    verif.app = verif.app
      ? await api(vPath(), { method: "PATCH", body: { expected_version: verif.app.version, details } })
      : await api(vPath(), { method: "POST", body: { details } });
    if (submit) verif.app = await api(vPath() + "/submit", { method: "POST", body: { expected_version: verif.app.version } });
    fillVerification();
    msg.style.color = "#2d8b4f";
    msg.textContent = submit ? "Submitted. We'll review it as soon as possible." : "Draft saved.";
  } catch (err) {
    msg.style.color = "#ab4c45";
    msg.textContent = err.status === 409 ? "Your application changed elsewhere. Click Refresh status and try again." : err.message;
    for (const [field, text] of Object.entries(err.fields || {})) {
      const el = $(`ve-${field}`);
      if (el) el.textContent = text;
    }
  } finally {
    for (const b of ["save-draft", "submit-application"]) $(b).disabled = false;
  }
}

function renderEmailVerify(m) {
  const box = $("email-verify");
  const verified = m.email_verified;
  box.hidden = verified && location.hash !== "#verified";
  $("resend-verification").hidden = verified;
  $("email-verify-text").textContent = verified
    ? "Email confirmed. You can submit your application."
    : (location.hash === "#verify-failed" ? "That link is invalid or expired. " : "") +
      `Confirm your email first: we sent a link to ${m.email}. Check your spam folder too.`;
  $("submit-application").disabled = !verified;
}

$("resend-verification").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    await api("/api/auth/resend-verification", { method: "POST" });
    $("email-verify-text").textContent = `New link sent to ${verif.merchant.email}.`;
  } catch (err) { $("email-verify-text").textContent = err.message; }
  finally { setTimeout(() => { e.target.disabled = false; }, 60000); }
});

async function showPending(m) {
  verif.merchant = m;
  showScreen("pending-screen");
  renderEmailVerify(m);
  if (!verif.schema) { verif.schema = await api("/verification/form"); renderVerificationFields(); }
  try { verif.app = await api(vPath()); }
  catch (err) { if (err.status === 404) verif.app = null; else throw err; }
  fillVerification();
}

function enterApp(me) {
  const m = me.merchant;
  state.merchant = m;
  showScreen("app-shell");
  setText("workspace-name", m.business_name);
  setText("workspace-avatar", m.business_name[0]?.toUpperCase() || "M");
  setText("workspace-meta", `${m.business_type.replace("_", " ")} · ${m.country} · ${m.currency.toUpperCase()}`);
  setText("user-name", m.email);
  setText("user-avatar", m.email[0]?.toUpperCase() || "M");
  setText("greeting-name", m.business_name);
  setText("today-date", today.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" }));
  setText("gateway-label", me.gateway === "stripe" ? "Stripe test mode" : "Mock gateway");
  renderKeys(me.api_keys);
  renderPolicy(m);
  navigate(state.page);
}

// ---------------------------------------------------------------- renderers

function renderPolicy(m) {
  setText("policy-currency", `${m.currency.toUpperCase()} wallets`);
  setText("policy-range", `${money(m.policy.min_topup)} – ${money(m.policy.max_topup)}`);
  setText("policy-max", money(m.policy.max_balance));
}

function renderKeys(keys) {
  $("key-info").innerHTML = keys.length
    ? keys.map(k => `<div class="key-row"><span class="table-primary">Active key</span><code>${esc(k.prefix)}…</code><span class="table-secondary">Created ${esc(dateLabel(k.created_at))} · last used ${esc(k.last_used_at ? dateLabel(k.last_used_at) : "never")}</span></div>`).join("")
    : `<p class="field-hint">No key yet. Generate one to start integrating.</p>`;
}

function renderChart(activity) {
  const byDate = new Map(activity.map(x => [String(x.day).slice(0, 10), Number(x.count)]));
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today); d.setDate(today.getDate() - (6 - i));
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return { key, label: d.toLocaleDateString("en-US", { weekday: "short" }) };
  });
  const max = Math.max(1, ...days.map(d => byDate.get(d.key) || 0));
  $("activity-chart").innerHTML = days.map(d => {
    const n = byDate.get(d.key) || 0;
    return `<div class="chart-col" title="${esc(d.label)}: ${n} transactions"><div class="chart-track"><div class="chart-bar" style="height:${Math.max(4, Math.round(n / max * 100))}%"></div></div><span class="chart-label">${esc(d.label)}</span></div>`;
  }).join("");
}

const TYPE_NAMES = { topup: "Top-up", charge: "Charge", refund: "Refund", adjustment: "Adjustment" };

function txnRow(t, full = false) {
  const pos = Number(t.amount) >= 0;
  return `<tr>
    <td><span class="table-primary">${esc(t.description || TYPE_NAMES[t.type])}</span><span class="table-secondary">${esc(t.id)}</span></td>
    <td><span class="table-primary">${esc(t.customer_name || t.customer_id)}</span><span class="table-secondary">${esc(t.customer_id)}</span></td>
    ${full ? `<td>${esc(t.reference || "—")}</td>` : ""}
    <td>${dateLabel(t.created_at)}</td>
    <td class="${pos ? "points-positive" : "points-negative"}">${pos ? "+" : "−"}${money(Math.abs(Number(t.amount)))}</td>
    ${full ? `<td>${money(t.balance_after)}</td>` : ""}
    <td><span class="type-pill type-${esc(t.type)}">${esc(TYPE_NAMES[t.type] || t.type)}</span></td>
  </tr>`;
}

async function loadOverview() {
  const [o, tx] = await Promise.all([api("/api/overview"), api("/api/transactions")]);
  setText("stat-float", money(o.float_total));
  setText("stat-wallets", o.wallets);
  setText("stat-active", o.active_wallets);
  setText("stat-topups", money(o.topped_up));
  setText("stat-spent", money(o.spent));
  setText("stat-txns", o.transaction_count);
  renderChart(o.activity);
  $("recent-transactions").innerHTML = tx.transactions.length
    ? tx.transactions.slice(0, 5).map(t => txnRow(t)).join("")
    : `<tr><td class="empty-row" colspan="5">No transactions yet.</td></tr>`;
}

async function loadCustomers() {
  const r = await api("/api/wallets?q=" + encodeURIComponent($("customer-search").value));
  const items = r.wallets;
  setText("customer-count", `${items.length} customer${items.length === 1 ? "" : "s"} shown`);
  $("customer-rows").innerHTML = items.length ? items.map(w => `<tr>
      <td><span class="table-primary">${esc(w.display_name || "—")}</span></td>
      <td><code>${esc(w.customer_id)}</code></td>
      <td class="table-primary">${money(w.balance)}</td>
      <td><span class="status-pill status-${esc(w.status)}">${esc(w.status)}</span></td>
      <td>${dateLabel(w.last_activity)}</td></tr>`).join("")
    : `<tr><td class="empty-row" colspan="5">No customer wallets found.</td></tr>`;
}

async function loadTransactions() {
  const r = await api("/api/transactions?type=" + encodeURIComponent($("transaction-filter").value));
  const items = r.transactions;
  setText("transaction-count", `${items.length} transaction${items.length === 1 ? "" : "s"} shown`);
  $("transaction-rows").innerHTML = items.length ? items.map(t => txnRow(t, true)).join("")
    : `<tr><td class="empty-row" colspan="7">No transactions found.</td></tr>`;
}

async function loadSettings() {
  const { settings: m } = await api("/api/settings");
  state.merchant = m;
  $("setting-name").value = m.business_name;
  $("setting-min").value = (m.policy.min_topup / 100).toFixed(2);
  $("setting-max").value = (m.policy.max_topup / 100).toFixed(2);
  $("setting-balance").value = (m.policy.max_balance / 100).toFixed(2);
  document.querySelectorAll(".cur").forEach(el => el.textContent = cur());
  $("account-details").innerHTML = `${esc(m.email)}<br>${esc(m.website)}<br>${esc(m.country)} · ${esc(m.business_type.replace("_", " "))} · ${esc(cur())}`;
}

const LOADERS = { overview: loadOverview, customers: loadCustomers, transactions: loadTransactions, settings: loadSettings, developers: async () => {} };
const TITLES = { overview: "Overview", customers: "Customer wallets", transactions: "Transactions", developers: "API keys", settings: "Settings" };

async function loadPage(page) {
  try { showBanner(""); await LOADERS[page](); }
  catch (e) { if (e.status === 401) boot(); else showBanner(e.message); }
}

function navigate(page) {
  state.page = page;
  document.querySelectorAll(".page").forEach(x => x.classList.toggle("active", x.id === "page-" + page));
  document.querySelectorAll(".nav-link").forEach(x => x.classList.toggle("active", x.dataset.page === page));
  $("breadcrumb").innerHTML = `Workspace <span class="crumb-separator">/</span> ${esc(TITLES[page])}`;
  $("sidebar").classList.remove("open");
  loadPage(page);
}

// ---------------------------------------------------------------- events

document.querySelectorAll("[data-page]").forEach(b => b.addEventListener("click", () => navigate(b.dataset.page)));
$("login-tab").addEventListener("click", () => setAuthMode("login"));
$("signup-tab").addEventListener("click", () => setAuthMode("signup"));
$("menu-button").addEventListener("click", () => $("sidebar").classList.add("open"));
$("sidebar-close").addEventListener("click", () => $("sidebar").classList.remove("open"));
$("pending-refresh").addEventListener("click", boot);
$("save-draft").addEventListener("click", () => saveVerification(false));
$("submit-application").addEventListener("click", () => saveVerification(true));
$("verification-form").addEventListener("submit", e => e.preventDefault());

$("auth-form").addEventListener("submit", async e => {
  e.preventDefault();
  const signup = e.currentTarget.dataset.mode === "signup";
  const btn = e.currentTarget.querySelector("[type=submit]");
  const body = { email: $("email").value, password: $("password").value };
  if (signup) Object.assign(body, {
    business_name: $("business-name").value, website: $("website").value,
    country: $("country").value.toUpperCase(), currency: $("currency").value.toLowerCase(),
    business_type: $("business-type").value, description: $("description").value,
  });
  btn.disabled = true; $("auth-error").hidden = true;
  try {
    await api(signup ? "/api/auth/register" : "/api/auth/login", { method: "POST", body });
    await boot();
  } catch (err) { $("auth-error").textContent = err.message; $("auth-error").hidden = false; }
  finally { btn.disabled = false; }
});

document.querySelectorAll(".logout-action").forEach(b => b.addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  state.csrf = ""; state.page = "overview";
  showScreen("auth-screen"); setAuthMode("login");
}));

$("roll-key").addEventListener("click", async () => {
  if (!confirm("Generate a new key? Your current key stops working immediately.")) return;
  try {
    const r = await api("/api/keys", { method: "POST" });
    const box = $("new-key");
    box.innerHTML = `<div class="keybox">${esc(r.secret_key)}</div><p class="field-hint">${esc(r.note)}</p>`;
    box.hidden = false;
    renderKeys((await api("/api/me")).api_keys);
  } catch (err) { showBanner(err.message); }
});

let searchTimer;
$("customer-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadPage("customers"), 180); });
$("transaction-filter").addEventListener("change", () => loadPage("transactions"));

$("settings-form").addEventListener("submit", async e => {
  e.preventDefault();
  const btn = e.currentTarget.querySelector("[type=submit]");
  const toMinor = id => Math.round(parseFloat($(id).value) * 100);
  btn.disabled = true;
  try {
    const r = await api("/api/settings", { method: "PUT", body: {
      business_name: $("setting-name").value, min_topup: toMinor("setting-min"),
      max_topup: toMinor("setting-max"), max_balance: toMinor("setting-balance") } });
    state.merchant = r.settings;
    setText("workspace-name", r.settings.business_name);
    setText("workspace-avatar", r.settings.business_name[0]?.toUpperCase() || "M");
    setText("greeting-name", r.settings.business_name);
    renderPolicy(r.settings);
    $("settings-message").style.color = "#2d8b4f";
    setText("settings-message", "Settings saved.");
  } catch (err) {
    $("settings-message").style.color = "#ab4c45";
    setText("settings-message", err.message);
  } finally { btn.disabled = false; }
});

// ---------------------------------------------------------------- start

async function boot() {
  try {
    const me = await api("/api/me");
    state.csrf = me.csrf_token;
    if (me.merchant.status !== "approved") await showPending(me.merchant);
    else enterApp(me);
  } catch (err) {
    showScreen("auth-screen");
    setAuthMode(location.hash === "#register" ? "signup" : "login");
    if (err.status !== 401) { $("auth-error").textContent = err.message; $("auth-error").hidden = false; }
  }
}

boot();
setInterval(() => { if (!$("app-shell").hidden && ["overview", "transactions"].includes(state.page)) loadPage(state.page); }, 8000);
