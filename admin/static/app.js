// ISO 3166-1 alpha-2 country code to name map
const COUNTRY_NAMES = {
  AF:"Afghanistan",AL:"Albania",DZ:"Algeria",AS:"American Samoa",AD:"Andorra",
  AO:"Angola",AG:"Antigua and Barbuda",AR:"Argentina",AM:"Armenia",AU:"Australia",
  AT:"Austria",AZ:"Azerbaijan",BS:"Bahamas",BH:"Bahrain",BD:"Bangladesh",
  BB:"Barbados",BY:"Belarus",BE:"Belgium",BZ:"Belize",BJ:"Benin",BM:"Bermuda",
  BT:"Bhutan",BO:"Bolivia",BA:"Bosnia and Herzegovina",BW:"Botswana",BR:"Brazil",
  BN:"Brunei",BG:"Bulgaria",BF:"Burkina Faso",BI:"Burundi",CV:"Cabo Verde",
  KH:"Cambodia",CM:"Cameroon",CA:"Canada",CF:"Central African Republic",TD:"Chad",
  CL:"Chile",CN:"China",CO:"Colombia",KM:"Comoros",CG:"Congo",
  CD:"Congo (DRC)",CR:"Costa Rica",CI:"Cote d'Ivoire",HR:"Croatia",CU:"Cuba",
  CY:"Cyprus",CZ:"Czechia",DK:"Denmark",DJ:"Djibouti",DM:"Dominica",
  DO:"Dominican Republic",EC:"Ecuador",EG:"Egypt",SV:"El Salvador",GQ:"Equatorial Guinea",
  ER:"Eritrea",EE:"Estonia",SZ:"Eswatini",ET:"Ethiopia",FJ:"Fiji",
  FI:"Finland",FR:"France",GA:"Gabon",GM:"Gambia",GE:"Georgia",
  DE:"Germany",GH:"Ghana",GR:"Greece",GD:"Grenada",GU:"Guam",
  GT:"Guatemala",GN:"Guinea",GW:"Guinea-Bissau",GY:"Guyana",HT:"Haiti",
  HN:"Honduras",HK:"Hong Kong",HU:"Hungary",IS:"Iceland",IN:"India",
  ID:"Indonesia",IR:"Iran",IQ:"Iraq",IE:"Ireland",IL:"Israel",
  IT:"Italy",JM:"Jamaica",JP:"Japan",JO:"Jordan",KZ:"Kazakhstan",
  KE:"Kenya",KI:"Kiribati",KP:"North Korea",KR:"South Korea",KW:"Kuwait",
  KG:"Kyrgyzstan",LA:"Laos",LV:"Latvia",LB:"Lebanon",LS:"Lesotho",
  LR:"Liberia",LY:"Libya",LI:"Liechtenstein",LT:"Lithuania",LU:"Luxembourg",
  MO:"Macao",MG:"Madagascar",MW:"Malawi",MY:"Malaysia",MV:"Maldives",
  ML:"Mali",MT:"Malta",MH:"Marshall Islands",MR:"Mauritania",MU:"Mauritius",
  MX:"Mexico",FM:"Micronesia",MD:"Moldova",MC:"Monaco",MN:"Mongolia",
  ME:"Montenegro",MA:"Morocco",MZ:"Mozambique",MM:"Myanmar",NA:"Namibia",
  NR:"Nauru",NP:"Nepal",NL:"Netherlands",NZ:"New Zealand",NI:"Nicaragua",
  NE:"Niger",NG:"Nigeria",MK:"North Macedonia",NO:"Norway",OM:"Oman",
  PK:"Pakistan",PW:"Palau",PS:"Palestine",PA:"Panama",PG:"Papua New Guinea",
  PY:"Paraguay",PE:"Peru",PH:"Philippines",PL:"Poland",PT:"Portugal",
  PR:"Puerto Rico",QA:"Qatar",RE:"Reunion",RO:"Romania",RU:"Russia",
  RW:"Rwanda",SA:"Saudi Arabia",SN:"Senegal",RS:"Serbia",SC:"Seychelles",
  SL:"Sierra Leone",SG:"Singapore",SK:"Slovakia",SI:"Slovenia",SB:"Solomon Islands",
  SO:"Somalia",ZA:"South Africa",SS:"South Sudan",ES:"Spain",LK:"Sri Lanka",
  SD:"Sudan",SR:"Suriname",SE:"Sweden",CH:"Switzerland",SY:"Syria",
  TW:"Taiwan",TJ:"Tajikistan",TZ:"Tanzania",TH:"Thailand",TL:"Timor-Leste",
  TG:"Togo",TO:"Tonga",TT:"Trinidad and Tobago",TN:"Tunisia",TR:"Turkey",
  TM:"Turkmenistan",TV:"Tuvalu",UG:"Uganda",UA:"Ukraine",AE:"United Arab Emirates",
  GB:"United Kingdom",US:"United States",UY:"Uruguay",UZ:"Uzbekistan",VU:"Vanuatu",
  VE:"Venezuela",VN:"Vietnam",YE:"Yemen",ZM:"Zambia",ZW:"Zimbabwe",
};

