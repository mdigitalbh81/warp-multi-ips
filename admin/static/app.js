const state = {
  config: null,
  status: null,
  instances: [],
};

const els = {
  configuredInstances: document.querySelector("#configuredInstances"),
  healthyInstances: document.querySelector("#healthyInstances"),
  proxyMode: document.querySelector("#proxyMode"),
  basePort: document.querySelector("#basePort"),
  modeNote: document.querySelector("#modeNote"),
  instancesBody: document.querySelector("#instancesBody"),
  refreshBtn: document.querySelector("#refreshBtn"),
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

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
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

function renderInstances() {
  els.instancesBody.innerHTML = state.instances.map((item) => {
    const warpLabel = item.warp ? "ON" : "OFF";
    const processLabel = item.process_healthy ? "Running" : "Down";
    const gostLabel = item.listener_healthy ? "Listening" : "Down";
    const health = item.health || "unknown";
    return `
      <tr>
        <td>${item.instance}</td>
        <td class="mono">:${item.proxy_port}</td>
        <td class="mono">127.0.0.1:${item.internal_port}</td>
        <td class="mono">${item.egress_ip || "-"}</td>
        <td>${pill(warpLabel, item.warp ? "on" : "off")}</td>
        <td>${pill(processLabel, item.process_healthy)}</td>
        <td>${pill(gostLabel, item.listener_healthy)}</td>
        <td>${pill(health[0].toUpperCase() + health.slice(1), health)}</td>
        <td>${formatDate(item.last_check)}</td>
      </tr>
    `;
  }).join("");
}

function populateSettings() {
  const cfg = state.config;
  if (!cfg) return;
  const form = els.settingsForm;
  form.proxy_mode.value = cfg.proxy_mode;
  form.instances.value = cfg.instances;
  form.proxy_base_port.value = cfg.proxy_base_port;
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
