let authToken = localStorage.getItem('fsb_token') || '';
let currentPage = 'dashboard';

/* ---- Init ---- */
document.addEventListener('DOMContentLoaded', () => {
    if (authToken) verifySession(); else showLogin();
    document.getElementById('login-password').addEventListener('keydown', e => {
        if (e.key === 'Enter') handleLogin();
    });
});

function showLogin() {
    document.getElementById('login-screen').classList.remove('hidden');
    document.getElementById('app').classList.add('hidden');
}

function showApp() {
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    navigate('dashboard');
}

/* ================================================================
   AUTH
   ================================================================ */
async function handleLogin() {
    const pw = document.getElementById('login-password').value;
    const btn = document.getElementById('login-btn');
    const err = document.getElementById('login-error');
    if (!pw) { showLoginError('请输入密码'); return; }
    btn.querySelector('.btn-text').classList.add('hidden');
    btn.querySelector('.btn-loader').classList.remove('hidden');
    err.classList.add('hidden');
    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pw })
        });
        const data = await res.json();
        if (data.success) {
            authToken = data.token;
            localStorage.setItem('fsb_token', authToken);
            showApp();
            toast('欢迎回来，管理员！', 'success');
        } else {
            showLoginError(data.message || '密码错误');
        }
    } catch (e) {
        showLoginError('网络连接异常');
    } finally {
        btn.querySelector('.btn-text').classList.remove('hidden');
        btn.querySelector('.btn-loader').classList.add('hidden');
    }
}

function showLoginError(msg) {
    const el = document.getElementById('login-error');
    el.textContent = msg; el.classList.remove('hidden');
}

async function verifySession() {
    try { const r = await api('/api/dashboard'); if (r) showApp(); else logout(); }
    catch { logout(); }
}

function handleLogout() {
    fetch('/api/logout', { method: 'POST', headers: { 'Authorization': 'Bearer ' + authToken } }).catch(() => {});
    logout();
}

function logout() {
    authToken = '';
    localStorage.removeItem('fsb_token');
    showLogin();
}

function togglePassword() {
    const i = document.getElementById('login-password');
    const ic = document.querySelector('.eye-btn i');
    if (i.type === 'password') { i.type = 'text'; ic.className = 'fas fa-eye-slash'; }
    else { i.type = 'password'; ic.className = 'fas fa-eye'; }
}

/* ================================================================
   API HELPER
   ================================================================ */
async function api(url, opts = {}) {
    try {
        const res = await fetch(url, {
            ...opts,
            headers: { 'Authorization': 'Bearer ' + authToken, 'Content-Type': 'application/json', ...(opts.headers || {}) }
        });
        if (res.status === 401) { logout(); toast('登录已过期', 'error'); return null; }
        return await res.json();
    } catch (e) { console.error('API:', e); toast('网络错误', 'error'); return null; }
}

/* ================================================================
   NAVIGATION
   ================================================================ */
const pageMeta = {
    dashboard: ['总览', '统计与运行信息'],
    users: ['用户', '用户管理'],
    shares: ['分享', '分享管理'],
    broadcast: ['群发', '发送消息'],
    banned: ['封禁', '封禁管理'],
    settings: ['设置', '机器人配置'],
    health: ['健康状态', '监控']
};

function navigate(page) {
    currentPage = page;
    document.querySelectorAll('.nav-link').forEach(el => el.classList.toggle('active', el.dataset.page === page));
    const m = pageMeta[page] || [page, ''];
    document.getElementById('page-title').textContent = m[0];
    document.getElementById('page-subtitle').textContent = m[1];
    loadPage(page);
    document.getElementById('sidebar').classList.remove('open');
}

function refreshCurrentPage() { loadPage(currentPage); toast('已刷新', 'info'); }

function toggleSidebar() {
    const sb = document.getElementById('sidebar');
    if (window.innerWidth <= 768) sb.classList.toggle('open');
}

/* ================================================================
   PAGE LOADERS
   ================================================================ */
async function loadPage(page) {
    const c = document.getElementById('page-content');
    c.innerHTML = '<div class="loading-container"><div class="spinner"></div></div>';
    switch (page) {
        case 'dashboard': await loadDashboard(c); break;
        case 'users': await loadUsers(c); break;
        case 'shares': await loadShares(c); break;
        case 'broadcast': loadBroadcast(c); break;
        case 'banned': await loadBanned(c); break;
        case 'settings': await loadSettings(c); break;
        case 'health': await loadHealth(c); break;
    }
}