function countryName(code) {
  if (!code) return "-";
  return COUNTRY_NAMES[code.toUpperCase()] || code;
}

// Clipboard helper with inline "Copied" feedback
function copyText(text, feedbackEl) {
  navigator.clipboard.writeText(text).then(() => {
    if (!feedbackEl) return;
    const prev = feedbackEl.textContent;
    feedbackEl.textContent = "Copied";
    feedbackEl.classList.add("copied");
    setTimeout(() => {
      feedbackEl.textContent = prev;
      feedbackEl.classList.remove("copied");
    }, 1200);
  }).catch(() => {});
}

// Note save via PATCH
async function saveNote(instanceId, text) {
  return api(`/api/instances/${instanceId}/note`, {
    method: "PATCH",
    body: JSON.stringify({ note: text }),
  });
}

const state = {
  config: null,
  status: null,
  instances: [],
  expandedRows: new Set(),
};

const els = {
  configuredInstances: document.querySelector("#configuredInstances"),
  healthyInstances: document.querySelector("#healthyInstances"),
  proxyMode: document.querySelector("#proxyMode"),
  basePort: document.querySelector("#basePort"),
  modeNote: document.querySelector("#modeNote"),
  instancesBody: document.querySelector("#instancesBody"),
  refreshBtn: document.querySelector("#refreshBtn"),
  exportBtn: document.querySelector("#exportBtn"),
  settingsBtn: document.querySelector("#settingsBtn"),
  settingsDialog: document.querySelector("#settingsDialog"),
  settingsForm: document.querySelector("#settingsForm"),
  closeSettings: document.querySelector("#closeSettings"),
  cancelSettings: document.querySelector("#cancelSettings"),
  settingsMessage: document.querySelector("#settingsMessage"),
  modeHelp: document.querySelector("#modeHelp"),
  applyProgress: document.querySelector("#applyProgress"),
  progressLabel: document.querySelector("#progressLabel"),
  progressPercent: document.querySelector("#progressPercent"),
  progressBar: document.querySelector("#progressBar"),
  updateAdminAccount: document.querySelector("#updateAdminAccount"),
  adminAccountMessage: document.querySelector("#adminAccountMessage"),
  exportDialog: document.querySelector("#exportDialog"),
  exportText: document.querySelector("#exportText"),
  exportMeta: document.querySelector("#exportMeta"),
  exportError: document.querySelector("#exportError"),
  exportAuthWarning: document.querySelector("#exportAuthWarning"),
  exportCopy: document.querySelector("#exportCopy"),
  exportDownload: document.querySelector("#exportDownload"),
  exportMessage: document.querySelector("#exportMessage"),
  closeExport: document.querySelector("#closeExport"),
  mobileCards: document.querySelector("#mobileCards"),
};

