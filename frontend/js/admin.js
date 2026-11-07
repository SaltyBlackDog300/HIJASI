const $ = (id) => document.getElementById(id);
let csrf = "", merchantsById = new Map();

function esc(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function showBanner(msg) { const el = $("banner"); el.textContent = msg || ""; el.hidden = !msg; }
function label(s) { return String(s).replaceAll("_", " ").replace(/^./, c => c.toUpperCase()); }
function when(iso) { return iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—"; }
function safeUrl(u) { try { const x = new URL(u); return ["http:", "https:"].includes(x.protocol) ? x.href : ""; } catch { return ""; } }

async function api(path, options = {}) {
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(csrf ? { "X-CSRF-Token": csrf } : {}) };
  const res = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options, headers, body: options.body ? JSON.stringify(options.body) : undefined });
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) {
    const e = new Error(data?.error?.message || "Request failed."); e.status = res.status; e.fields = data?.error?.fields || {};
    throw e;
  }
  return data;
}

function show(id) { $("login-screen").hidden = id !== "login"; $("app-shell").hidden = id !== "app"; }

// ---------------------------------------------------------------- review queue (teammate's verification module)

const ACTIONS = {
  submitted: ["start_review"],
  under_review: ["approve", "request_information", "reject"],
  approved: ["suspend", "reopen"], rejected: ["reopen"], suspended: ["reopen"],
};
const ACTION_STYLE = { approve: "approve", start_review: "approve", reject: "reject", suspend: "reject" };
const CHECKS = {
  business_identity_checked: "Business identity matches the official register",
  products_match_declaration: "Products match the declaration and catalogue",
  sandbox_policy_checked: "Business complies with the sandbox policy",
};
const DETAIL_ORDER = ["legal_name", "registration_number", "registration_country", "contact_name", "contact_email", "website",
  "product_category", "product_description", "sample_products", "selling_countries", "regulated_products", "licence_details"];

function detailValue(k, v) {
  if (Array.isArray(v)) return esc(v.join(", "));
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (k === "website" && safeUrl(v)) return `<a class="text-link" href="${esc(safeUrl(v))}" target="_blank" rel="noopener noreferrer">${esc(v)}</a>`;
  if (k === "product_category") return esc(label(v));
  return esc(v);
}