/* ---- DASHBOARD ---- */
async function loadDashboard(c) {
    const d = await api('/api/dashboard');
    if (!d) return;
    const el = document.getElementById('sidebar-bot-name');
    if (el) el.textContent = d.system.bot_username ? '@' + d.system.bot_username : '管理面板';
    c.innerHTML = `
        <div class="stats-grid">
            ${statCard('fa-users', 'purple', formatNum(d.users.total), '用户总数', '今日 +' + d.users.today)}
            ${statCard('fa-share-nodes', 'blue', formatNum(d.shares.total), '分享总数', formatNum(d.shares.share_accessed) + ' 次访问')}
            ${statCard('fa-file-arrow-down', 'green', formatNum(d.shares.files_shared), '发送文件')}
            ${statCard('fa-link', 'orange', formatNum(d.shares.links_generated), '生成链接')}
        </div>
        <div class="stats-grid">
            ${statCard('fa-user-plus', 'cyan', formatNum(d.users.week), '新增用户（7天）')}
            ${statCard('fa-user-slash', 'red', formatNum(d.users.banned), '被封禁用户')}
            ${statCard('fa-tower-broadcast', 'blue', formatNum(d.activity.broadcasts), '群发次数')}
            ${statCard('fa-clock', 'purple', formatUptime(d.system.uptime), '运行时长')}
        </div>
        <div class="card">
            <div class="card-header">
                <div class="card-title"><i class="fas fa-circle-info"></i> 系统信息</div>
                <span class="badge ${d.system.database === 'connected' ? 'badge-success' : 'badge-danger'}">
                    <i class="fas fa-database"></i> ${d.system.database === 'connected' ? 'connected' : 'disconnected'}
                </span>
            </div>
            <div>
                <div class="setting-item"><div class="setting-info"><div class="setting-name">机器人用户名</div></div><div class="setting-value">@${d.system.bot_username || 'N/A'}</div></div>
                <div class="setting-item"><div class="setting-info"><div class="setting-name">完成验证次数</div></div><div class="setting-value">${formatNum(d.activity.tokens_verified)}</div></div>
            </div>
        </div>`;
}

function statCard(icon, color, value, label, badge) {
    return `<div class="stat-card">
        <div class="stat-header"><div class="stat-icon ${color}"><i class="fas ${icon}"></i></div>${badge ? `<span class="stat-badge up">${badge}</span>` : ''}</div>
        <div class="stat-value">${value}</div><div class="stat-label">${label}</div>
    </div>`;
}

/* ---- USERS ---- */
async function loadUsers(c, page) {
    page = page || 1;
    const d = await api(`/api/users?page=${page}&per_page=20`);
    if (!d) return;
    let rows = '';
    if (!d.users.length) {
        rows = '<tr><td colspan="3" class="empty-state"><i class="fas fa-users"></i><h3>暂无用户</h3></td></tr>';
    } else {
        d.users.forEach((uid, i) => {
            rows += `<tr><td>${(page - 1) * 20 + i + 1}</td><td><span class="code-text">${uid}</span></td>
            <td><button class="btn btn-danger btn-sm" onclick="banUserModal(${uid})"><i class="fas fa-ban"></i> 封禁</button></td></tr>`;
        });
    }
    c.innerHTML = `<div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-users"></i> 全部用户（${formatNum(d.total)}）</div></div>
        <table class="data-table"><thead><tr><th>#</th><th>用户ID</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>
        ${pagination(d.page, d.total_pages, 'loadUsersPage')}</div>`;
}
function loadUsersPage(p) { loadUsers(document.getElementById('page-content'), p); }

/* ================================================================
   SHARES — card layout with inline editing
   ================================================================ */

