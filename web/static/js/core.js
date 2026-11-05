(function () {
  function safeGetStorage(key) {
    try { return localStorage.getItem(key) || ''; } catch (_) { return ''; }
  }
  function safeSetStorage(key, val) {
    try {
      if (val == null || val === '') localStorage.removeItem(key);
      else localStorage.setItem(key, val);
    } catch (_) {}
  }

  const state = {
    token: safeGetStorage('fsb_token'),
    platformToken: safeGetStorage('fsb_platform_token'),
    session: null,
    currentPage: '',
    demoMode: false
  };

  function esc(value) {
    const replacements = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return (value == null ? '' : String(value)).replace(/[&<>"']/g, ch => replacements[ch]);
  }

  async function api(url, options = {}) {
    if (state.demoMode) return demoApi(url, options);
    const response = await fetch(url, {
      ...options,
      headers: {
        Authorization: 'Bearer ' + state.token,
        'Content-Type': 'application/json',
        ...(options.headers || {})
      }
    });
    let data = {};
    try { data = await response.json(); } catch (_) { data = { error: 'Invalid server response' }; }
    if (response.status === 401) {
      window.FSB.logout(false);
      throw new Error('登录已过期');
    }
    if (!response.ok) throw new Error(data.error || '请求失败');
    return data;
  }

  function toast(message, type = 'info') {
    const element = document.createElement('div');
    element.className = `toast toast-${type}`;
    element.textContent = message;
    document.getElementById('toast-container').appendChild(element);
    setTimeout(() => element.remove(), 3500);
  }

  function modal(title, body, actions = '') {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = body;
    document.getElementById('modal-actions').innerHTML = actions;
    document.getElementById('modal-overlay').classList.remove('hidden');
  }

  function closeModal() {
    document.getElementById('modal-overlay').classList.add('hidden');
  }

  function pagination(page, pages, callback) {
    if (!pages || pages <= 1) return '';
    return `<div class="pagination">
      <button type="button" class="btn btn-ghost btn-sm" ${page <= 1 ? 'disabled' : ''} onclick="${callback}(${page - 1})">上一页</button>
      <span>第 ${page} / ${pages} 页</span>
      <button type="button" class="btn btn-ghost btn-sm" ${page >= pages ? 'disabled' : ''} onclick="${callback}(${page + 1})">下一页</button>
    </div>`;
  }

  async function pollJob(jobId, onProgress, prefix = '/api/jobs/') {
    for (;;) {
      const job = await api(prefix + encodeURIComponent(jobId));
      if (onProgress) onProgress(job);
      if (['completed', 'failed', 'cancelled'].includes(job.state)) return job;
      await new Promise(resolve => setTimeout(resolve, 800));
    }
  }

  // Built-in Demo Mock API when previewed in Canvas / offline browser
  const demoTenants = [
    {
      id: 't_101',
      slug: 'media_hub_cn',
      display_name: '极光影视资源站',
      owner_id: 681920311,
      db_channel_id: -100198237465,
      admin_ids: [681920311, 512900112],
      bot_username: 'AuroraShareBot',
      bot_token_hint: '719283:*****9aB2',
      enabled: true,
      runtime: { status: 'running', worker_id: 'worker-kr-01' }
    },
    {
      id: 't_102',
      slug: 'design_assets',
      display_name: '果冻设计素材库',
      owner_id: 801234991,
      db_channel_id: -100177665544,
      admin_ids: [801234991],
      bot_username: 'JellyAssetBot',
      bot_token_hint: '698112:*****4fE9',
      enabled: true,
      runtime: { status: 'running', worker_id: 'worker-kr-01' }
    }
  ];
  let demoShares = [
    { id: 'sh_1', key: 'JELLY2026A', code: 'JELLY2026A', file_name: '3D_Jelly_Icon_Pack_v3.zip', media_type: 'document', password: '', download_count: 428, view_count: 1120 },
    { id: 'sh_2', key: 'GLASS4K99B', code: 'GLASS4K99B', file_name: 'Glassmorphism_UI_Kit_4K.mp4', media_type: 'video', password: 'vip88', download_count: 196, view_count: 640 }
  ];
  let demoBanned = [
    { user_id: 900112233, reason: '恶意高频刷取分享链接' }
  ];
  let demoSettings = {
    is_verify: false,
    protect_content: true,
    show_promo: true,
    disable_channel_button: false,
    verify_expire: 86400,
    auto_delete_time: 300,
    share_code_length: 10,
    rate_limit_max: 12,
    rate_limit_window: 60,
    force_sub_channels: [-100166554433],
    bound_channels: [-100199887766],
    promo_text: '✨ 由 极光果冻分享平台 驱动',
    custom_caption: '{previouscaption}\n📦 文件名: {filename}',
    custom_buttons: '📢 官方频道 - https://t.me/AuroraChannel',
    start_message: '👋 欢迎使用极光文件分享机器人！发送取件链接即可秒级获取资源。',
    force_sub_message: '⚠️ 请先加入以下主频道后再点击重试取件：',
    help_text: '发送 /share 开始上传文件并生成专属取件码。',
    admin_help_text: '/broadcast 群发通知 | /ban 封禁用户 | /stats 查看统计',
    keyword_button_text: '📥 点击提取资源'
  };

  async function demoApi(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : {};
    if (url === '/api/session') {
      const role = state.token === 'demo_platform' ? 'platform' : 'tenant';
      return {
        role,
        tenant_id: role === 'tenant' ? 't_101' : null,
        impersonated: Boolean(state.platformToken),
        tenant: { slug: 'media_hub_cn', display_name: '极光影视资源站' }
      };
    }
    if (url === '/api/platform/tenants' && method === 'GET') {
      return { tenants: demoTenants, total: demoTenants.length };
    }
    if (url === '/api/platform/tenants' && method === 'POST') {
      const created = {
        id: 't_' + Date.now(),
        slug: body.slug || 'new_tenant',
        display_name: body.display_name || body.slug || '新租户',
        owner_id: body.owner_id || 10001,
        db_channel_id: body.db_channel_id || -100123456,
        admin_ids: [body.owner_id || 10001],
        bot_username: 'NewTenantBot',
        bot_token_hint: '788990:*****1xY9',
        enabled: true,
        runtime: { status: 'running' },
        temporary_password: 'Jelly-' + Math.random().toString(36).slice(2, 10) + '!9A'
      };
      demoTenants.unshift(created);
      return created;
    }
    const tenantMatch = url.match(/^\/api\/platform\/tenants\/([^/]+)$/);
    if (tenantMatch && method === 'GET') {
      return demoTenants.find(t => t.id === tenantMatch[1]) || demoTenants[0];
    }
    if (tenantMatch && method === 'PATCH') {
      const item = demoTenants.find(t => t.id === tenantMatch[1]);
      if (item) Object.assign(item, body);
      return item || {};
    }
    const toggleMatch = url.match(/^\/api\/platform\/tenants\/([^/]+)\/(enable|disable|reset-password|restart|impersonate)$/);
    if (toggleMatch && method === 'POST') {
      const [, id, act] = toggleMatch;
      const item = demoTenants.find(t => t.id === id);
      if (act === 'enable' && item) item.enabled = true;
      if (act === 'disable' && item) item.enabled = false;
      if (act === 'restart') return { success: true, job_id: 'job_restart_1' };
      if (act === 'impersonate') return { success: true, token: 'demo_tenant', tenant_id: id };
      return { success: true };
    }
    if (url === '/api/platform/audit') {
      return {
        entries: [
          { created_at: Math.floor(Date.now() / 1000) - 180, action: 'tenant.restart', actor_role: 'platform', actor_id: 'admin', tenant_id: 't_101', target: 'AuroraShareBot' },
          { created_at: Math.floor(Date.now() / 1000) - 3600, action: 'tenant.create', actor_role: 'platform', actor_id: 'admin', tenant_id: 't_102', target: 'JellyAssetBot' }
        ],
        total: 2,
        page: 1
      };
    }
    if (url.startsWith('/api/platform/jobs/') || url.startsWith('/api/jobs/')) {
      return { id: 'job_1', state: 'completed', progress: { current: 128, total: 128, successful: 128, failed: 0 } };
    }
    if (url === '/api/dashboard') {
      return {
        tenant: { slug: 'media_hub_cn', display_name: '极光影视资源站' },
        users: { total: 3842 },
        shares: { total: demoShares.length, views: 1760, downloads: 624 },
        system: { bot: true, bot_username: '@AuroraShareBot', db_channel_status: '已连接 (-100198237465)', last_error: '' }
      };
    }
    if (url.startsWith('/api/users')) {
      return {
        items: [{ user_id: 681920311 }, { user_id: 512900112 }, { user_id: 774829104 }],
        users: [{ user_id: 681920311 }, { user_id: 512900112 }, { user_id: 774829104 }],
        page: 1,
        total_pages: 1
      };
    }
    if (url.startsWith('/api/shares') && method === 'GET') {
      const m = url.match(/^\/api\/shares\/([^/?]+)$/);
      if (m) return demoShares.find(s => s.id === m[1] || s.code === m[1]) || demoShares[0];
      return { items: demoShares, shares: demoShares, page: 1, total_pages: 1 };
    }
    const shareItemMatch = url.match(/^\/api\/shares\/([^/?]+)$/);
    if (shareItemMatch && (method === 'PUT' || method === 'PATCH')) {
      const s = demoShares.find(x => x.id === shareItemMatch[1] || x.code === shareItemMatch[1]);
      if (s) Object.assign(s, body);
      return { success: true };
    }
    if (shareItemMatch && method === 'DELETE') {
      demoShares = demoShares.filter(x => x.id !== shareItemMatch[1] && x.code !== shareItemMatch[1]);
      return { success: true };
    }
    if (url.endsWith('/forward') && method === 'POST') {
      return { success: true, job_id: 'job_fwd_1' };
    }
    if (url === '/api/broadcast' && method === 'POST') {
      return { success: true, job_id: 'job_bc_1' };
    }
    if (url === '/api/banned' && method === 'GET') {
      return { banned_users: demoBanned, total: demoBanned.length };
    }
    if (url === '/api/ban' && method === 'POST') {
      demoBanned.unshift({ user_id: body.user_id, reason: body.reason || '违规操作' });
      return { success: true };
    }
    if (url === '/api/unban' && method === 'POST') {
      demoBanned = demoBanned.filter(u => u.user_id !== body.user_id);
      return { success: true };
    }
    if (url === '/api/settings' && method === 'GET') {
      return demoSettings;
    }
    if (url === '/api/settings' && method === 'PUT') {
      demoSettings = { ...demoSettings, ...body };
      return { success: true };
    }
    if (url === '/api/system/health') {
      return { database: '🟢 Connected (Motor)', bot: '🟢 Running (@AuroraShareBot)', web_memory_mb: 48.6, last_error: '' };
    }
    return { success: true };
  }

  window.FSB = { state, esc, api, toast, modal, closeModal, pagination, pollJob, safeSetStorage };
})();