let progressTimer = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    try {
      const body = await response.json();
      message = body.errors ? body.errors.join(", ") : body.error || message;
    } catch {
      // Keep default message.
    }
    throw new Error(message);
  }
  return response.json();
}

function titleMode(mode) {
  return mode === "dedicated" ? "Dedicated" : "Round Robin";
}

function healthClass(value) {
  if (value === true || value === "healthy" || value === "on") return "ok";
  if (value === "degraded" || value === "connecting") return "warn";
  return "bad";
}

function pill(label, value) {
  return `<span class="pill ${healthClass(value)}">${label}</span>`;
}

function renderSummary() {
  const cfg = state.config;
  const status = state.status;
  if (!cfg || !status) return;
  els.configuredInstances.textContent = status.configured_instances;
  els.healthyInstances.textContent = status.healthy_instances;
  els.proxyMode.textContent = titleMode(cfg.proxy_mode);
  els.basePort.textContent = cfg.proxy_base_port;
  els.modeNote.textContent = cfg.proxy_mode === "dedicated"
    ? "Dedicated ports are bound to WARP instances. Current Egress IPs remain Cloudflare shared and dynamic."
    : "Round-robin mode can alternate connections between healthy WARP instances.";
}

// Build the expandable details row
function buildDetailsRow(item, colSpan) {
  const wd = item.watchdog || {};
  const tr = document.createElement("tr");
  tr.className = "details-row";
  const td = document.createElement("td");
  td.colSpan = colSpan;
  const inner = document.createElement("div");
  inner.className = "details-inner";

  const details = [
    { label: "Internal WARP Endpoint", value: `127.0.0.1:${item.internal_port}` },
    { label: "Recovery", value: (wd.recovery_status || "none") === "none" ? "-" : wd.recovery_status },
    { label: "Failures", value: wd.consecutive_failures !== undefined ? wd.consecutive_failures : "-" },
    { label: "Reconnects", value: wd.reconnect_count !== undefined ? wd.reconnect_count : "-" },
    { label: "Restarts", value: wd.restart_count !== undefined ? wd.restart_count : "-" },
    { label: "Last Check", value: wd.last_check ? new Date(wd.last_check).toLocaleString() : "-" },
    { label: "Last Recovery", value: (wd.last_reconnect || wd.last_restart) ? new Date(wd.last_reconnect || wd.last_restart).toLocaleString() : "-" },
  ];

  for (const d of details) {
    const div = document.createElement("div");
    div.className = "detail-item";
    const lbl = document.createElement("span");
    lbl.className = "detail-label";
    lbl.textContent = d.label;
    const val = document.createElement("span");
    val.className = "detail-value";
    val.textContent = d.value;
    div.append(lbl, val);
    inner.appendChild(div);
  }

  td.appendChild(inner);
  tr.appendChild(td);
  return tr;
}