async function loadShares(c, page, search) {
    page = page || 1; search = search || '';
    const q = search ? `&search=${encodeURIComponent(search)}` : '';
    const d = await api(`/api/shares?page=${page}&per_page=15${q}`);
    if (!d) return;

    let cards = '';
    if (!d.shares.length) {
        cards = '<div class="empty-state" style="padding:60px 20px"><i class="fas fa-share-nodes"></i><h3>No shares found</h3></div>';
    } else {
        for (const s of d.shares) {
            const filesCount = Math.max(1, s.files_count || 1);
            const defaultChecked = filesCount >= 2 ? [1, 2] : [1];
            let forwardItems = '';
            for (let i = 1; i <= filesCount; i++) {
                const checked = defaultChecked.includes(i) ? 'checked' : '';
                forwardItems += `<label style="margin-right:6px;display:inline-flex;gap:3px;align-items:center;font-size:.82rem">
                    <input type="checkbox" class="fwd-item-${s.code}" value="${i}" ${checked}
                           onchange="syncFwd('${s.code}',${filesCount})">${i}</label>`;
            }

            cards += `
            <div class="card share-card" style="margin-bottom:14px">
                <!-- Row 1: Forward Selection + Fwd/Save + lock/Files/Views/eye/delete -->
                <div class="card-header" style="flex-wrap:wrap;gap:6px;padding:12px 16px">
                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;flex:1;min-width:0">
                        <label style="display:inline-flex;gap:3px;align-items:center;font-size:.82rem;white-space:nowrap">
                            <input type="checkbox" id="fwd-all-${s.code}"
                                   onchange="toggleFwdAll('${s.code}',${filesCount})"> 全选
                        </label>
                        ${forwardItems}
                        <button class="btn btn-success btn-sm" id="fwd-btn-${s.code}" onclick="doForward('${s.code}')" style="padding:4px 10px;font-size:.78rem">
                            <i class="fas fa-paper-plane"></i> 转发
                        </button>
                        <button class="btn btn-accent btn-sm" onclick="doSave('${s.code}')" style="padding:4px 10px;font-size:.78rem">
                            <i class="fas fa-save"></i> 保存
                        </button>
                    </div>
                    <div style="display:flex;align-items:center;gap:10px;margin-left:auto;flex-shrink:0">
                        <button class="btn btn-ghost btn-sm" id="lock-btn-${s.code}" onclick="toggleProtect('${s.code}')"
                                title="点击切换转发保护" style="padding:4px 8px;font-size:.85rem;cursor:pointer">
                            <span id="lock-icon-${s.code}">${s.protect_content ? '🔒' : '🔓'}</span>
                        </button>
                        <span style="font-size:.82rem;color:var(--text-secondary);white-space:nowrap">📁${s.files_count}</span>
                        <span style="font-size:.82rem;color:var(--text-secondary);white-space:nowrap">👁${formatNum(s.access_count)}</span>
                        <button class="btn btn-ghost btn-sm" onclick="viewShareInfo('${s.code}')" title="详情" style="padding:4px 8px"><i class="fas fa-eye"></i></button>
                        <button class="btn btn-danger btn-sm" onclick="deleteShare('${s.code}')" title="删除" style="padding:4px 8px"><i class="fas fa-trash"></i></button>
                    </div>
                </div>

                <!-- Row 2: Keywords + Media Text -->
                <div style="padding:10px 16px 14px;display:flex;gap:10px;flex-wrap:wrap">
                    <div style="width:140px;flex-shrink:0">
                        <label class="form-label" style="margin-bottom:3px;font-size:.78rem">关键词</label>
                        <input class="form-input" id="kw-${s.code}" value="${esc((s.keywords || []).join(','))}"
                               placeholder="逗号分隔" style="width:100%;font-size:.85rem;padding:5px 8px">
                    </div>
                    <div style="flex:1;min-width:160px">
                        <label class="form-label" style="margin-bottom:3px;font-size:.78rem">媒体组文字</label>
                        <textarea class="form-textarea" id="gt-${s.code}" rows="1"
                                  oninput="autoGrow(this)"
                                  placeholder="输入媒体组文字..." style="font-size:.85rem;padding:5px 8px;resize:none;overflow:hidden;min-height:30px">${esc(s.group_text || '')}</textarea>
                    </div>
                </div>
            </div>`;
        }
    }

    c.innerHTML = `
        <div class="card" style="margin-bottom:16px">
            <div class="card-header">
                <div class="card-title"><i class="fas fa-share-nodes"></i> 分享（${formatNum(d.total)}）</div>
                <div class="card-actions">
                    <div class="search-box"><i class="fas fa-search"></i>
                        <input type="text" placeholder="搜索..." id="share-search" value="${esc(search)}"
                               onkeydown="if(event.key==='Enter')searchShares()">
                    </div>
                    <button class="btn btn-ghost btn-sm" onclick="searchShares()"><i class="fas fa-search"></i></button>
                </div>
            </div>
        </div>
        <div id="shares-list">${cards}</div>
        ${pagination(d.page, d.total_pages, 'loadSharesPage')}`;

    if (d.shares.length) {
        d.shares.forEach(s => {
            syncFwd(s.code, Math.max(1, s.files_count || 1));
        });
        /* 初始化 textarea 自动高度 */
        requestAnimationFrame(() => {
            d.shares.forEach(s => {
                const ta = document.getElementById('gt-' + s.code);
                if (ta && ta.value) autoGrow(ta);
            });
        });
    }
}

function loadSharesPage(p) { loadShares(document.getElementById('page-content'), p); }
function searchShares() {
    const v = document.getElementById('share-search');
    loadShares(document.getElementById('page-content'), 1, v ? v.value.trim() : '');
}

