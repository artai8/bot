(function () {
  window.FSB = window.FSB || {};

  function safeGetStorage(key) {
    try {
      return localStorage.getItem(key) || "";
    } catch (_) {
      return "";
    }
  }

  function safeSetStorage(key, val) {
    try {
      if (val === null || val === undefined || val === "") {
        localStorage.removeItem(key);
      } else {
        localStorage.setItem(key, String(val));
      }
    } catch (_) {}
  }

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toast(message, type) {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const item = document.createElement("div");
    item.className = "toast " + (type || "info");
    item.textContent = message;
    container.appendChild(item);
    setTimeout(function () {
      item.remove();
    }, 3500);
  }

  function showLoading(show) {
    const el = document.getElementById("loading-overlay");
    if (!el) return;
    if (show) el.classList.remove("hidden");
    else el.classList.add("hidden");
  }

  function renderModal(title, bodyHtml, footerHtml) {
    const modal = document.getElementById("modal-container");
    const tEl = document.getElementById("modal-title");
    const bEl = document.getElementById("modal-body");
    const fEl = document.getElementById("modal-footer");
    if (!modal || !tEl || !bEl || !fEl) return;
    tEl.textContent = title || "提示";
    bEl.innerHTML = bodyHtml || "";
    fEl.innerHTML =
      footerHtml ||
      '<button type="button" class="btn btn-secondary" onclick="window.FSB.closeModal()">关闭</button>';
    modal.classList.remove("hidden");
  }

  function closeModal() {
    const modal = document.getElementById("modal-container");
    if (modal) modal.classList.add("hidden");
  }

  function formatDate(ts) {
    if (!ts) return "-";
    const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString("zh-CN", { hour12: false });
  }

  function formatNumber(num) {
    if (num === null || num === undefined) return "0";
    return Number(num).toLocaleString("zh-CN");
  }

  async function api(url, options) {
    options = options || {};
    const headers = Object.assign({}, options.headers || {});
    const token = safeGetStorage("fsb_token");
    if (token) {
      headers["Authorization"] = "Bearer " + token;
    }
    if (window.FSB.sessionRole === "platform" && window.FSB.selectedTenantId) {
      headers["X-Tenant-Id"] = window.FSB.selectedTenantId;
    }
    if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }
    options.headers = headers;

    const resp = await fetch(url, options);
    let data = {};
    try {
      data = await resp.json();
    } catch (_) {}

    if (resp.status === 401 && !url.includes("/api/login") && !url.includes("/api/session")) {
      safeSetStorage("fsb_token", "");
      if (window.FSB.showLogin) window.FSB.showLogin();
      throw new Error(data.error || "登录状态已失效，请重新登录");
    }
    if (!resp.ok) {
      throw new Error(data.error || "请求失败 (" + resp.status + ")");
    }
    return data;
  }

  Object.assign(window.FSB, {
    safeGetStorage: safeGetStorage,
    safeSetStorage: safeSetStorage,
    esc: esc,
    toast: toast,
    showLoading: showLoading,
    renderModal: renderModal,
    closeModal: closeModal,
    formatDate: formatDate,
    formatNumber: formatNumber,
    api: api,
    sessionRole: "",
    selectedTenantId: safeGetStorage("fsb_selected_tenant_id") || "",
  });
})();