// Build a single table row using DOM APIs (no innerHTML for user data).
function buildInstanceRow(item) {
  const tr = document.createElement("tr");
  const host = item.proxy_host_omniroute || "";
  const port = String(item.proxy_port);
  const proxyAddr = host ? `${host}:${port}` : "";
  const health = item.health || "unknown";
  const warpLabel = item.warp ? "ON" : "OFF";
  const colSpan = 9; // number of main columns

  // 1. Instance
  const tdIdx = document.createElement("td");
  tdIdx.textContent = item.instance;
  tr.appendChild(tdIdx);

  // 2. OmniRoute Proxy (host:port with copy button)
  const tdProxy = document.createElement("td");
  tdProxy.className = "proxy-cell";
  if (proxyAddr) {
    const addrWrap = document.createElement("div");
    addrWrap.className = "proxy-address";
    const addrText = document.createElement("span");
    addrText.className = "proxy-address-text";
    addrText.textContent = proxyAddr;
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "copy-inline-btn";
    copyBtn.textContent = "\u{1F4CB}";
    copyBtn.title = "Copy proxy address";
    copyBtn.addEventListener("click", () => copyText(proxyAddr, copyBtn));
    addrWrap.append(addrText, copyBtn);
    tdProxy.appendChild(addrWrap);
  } else {
    tdProxy.textContent = "-";
  }
  tr.appendChild(tdProxy);

  // 3. Current Egress IP
  const tdEgress = document.createElement("td");
  tdEgress.className = "mono";
  tdEgress.textContent = item.egress_ip || "-";
  tr.appendChild(tdEgress);

  // 4. Country
  const tdCountry = document.createElement("td");
  tdCountry.textContent = item.country_code ? countryName(item.country_code) : "-";
  tr.appendChild(tdCountry);

  // 5. Colo
  const tdColo = document.createElement("td");
  tdColo.className = "mono";
  tdColo.textContent = item.colo || "-";
  tr.appendChild(tdColo);

  // 6. Notes (editable)
  const tdNote = document.createElement("td");
  tdNote.className = "note-cell";
  const noteDisplay = document.createElement("span");
  noteDisplay.className = "note-text";
  noteDisplay.textContent = item.note || "";
  const noteEditBtn = document.createElement("button");
  noteEditBtn.type = "button";
  noteEditBtn.className = "note-edit-btn";
  noteEditBtn.textContent = item.note ? "Edit" : "Add";
  noteEditBtn.title = "Edit note";

  const noteInput = document.createElement("input");
  noteInput.type = "text";
  noteInput.className = "note-input";
  noteInput.maxLength = 500;
  noteInput.value = item.note || "";
  noteInput.hidden = true;

  const noteSaveBtn = document.createElement("button");
  noteSaveBtn.type = "button";
  noteSaveBtn.className = "note-save-btn";
  noteSaveBtn.textContent = "Save";
  noteSaveBtn.hidden = true;

  noteEditBtn.addEventListener("click", () => {
    noteInput.value = noteDisplay.textContent;
    noteInput.hidden = false;
    noteSaveBtn.hidden = false;
    noteEditBtn.hidden = true;
    noteDisplay.hidden = true;
    noteInput.focus();
  });

  async function commitNote() {
    const text = noteInput.value.trim();
    noteSaveBtn.disabled = true;
    noteSaveBtn.textContent = "...";
    try {
      const res = await saveNote(item.instance, text);
      noteDisplay.textContent = res.note || "";
      const idx = state.instances.findIndex(i => i.instance === item.instance);
      if (idx !== -1) state.instances[idx].note = res.note || "";
    } catch (e) {
      noteDisplay.textContent = noteInput.value.trim();
    }
    noteInput.hidden = true;
    noteSaveBtn.hidden = true;
    noteEditBtn.hidden = false;
    noteEditBtn.textContent = noteDisplay.textContent ? "Edit" : "Add";
    noteDisplay.hidden = false;
    noteSaveBtn.disabled = false;
    noteSaveBtn.textContent = "Save";
  }

  noteSaveBtn.addEventListener("click", commitNote);
  noteInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commitNote();
    if (e.key === "Escape") {
      noteInput.hidden = true;
      noteSaveBtn.hidden = true;
      noteEditBtn.hidden = false;
      noteDisplay.hidden = false;
    }
  });

  tdNote.append(noteDisplay, noteEditBtn, noteInput, noteSaveBtn);
  tr.appendChild(tdNote);

  // 7. WARP
  const tdWarp = document.createElement("td");
  tdWarp.innerHTML = pill(warpLabel, item.warp ? "on" : "off");
  tr.appendChild(tdWarp);

  // 8. Health
  const tdHealth = document.createElement("td");
  tdHealth.innerHTML = pill(health[0].toUpperCase() + health.slice(1), health);
  tr.appendChild(tdHealth);

  // 9. Actions (expand, reconnect, restart)
  const tdActions = document.createElement("td");

  const expandBtn = document.createElement("button");
  expandBtn.type = "button";
  expandBtn.className = "expand-btn";
  expandBtn.textContent = state.expandedRows.has(item.instance) ? "\u25B2" : "\u25BC";
  expandBtn.title = "Toggle details";
  expandBtn.addEventListener("click", () => {
    if (state.expandedRows.has(item.instance)) {
      state.expandedRows.delete(item.instance);
    } else {
      state.expandedRows.add(item.instance);
    }
    renderInstances();
  });

  const recBtn = document.createElement("button");
  recBtn.type = "button";
  recBtn.className = "action-btn";
  recBtn.textContent = "Reconnect";
  recBtn.addEventListener("click", async () => {
    recBtn.disabled = true;
    try {
      await api(`/api/instances/${item.instance}/reconnect`, { method: "POST", body: "{}" });
      load();
    } catch (e) {
      alert(e.message);
    } finally {
      recBtn.disabled = false;
    }
  });

  const restBtn = document.createElement("button");
  restBtn.type = "button";
  restBtn.className = "action-btn rest-btn";
  restBtn.textContent = "Restart WARP";
  restBtn.addEventListener("click", async () => {
    if (!confirm(`Are you sure you want to restart WARP instance ${item.instance}?`)) return;
    restBtn.disabled = true;
    try {
      await api(`/api/instances/${item.instance}/restart`, { method: "POST", body: "{}" });
      load();
    } catch (e) {
      alert(e.message);
    } finally {
      restBtn.disabled = false;
    }
  });

  tdActions.append(expandBtn, recBtn, restBtn);
  tr.appendChild(tdActions);

  // Create details row if expanded
  const detailsRow = state.expandedRows.has(item.instance)
    ? buildDetailsRow(item, colSpan)
    : null;

  return { tr, detailsRow };
}