/* Forward helpers */
function toggleFwdAll(code, total) {
    const all = document.getElementById('fwd-all-' + code);
    document.querySelectorAll('.fwd-item-' + code).forEach(i => { i.checked = all.checked; });
}

function syncFwd(code, total) {
    const all = document.getElementById('fwd-all-' + code);
    const items = Array.from(document.querySelectorAll('.fwd-item-' + code));
    if (all && items.length) all.checked = items.every(i => i.checked);
}

function getFwdSelection(code) {
    const all = document.getElementById('fwd-all-' + code);
    if (all && all.checked) return { forward_all: true, forward_indices: [] };
    const items = Array.from(document.querySelectorAll('.fwd-item-' + code + ':checked'))
        .map(i => parseInt(i.value)).filter(v => !isNaN(v));
    return { forward_all: false, forward_indices: items };
}

/* Toggle protect_content */
async function toggleProtect(code) {
    const icon = document.getElementById('lock-icon-' + code);
    if (!icon) return;
    const currentlyLocked = icon.textContent.trim() === '🔒';
    const newValue = !currentlyLocked;
    const r = await api(`/api/shares/${code}`, {
        method: 'PUT',
        body: JSON.stringify({ protect_content: newValue })
    });
    if (r && r.success) {
        icon.textContent = newValue ? '🔒' : '🔓';
        toast(newValue ? 'Protected ✓' : 'Unprotected ✓', 'success');
    } else {
        toast('Failed to toggle', 'error');
    }
}

/* Save */
async function doSave(code) {
    const keywords = document.getElementById('kw-' + code)?.value || '';
    const group_text = document.getElementById('gt-' + code)?.value || '';
    const r = await api(`/api/shares/${code}`, {
        method: 'PUT',
        body: JSON.stringify({ keywords, group_text })
    });
    if (r && r.success) toast('Saved ✓', 'success');
    else toast('Save failed', 'error');
}

/* Forward */
async function doForward(code) {
    const keywords = document.getElementById('kw-' + code)?.value || '';
    const group_text = document.getElementById('gt-' + code)?.value || '';
    const selection = getFwdSelection(code);
    const btn = document.getElementById('fwd-btn-' + code);
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>'; }
    const r = await api(`/api/shares/${code}/forward`, {
        method: 'POST',
        body: JSON.stringify({ keywords, group_text, ...selection })
    });
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-paper-plane"></i> Fwd'; }
    if (r && r.success) toast(`Forwarded → ${r.successful} ch`, 'success');
    else toast(r && r.error ? r.error : 'Forward failed', 'error');
}

/* Eye button → modal with readonly details */
async function viewShareInfo(code) {
    const d = await api(`/api/shares/${code}`);
    if (!d) return;
    const date = d.created_at ? new Date(d.created_at * 1000).toLocaleString() : 'N/A';
    const link = d.link ? `<a href="${esc(d.link)}" target="_blank" style="word-break:break-all">${esc(d.link)}</a>` : 'N/A';
    const kw = (d.keywords && d.keywords.length) ? d.keywords.join(', ') : 'None';

    openModal('Share Details', `
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Code</div></div>
            <div class="setting-value"><span class="code-text">${esc(d.code)}</span></div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Title</div></div>
            <div class="setting-value">${esc(d.title || 'Untitled')}</div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Link</div></div>
            <div class="setting-value">${link}</div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Media Text</div></div>
            <div class="setting-value">${esc(d.group_text || 'None')}</div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Keywords</div></div>
            <div class="setting-value">${esc(kw)}</div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Files</div></div>
            <div class="setting-value">${d.files_count}</div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Views</div></div>
            <div class="setting-value">${formatNum(d.access_count)}</div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Protected</div></div>
            <div class="setting-value">${d.protect_content ? 'Yes' : 'No'}</div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Owner</div></div>
            <div class="setting-value">${d.owner_id}</div></div>
        <div class="setting-item"><div class="setting-info"><div class="setting-name">Created</div></div>
            <div class="setting-value">${date}</div></div>`,
        `<button class="btn btn-ghost" onclick="closeModal()">Close</button>`);
}

/* Delete share */
function deleteShare(code) {
    openModal('Delete Share',
        `<p style="margin-bottom:12px">Delete share <strong>${code}</strong>?</p>
         <p style="color:var(--danger);font-size:.82rem"><i class="fas fa-triangle-exclamation"></i> This cannot be undone.</p>`,
        `<button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
         <button class="btn btn-danger" onclick="confirmDeleteShare('${code}')"><i class="fas fa-trash"></i> Delete</button>`);
}
async function confirmDeleteShare(code) {
    const r = await api(`/api/shares/${code}`, { method: 'DELETE' });
    closeModal();
    if (r && r.success) { toast('Share deleted', 'success'); navigate('shares'); }
    else toast('Failed', 'error');
}

