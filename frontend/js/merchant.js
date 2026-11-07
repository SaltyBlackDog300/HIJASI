let currency = "nok";

function show(view) {
  for (const id of ["authView", "pendingView", "dashView"]) $("#" + id).classList.toggle("hidden", id !== view);
  $("#logoutBtn").classList.toggle("hidden", view === "authView");
}

function switchTab(tab) {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  $("#loginForm").classList.toggle("hidden", tab !== "login");
  $("#registerForm").classList.toggle("hidden", tab !== "register");
  $("#authAlert").classList.add("hidden");
}

document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));

$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await api("/api/auth/login", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    load();
  } catch (err) { showAlert($("#authAlert"), err.message); }
});

$("#registerForm").addEventListener("submit", async e => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  data.country = data.country.toUpperCase();
  data.currency = data.currency.toLowerCase();
  try {
    await api("/api/auth/register", { method: "POST", body: data });
    load();
  } catch (err) { showAlert($("#authAlert"), err.message); }
});

$("#logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  show("authView");
});

$("#rollKey").addEventListener("click", async () => {
  if (!confirm("Generate a new key? Your current key will stop working.")) return;
  try {
    const r = await api("/api/keys", { method: "POST" });
    const box = $("#newKey");
    box.innerHTML = `<div class="keybox">${esc(r.secret_key)}</div><p class="muted" style="margin-top:6px">${esc(r.note)}</p>`;
    box.classList.remove("hidden");
    loadMe();
  } catch (err) { alert(err.message); }
});

$("#policyForm").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {};
  for (const k of ["min_topup", "max_topup", "max_balance"]) body[k] = Math.round(parseFloat(f.get(k)) * 100);
  try {
    await api("/api/policy", { method: "PUT", body });
    showAlert($("#policyAlert"), "Policy saved.", "info");
  } catch (err) { showAlert($("#policyAlert"), err.message); }
});

async function loadMe() {
  const me = await api("/api/me");
  const m = me.merchant;
  currency = m.currency;
  $("#merchantName").textContent = m.business_name;
  $("#merchantMeta").textContent = `${m.business_type.replace("_", " ")} · ${m.country} · ${m.currency.toUpperCase()}`;
  $("#gatewayName").textContent = me.gateway === "stripe" ? "Stripe (test mode)" : "Mock gateway";
  $("#keyInfo").innerHTML = me.api_keys.length
    ? me.api_keys.map(k => `Active key <code>${esc(k.prefix)}…</code> · last used ${esc(fmtDate(k.last_used_at))}`).join("<br>")
    : "No key yet.";
  const p = m.policy, form = $("#policyForm");
  form.min_topup.value = (p.min_topup / 100).toFixed(2);
  form.max_topup.value = (p.max_topup / 100).toFixed(2);
  form.max_balance.value = (p.max_balance / 100).toFixed(2);
  return m;
}

async function loadData() {
  const [stats, wallets, txns] = await Promise.all([api("/api/stats"), api("/api/wallets"), api("/api/transactions")]);
  $("#statFloat").textContent = money(stats.float_total, currency);
  $("#statWallets").textContent = stats.wallets;
  $("#statTopups").textContent = money(stats.by_type.topup?.total || 0, currency);
  $("#statSpent").textContent = money(-(stats.by_type.charge?.total || 0) - (stats.by_type.refund?.total || 0), currency);

  $("#walletRows").innerHTML = wallets.wallets.map(w => `
    <tr><td class="mono">${esc(w.customer_id)}</td><td>${esc(w.display_name || "—")}</td>
    <td><span class="badge ${esc(w.status)}">${esc(w.status)}</span></td>
    <td class="num">${money(w.balance, w.currency)}</td></tr>`).join("")
    || `<tr><td colspan="4" class="muted">No wallets yet. Create one through the API.</td></tr>`;

  $("#txnRows").innerHTML = txns.transactions.map(t => `
    <tr><td>${esc(fmtDate(t.created_at))}</td><td class="mono">${esc(t.id)}</td>
    <td class="mono">${esc(t.customer_id)}</td><td>${esc(t.type)}</td><td>${esc(t.reference || "—")}</td>
    <td class="num ${t.amount < 0 ? "neg" : "pos"}">${money(t.amount, currency)}</td>
    <td class="num">${money(t.balance_after, currency)}</td></tr>`).join("")
    || `<tr><td colspan="7" class="muted">No transactions yet.</td></tr>`;
}

async function load() {
  try {
    const m = await loadMe();
    if (m.status !== "approved") {
      $("#pendingTitle").textContent = m.status === "pending" ? "Under review" : `Application ${m.status}`;
      $("#pendingText").textContent = m.status === "pending"
        ? "We're checking your business. You'll get API access once approved."
        : (m.review_note || "Contact support for details.");
      show("pendingView");
      return;
    }
    show("dashView");
    await loadData();
  } catch (err) {
    if (err.status === 401) {
      show("authView");
      switchTab(location.hash === "#register" ? "register" : "login");
    } else alert(err.message);
  }
}

load();
setInterval(() => { if (!$("#dashView").classList.contains("hidden")) loadData().catch(() => {}); }, 5000);