// Build a mobile card for small screens
function buildMobileCard(item) {
  const host = item.proxy_host_omniroute || "";
  const port = String(item.proxy_port);
  const proxyAddr = host ? `${host}:${port}` : "-";
  const health = item.health || "unknown";
  const warpLabel = item.warp ? "ON" : "OFF";
  const wd = item.watchdog || {};

  const card = document.createElement("div");
  card.className = "mobile-card";

  // Header
  const head = document.createElement("div");
  head.className = "mobile-card-head";
  const title = document.createElement("strong");
  title.textContent = `Instance ${item.instance}`;
  const badges = document.createElement("div");
  badges.innerHTML = pill(warpLabel, item.warp ? "on" : "off") + " " + pill(health[0].toUpperCase() + health.slice(1), health);
  head.append(title, badges);
  card.appendChild(head);

  // Rows
  const rows = [
    { label: "OmniRoute Proxy", value: proxyAddr, copy: proxyAddr !== "-" ? proxyAddr : null },
    { label: "Egress IP", value: item.egress_ip || "-", copy: item.egress_ip || null },
    { label: "Country", value: item.country_code ? countryName(item.country_code) : "-" },
    { label: "Colo", value: item.colo || "-" },
    { label: "Notes", value: item.note || "-" },
  ];

  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "mobile-card-row";
    const lbl = document.createElement("span");
    lbl.className = "mobile-card-label";
    lbl.textContent = r.label;
    const val = document.createElement("span");
    val.className = "mobile-card-value";
    val.textContent = r.value;
    row.append(lbl, val);
    if (r.copy) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copy-inline-btn";
      btn.textContent = "\u{1F4CB}";
      btn.addEventListener("click", () => copyText(r.copy, btn));
      row.appendChild(btn);
    }
    card.appendChild(row);
  }

  // Actions
  const acts = document.createElement("div");
  acts.className = "mobile-card-actions";

  const recBtn = document.createElement("button");
  recBtn.type = "button";
  recBtn.className = "action-btn";
  recBtn.textContent = "Reconnect";
  recBtn.addEventListener("click", async () => {
    recBtn.disabled = true;
    try {
      await api(`/api/instances/${item.instance}/reconnect`, { method: "POST", body: "{}" });
      load();
    } catch (e) { alert(e.message); }
    finally { recBtn.disabled = false; }
  });

  const restBtn = document.createElement("button");
  restBtn.type = "button";
  restBtn.className = "action-btn rest-btn";
  restBtn.textContent = "Restart WARP";
  restBtn.addEventListener("click", async () => {
    if (!confirm(`Restart WARP instance ${item.instance}?`)) return;
    restBtn.disabled = true;
    try {
      await api(`/api/instances/${item.instance}/restart`, { method: "POST", body: "{}" });
      load();
    } catch (e) { alert(e.message); }
    finally { restBtn.disabled = false; }
  });

  const detailBtn = document.createElement("button");
  detailBtn.type = "button";
  detailBtn.className = "expand-btn";
  detailBtn.textContent = state.expandedRows.has(item.instance) ? "\u25B2 Details" : "\u25BC Details";
  detailBtn.addEventListener("click", () => {
    if (state.expandedRows.has(item.instance)) {
      state.expandedRows.delete(item.instance);
    } else {
      state.expandedRows.add(item.instance);
    }
    renderInstances();
  });

  acts.append(recBtn, restBtn, detailBtn);
  card.appendChild(acts);

  // Expandable details
  if (state.expandedRows.has(item.instance)) {
    const det = document.createElement("div");
    det.className = "mobile-card-details";
    const detailItems = [
      { label: "Internal WARP Endpoint", value: `127.0.0.1:${item.internal_port}` },
      { label: "Recovery", value: (wd.recovery_status || "none") === "none" ? "-" : wd.recovery_status },
      { label: "Failures", value: wd.consecutive_failures !== undefined ? String(wd.consecutive_failures) : "-" },
      { label: "Reconnects", value: wd.reconnect_count !== undefined ? String(wd.reconnect_count) : "-" },
      { label: "Restarts", value: wd.restart_count !== undefined ? String(wd.restart_count) : "-" },
      { label: "Last Check", value: wd.last_check ? new Date(wd.last_check).toLocaleString() : "-" },
      { label: "Last Recovery", value: (wd.last_reconnect || wd.last_restart) ? new Date(wd.last_reconnect || wd.last_restart).toLocaleString() : "-" },
    ];
    for (const d of detailItems) {
      const row = document.createElement("div");
      row.className = "mobile-card-row";
      const lbl = document.createElement("span");
      lbl.className = "mobile-card-label";
      lbl.textContent = d.label;
      const val = document.createElement("span");
      val.className = "mobile-card-value";
      val.textContent = d.value;
      row.append(lbl, val);
      det.appendChild(row);
    }
    card.appendChild(det);
  }

  return card;
}