function card(a) {
  const m = merchantsById.get(Number(a.merchant_id));
  const d = a.details || {};
  const flags = m?.risk_flags || [];
  const actions = ACTIONS[a.status] || [];
  const needsReview = a.status === "under_review";
  return `<article class="review-card" data-id="${esc(a.id)}" data-version="${a.version}">
    <header class="review-head">
      <div><span class="table-primary">${esc(d.legal_name || m?.business_name || "Merchant " + a.merchant_id)}</span>
        <span class="table-secondary">${esc(m ? `${m.email} · account #${m.id}` : `account #${a.merchant_id}`)} · updated ${esc(when(a.updated_at))}</span></div>
      <div class="review-tags">${flags.map(f => `<span class="flag">${esc(f)}</span>`).join("")}
        <span class="status-pill status-${esc(a.status)}">${esc(label(a.status))}</span><span class="table-secondary">v${a.version}</span></div>
    </header>
    <dl class="detail-grid">${DETAIL_ORDER.filter(k => k in d && d[k] !== "").map(k => `<dt>${esc(label(k))}</dt><dd>${detailValue(k, d[k])}</dd>`).join("")}</dl>
    ${a.review_reason ? `<p class="field-hint">Last note to merchant: ${esc(a.review_reason)}</p>` : ""}
    ${actions.length ? `<div class="review-form">
      ${needsReview ? `<div class="check-list">${Object.entries(CHECKS).map(([k, t]) => `<label class="check-field"><input type="checkbox" data-check="${k}"> ${esc(t)}</label>`).join("")}</div>` : ""}
      ${a.status !== "submitted" ? `<label>Reason (visible to the merchant)</label><textarea data-reason rows="2" maxlength="2000" placeholder="e.g. Register entry and catalogue matched the declaration."></textarea>` : ""}
      ${needsReview ? `<label>Evidence (internal, one reference per line)</label><textarea data-evidence rows="2" placeholder="e.g. Brønnøysund register 912345678 checked 7 Nov"></textarea>` : ""}
      <p class="form-message" data-msg></p>
      <div class="button-row">${actions.map(x => `<button class="small-button ${ACTION_STYLE[x] || ""}" type="button" data-action="${x}">${esc(label(x))}</button>`).join("")}
        <button class="small-button" type="button" data-audit>Audit history</button></div>
    </div>` : `<div class="button-row"><button class="small-button" type="button" data-audit>Audit history</button></div>`}
    <ol class="audit-list" data-audit-list hidden></ol>
  </article>`;
}

async function loadQueue() {
  const status = $("queue-status").value;
  const r = await api(`/verification/admin/applications?status=${encodeURIComponent(status)}&limit=100`);
  $("queue-count").textContent = `${r.items.length} application${r.items.length === 1 ? "" : "s"} ${label(status).toLowerCase()}`;
  $("queue").innerHTML = r.items.length ? r.items.map(card).join("") : `<p class="empty-row">Nothing in this queue.</p>`;
}

async function review(cardEl, action) {
  const msg = cardEl.querySelector("[data-msg]");
  const checks = Object.fromEntries([...cardEl.querySelectorAll("[data-check]")].map(c => [c.dataset.check, c.checked]));
  const reason = cardEl.querySelector("[data-reason]")?.value || "";
  const evidence = (cardEl.querySelector("[data-evidence]")?.value || "").split("\n").map(v => v.trim()).filter(Boolean);
  cardEl.querySelectorAll("button").forEach(b => b.disabled = true);
  try {
    await api(`/verification/admin/applications/${encodeURIComponent(cardEl.dataset.id)}/review`, {
      method: "POST", body: { action, expected_version: Number(cardEl.dataset.version), reason, checks, evidence } });
    showBanner("");
    await load();
  } catch (err) {
    msg.style.color = "#ab4c45";
    msg.textContent = err.status === 409 ? "This application changed. Refreshing…" : err.message;
    cardEl.querySelectorAll("button").forEach(b => b.disabled = false);
    if (err.status === 409) setTimeout(load, 1200);
  }
}

async function toggleAudit(cardEl) {
  const list = cardEl.querySelector("[data-audit-list]");
  if (!list.hidden) { list.hidden = true; return; }
  const r = await api(`/verification/admin/applications/${encodeURIComponent(cardEl.dataset.id)}/audit`);
  list.innerHTML = r.items.map(e => {
    const ev = e.evidence || {};
    const extra = [ev.reason && `Reason: ${ev.reason}`, ...(ev.evidence || []).map(x => `Evidence: ${x}`), ev.note].filter(Boolean);
    return `<li><span class="table-primary">${esc(label(e.action))}</span>
      <span class="table-secondary">${esc(e.from_status ? label(e.from_status) + " → " : "")}${esc(label(e.to_status))} · v${e.application_version} · ${esc(e.actor_id)} · ${esc(when(e.created_at))}</span>
      ${extra.map(x => `<span class="table-secondary wrap">${esc(x)}</span>`).join("")}</li>`;
  }).join("");
  list.hidden = false;
}

// ---------------------------------------------------------------- merchants table + stats

function renderMerchants(list) {
  $("rows").innerHTML = list.length ? list.map(m => `<tr>
      <td><span class="table-primary">${esc(m.business_name)}</span><span class="table-secondary">${esc(m.email)}</span></td>
      <td>${esc(label(m.business_type))}</td>
      <td>${esc(m.country)}</td>
      <td>${safeUrl(m.website) ? `<a class="text-link" href="${esc(safeUrl(m.website))}" target="_blank" rel="noopener noreferrer">${esc(m.website)}</a>` : esc(m.website)}</td>
      <td><span class="status-pill status-${esc(m.status)}">${esc(m.status)}</span></td>
      <td>${m.risk_flags.map(x => `<span class="flag">${esc(x)}</span>`).join("") || '<span class="table-secondary">none</span>'}</td></tr>`).join("")
    : `<tr><td class="empty-row" colspan="6">No merchants yet.</td></tr>`;
}

async function load() {
  try {
    const [stats, list] = await Promise.all([api("/api/admin/stats"), api("/api/admin/merchants")]);
    csrf = list.csrf_token;
    merchantsById = new Map(list.merchants.map(m => [m.id, m]));
    show("app");
    const apps = stats.applications || {};
    $("s-pending").textContent = (apps.submitted || 0) + (apps.under_review || 0);
    $("s-approved").textContent = stats.merchants.approved || 0;
    $("s-wallets").textContent = stats.wallets;
    $("s-volume").textContent = stats.topup_volume.toLocaleString("en-US");
    renderMerchants(list.merchants);
    await loadQueue();
  } catch (err) {
    if (err.status === 401) show("login"); else showBanner(err.message);
  }
}

$("queue").addEventListener("click", e => {
  const cardEl = e.target.closest(".review-card");
  if (!cardEl) return;
  const a = e.target.closest("[data-action]");
  if (a) review(cardEl, a.dataset.action);
  if (e.target.closest("[data-audit]")) toggleAudit(cardEl).catch(err => showBanner(err.message));
});

$("login-form").addEventListener("submit", async e => {
  e.preventDefault();
  $("login-error").hidden = true;
  try {
    const r = await api("/api/admin/login", { method: "POST", body: { token: $("token").value } });
    csrf = r.csrf_token;
    load();
  } catch (err) { $("login-error").textContent = err.message; $("login-error").hidden = false; }
});

$("queue-status").addEventListener("change", () => loadQueue().catch(err => showBanner(err.message)));
$("menu-button").addEventListener("click", () => $("sidebar").classList.add("open"));
$("sidebar-close").addEventListener("click", () => $("sidebar").classList.remove("open"));

load();
