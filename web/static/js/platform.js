(function () {
    const { api, esc, toast, modal, closeModal, pollJob } = window.FSB;

    async function tenants(container) {
        const data = await api('/api/platform/tenants');
        const rows = data.tenants.map(t => `<tr>
            <td><strong>${esc(t.display_name)}</strong><br><span class="text-muted">${esc(t.slug)}</span></td>
            <td>${t.bot_username ? '@' + esc(t.bot_username) : '—'}<br><span class="code-text">${esc(t.bot_token_hint)}</span></td>
            <td><span class="badge ${t.runtime.status === 'running' ? 'badge-success' : t.runtime.status === 'error' ? 'badge-danger' : 'badge-warning'}">${esc(t.runtime.status)}</span></td>
            <td>${t.enabled ? '<span class="badge badge-success">启用</span>' : '<span class="badge badge-warning">停用</span>'}</td>
            <td><div class="table-actions">
                <button class="btn btn-ghost btn-sm" onclick="Platform.edit('${t.id}')">编辑</button>
                <button class="btn btn-ghost btn-sm" onclick="Platform.impersonate('${t.id}')">进入后台</button>
                ${t.enabled ? `<button class="btn btn-ghost btn-sm" onclick="Platform.restart('${t.id}')">重启</button>` : ''}
                <button class="btn ${t.enabled ? 'btn-danger' : 'btn-success'} btn-sm" onclick="Platform.toggle('${t.id}', ${!t.enabled})">${t.enabled ? '停用' : '启用'}</button>
            </div></td>
        </tr>`).join('');
        container.innerHTML = `<div class="page-actions"><button class="btn btn-accent" onclick="Platform.create()"><i class="fas fa-plus"></i> 新建租户</button></div>
            <div class="table-card"><table><thead><tr><th>租户</th><th>Bot</th><th>运行状态</th><th>租户状态</th><th>操作</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty-state">暂无租户</td></tr>'}</tbody></table></div>`;
    }

    function tenantForm(value = {}) {
        return `<div class="form-group"><label class="form-label">租户标识</label><input class="form-input" id="tenant-slug" value="${esc(value.slug || '')}" ${value.id ? 'disabled' : ''}></div>
            <div class="form-group"><label class="form-label">显示名称</label><input class="form-input" id="tenant-name" value="${esc(value.display_name || '')}"></div>
            <div class="form-group"><label class="form-label">Bot Token ${value.id ? '（留空表示不更换）' : ''}</label><input class="form-input" type="password" id="tenant-token"></div>
            <div class="form-group"><label class="form-label">Owner Telegram ID</label><input class="form-input" type="number" id="tenant-owner" value="${esc(value.owner_id || '')}"></div>
            <div class="form-group"><label class="form-label">数据库频道 ID</label><input class="form-input" type="number" id="tenant-channel" value="${esc(value.db_channel_id || '')}"></div>
            ${value.id ? `<div class="form-group"><label class="form-label">管理员 ID（逗号分隔）</label><input class="form-input" id="tenant-admins" value="${esc((value.admin_ids || []).join(','))}"></div>` : '<p class="text-muted">后台密码由系统自动生成；Owner 将自动成为租户管理员。</p>'}`;
    }

    function formData(isCreate) {
        const result = {
            display_name: document.getElementById('tenant-name').value.trim(),
            owner_id: Number(document.getElementById('tenant-owner').value),
            db_channel_id: Number(document.getElementById('tenant-channel').value)
        };
        const admins = document.getElementById('tenant-admins');
        if (admins) result.admin_ids = admins.value.split(',').map(v => v.trim()).filter(Boolean).map(Number);
        const token = document.getElementById('tenant-token').value.trim();
        if (token) result.bot_token = token;
        if (isCreate) {
            result.slug = document.getElementById('tenant-slug').value.trim();
        }
        return result;
    }

    function create() {
        modal('新建租户', tenantForm(), '<button class="btn btn-ghost" onclick="FSB.closeModal()">取消</button><button class="btn btn-accent" onclick="Platform.saveNew()">验证并创建</button>');
    }

    async function saveNew() {
        try {
            const tenant = await api('/api/platform/tenants', { method: 'POST', body: JSON.stringify(formData(true)) });
            modal('租户已创建', `<p>请立即保存以下登录信息，密码关闭后不再显示。</p><div class="form-group"><label class="form-label">租户标识</label><div class="code-text">${esc(tenant.slug)}</div></div><div class="form-group"><label class="form-label">临时密码</label><div class="code-text">${esc(tenant.temporary_password)}</div></div>`, '<button class="btn btn-accent" onclick="FSB.closeModal(); App.navigate(\'tenants\')">完成</button>');
        } catch (error) { toast(error.message, 'error'); }
    }

    async function edit(id) {
        try {
            const tenant = await api('/api/platform/tenants/' + id);
            modal('编辑租户', tenantForm(tenant), `<button class="btn btn-ghost" onclick="Platform.resetPassword('${id}')">重置密码</button><button class="btn btn-accent" onclick="Platform.saveEdit('${id}')">保存</button>`);
        } catch (error) { toast(error.message, 'error'); }
    }

    async function saveEdit(id) {
        try {
            await api('/api/platform/tenants/' + id, { method: 'PATCH', body: JSON.stringify(formData(false)) });
            closeModal(); toast('租户已更新，Worker 将自动应用配置', 'success'); window.App.navigate('tenants');
        } catch (error) { toast(error.message, 'error'); }
    }

    async function toggle(id, enabled) {
        try {
            await api(`/api/platform/tenants/${id}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' });
            toast(enabled ? '租户已启用' : '租户已停用', 'success'); window.App.navigate('tenants');
        } catch (error) { toast(error.message, 'error'); }
    }

    function resetPassword(id) {
        modal('重置租户密码', '<div class="form-group"><label class="form-label">新密码（至少 12 位）</label><input class="form-input" type="password" id="reset-password"></div>', `<button class="btn btn-accent" onclick="Platform.savePassword('${id}')">确认重置</button>`);
    }

    async function savePassword(id) {
        try {
            await api(`/api/platform/tenants/${id}/reset-password`, { method: 'POST', body: JSON.stringify({ web_password: document.getElementById('reset-password').value }) });
            closeModal(); toast('密码已重置', 'success');
        } catch (error) { toast(error.message, 'error'); }
    }

    async function impersonate(id) {
        try {
            const result = await api(`/api/platform/tenants/${id}/impersonate`, { method: 'POST' });
            window.FSB.state.platformToken = window.FSB.state.token;
            localStorage.setItem('fsb_platform_token', window.FSB.state.platformToken);
            window.FSB.state.token = result.token;
            localStorage.setItem('fsb_token', result.token);
            await window.App.loadSession();
        } catch (error) { toast(error.message, 'error'); }
    }

    async function restart(id) {
        try {
            const result = await api(`/api/platform/tenants/${id}/restart`, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() } });
            toast('重启任务已创建', 'info');
            const job = await pollJob(result.job_id, null, '/api/platform/jobs/');
            toast(job.state === 'completed' ? 'Bot 已重启' : `重启失败：${job.last_error}`, job.state === 'completed' ? 'success' : 'error');
            window.App.navigate('tenants');
        } catch (error) { toast(error.message, 'error'); }
    }

    async function audit(container) {
        const data = await api('/api/platform/audit');
        container.innerHTML = `<div class="table-card"><table><thead><tr><th>时间</th><th>操作</th><th>操作者</th><th>租户</th><th>目标</th></tr></thead><tbody>${data.entries.map(e => `<tr><td>${new Date(e.created_at * 1000).toLocaleString()}</td><td>${esc(e.action)}</td><td>${esc(e.actor_role)} / ${esc(e.actor_id)}</td><td>${esc(e.tenant_id || '—')}</td><td>${esc(e.target || '—')}</td></tr>`).join('')}</tbody></table></div>`;
    }

    window.Platform = { tenants, audit, create, saveNew, edit, saveEdit, toggle, resetPassword, savePassword, impersonate, restart };
})();