function renderInstances() {
  // Desktop table
  els.instancesBody.innerHTML = "";
  for (const item of state.instances) {
    const { tr, detailsRow } = buildInstanceRow(item);
    els.instancesBody.appendChild(tr);
    if (detailsRow) {
      els.instancesBody.appendChild(detailsRow);
    }
  }

  // Mobile cards
  els.mobileCards.innerHTML = "";
  els.mobileCards.hidden = false;
  for (const item of state.instances) {
    els.mobileCards.appendChild(buildMobileCard(item));
  }
}

function populateSettings() {
  const cfg = state.config;
  if (!cfg) return;
  const form = els.settingsForm;
  form.proxy_mode.value = cfg.proxy_mode;
  form.instances.value = cfg.instances;
  form.proxy_base_port.value = cfg.proxy_base_port;
  form.proxy_host_omniroute.value = cfg.proxy_host_omniroute || "";
  form.proxy_max_rps.value = cfg.proxy_max_rps;
  form.warp_connect_timeout.value = cfg.warp_connect_timeout;
  form.auto_refresh_interval.value = cfg.auto_refresh_interval;
  form.proxy_auth_enabled.checked = cfg.proxy_auth_enabled;
  form.proxy_user.value = cfg.proxy_user || "";
  form.proxy_password.value = "";
  form.admin_current_username.value = cfg.admin_account?.username || "";
  form.admin_new_username.value = "";
  form.admin_current_password.value = "";
  form.admin_new_password.value = "";
  form.admin_confirm_password.value = "";
  els.modeHelp.textContent = cfg.proxy_mode === "dedicated"
    ? "Dedicated mode exposes one SOCKS5 listener per instance and preserves port-to-instance mapping."
    : "Round Robin mode shares the WARP proxy ports and may alternate connections across instances.";
}

