// NIFTYBOX Terminal Client Application
let ws = null;
let currentTab = "tab-oc";
let selectedExpiry = "";
let selectedTf = "5m";
let currentStrikes = [];

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initModals();
  initExpiries();
  connectWebSocket();
  startFallbackPolling();
  checkUrlParams();
  loadDataHubFiles();
  loadEodRadar();
  loadMacroTable();
});

// Check if URL has ?auth_success=1 or errors
function checkUrlParams() {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.has("auth_success")) {
    showToast("🎉 Fyers Authorization Successful! Fresh access token active.", "success");
    window.history.replaceState({}, document.title, window.location.pathname);
  } else if (urlParams.has("auth_error")) {
    showToast(`⚠️ Auth error: ${urlParams.get("auth_error")}`, "error");
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}

// ---------------------------------------------------------------------------
// Tabs System
// ---------------------------------------------------------------------------
function initTabs() {
  const tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      
      btn.classList.add("active");
      const target = btn.dataset.tab;
      currentTab = target;
      document.getElementById(target).classList.add("active");

      if (target === "tab-oc") fetchOptionChain();
      if (target === "tab-macro") loadMacroTable();
      if (target === "tab-eod") loadEodRadar();
      if (target === "tab-chart") renderCharts();
      if (target === "tab-files") loadDataHubFiles();
    });
  });
}

