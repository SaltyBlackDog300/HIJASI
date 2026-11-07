// Shared helpers for the dashboard pages.
async function api(path, options = {}) {
  const opts = { credentials: "same-origin", headers: {}, ...options };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON */ }
  if (!res.ok) {
    const msg = (data && data.error && data.error.message) || `Request failed (${res.status})`;
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function money(minor, currency) {
  const v = (minor || 0) / 100;
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: (currency || "nok").toUpperCase() }).format(v);
  } catch (_) {
    return `${v.toFixed(2)} ${(currency || "").toUpperCase()}`;
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function $(sel, root = document) { return root.querySelector(sel); }

function showAlert(el, msg, kind = "error") {
  el.className = `alert ${kind}`;
  el.textContent = msg;
  el.classList.remove("hidden");
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}
