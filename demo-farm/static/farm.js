const $ = s => document.querySelector(s);
let products = [], currency = "eur", cart = {}, balance = 0, checkoutId = newCheckoutId();

function newCheckoutId() { return (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^a-zA-Z0-9-]/g, ""); }

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

function money(minor) {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: currency.toUpperCase() }).format(minor / 100); }
  catch (_) { return (minor / 100).toFixed(2) + " " + currency.toUpperCase(); }
}

async function call(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json" }, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(data?.error?.message || "Something went wrong"); e.status = res.status; throw e; }
  return data;
}

function notice(msg, kind = "") {
  const n = $("#notice");
  n.textContent = msg;
  n.className = "notice " + kind;
  clearTimeout(notice.t);
  notice.t = setTimeout(() => n.classList.add("hidden"), 6000);
}

function renderProducts() {
  $("#products").innerHTML = products.map(p => `
    <div class="product">
      <div class="emoji">${p.emoji}</div>
      <div class="name">${esc(p.name)}</div>
      <div class="unit">${esc(p.unit)}</div>
      <div class="price">${money(p.price)}</div>
      <button class="btn light" data-add="${esc(p.id)}">Add to basket</button>
    </div>`).join("");
}

function cartTotal() { return Object.entries(cart).reduce((s, [id, q]) => s + products.find(p => p.id === id).price * q, 0); }

function renderCart() {
  const ids = Object.keys(cart);
  $("#cart").innerHTML = ids.length ? ids.map(id => {
    const p = products.find(x => x.id === id);
    return `<div class="cart-line"><span>${esc(p.name)}</span>
      <span class="qty"><button data-dec="${esc(id)}">−</button> ${cart[id]} <button data-add="${esc(id)}">+</button></span></div>`;
  }).join("") : `<p class="empty">Your basket is empty.</p>`;
  const total = cartTotal();
  $("#cartTotal").textContent = money(total);
  const pay = $("#payBtn");
  pay.disabled = total === 0;
  pay.textContent = total > balance ? `Top up to pay (${money(total - balance)} short)` : "Pay with wallet";
}

async function renderTopups(policy) {
  const presets = [2000, 5000, 10000].filter(a => !policy || (a >= policy.min_topup && a <= policy.max_topup));
  $("#topupBtns").innerHTML = presets.map(a => `<button class="btn" data-topup="${a}">+ ${money(a)}<small data-pay="${a}"></small></button>`).join("");
  // Price breakdown from Kassara before checkout: wallet credit + Kassara fee.
  const quotes = await Promise.all(presets.map(a => call(`/api/topup-quote?amount=${a}`).catch(() => null)));
  let anyFee = false;
  quotes.forEach((q, i) => {
    if (!q) return;
    const el = document.querySelector(`[data-pay="${presets[i]}"]`);
    if (q.fee) { anyFee = true; el.textContent = `pay ${money(q.total)}`; }
  });
  const q = quotes.find(Boolean);
  $("#feeNote").textContent = q && anyFee
    ? `Top-ups include a ${q.fee_label}. Your wallet receives the top-up amount. Card processing is handled by the payment provider.`
    : "";
}

async function loadWallet() {
  const d = await call("/api/wallet");
  balance = d.wallet.balance;
  currency = d.wallet.currency;
  $("#whoName").textContent = d.name;
  $("#balance").textContent = money(balance);
  $("#history").innerHTML = d.transactions.map(t => `
    <li><span class="desc">${esc(t.type === "topup" ? "Top-up" : t.type === "refund" ? "Refund" : (t.description || t.reference))}</span>
    <span class="${t.amount > 0 ? "plus" : "minus"}">${t.amount > 0 ? "+" : ""}${money(t.amount)}</span></li>`).join("")
    || `<li class="empty">No activity yet.</li>`;
  renderCart();
}

document.addEventListener("click", async e => {
  const t = e.target;
  if (t.dataset.add) { cart[t.dataset.add] = (cart[t.dataset.add] || 0) + 1; saveCart(); renderCart(); }
  if (t.dataset.dec) { if (--cart[t.dataset.dec] <= 0) delete cart[t.dataset.dec]; saveCart(); renderCart(); }
  if (t.dataset.topup) {
    t.disabled = true;
    try {
      const r = await call("/api/topup", { method: "POST", body: { amount: Number(t.dataset.topup) } });
      location.href = r.checkout_url;   // go to the payment page
    } catch (err) { notice(err.message, "error"); t.disabled = false; }
  }
});

$("#payBtn").addEventListener("click", async () => {
  const total = cartTotal();
  if (total > balance) { notice("Not enough wallet balance — top up first.", "error"); return; }
  const btn = $("#payBtn");
  btn.disabled = true;
  try {
    const items = Object.entries(cart).map(([id, qty]) => ({ id, qty }));
    const r = await call("/api/checkout", { method: "POST", body: { items, checkout_id: checkoutId } });
    saveCart();
    location.href = r.url;   // HIJASI secure checkout: the customer confirms with a one-time code
  } catch (err) { notice(err.message, "error"); btn.disabled = false; }
});

function saveCart() { try { sessionStorage.setItem("farm_cart", JSON.stringify(cart)); } catch (_) {} }
function restoreCart() {
  try { const c = JSON.parse(sessionStorage.getItem("farm_cart") || "{}"); if (c && typeof c === "object") cart = c; } catch (_) {}
  for (const id of Object.keys(cart)) if (!products.some(p => p.id === id) || !Number.isInteger(cart[id]) || cart[id] < 1) delete cart[id];
}
function clearCart() { cart = {}; checkoutId = newCheckoutId(); try { sessionStorage.removeItem("farm_cart"); } catch (_) {} }

$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await call("/api/login", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    start();
  } catch (err) { notice(err.message, "error"); }
});

$("#logout").addEventListener("click", async () => { await call("/api/logout", { method: "POST" }); location.reload(); });

async function handleReturn() {
  const q = new URLSearchParams(location.search);
  const cs = q.get("checkout");
  if (cs) {
    history.replaceState(null, "", "/");
    try {
      const r = await call(`/api/checkout/${encodeURIComponent(cs)}`);   // verified server-side
      if (r.status === "paid") { clearCart(); notice(`Order paid! ${money(r.amount)} taken from your wallet. Delivery on Saturday.`); }
      else notice(`Payment ${r.status}. Your basket is still here.`, "error");
    } catch (err) { notice(err.message, "error"); }
    renderCart();
    await loadWallet();
    return;
  }
  const id = q.get("topup");
  if (!id) return;
  history.replaceState(null, "", "/");
  try {
    const t = await call(`/api/topup/${encodeURIComponent(id)}`);
    if (t.status === "succeeded") notice(`Wallet topped up with ${money(t.amount)}.` + (t.fee ? ` You paid ${money(t.total)} including a ${money(t.fee)} Kassara fee.` : ""));
    else notice(`Top-up ${t.status}.`, "error");
  } catch (err) { notice(err.message, "error"); }
}

async function start() {
  try {
    const p = await call("/api/products");
    products = p.products;
    currency = p.currency;
    renderProducts();
    restoreCart();
    renderTopups(p.policy);
    await loadWallet();
    $("#loginView").classList.add("hidden");
    $("#shopView").classList.remove("hidden");
    $("#who").classList.remove("hidden");
    await handleReturn();
  } catch (err) {
    if (err.status === 401) { $("#loginView").classList.remove("hidden"); return; }
    notice(err.message, "error");
  }
}

start();