// ---------------------------------------------------------------------------
// WebSocket Real-time Feed
// ---------------------------------------------------------------------------
function connectWebSocket() {
  const loc = window.location;
  const wsUrl = `ws://${loc.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("[WebSocket] Connected to NIFTYBOX stream.");
  };

  ws.onmessage = (event) => {
    try {
      const packet = JSON.parse(event.data);
      if (packet.type === "tick") {
        updateTopKpis(packet.summary);
        updateHeaderStatus(packet.status, packet.totp);
        if (currentTab === "tab-oc") {
          fetchOptionChain();
        }
      }
    } catch (e) {
      console.error("[WebSocket] Parse error:", e);
    }
  };

  ws.onclose = () => {
    console.log("[WebSocket] Closed. Reconnecting in 3s...");
    setTimeout(connectWebSocket, 3000);
  };
}

function startFallbackPolling() {
  setInterval(async () => {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      const totpRes = await fetch("/api/totp");
      const totpData = await totpRes.json();
      updateHeaderStatus(data.status, totpData);
      
      const liveRes = await fetch(`/api/live?expiry_ts=${selectedExpiry}`);
      const liveData = await liveRes.json();
      if (liveData && liveData.summary) {
        updateTopKpis(liveData.summary);
      }
    } catch (e) {
      // offline polling
    }
  }, 4000);
}

// ---------------------------------------------------------------------------
// UI State Updates
// ---------------------------------------------------------------------------
function updateHeaderStatus(status, totp) {
  const connPill = document.getElementById("conn-pill");
  const connText = document.getElementById("conn-text");
  
  if (status && status.connected) {
    connPill.className = "conn-pill conn-live";
    connText.innerHTML = `🟢 FYERS LIVE [${status.fy_id}]`;
  } else {
    connPill.className = "conn-pill conn-offline";
    connText.innerHTML = `⚠️ FYERS OFFLINE`;
  }

  // TOTP Card
  const totpDigits = document.getElementById("totp-digits");
  const totpTimer = document.getElementById("totp-timer");
  if (totp && totp.has_key) {
    const c = totp.code || "------";
    totpDigits.textContent = c.length === 6 ? `${c.slice(0,3)} ${c.slice(3)}` : c;
    totpTimer.textContent = `${totp.remaining_secs || 0}s`;
  } else {
    totpDigits.textContent = "KEY MISSING";
    totpTimer.textContent = "--";
  }
}

function updateTopKpis(s) {
  if (!s) return;

  // Spot
  const spotEl = document.getElementById("kpi-spot");
  const spotSub = document.getElementById("kpi-spot-sub");
  if (spotEl) spotEl.textContent = Number(s.spot_price).toLocaleString("en-IN", { minimumFractionDigits: 2 });
  if (spotSub) {
    const chg = s.spot_change || 0;
    const pct = s.spot_pct || 0;
    const isUp = chg >= 0;
    spotSub.className = `kpi-sub ${isUp ? 'text-up' : 'text-down'}`;
    spotSub.innerHTML = `<span>${isUp ? '▲' : '▼'} ${Math.abs(chg).toFixed(2)} (${Math.abs(pct).toFixed(2)}%)</span>`;
  }

  // Future & Basis
  const futEl = document.getElementById("kpi-fut");
  const futSub = document.getElementById("kpi-fut-sub");
  if (futEl) futEl.textContent = Number(s.fut_price).toLocaleString("en-IN", { minimumFractionDigits: 2 });
  if (futSub) {
    const basis = s.basis_diff || 0;
    futSub.className = `kpi-sub ${basis >= 0 ? 'text-up' : 'text-down'}`;
    futSub.innerHTML = `<span>Basis: ${basis >= 0 ? '+' : ''}${basis.toFixed(2)} (${s.basis_type || 'Premium'})</span>`;
  }

  // PCR & Max Pain
  const pcrEl = document.getElementById("kpi-pcr");
  const pcrSub = document.getElementById("kpi-pcr-sub");
  if (pcrEl) pcrEl.textContent = Number(s.pcr || 1.0).toFixed(2);
  if (pcrSub) pcrSub.innerHTML = `<span>Max Pain: <strong>${s.max_pain || s.atm_strike}</strong></span>`;

  // Net Basket Delta
  const deltaEl = document.getElementById("kpi-delta");
  const deltaSub = document.getElementById("kpi-delta-sub");
  if (deltaEl) {
    const dVal = s.net_basket_delta || 0;
    deltaEl.className = `kpi-val ${dVal >= 0 ? 'text-up' : 'text-down'}`;
    deltaEl.textContent = `${dVal >= 0 ? '+' : ''}${(dVal / 1e5).toFixed(2)} L`;
  }
  if (deltaSub) {
    deltaSub.innerHTML = `<span>Calls: ${(s.active_ce_oi_chg/1e5).toFixed(1)}L | Puts: ${(s.active_pe_oi_chg/1e5).toFixed(1)}L</span>`;
  }

  // Signals
  const sig1El = document.getElementById("kpi-sig1");
  const sig2El = document.getElementById("kpi-sig2");
  if (sig1El) sig1El.textContent = s.overall_signal || "—";
  if (sig2El) sig2El.textContent = s.smart_signal || "—";
}

// ---------------------------------------------------------------------------
// Option Chain Renderer
// ---------------------------------------------------------------------------
async function fetchOptionChain() {
  try {
    const res = await fetch(`/api/optionchain?expiry_ts=${selectedExpiry}`);
    const data = await res.json();
    if (!data || !data.strikes) return;

    currentStrikes = data.strikes;
    const atm = data.summary.atm_strike;
    renderOptionChainTable(data.strikes, atm);
  } catch (e) {
    console.error("Error fetching option chain:", e);
  }
}

function renderOptionChainTable(strikes, atm) {
  const tbody = document.getElementById("oc-tbody");
  if (!tbody) return;

  let maxOi = 1;
  strikes.forEach(s => {
    if (s.CE && s.CE.oi > maxOi) maxOi = s.CE.oi;
    if (s.PE && s.PE.oi > maxOi) maxOi = s.PE.oi;
  });

  let html = "";
  strikes.forEach(row => {
    const ce = row.CE || {};
    const pe = row.PE || {};
    const strike = row.strike;
    const isAtm = strike === atm;

    const ceOiWidth = Math.min(100, Math.round(((ce.oi || 0) / maxOi) * 100));
    const peOiWidth = Math.min(100, Math.round(((pe.oi || 0) / maxOi) * 100));

    const ceChgClass = (ce.ltpch || 0) >= 0 ? "text-up" : "text-down";
    const peChgClass = (pe.ltpch || 0) >= 0 ? "text-up" : "text-down";

    html += `
      <tr class="${isAtm ? 'atm-row' : ''}">
        <!-- CALLS -->
        <td>${ce.iv ? ce.iv.toFixed(1) + '%' : '—'}</td>
        <td>${ce.delta ? ce.delta.toFixed(2) : '—'}</td>
        <td>${Number(ce.volume || 0).toLocaleString()}</td>
        <td class="${(ce.change_in_oi || 0) >= 0 ? 'text-up' : 'text-down'}">${Number(ce.change_in_oi || 0).toLocaleString()}</td>
        <td class="heat-cell">
          <div class="heat-bar-ce" style="width: ${ceOiWidth}%"></div>
          <span class="heat-val">${Number(ce.oi || 0).toLocaleString()}</span>
        </td>
        <td style="font-weight:700;">${Number(ce.ltp || 0).toFixed(2)}</td>
        <td class="${ceChgClass}">${(ce.ltpch || 0) >= 0 ? '+' : ''}${Number(ce.ltpch || 0).toFixed(2)}</td>

        <!-- STRIKE -->
        <td class="center">
          <span class="strike-pill ${isAtm ? 'strike-atm' : ''}">${strike} ${isAtm ? '★ ATM' : ''}</span>
        </td>

        <!-- PUTS -->
        <td class="${peChgClass}">${(pe.ltpch || 0) >= 0 ? '+' : ''}${Number(pe.ltpch || 0).toFixed(2)}</td>
        <td style="font-weight:700;">${Number(pe.ltp || 0).toFixed(2)}</td>
        <td class="heat-cell">
          <div class="heat-bar-pe" style="width: ${peOiWidth}%"></div>
          <span class="heat-val">${Number(pe.oi || 0).toLocaleString()}</span>
        </td>
        <td class="${(pe.change_in_oi || 0) >= 0 ? 'text-up' : 'text-down'}">${Number(pe.change_in_oi || 0).toLocaleString()}</td>
        <td>${Number(pe.volume || 0).toLocaleString()}</td>
        <td>${pe.delta ? pe.delta.toFixed(2) : '—'}</td>
        <td>${pe.iv ? pe.iv.toFixed(1) + '%' : '—'}</td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Macro Bar-by-Bar Table
// ---------------------------------------------------------------------------
async function loadMacroTable() {
  try {
    const res = await fetch(`/api/macro-table?timeframe=${selectedTf}`);
    const data = await res.json();
    const tbody = document.getElementById("macro-tbody");
    if (!tbody || !data.rows) return;

    let html = "";
    data.rows.forEach(r => {
      const pClass = r.change >= 0 ? "text-up" : "text-down";
      const cvdClass = r.cvd >= 0 ? "text-up" : "text-down";
      const netClass = r.net_delta_lakhs >= 0 ? "text-up" : "text-down";

      let sig1Class = "pill-unwind";
      if (r.signal1.includes("Long Build-up")) sig1Class = "pill-bull";
      else if (r.signal1.includes("Short Build-up")) sig1Class = "pill-bear";
      else if (r.signal1.includes("Short Covering")) sig1Class = "pill-cover";

      let sig2Class = "pill-unwind";
      if (r.signal2.includes("Bear Trap")) sig2Class = "pill-trap";
      else if (r.signal2.includes("Bull Trap")) sig2Class = "pill-trap";
      else if (r.signal2.includes("Long Buildup")) sig2Class = "pill-bull";
      else if (r.signal2.includes("Short Buildup")) sig2Class = "pill-bear";

      html += `
        <tr>
          <td style="font-weight:700; color:var(--cyan);">${r.time}</td>
          <td>${Number(r.spot).toFixed(2)}</td>
          <td class="${pClass}">${r.change >= 0 ? '+' : ''}${r.change.toFixed(2)}</td>
          <td>${Number(r.future).toFixed(2)}</td>
          <td>${r.basis >= 0 ? '+' : ''}${r.basis.toFixed(2)}</td>
          <td class="${r.vol_delta >= 0 ? 'text-up' : 'text-down'}">${r.vol_delta > 0 ? '+' : ''}${r.vol_delta}</td>
          <td class="${cvdClass}">${Number(r.cvd).toLocaleString()}</td>
          <td class="${r.ce_delta_lakhs >= 0 ? 'text-up' : 'text-down'}">${r.ce_delta_lakhs >= 0 ? '+' : ''}${r.ce_delta_lakhs}L</td>
          <td class="${r.pe_delta_lakhs >= 0 ? 'text-up' : 'text-down'}">${r.pe_delta_lakhs >= 0 ? '+' : ''}${r.pe_delta_lakhs}L</td>
          <td class="${netClass}" style="font-weight:bold;">${r.net_delta_lakhs >= 0 ? '+' : ''}${r.net_delta_lakhs}L</td>
          <td><span class="pill ${sig1Class}">${r.signal1}</span></td>
          <td><span class="pill ${sig2Class}">${r.signal2}</span></td>
        </tr>
      `;
    });
    tbody.innerHTML = html;
  } catch (e) {
    console.error("Macro table error:", e);
  }
}

// ---------------------------------------------------------------------------
// EOD Radar
// ---------------------------------------------------------------------------
async function loadEodRadar() {
  try {
    const res = await fetch("/api/eod-radar");
    const data = await res.json();
    
    document.getElementById("radar-verdict").textContent = data.verdict || "—";
    document.getElementById("radar-rationale").textContent = data.bias_rationale || "";
    document.getElementById("radar-range").textContent = data.expected_range || "—";
    document.getElementById("radar-confidence").textContent = `Confidence: ${data.confidence || '0%'}`;
    document.getElementById("radar-straddle").textContent = `± ${data.straddle_points || 0} pts`;
  } catch (e) {
    console.error("EOD radar error:", e);
  }
}

// ---------------------------------------------------------------------------
// Intraday Data Hub & Zero-Duplicate Catalog
// ---------------------------------------------------------------------------
async function loadDataHubFiles() {
  try {
    const res = await fetch("/api/files");
    const data = await res.json();
    const tbody = document.getElementById("files-tbody");
    if (!tbody || !data.files) return;

    if (data.files.length === 0) {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--text-muted);">No recorded files yet today.</td></tr>`;
      return;
    }

    let html = "";
    data.files.forEach(f => {
      html += `
        <tr>
          <td style="font-family:'JetBrains Mono'; font-weight:700; color:var(--cyan);">${f.filename}</td>
          <td>${f.size_kb} KB</td>
          <td>${f.modified}</td>
          <td>
            <a href="/api/download-file?filename=${f.filename}" download class="btn-icon" style="text-decoration:none; display:inline-flex;">
              ⬇ Download CSV
            </a>
          </td>
        </tr>
      `;
    });
    tbody.innerHTML = html;
  } catch (e) {
    console.error("Files catalog error:", e);
  }
}