/* ---- BROADCAST ---- */
function loadBroadcast(c) {
    c.innerHTML = `<div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-tower-broadcast"></i> Send Broadcast</div></div>
        <div class="broadcast-form">
            <div class="form-group"><label class="form-label">Message (HTML supported)</label><textarea class="form-textarea" id="broadcast-msg" rows="6" placeholder="Enter broadcast message..."></textarea></div>
            <button class="btn btn-accent" onclick="sendBroadcast()" id="broadcast-btn"><i class="fas fa-paper-plane"></i> Send Broadcast</button>
            <div id="broadcast-result" style="margin-top:20px"></div>
        </div></div>`;
}
async function sendBroadcast() {
    const msg = document.getElementById('broadcast-msg').value.trim();
    if (!msg) { toast('Message required', 'error'); return; }
    const btn = document.getElementById('broadcast-btn');
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Sending...';
    const r = await api('/api/broadcast', { method: 'POST', body: JSON.stringify({ message: msg }) });
    btn.disabled = false; btn.innerHTML = '<i class="fas fa-paper-plane"></i> Send Broadcast';
    if (r && r.success) {
        document.getElementById('broadcast-result').innerHTML = `<div class="card" style="margin:0"><div style="padding:20px">
            <h3 style="color:var(--success);margin-bottom:12px"><i class="fas fa-check-circle"></i> 群发完成</h3>
            <div class="setting-item"><div class="setting-info"><div class="setting-name">总计</div></div><div class="setting-value">${r.total}</div></div>
            <div class="setting-item"><div class="setting-info"><div class="setting-name">成功</div></div><div class="setting-value" style="color:var(--success)">${r.successful}</div></div>
            <div class="setting-item"><div class="setting-info"><div class="setting-name">失败</div></div><div class="setting-value" style="color:var(--danger)">${r.failed}</div></div>
        </div></div>`;
        toast(`已发送给 ${r.successful} 位用户`, 'success');
    } else toast('群发失败', 'error');
}