function setProgress(operation) {
  if (!operation || operation.status !== "running") {
    els.applyProgress.hidden = true;
    els.settingsForm.classList.remove("is-applying");
    return;
  }
  const total = Number(operation.total || 1);
  const current = Number(operation.current || 0);
  const percent = Math.max(5, Math.min(99, Math.round((current / total) * 100)));
  els.applyProgress.hidden = false;
  els.settingsForm.classList.add("is-applying");
  els.progressLabel.textContent = operation.message || "Applying...";
  els.progressPercent.textContent = `${percent}%`;
  els.progressBar.style.width = `${percent}%`;
}

async function pollOperation() {
  try {
    const status = await api("/api/status");
    state.status = status;
    setProgress(status.operation);
    renderSummary();
    if (!status.operation || status.operation.status !== "running") {
      clearInterval(progressTimer);
      progressTimer = null;
    }
  } catch {
    // Keep the existing progress visible while the container is busy.
  }
}

async function load() {
  const [config, status, instances] = await Promise.all([
    api("/api/config"),
    api("/api/status"),
    api("/api/instances"),
  ]);
  state.config = config;
  state.status = status;
  state.instances = instances;
  renderSummary();
  renderInstances();
  populateSettings();
}

// -- Export OmniRoute --

async function openExport() {
  els.exportError.hidden = true;
  els.exportAuthWarning.hidden = true;
  els.exportText.value = "";
  els.exportMeta.innerHTML = "";
  els.exportMessage.textContent = "";

  try {
    const data = await api("/api/export/omniroute");
    if (!data.ok) {
      els.exportError.textContent = data.error || "Export failed.";
      els.exportError.hidden = false;
      els.exportText.value = "";
      els.exportDialog.showModal();
      return;
    }

    if (data.auth_warning) {
      els.exportAuthWarning.textContent = data.auth_warning;
      els.exportAuthWarning.hidden = false;
    }

    els.exportText.value = data.text || "";
    els.exportMeta.innerHTML = `
      <span>Proxies: <strong>${data.count}</strong></span>
      <span>Host: <strong>${data.host}</strong></span>
      <span>Ports: <strong>${data.port_range}</strong></span>
    `;
    els.exportDialog.showModal();
  } catch (e) {
    els.exportError.textContent = e.message;
    els.exportError.hidden = false;
    els.exportDialog.showModal();
  }
}

els.exportBtn.addEventListener("click", openExport);
els.closeExport.addEventListener("click", () => els.exportDialog.close());

els.exportCopy.addEventListener("click", () => {
  const text = els.exportText.value;
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    els.exportMessage.textContent = "Copied to clipboard";
    setTimeout(() => { els.exportMessage.textContent = ""; }, 2000);
  }).catch(() => {
    els.exportMessage.textContent = "Copy failed";
  });
});

els.exportDownload.addEventListener("click", () => {
  const text = els.exportText.value;
  if (!text) return;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "omniroute-proxies.txt";
  a.click();
  URL.revokeObjectURL(url);
  els.exportMessage.textContent = "Downloaded";
  setTimeout(() => { els.exportMessage.textContent = ""; }, 2000);
});

