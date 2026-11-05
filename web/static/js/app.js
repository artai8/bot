(function () {
  const { state, api, toast, closeModal, safeSetStorage } = window.FSB;
  const pages = {
    platform: {
      tenants: ['租户管理', '多租户生命周期、代管与 Worker 租约状态', 'building'],
      audit: ['审计日志', '平台创建、启停及代管操作追踪', 'clipboard-list']
    },
    tenant: {
      dashboard: ['运行总览', '核心指标统计与 Bot 实时在线状态', 'chart-pie'],
      users: ['用户列表', '访问过本机器人的 Telegram 用户管理', 'users'],
      shares: ['分享管理', '受控文件 Key、密码保护与手动补发', 'share-nodes'],
      broadcast: ['全员群发', '受全局并发限流保护的异步广播任务', 'tower-broadcast'],
      banned: ['封禁名单', '违规用户拦截与解封管理', 'user-slash'],
      settings: ['参数设置', '验证、防转发、强制订阅与文案模板热更新', 'gear'],
      health: ['健康诊断', '租户 Bot、数据库连接与内存占用监控', 'heart-pulse']
    }
  };

  function showLogin() {
    document.getElementById('login-screen').classList.remove('hidden');
    document.getElementById('app').classList.add('hidden');
  }
  function showApp() {
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
  }

  function navigation() {
    const role = state.session.role;
    const entries = pages[role];
    document.getElementById('sidebar-nav').innerHTML = `<div class="nav-group"><div class="nav-group-title">${role === 'platform' ? '平台控制面' : '租户控制台'}</div>${Object.entries(entries).map(([key, value]) => `<a class="nav-link" data-page="${key}" onclick="App.navigate('${key}')"><i class="fas fa-${value[2]}"></i><span>${value[0]}</span></a>`).join('')}</div>`;
    document.getElementById('sidebar-role').textContent = role === 'platform' ? '平台管理员' : state.session.impersonated ? '平台代管模式' : '租户管理员';
    document.getElementById('sidebar-context').textContent = state.session.tenant?.display_name || '全局平台控制面';
    document.getElementById('leave-impersonation').classList.toggle('hidden', !state.session.impersonated);
  }

  async function loadSession() {
    try {
      state.session = await api('/api/session');
      showApp();
      navigation();
      navigate(state.session.role === 'platform' ? 'tenants' : 'dashboard');
    } catch (_) {
      logout(false);
    }
  }

  async function login(event) {
    if (event) event.preventDefault();
    const scope = document.querySelector('input[name="login-scope"]:checked').value;
    const password = document.getElementById('login-password').value;
    const slug = document.getElementById('login-tenant').value.trim();
    const error = document.getElementById('login-error');
    error.classList.add('hidden');
    try {
      if (state.demoMode) {
        state.token = scope === 'platform' ? 'demo_platform' : 'demo_tenant';
        safeSetStorage('fsb_token', state.token);
        await loadSession();
        toast('演示模式登录成功', 'success');
        return;
      }
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope, tenant_slug: slug, password })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || data.message || '登录失败');
      state.token = data.token;
      safeSetStorage('fsb_token', data.token);
      await loadSession();
      toast('登录成功', 'success');
    } catch (exception) {
      error.textContent = exception.message;
      error.classList.remove('hidden');
    }
  }

  async function logout(callApi = true) {
    if (callApi && state.token && !state.demoMode) {
      fetch('/api/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + state.token } }).catch(() => {});
    }
    state.token = '';
    state.session = null;
    state.platformToken = '';
    safeSetStorage('fsb_token', '');
    safeSetStorage('fsb_platform_token', '');
    showLogin();
  }

  async function leaveImpersonation() {
    if (!state.platformToken) return logout();
    if (!state.demoMode) {
      fetch('/api/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + state.token } }).catch(() => {});
    }
    state.token = state.platformToken;
    state.platformToken = '';
    safeSetStorage('fsb_token', state.token);
    safeSetStorage('fsb_platform_token', '');
    await loadSession();
  }

  async function navigate(page) {
    state.currentPage = page;
    document.querySelectorAll('.nav-link').forEach(el => el.classList.toggle('active', el.dataset.page === page));
    const meta = pages[state.session.role][page];
    document.getElementById('page-title').textContent = meta[0];
    document.getElementById('page-subtitle').textContent = meta[1];
    await loadPage(page);
    document.getElementById('sidebar').classList.remove('open');
  }

  async function loadPage(page, arg1, arg2) {
    const container = document.getElementById('page-content');
    container.innerHTML = '<div class="loading-container"><div class="spinner"></div></div>';
    try {
      if (state.session.role === 'platform') await window.Platform[page](container, arg1, arg2);
      else await window.Tenant[page](container, arg1, arg2);
    } catch (error) {
      container.innerHTML = `<div class="empty-state">${window.FSB.esc(error.message)}</div>`;
      toast(error.message, 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('login-form').addEventListener('submit', login);
    document.querySelectorAll('input[name="login-scope"]').forEach(input => {
      input.addEventListener('change', () => {
        document.getElementById('tenant-slug-row').classList.toggle('hidden', input.value === 'platform' && input.checked);
      });
    });
    document.getElementById('toggle-password').addEventListener('click', () => {
      const input = document.getElementById('login-password');
      input.type = input.type === 'password' ? 'text' : 'password';
    });
    document.getElementById('logout-btn').addEventListener('click', () => logout());
    document.getElementById('leave-impersonation').addEventListener('click', leaveImpersonation);
    document.getElementById('refresh-btn').addEventListener('click', () => loadPage(state.currentPage));
    document.getElementById('theme-btn').addEventListener('click', () => document.body.classList.toggle('light-theme'));
    document.getElementById('mobile-toggle').addEventListener('click', () => document.getElementById('sidebar').classList.toggle('open'));
    document.getElementById('sidebar-overlay').addEventListener('click', () => document.getElementById('sidebar').classList.remove('open'));
    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.getElementById('modal-overlay').addEventListener('click', event => {
      if (event.target.id === 'modal-overlay') closeModal();
    });

    // Detect if backend API is reachable; if in Canvas preview, activate interactive demo mode automatically
    try {
      const ping = await fetch('/api/health', { method: 'GET' });
      if (!ping.ok) throw new Error('offline');
    } catch (_) {
      state.demoMode = true;
      if (!state.token) state.token = 'demo_tenant';
    }

    if (state.token) loadSession();
    else showLogin();
  });

  window.App = { loadSession, navigate, loadPage };
  window.FSB.logout = logout;
})();
