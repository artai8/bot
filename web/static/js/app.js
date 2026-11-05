(function () {
    const { state, api, toast, closeModal } = window.FSB;
    const pages = {
        platform: {
            tenants: ['租户', '租户生命周期与 Worker 状态', 'building'],
            audit: ['审计日志', '平台及代管操作记录', 'clipboard-list']
        },
        tenant: {
            dashboard: ['总览', '统计与运行信息', 'chart-pie'],
            users: ['用户', '用户管理', 'users'],
            shares: ['分享', '分享管理', 'share-nodes'],
            broadcast: ['群发', '异步广播任务', 'tower-broadcast'],
            banned: ['封禁', '封禁管理', 'user-slash'],
            settings: ['设置', '机器人配置', 'gear'],
            health: ['健康状态', '租户 Bot 与 Worker 状态', 'heart-pulse']
        }
    };

    function showLogin() { document.getElementById('login-screen').classList.remove('hidden'); document.getElementById('app').classList.add('hidden'); }
    function showApp() { document.getElementById('login-screen').classList.add('hidden'); document.getElementById('app').classList.remove('hidden'); }

    function navigation() {
        const role = state.session.role;
        const entries = pages[role];
        document.getElementById('sidebar-nav').innerHTML = `<div class="nav-group"><div class="nav-group-title">${role === 'platform' ? '平台' : '租户'}</div>${Object.entries(entries).map(([key, value]) => `<a class="nav-link" data-page="${key}" onclick="App.navigate('${key}')"><i class="fas fa-${value[2]}"></i><span>${value[0]}</span></a>`).join('')}</div>`;
        document.getElementById('sidebar-role').textContent = role === 'platform' ? '平台管理员' : state.session.impersonated ? '平台代管' : '租户管理员';
        document.getElementById('sidebar-context').textContent = state.session.tenant?.display_name || '平台控制面';
        document.getElementById('leave-impersonation').classList.toggle('hidden', !state.session.impersonated);
    }

    async function loadSession() {
        try {
            state.session = await api('/api/session');
            showApp(); navigation();
            navigate(state.session.role === 'platform' ? 'tenants' : 'dashboard');
        } catch (_) { logout(false); }
    }

    async function login() {
        const scope = document.querySelector('input[name="login-scope"]:checked').value;
        const password = document.getElementById('login-password').value;
        const slug = document.getElementById('login-tenant').value.trim();
        const error = document.getElementById('login-error');
        error.classList.add('hidden');
        try {
            const response = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope, tenant_slug: slug, password }) });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || data.message || '登录失败');
            state.token = data.token; localStorage.setItem('fsb_token', data.token); await loadSession(); toast('登录成功', 'success');
        } catch (exception) { error.textContent = exception.message; error.classList.remove('hidden'); }
    }

    async function logout(callApi = true) {
        if (callApi && state.token) fetch('/api/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + state.token } }).catch(() => {});
        state.token = ''; state.session = null; state.platformToken = '';
        localStorage.removeItem('fsb_token'); localStorage.removeItem('fsb_platform_token'); showLogin();
    }

    async function leaveImpersonation() {
        if (!state.platformToken) return logout();
        fetch('/api/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + state.token } }).catch(() => {});
        state.token = state.platformToken; state.platformToken = '';
        localStorage.setItem('fsb_token', state.token); localStorage.removeItem('fsb_platform_token'); await loadSession();
    }

    async function navigate(page) {
        state.currentPage = page;
        document.querySelectorAll('.nav-link').forEach(element => element.classList.toggle('active', element.dataset.page === page));
        const meta = pages[state.session.role][page];
        document.getElementById('page-title').textContent = meta[0]; document.getElementById('page-subtitle').textContent = meta[1];
        await loadPage(page); document.getElementById('sidebar').classList.remove('open');
    }

    async function loadPage(page, arg1, arg2) {
        const container = document.getElementById('page-content');
        container.innerHTML = '<div class="loading-container"><div class="spinner"></div></div>';
        try {
            if (state.session.role === 'platform') await Platform[page](container, arg1, arg2);
            else await Tenant[page](container, arg1, arg2);
        } catch (error) { container.innerHTML = `<div class="empty-state">${window.FSB.esc(error.message)}</div>`; toast(error.message, 'error'); }
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('login-form').addEventListener('submit', login);
        document.querySelectorAll('input[name="login-scope"]').forEach(input => input.addEventListener('change', () => document.getElementById('tenant-slug-row').classList.toggle('hidden', input.value === 'platform' && input.checked)));
        document.getElementById('toggle-password').addEventListener('click', () => { const input = document.getElementById('login-password'); input.type = input.type === 'password' ? 'text' : 'password'; });
        document.getElementById('logout-btn').addEventListener('click', () => logout());
        document.getElementById('leave-impersonation').addEventListener('click', leaveImpersonation);
        document.getElementById('refresh-btn').addEventListener('click', () => loadPage(state.currentPage));
        document.getElementById('mobile-toggle').addEventListener('click', () => document.getElementById('sidebar').classList.toggle('open'));
        document.getElementById('sidebar-overlay').addEventListener('click', () => document.getElementById('sidebar').classList.remove('open'));
        document.getElementById('modal-close').addEventListener('click', closeModal);
        document.getElementById('modal-overlay').addEventListener('click', event => { if (event.target.id === 'modal-overlay') closeModal(); });
        if (state.token) loadSession(); else showLogin();
    });

    window.App = { loadSession, navigate, loadPage };
    window.FSB.logout = logout;
})();
