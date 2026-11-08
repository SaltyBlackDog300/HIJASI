// Renders the Kassara plans from /api/pricing (server is the single source of truth).
(function () {
  const eur = (cents) => "€" + (cents / 100).toLocaleString("en-IE", { minimumFractionDigits: cents % 100 ? 2 : 0, maximumFractionDigits: 2 });
  const perTxn = (v) => "€" + String(v).replace(/0+$/, "").replace(/\.$/, "");
  const esc = (s) => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function feeText(p) {
    return p.topup_fee_percent ? `${p.topup_fee_percent}% per top-up, min. ${eur(p.topup_fee_min)}` : "0% within allowance";
  }
  function allowanceText(p) {
    if (p.topup_fee_percent) return "Not applicable";
    return p.topup_volume_allowance == null ? "To be confirmed" : `${eur(p.topup_volume_allowance)} / month`;
  }

  window.renderPlanCards = function (el, plans, opts = {}) {
    el.innerHTML = plans.map(p => `
      <article class="plan-card${p.highlight ? " plan-highlight" : ""}${opts.current === p.id ? " plan-current" : ""}">
        ${p.highlight ? '<span class="plan-badge">Most popular</span>' : ""}
        <h3>${esc(p.name)}</h3>
        <div class="plan-price"><strong>${eur(p.monthly_price)}</strong><span>/ month</span></div>
        <dl class="plan-facts">
          <div><dt>Kassara top-up fee</dt><dd>${feeText(p)}</dd></div>
          <div><dt>Wallet transactions</dt><dd>${p.included_transactions.toLocaleString("en")} / month</dd></div>
          <div><dt>Extra transactions</dt><dd>${perTxn(p.overage_per_transaction)} each</dd></div>
          <div><dt>Top-up volume allowance</dt><dd>${allowanceText(p)}</dd></div>
        </dl>
        <ul class="plan-features">${p.features.map(f => `<li>${esc(f)}</li>`).join("")}</ul>
        ${opts.action ? opts.action(p) : ""}
      </article>`).join("");
  };

  const landing = document.getElementById("pricing-plans");
  if (landing) {
    fetch("/api/pricing").then(r => r.json()).then(d => {
      window.renderPlanCards(landing, d.plans, {
        action: p => `<a class="${p.highlight ? "primary-button" : "secondary-button"} plan-cta" href="/merchant#register">Get started <span aria-hidden="true">→</span></a>`,
      });
    }).catch(() => { landing.innerHTML = '<p class="field-hint">Pricing could not be loaded. Please refresh.</p>'; });
  }
})();