// ---------------------------------------------------------------------------
// Expiries Initializer
// ---------------------------------------------------------------------------
async function initExpiries() {
  try {
    const res = await fetch("/api/expiries");
    const data = await res.json();
    const sel = document.getElementById("expiry-select");
    if (!sel || !data.all_expiries) return;

    sel.innerHTML = "";
    data.all_expiries.forEach(exp => {
      const opt = document.createElement("option");
      opt.value = exp.timestamp;
      opt.textContent = `${exp.flag} (${exp.date} • ${exp.dte}d)`;
      sel.appendChild(opt);
    });

    sel.addEventListener("change", () => {
      selectedExpiry = sel.value;
      fetchOptionChain();
    });
  } catch (e) {
    console.error("Expiries fetch error:", e);
  }
}

// ---------------------------------------------------------------------------
// Modals & Settings
// ---------------------------------------------------------------------------
function initModals() {
  const modal = document.getElementById("settings-modal");
  const openBtn = document.getElementById("btn-open-settings");
  const closeBtn = document.getElementById("btn-close-modal");
  const saveBtn = document.getElementById("btn-save-creds");
  const authBtn = document.getElementById("btn-fyers-auth");
  const autoLoginBtn = document.getElementById("btn-auto-login");

  openBtn.addEventListener("click", () => modal.style.display = "flex");
  closeBtn.addEventListener("click", () => modal.style.display = "none");
  window.addEventListener("click", (e) => {
    if (e.target === modal) modal.style.display = "none";
  });

  authBtn.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/auth/login-url");
      const data = await res.json();
      if (data.auth_url) {
        window.open(data.auth_url, "_blank");
        showToast("Opening Fyers login. Authorize to redirect automatically!", "info");
      }
    } catch (e) {
      showToast("Failed to generate Fyers login URL.", "error");
    }
  });

  autoLoginBtn.addEventListener("click", async () => {
    showToast("Starting automated desktop TOTP login...", "info");
    try {
      const res = await fetch("/api/auth/auto-login", { method: "POST" });
      const data = await res.json();
      if (data.success) {
        showToast("✅ Auto-Login successful! Fresh token active.", "success");
        modal.style.display = "none";
      } else if (data.needs_browser) {
        window.open(data.auth_url, "_blank");
        showToast("Challenged by captcha. Opened 1-Click browser authorize.", "info");
      } else {
        showToast(`❌ Auto-Login error: ${data.error || 'Failed'}`, "error");
      }
    } catch (e) {
      showToast("Auto-login request failed.", "error");
    }
  });

  saveBtn.addEventListener("click", async () => {
    const creds = {
      client_id: document.getElementById("input-appid").value.trim(),
      secret_key: document.getElementById("input-secret").value.trim(),
      fy_id: document.getElementById("input-fyid").value.trim(),
      pin: document.getElementById("input-pin").value.trim(),
      totp_key: document.getElementById("input-totp").value.trim()
    };

    try {
      const res = await fetch("/api/auth/save-creds", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(creds)
      });
      const data = await res.json();
      if (data.success) {
        showToast("Credentials saved successfully!", "success");
        modal.style.display = "none";
      }
    } catch (e) {
      showToast("Error saving credentials.", "error");
    }
  });
}

function showToast(msg, type = "info") {
  const t = document.createElement("div");
  t.style.position = "fixed";
  t.style.bottom = "24px";
  t.style.right = "24px";
  t.style.background = type === "success" ? "#064e3b" : (type === "error" ? "#7f1d1d" : "#1e3a8a");
  t.style.color = "#ffffff";
  t.style.padding = "12px 20px";
  t.style.borderRadius = "8px";
  t.style.boxShadow = "0 8px 30px rgba(0,0,0,0.6)";
  t.style.zIndex = "9999";
  t.style.fontWeight = "700";
  t.style.fontSize = "13px";
  t.style.border = "1px solid rgba(255,255,255,0.2)";
  t.textContent = msg;

  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
