function show(view) {
  $("#loginView").classList.toggle("hidden", view !== "login");
  $("#mainView").classList.toggle("hidden", view !== "main");
}

$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await api("/api/admin/login", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    load();
  } catch (err) { showAlert($("#loginAlert"), err.message); }
});

async function review(id, decision) {
  const note = decision === "approve" ? "" : prompt(`Reason to ${decision}?`) || "";
  try {
    await api(`/api/admin/merchants/${id}/review`, { method: "POST", body: { decision, note } });
    load();
  } catch (err) { alert(err.message); }
}

function actions(m) {
  const b = (d, label, cls = "") => `<button class="btn small ${cls}" onclick="review(${m.id}, '${d}')">${label}</button>`;
  if (m.status === "pending") return b("approve", "Approve", "primary") + " " + b("reject", "Reject", "danger");
  if (m.status === "approved") return b("suspend", "Suspend", "danger");
  return b("approve", "Re-approve");
}

async function load() {
  try {
    const [stats, list] = await Promise.all([api("/api/admin/stats"), api("/api/admin/merchants")]);
    show("main");
    $("#sPending").textContent = stats.merchants.pending || 0;
    $("#sApproved").textContent = stats.merchants.approved || 0;
    $("#sWallets").textContent = stats.wallets;
    $("#sVolume").textContent = stats.topup_volume.toLocaleString();
    $("#rows").innerHTML = list.merchants.map(m => `
      <tr>
        <td><b>${esc(m.business_name)}</b><br><span class="muted" style="white-space:normal;font-size:.85rem">${esc(m.description || "")}</span></td>
        <td>${esc(m.business_type.replace("_", " "))}</td>
        <td>${esc(m.country)}</td>
        <td><a href="${esc(m.website)}" target="_blank" rel="noopener noreferrer">${esc(m.website)}</a></td>
        <td><span class="badge ${esc(m.status)}">${esc(m.status)}</span></td>
        <td>${m.risk_flags.map(f => `<span class="flag">${esc(f)}</span>`).join("") || '<span class="muted">none</span>'}</td>
        <td>${actions(m)}</td>
      </tr>`).join("") || `<tr><td colspan="7" class="muted">No applications yet.</td></tr>`;
  } catch (err) {
    if (err.status === 401) show("login"); else alert(err.message);
  }
}

load();