/* ---- BANNED ---- */
async function loadBanned(c) {
    const d = await api('/api/banned');
    if (!d) return;
    let rows = '';
    if (!d.banned_users.length) {
        rows = '<tr><td colspan="4" class="empty-state"><i class="fas fa-user-check"></i><h3>暂无被封禁用户</h3><p>一切正常！</p></td></tr>';
    } else {
        d.banned_users.forEach((u, i) => {
            const dt = u.banned_at ? new Date(u.banned_at * 1000).toLocaleDateString() : 'N/A';
            rows += `<tr><td>${i + 1}</td><td><span class="code-text">${u.user_id}</span></td><td>${esc(u.reason || '未提供原因')}</td>
            <td><button class="btn btn-success btn-sm" onclick="unbanUser(${u.user_id})"><i class="fas fa-user-check"></i> 解封</button></td></tr>`;
        });
    }
    c.innerHTML = `<div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-user-slash"></i> 被封禁用户（${d.total}）</div>
        <button class="btn btn-danger" onclick="banUserModal()"><i class="fas fa-ban"></i> 封禁用户</button></div>
        <table class="data-table"><thead><tr><th>#</th><th>用户ID</th><th>原因</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function banUserModal(prefill) {
    prefill = prefill || '';
    openModal('封禁用户',
        `<div class="form-group"><label class="form-label">用户ID</label><input class="form-input" type="number" id="ban-uid" placeholder="请输入用户ID" value="${prefill}"></div>
         <div class="form-group"><label class="form-label">原因</label><input class="form-input" type="text" id="ban-reason" placeholder="可选"></div>`,
        `<button class="btn btn-ghost" onclick="closeModal()">取消</button><button class="btn btn-danger" onclick="confirmBan()"><i class="fas fa-ban"></i> 确认封禁</button>`);
}
async function confirmBan() {
    const uid = document.getElementById('ban-uid').value;
    const reason = document.getElementById('ban-reason').value || '管理员面板封禁';
    if (!uid) { toast('需要用户ID', 'error'); return; }
    const r = await api('/api/ban', { method: 'POST', body: JSON.stringify({ user_id: parseInt(uid), reason }) });
    closeModal();
    if (r && r.success) { toast(`已封禁用户 ${uid}`, 'success'); if (currentPage === 'banned') navigate('banned'); } else toast('封禁失败', 'error');
}
async function unbanUser(uid) {
    const r = await api('/api/unban', { method: 'POST', body: JSON.stringify({ user_id: uid }) });
    if (r && r.success) { toast(`已解封用户 ${uid}`, 'success'); navigate('banned'); } else toast('解封失败', 'error');
}

/* ---- SETTINGS ---- */
async function loadSettings(c) {
    const d = await api('/api/settings');
    if (!d) return;
    c.innerHTML = `
        <div class="settings-grid">
            <div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-shield-halved"></i> 安全</div></div><div>
                ${toggle('is_verify', '令牌验证', '使用前需完成外链验证', d.is_verify)}
                ${numInput('verify_expire', '验证有效期（秒）', '令牌有效时长', d.verify_expire)}
                ${toggle('protect_content', '禁止转发保护', '全局阻止转发/保存', d.protect_content)}
            </div></div>
            <div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-sliders"></i> 功能</div></div><div>
                ${numInput('auto_delete_time', '自动删除（秒）', '0 表示关闭', d.auto_delete_time)}
                ${numInput('share_code_length', '分享码长度', '字符数量', d.share_code_length)}
                ${toggle('show_promo', '显示推广文案', '在消息中附加推广', d.show_promo)}
                ${toggle('disable_channel_button', '隐藏频道按钮', '隐藏帖子上的按钮', d.disable_channel_button)}
            </div></div>
            <div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-gauge-high"></i> 速率限制</div></div><div>
                ${numInput('rate_limit_max', '最大请求数', '每个时间窗口', d.rate_limit_max)}
                ${numInput('rate_limit_window', '时间窗口（秒）', '统计时间窗', d.rate_limit_window)}
            </div></div>
            <div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-list"></i> 强制关注频道</div></div><div>
                ${(Array.isArray(d.force_sub_channels) && d.force_sub_channels.length)
                    ? d.force_sub_channels.map(ch => `<div class="setting-item"><div class="setting-info"><div class="setting-name">频道ID</div></div><div class="setting-value">${ch}</div><div style="margin-left:auto"><button class="btn btn-danger btn-sm" onclick="removeForceChannel(${ch})"><i class="fas fa-trash"></i> 移除</button></div></div>`).join('')
                    : '<div class="setting-item"><div class="setting-info"><div class="setting-name">尚未配置频道</div><div class="setting-desc">在下方输入框添加频道ID</div></div></div>'}
                <div class="setting-item"><div class="setting-info"><div class="setting-name">添加频道ID</div><div class="setting-desc">例如 -1001234567890</div></div>
                    <input type="number" class="form-input" id="force-channel-input" placeholder="-1001234567890">
                    <button class="btn btn-accent btn-sm" style="margin-left:10px" onclick="addForceChannel(${JSON.stringify(d.force_sub_channels || [])})"><i class="fas fa-plus"></i> 添加</button>
                </div>
            </div></div>
            <div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-hashtag"></i> 关键词回复绑定频道</div></div><div>
                ${(Array.isArray(d.bound_channels) && d.bound_channels.length)
                    ? d.bound_channels.map(ch => `<div class="setting-item"><div class="setting-info"><div class="setting-name">频道ID</div></div><div class="setting-value">${ch}</div><div style="margin-left:auto"><button class="btn btn-danger btn-sm" onclick="removeBoundChannel(${ch})"><i class="fas fa-trash"></i> 移除</button></div></div>`).join('')
                    : '<div class="setting-item"><div class="setting-info"><div class="setting-name">尚未绑定频道</div><div class="setting-desc">在讨论组评论按关键字回复分享链接</div></div></div>'}
                <div class="setting-item"><div class="setting-info"><div class="setting-name">添加频道ID</div><div class="setting-desc">例如 -1001234567890</div></div>
                    <input type="number" class="form-input" id="bound-channel-input" placeholder="-1001234567890">
                    <button class="btn btn-accent btn-sm" style="margin-left:10px" onclick="addBoundChannel(${JSON.stringify(d.bound_channels || [])})"><i class="fas fa-plus"></i> 添加</button>
                </div>
            </div></div>
        </div>
        <div class="card" style="margin-top:20px"><div class="card-header"><div class="card-title"><i class="fas fa-message"></i> 文案模板</div></div>
        <div style="padding:20px">
            <div class="form-group"><label class="form-label">开始消息（HTML）</label><textarea class="form-textarea" id="s-start_message" rows="3">${esc(d.start_message || '')}</textarea></div>
            <div class="form-group"><label class="form-label">强制关注提示（HTML）</label><textarea class="form-textarea" id="s-force_sub_message" rows="3">${esc(d.force_sub_message || '')}</textarea></div>
            <div class="form-group"><label class="form-label">用户回复文本</label><textarea class="form-textarea" id="s-user_reply_text" rows="2">${esc(d.user_reply_text || '')}</textarea></div>
            <div class="form-group"><label class="form-label">推广文案（HTML）</label><textarea class="form-textarea" id="s-promo_text" rows="2">${esc(d.promo_text || '')}</textarea></div>
            <div class="form-group"><label class="form-label">关于（HTML）</label><textarea class="form-textarea" id="s-about_text" rows="3">${esc(d.about_text || '')}</textarea></div>
            <div class="form-group"><label class="form-label">帮助（HTML）</label><textarea class="form-textarea" id="s-help_text" rows="4">${esc(d.help_text || '')}</textarea></div>
            <div class="form-group"><label class="form-label">管理员帮助（HTML）</label><textarea class="form-textarea" id="s-admin_help_text" rows="6">${esc(d.admin_help_text || '')}</textarea></div>
            <div class="form-group"><label class="form-label">关键词按钮文本</label><input class="form-input" id="s-keyword_button_text" value="${esc(d.keyword_button_text || '')}" placeholder="例如：🔗 获取资源"></div>
            <div class="form-group"><label class="form-label">自定义字幕</label><input class="form-input" id="s-custom_caption" value="${esc(d.custom_caption || '')}" placeholder="留空使用原文案"></div>
            <div class="form-group"><label class="form-label">自定义按钮（text|url, ...）</label><input class="form-input" id="s-custom_buttons" value="${esc(d.custom_buttons || '')}" placeholder="按钮1|https://... , 按钮2|https://..."></div>
        </div></div>
        <div style="margin-top:20px;display:flex;gap:10px">
            <button class="btn btn-accent" onclick="saveAllSettings()"><i class="fas fa-save"></i> 保存全部设置</button>
            <button class="btn btn-ghost" onclick="navigate('settings')"><i class="fas fa-arrows-rotate"></i> 重新加载</button>
        </div>`;
}

function toggle(key, name, desc, val) {
    return `<div class="setting-item"><div class="setting-info"><div class="setting-name">${name}</div><div class="setting-desc">${desc}</div></div>
        <label class="toggle"><input type="checkbox" id="s-${key}" ${val ? 'checked' : ''}><span class="toggle-slider"></span></label></div>`;
}

function numInput(key, name, desc, val) {
    return `<div class="setting-item"><div class="setting-info"><div class="setting-name">${name}</div><div class="setting-desc">${desc}</div></div>
        <input type="number" class="form-input" id="s-${key}" value="${val}"></div>`;
}

async function saveAllSettings() {
    const settings = {};
    ['is_verify', 'protect_content', 'show_promo', 'disable_channel_button'].forEach(k => {
        const el = document.getElementById('s-' + k);
        if (el) settings[k] = el.checked;
    });
    ['verify_expire', 'auto_delete_time', 'share_code_length', 'rate_limit_max', 'rate_limit_window'].forEach(k => {
        const el = document.getElementById('s-' + k);
        if (el) settings[k] = parseInt(el.value) || 0;
    });
    ['start_message', 'force_sub_message', 'user_reply_text', 'promo_text', 'about_text', 'help_text', 'admin_help_text', 'keyword_button_text', 'custom_caption', 'custom_buttons'].forEach(k => {
        const el = document.getElementById('s-' + k);
        if (el) settings[k] = el.value;
    });
    const r = await api('/api/settings', { method: 'PUT', body: JSON.stringify(settings) });
    if (r && r.success) toast(`已保存 ${r.updated.length} 项设置`, 'success');
    else toast('保存失败', 'error');
}

async function addBoundChannel(current) {
    try {
        const input = document.getElementById('bound-channel-input');
        const val = input ? parseInt(input.value) : 0;
        if (!val) { toast('Channel ID required', 'error'); return; }
        const set = Array.isArray(current) ? current.slice() : [];
        if (!set.includes(val)) set.push(val);
        const r = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ bound_channels: set }) });
        if (r && r.success) { toast('Channel added', 'success'); navigate('settings'); }
        else toast('Failed to add', 'error');
    } catch (e) {
        toast('Error', 'error');
    }
}

async function addForceChannel(current) {
    try {
        const input = document.getElementById('force-channel-input');
        const val = input ? parseInt(input.value) : 0;
        if (!val) { toast('需要频道ID', 'error'); return; }
        const set = Array.isArray(current) ? current.slice() : [];
        if (!set.includes(val)) set.push(val);
        const r = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ force_sub_channels: set }) });
        if (r && r.success) { toast('已添加频道', 'success'); navigate('settings'); }
        else toast('添加失败', 'error');
    } catch (e) {
        toast('错误', 'error');
    }
}

async function removeForceChannel(id) {
    try {
        const d = await api('/api/settings');
        const list = Array.isArray(d && d.force_sub_channels) ? d.force_sub_channels : [];
        const next = list.filter(x => x !== id);
        const r = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ force_sub_channels: next }) });
        if (r && r.success) { toast('已移除频道', 'success'); navigate('settings'); }
        else toast('移除失败', 'error');
    } catch (e) {
        toast('错误', 'error');
    }
}

async function removeBoundChannel(id) {
    try {
        const d = await api('/api/settings');
        const list = Array.isArray(d && d.bound_channels) ? d.bound_channels : [];
        const next = list.filter(x => x !== id);
        const r = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ bound_channels: next }) });
        if (r && r.success) { toast('Channel removed', 'success'); navigate('settings'); }
        else toast('Failed to remove', 'error');
    } catch (e) {
        toast('Error', 'error');
    }
}

/* ---- HEALTH ---- */
async function loadHealth(c) {
    const d = await api('/api/health');
    if (!d) return;
    const dbColor = d.database === 'connected' ? 'var(--success)' : 'var(--danger)';
    c.innerHTML = `
        <div class="health-grid">
            <div class="health-item"><div class="health-icon" style="color:${dbColor}"><i class="fas fa-database"></i></div><div class="health-label">数据库</div><div class="health-value" style="color:${dbColor}">${d.database === 'connected' ? '已连接' : '未连接'}</div></div>
            <div class="health-item"><div class="health-icon" style="color:var(--info)"><i class="fas fa-microchip"></i></div><div class="health-label">内存</div><div class="health-value">${d.memory_mb} MB</div></div>
            <div class="health-item"><div class="health-icon" style="color:var(--warning)"><i class="fas fa-gauge"></i></div><div class="health-label">CPU</div><div class="health-value">${d.cpu_percent}%</div></div>
            <div class="health-item"><div class="health-icon" style="color:var(--purple)"><i class="fas fa-layer-group"></i></div><div class="health-label">线程数</div><div class="health-value">${d.threads}</div></div>
        </div>
        <div class="card"><div class="card-header"><div class="card-title"><i class="fas fa-terminal"></i> 操作</div></div>
        <div style="padding:20px"><button class="btn btn-accent" onclick="loadHealth(document.getElementById('page-content'))"><i class="fas fa-arrows-rotate"></i> 刷新</button></div></div>`;
}

/* ================================================================
   UTILITIES
   ================================================================ */
function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
}

function pagination(cur, total, fn) {
    if (total <= 1) return '';
    let h = '<div class="pagination">';
    h += `<button onclick="${fn}(${cur - 1})" ${cur <= 1 ? 'disabled' : ''}><i class="fas fa-chevron-left"></i></button>`;
    const s = Math.max(1, cur - 2), e = Math.min(total, cur + 2);
    if (s > 1) { h += `<button onclick="${fn}(1)">1</button>`; if (s > 2) h += '<button disabled>…</button>'; }
    for (let i = s; i <= e; i++) h += `<button class="${i === cur ? 'active' : ''}" onclick="${fn}(${i})">${i}</button>`;
    if (e < total) { if (e < total - 1) h += '<button disabled>…</button>'; h += `<button onclick="${fn}(${total})">${total}</button>`; }
    h += `<button onclick="${fn}(${cur + 1})" ${cur >= total ? 'disabled' : ''}><i class="fas fa-chevron-right"></i></button></div>`;
    return h;
}

function formatNum(n) {
    if (n == null) return '0';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(n);
}

function formatUptime(s) {
    if (!s) return '0m';
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    let r = ''; if (d) r += d + 'd '; if (h) r += h + 'h '; r += m + 'm'; return r;
}

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

function toast(msg, type) {
    type = type || 'info';
    const icons = { success: 'fa-check-circle', error: 'fa-circle-xmark', info: 'fa-circle-info' };
    const ct = document.getElementById('toast-container');
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.innerHTML = `<i class="fas ${icons[type] || icons.info}"></i><span>${msg}</span>`;
    ct.appendChild(t);
    setTimeout(() => { t.style.animation = 'toastOut .3s forwards'; setTimeout(() => t.remove(), 300); }, 4000);
}

function openModal(title, body, actions) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = body;
    document.getElementById('modal-actions').innerHTML = actions || '';
    document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal() { document.getElementById('modal-overlay').classList.add('hidden'); }
