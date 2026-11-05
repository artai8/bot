(function () {
  window.FSB = window.FSB || {};
  const FSB = window.FSB;

  let isAdminEntryRoute = false;
  let allowRegistration = true;
  let requireInviteCode = false;

  function switchAuthTab(mode) {
    if (mode === "register" && !allowRegistration) {
      FSB.toast("管理员当前已关闭新用户注册", "error");
      return;
    }
    const loginForm = document.getElementById("login-form");
    const regForm = document.getElementById("register-form");
    const tabs = document.querySelectorAll("#tenant-auth-tabs .auth-tab");
    tabs.forEach(function (btn) {
      if (btn.getAttribute("data-mode") === mode) btn.classList.add("active");
      else btn.classList.remove("active");
    });
    if (mode === "register") {
      loginForm.classList.add("hidden");
      regForm.classList.remove("hidden");
    } else {
      regForm.classList.add("hidden");
      loginForm.classList.remove("hidden");
    }
  }

  function configureAuthUI() {
    const titleEl = document.getElementById("auth-title");
    const subEl = document.getElementById("auth-subtitle");
    const tabsEl = document.getElementById("tenant-auth-tabs");
    const regTabBtn = document.getElementById("register-tab-btn");
    const inviteGroup = document.getElementById("reg-invite-group");
    const idLabel = document.getElementById("login-id-label");
    const idInput = document.getElementById("login-username");
    const regForm = document.getElementById("register-form");
    const loginForm = document.getElementById("login-form");

    if (isAdminEntryRoute) {
      titleEl.textContent = "系统管理员后台登录";
      subEl.textContent = "管理全平台所有租户的所有设置项与所有数据";
      tabsEl.classList.add("hidden");
      regForm.classList.add("hidden");
      loginForm.classList.remove("hidden");
      idLabel.textContent = "管理员登录 ID";
      idInput.placeholder = "请输入管理员 ID (默认 admin)";
    } else {
      titleEl.textContent = "租户自助控制中心";
      subEl.textContent = "登录或注册您的专属 Telegram 文件分享机器人";
      tabsEl.classList.remove("hidden");
      if (regTabBtn) {
        if (allowRegistration) regTabBtn.classList.remove("hidden");
        else regTabBtn.classList.add("hidden");
      }
      if (inviteGroup) {
        if (requireInviteCode) inviteGroup.classList.remove("hidden");
        else inviteGroup.classList.add("hidden");
      }
      idLabel.textContent = "租户账号标识 (Slug)";
      idInput.placeholder = "请输入您的租户账号标识";
    }
  }

  function showLogin() {
    document.getElementById("app-container").classList.add("hidden");
    document.getElementById("login-modal").classList.remove("hidden");
    configureAuthUI();
  }

  function buildSidebar() {
    const nav = document.getElementById("sidebar-nav");
    const switcher = document.getElementById("admin-tenant-switcher-box");
    const roleBadge = document.getElementById("sidebar-role-badge");
    const brand = document.getElementById("sidebar-brand");

    if (FSB.sessionRole === "platform") {
      brand.textContent = "超级管理员系统";
      roleBadge.textContent = "管理员";
      roleBadge.className = "badge badge-warning";
      switcher.classList.remove("hidden");
      nav.innerHTML = `
        <div class="nav-section-title">平台全局管理</div>
        <button class="nav-item" data-page="admin_tenants" onclick="window.FSB.loadPage('admin_tenants')">🏢 全部租户总览</button>
        <button class="nav-item" data-page="admin_security" onclick="window.FSB.loadPage('admin_security')">🔐 管理员与注册设置</button>
        <div class="nav-section-title">当前所选租户的全量管理</div>
        <button class="nav-item" data-page="dashboard" onclick="window.FSB.loadPage('dashboard')">📊 租户数据概览</button>
        <button class="nav-item" data-page="settings" onclick="window.FSB.loadPage('settings')">⚙️ 租户全部设置项</button>
        <button class="nav-item" data-page="shares" onclick="window.FSB.loadPage('shares')">📁 租户分享合集数据</button>
        <button class="nav-item" data-page="users" onclick="window.FSB.loadPage('users')">👥 租户用户与封禁数据</button>
        <button class="nav-item" data-page="cdkeys" onclick="window.FSB.loadPage('cdkeys')">🔑 租户卡密 (CDKey) 数据</button>
        <button class="nav-item" data-page="broadcast" onclick="window.FSB.loadPage('broadcast')">📢 租户消息广播</button>
      `;
    } else {
      brand.textContent = "租户控制台";
      roleBadge.textContent = "租户";
      roleBadge.className = "badge badge-info";
      switcher.classList.add("hidden");
      nav.innerHTML = `
        <div class="nav-section-title">租户中心</div>
        <button class="nav-item" data-page="dashboard" onclick="window.FSB.loadPage('dashboard')">📊 数据概览</button>
        <button class="nav-item" data-page="settings" onclick="window.FSB.loadPage('settings')">⚙️ 机器人与全部设置</button>
        <button class="nav-item" data-page="shares" onclick="window.FSB.loadPage('shares')">📁 分享链接与合集</button>
        <button class="nav-item" data-page="users" onclick="window.FSB.loadPage('users')">👥 用户与权限管理</button>
        <button class="nav-item" data-page="cdkeys" onclick="window.FSB.loadPage('cdkeys')">🔑 卡密 (CDKey) 管理</button>
        <button class="nav-item" data-page="broadcast" onclick="window.FSB.loadPage('broadcast')">📢 消息广播</button>
      `;
    }
  }

  async function showApp() {
    document.getElementById("login-modal").classList.add("hidden");
    document.getElementById("app-container").classList.remove("hidden");
    buildSidebar();
    if (FSB.sessionRole === "platform") {
      await FSB.refreshAdminTenantSelector();
      await loadPage("admin_tenants");
    } else {
      await loadPage("dashboard");
    }
  }

  async function loadPage(pageName) {
    FSB.currentPage = pageName;
    const items = document.querySelectorAll("#sidebar-nav .nav-item");
    items.forEach(function (el) {
      if (el.getAttribute("data-page") === pageName) el.classList.add("active");
      else el.classList.remove("active");
    });

    const titleMap = {
      admin_tenants: "全部租户管理（可管理所有租户的设置与数据）",
      admin_security: "管理员登录ID、密码、后台路径后缀与注册控制",
      dashboard: "租户数据概览",
      settings: "全部设置项与机器人核心配置",
      shares: "分享合集与链接数据管理",
      users: "用户数据与封禁/解锁管理",
      cdkeys: "卡密 (CDKey) 数据管理",
      broadcast: "消息广播推送",
    };
    const titleEl = document.getElementById("page-title");
    if (titleEl) titleEl.textContent = titleMap[pageName] || "控制台";

    try {
      if (pageName === "admin_tenants") await FSB.renderAdminTenantsOverview();
      else if (pageName === "admin_security") await FSB.renderAdminSecuritySettings();
      else if (pageName === "dashboard") await FSB.renderDashboard();
      else if (pageName === "settings") await FSB.renderSettings();
      else if (pageName === "shares") await FSB.renderShares(1);
      else if (pageName === "users") await FSB.renderUsers(1);
      else if (pageName === "cdkeys") await FSB.renderCDKeys();
      else if (pageName === "broadcast") await FSB.renderBroadcast();
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function handleLogin(e) {
    e.preventDefault();
    const username = document.getElementById("login-username").value.trim();
    const password = document.getElementById("login-password").value;

    const payload = isAdminEntryRoute
      ? { scope: "platform", admin_id: username, password: password }
      : { scope: "tenant", slug: username, password: password };

    try {
      FSB.showLoading(true);
      const res = await FSB.api("/api/login", { method: "POST", body: payload });
      FSB.safeSetStorage("fsb_token", res.token);
      FSB.sessionRole = res.role;
      FSB.toast("登录成功", "success");
      await showApp();
    } catch (err) {
      FSB.toast(err.message, "error");
    } finally {
      FSB.showLoading(false);
    }
  }

  async function handleRegister(e) {
    e.preventDefault();
    const inviteEl = document.getElementById("reg-invite-code");
    const payload = {
      slug: document.getElementById("reg-slug").value.trim(),
      display_name: document.getElementById("reg-display-name").value.trim(),
      password: document.getElementById("reg-password").value,
      invite_code: inviteEl ? inviteEl.value.trim() : "",
      bot_token: document.getElementById("reg-bot-token").value.trim(),
      owner_id: Number(document.getElementById("reg-owner-id").value || 0),
      db_channel_id: Number(document.getElementById("reg-db-channel").value || 0),
    };
    try {
      FSB.showLoading(true);
      const res = await FSB.api("/api/register", { method: "POST", body: payload });
      FSB.safeSetStorage("fsb_token", res.token);
      FSB.sessionRole = res.role;
      FSB.toast("注册成功！请确认或完善您的机器人配置", "success");
      await showApp();
      await loadPage("settings");
    } catch (err) {
      FSB.toast(err.message, "error");
    } finally {
      FSB.showLoading(false);
    }
  }

  async function logout() {
    try {
      await FSB.api("/api/logout", { method: "POST" });
    } catch (_) {}
    FSB.safeSetStorage("fsb_token", "");
    FSB.sessionRole = "";
    showLogin();
  }

  async function initApp() {
    const rawPath = window.location.pathname.replace(/^\/+|\/+$/g, "");
    try {
      const routeRes = await fetch("/api/route-info?path=" + encodeURIComponent(rawPath));
      if (routeRes.ok) {
        const routeData = await routeRes.json();
        isAdminEntryRoute = Boolean(routeData.is_admin_entry);
        allowRegistration = routeData.allow_registration !== false;
        requireInviteCode = Boolean(routeData.require_invite_code);
      }
    } catch (_) {}

    const loginForm = document.getElementById("login-form");
    if (loginForm) loginForm.addEventListener("submit", handleLogin);
    const regForm = document.getElementById("register-form");
    if (regForm) regForm.addEventListener("submit", handleRegister);

    const token = FSB.safeGetStorage("fsb_token");
    if (token) {
      try {
        const sess = await FSB.api("/api/session");
        if (sess && sess.authenticated) {
          if ((isAdminEntryRoute && sess.role === "platform") || (!isAdminEntryRoute && sess.role === "tenant")) {
            FSB.sessionRole = sess.role;
            await showApp();
            return;
          }
        }
      } catch (_) {}
    }
    showLogin();
  }

  Object.assign(window.FSB, {
    switchAuthTab: switchAuthTab,
    showLogin: showLogin,
    showApp: showApp,
    loadPage: loadPage,
    logout: logout,
  });

  document.addEventListener("DOMContentLoaded", initApp);
})();