// -- Event listeners --

els.refreshBtn.addEventListener("click", async () => {
  els.refreshBtn.disabled = true;
  els.refreshBtn.textContent = "Refreshing...";
  try {
    state.instances = await api("/api/refresh", { method: "POST", body: "{}" });
    state.status = await api("/api/status");
    renderSummary();
    renderInstances();
  } finally {
    els.refreshBtn.disabled = false;
    els.refreshBtn.textContent = "Refresh Egress IPs";
  }
});

els.settingsBtn.addEventListener("click", () => {
  populateSettings();
  els.settingsDialog.showModal();
});

els.closeSettings.addEventListener("click", () => els.settingsDialog.close());
els.cancelSettings.addEventListener("click", () => els.settingsDialog.close());

els.settingsForm.proxy_mode.addEventListener("change", () => {
  els.modeHelp.textContent = els.settingsForm.proxy_mode.value === "dedicated"
    ? "Dedicated mode exposes one SOCKS5 listener per instance and preserves port-to-instance mapping."
    : "Round Robin mode shares the WARP proxy ports and may alternate connections across instances.";
});

els.updateAdminAccount.addEventListener("click", async () => {
  const form = els.settingsForm;
  const payload = {
    current_password: form.admin_current_password.value,
    new_username: form.admin_new_username.value.trim(),
    new_password: form.admin_new_password.value,
    confirm_password: form.admin_confirm_password.value,
  };
  els.adminAccountMessage.textContent = "Updating...";
  els.updateAdminAccount.disabled = true;
  try {
    const response = await api("/api/admin/credentials", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    els.adminAccountMessage.textContent = response.message || "Administrator credentials updated successfully.";
    form.admin_current_username.value = response.account?.username || payload.new_username || form.admin_current_username.value;
    form.admin_new_username.value = "";
    form.admin_current_password.value = "";
    form.admin_new_password.value = "";
    form.admin_confirm_password.value = "";
    state.config = await api("/api/config");
  } catch (error) {
    els.adminAccountMessage.textContent = error.message;
  } finally {
    els.updateAdminAccount.disabled = false;
  }
});

els.settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = els.settingsForm;
  const payload = {
    proxy_mode: form.proxy_mode.value,
    instances: Number(form.instances.value),
    proxy_base_port: Number(form.proxy_base_port.value),
    proxy_host_omniroute: form.proxy_host_omniroute.value.trim(),
    proxy_max_rps: Number(form.proxy_max_rps.value),
    warp_connect_timeout: Number(form.warp_connect_timeout.value),
    auto_refresh_interval: Number(form.auto_refresh_interval.value),
    proxy_auth_enabled: form.proxy_auth_enabled.checked,
    proxy_user: form.proxy_user.value.trim(),
  };
  if (form.proxy_password.value) {
    payload.proxy_password = form.proxy_password.value;
  }
  els.settingsMessage.textContent = "Applying...";
  setProgress({ status: "running", message: "Sending configuration", current: 0, total: 1 });
  progressTimer = setInterval(pollOperation, 1000);
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify(payload) });
    clearInterval(progressTimer);
    progressTimer = null;
    els.progressLabel.textContent = "Applied";
    els.progressPercent.textContent = "100%";
    els.progressBar.style.width = "100%";
    els.settingsMessage.textContent = "Applied";
    await load();
    setTimeout(() => els.settingsDialog.close(), 500);
  } catch (error) {
    clearInterval(progressTimer);
    progressTimer = null;
    els.settingsForm.classList.remove("is-applying");
    els.settingsMessage.textContent = error.message;
  }
});

load().catch((error) => {
  els.modeNote.textContent = error.message;
});

setInterval(() => {
  load().catch(() => {});
}, 30000);
