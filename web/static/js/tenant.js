(function () {
    const { api, esc, toast, modal, closeModal, pagination, pollJob } = window.FSB;

    function card(icon, value, label) {
        return `<div class="stat-card"><div class="stat-icon"><i class="fas fa-${icon}"></i></div><div class="stat-info"><div class="stat-value">${esc(value)}</div><div class="stat-label">${esc(label)}</div></div></div>`;
    }

    async function dashboard(container) {
        const data = await api('/api/dashboard');
        document.getElementById('sidebar-context').textContent = data.tenant.display_name;
        container.innerHTML = `<div class="stats-grid">
            ${card('users', data.users.total, '总用户')}${card('share-nodes', data.shares.total, '分享')}${card('eye', data.shares.share_accessed, '访问')}${card('user-slash', data.users.banned, '封禁')}
        </div><div class="card"><div class="card-header"><h3>运行状态</h3></div><div class="card-body">
            <div class="health-grid"><div class="health-item"><div class="health-label">Bot</div><div class="health-value">${esc(data.system.status)}</div></div>
            <div class="health-item"><div class="health-label">用户名</div><div class="health-value">${data.system.bot_username ? '@' + esc(data.system.bot_username) : '—'}</div></div>
            <div class="health-item"><div class="health-label">数据库</div><div class="health-value">${esc(data.system.database)}</div></div></div>
            ${data.system.last_error ? `<div class="alert alert-danger">${esc(data.system.last_error)}</div>` : ''}
        </div></div>`;
    }

    async function users(container, page = 1) {
        const data = await api(`/api/users?page=${page}`);
        container.innerHTML = `<div class="table-card"><table><thead><tr><th>#</th><th>Telegram User ID</th><th>操作</th></tr></thead><tbody>${data.users.map((id, index) => `<tr><td>${(page - 1) * data.per_page + index + 1}</td><td><span class="code-text">${id}</span></td><td><button class="btn btn-danger btn-sm" onclick="Tenant.banModal(${id})">封禁</button></td></tr>`).join('')}</tbody></table>${pagination(page, data.total_pages, 'Tenant.usersPage')}</div>`;
    }
    function usersPage(page) { window.App.loadPage('users', page); }

    async function shares(container, page = 1, search = '') {
        const data = await api(`/api/shares?page=${page}&search=${encodeURIComponent(search)}`);
        container.innerHTML = `<div class="page-actions"><div class="search-box"><input class="form-input" id="share-search" value="${esc(search)}" placeholder="标题、关键词或分享码"><button class="btn btn-accent" onclick="Tenant.searchShares()">搜索</button></div></div>
            <div class="table-card"><table><thead><tr><th>分享</th><th>文件</th><th>访问</th><th>保护</th><th>操作</th></tr></thead><tbody>${data.shares.map(s => `<tr>
                <td><strong>${esc(s.title)}</strong><br><span class="code-text">${esc(s.code)}</span></td><td>${s.files_count}</td><td>${s.access_count}</td><td>${s.protect_content ? '🔒' : '🔓'}</td>
                <td><div class="table-actions"><button class="btn btn-ghost btn-sm" onclick="Tenant.editShare('${s.code}')">编辑</button><button class="btn btn-ghost btn-sm" onclick="Tenant.forwardShare('${s.code}')">转发</button><button class="btn btn-danger btn-sm" onclick="Tenant.removeShare('${s.code}')">删除</button></div></td>
            </tr>`).join('')}</tbody></table>${pagination(page, data.total_pages, 'Tenant.sharesPage')}</div>`;
    }
    function sharesPage(page) { window.App.loadPage('shares', page); }
    function searchShares() { window.App.loadPage('shares', 1, document.getElementById('share-search').value); }

    async function editShare(code) {
        try {
            const share = await api('/api/shares/' + code);
            modal('编辑分享', `<div class="form-group"><label class="form-label">标题</label><input class="form-input" id="share-title" value="${esc(share.title)}"></div>
                <div class="form-group"><label class="form-label">关键词（逗号分隔）</label><input class="form-input" id="share-keywords" value="${esc(share.keywords.join(','))}"></div>
                <div class="form-group"><label class="form-label">频道文字</label><textarea class="form-textarea" id="share-text">${esc(share.group_text)}</textarea></div>
                <label><input type="checkbox" id="share-protect" ${share.protect_content ? 'checked' : ''}> 禁止转发</label>`,
                `<button class="btn btn-accent" onclick="Tenant.saveShare('${code}')">保存</button>`);
        } catch (error) { toast(error.message, 'error'); }
    }
    function sharePayload() {
        return { title: document.getElementById('share-title').value, keywords: document.getElementById('share-keywords').value, group_text: document.getElementById('share-text').value, protect_content: document.getElementById('share-protect').checked };
    }
    async function saveShare(code) {
        try { await api('/api/shares/' + code, { method: 'PUT', body: JSON.stringify(sharePayload()) }); closeModal(); toast('分享已保存', 'success'); window.App.navigate('shares'); }
        catch (error) { toast(error.message, 'error'); }
    }
    async function forwardShare(code) {
        try {
            const share = await api('/api/shares/' + code);
            modal('转发到绑定频道', `<p>将分享 <strong>${esc(share.title)}</strong> 转发到当前绑定频道。</p><div class="form-group"><label class="form-label">转发前 N 个文件</label><input class="form-input" type="number" id="forward-count" value="2" min="1" max="${share.files_count}"></div>`, `<button class="btn btn-accent" onclick="Tenant.runForward('${code}')">创建任务</button>`);
        } catch (error) { toast(error.message, 'error'); }
    }
    async function runForward(code) {
        try {
            const result = await api(`/api/shares/${code}/forward`, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ forward_count: Number(document.getElementById('forward-count').value) }) });
            closeModal(); toast('转发任务已创建', 'info');
            const job = await pollJob(result.job_id, job => toast(`任务进度：${job.state}`, 'info'));
            toast(job.state === 'completed' ? '转发完成' : `转发失败：${job.last_error}`, job.state === 'completed' ? 'success' : 'error');
        } catch (error) { toast(error.message, 'error'); }
    }
    async function removeShare(code) {
        if (!confirm(`确认删除分享 ${code}？`)) return;
        try { await api('/api/shares/' + code, { method: 'DELETE' }); toast('分享已删除', 'success'); window.App.navigate('shares'); }
        catch (error) { toast(error.message, 'error'); }
    }

    function broadcast(container) {
        container.innerHTML = `<div class="card"><div class="card-header"><h3>群发消息</h3></div><div class="card-body"><textarea class="form-textarea" id="broadcast-message" rows="8" placeholder="输入要发送的消息"></textarea><button class="btn btn-accent" id="broadcast-send" onclick="Tenant.sendBroadcast()">创建广播任务</button><div id="broadcast-progress" class="text-muted"></div></div></div>`;
    }
    async function sendBroadcast() {
        const message = document.getElementById('broadcast-message').value.trim();
        if (!message) return toast('请输入消息', 'error');
        try {
            const result = await api('/api/broadcast', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ message }) });
            document.getElementById('broadcast-send').disabled = true;
            const job = await pollJob(result.job_id, job => {
                const p = job.progress || {};
                document.getElementById('broadcast-progress').textContent = `${job.state} ${p.current || 0}/${p.total || 0}，成功 ${p.successful || 0}，失败 ${p.failed || 0}`;
            });
            toast(job.state === 'completed' ? '广播完成' : `广播失败：${job.last_error}`, job.state === 'completed' ? 'success' : 'error');
        } catch (error) { toast(error.message, 'error'); }
        finally { document.getElementById('broadcast-send').disabled = false; }
    }

    async function banned(container) {
        const data = await api('/api/banned');
        container.innerHTML = `<div class="page-actions"><button class="btn btn-accent" onclick="Tenant.banModal()">封禁用户</button></div><div class="table-card"><table><thead><tr><th>User ID</th><th>原因</th><th>操作</th></tr></thead><tbody>${data.banned_users.map(u => `<tr><td>${u.user_id}</td><td>${esc(u.reason)}</td><td><button class="btn btn-success btn-sm" onclick="Tenant.unban(${u.user_id})">解封</button></td></tr>`).join('')}</tbody></table></div>`;
    }
    function banModal(userId = '') { modal('封禁用户', `<div class="form-group"><label class="form-label">User ID</label><input class="form-input" id="ban-id" type="number" value="${userId}"></div><div class="form-group"><label class="form-label">原因</label><input class="form-input" id="ban-reason"></div>`, '<button class="btn btn-danger" onclick="Tenant.ban()">确认封禁</button>'); }
    async function ban() { try { await api('/api/ban', { method: 'POST', body: JSON.stringify({ user_id: Number(document.getElementById('ban-id').value), reason: document.getElementById('ban-reason').value }) }); closeModal(); toast('已封禁', 'success'); window.App.navigate('banned'); } catch (error) { toast(error.message, 'error'); } }
    async function unban(id) { try { await api('/api/unban', { method: 'POST', body: JSON.stringify({ user_id: id }) }); toast('已解封', 'success'); window.App.navigate('banned'); } catch (error) { toast(error.message, 'error'); } }

    const boolFields = [['is_verify','启用短链验证'],['protect_content','默认禁止转发'],['show_promo','显示推广语'],['disable_channel_button','隐藏频道按钮']];
    const numberFields = [['verify_expire','验证有效期'],['auto_delete_time','自动删除秒数'],['share_code_length','分享码长度'],['rate_limit_max','限流次数'],['rate_limit_window','限流窗口秒数']];
    const textFields = [['promo_text','推广语'],['custom_caption','自定义文件说明'],['custom_buttons','自定义按钮'],['start_message','欢迎消息'],['force_sub_message','强制订阅消息'],['help_text','帮助'],['admin_help_text','管理员帮助'],['keyword_button_text','关键词按钮文字']];
    async function settings(container) {
        const data = await api('/api/settings');
        container.innerHTML = `<div class="settings-grid"><div class="card"><div class="card-header"><h3>行为设置</h3></div><div class="card-body">
            ${boolFields.map(([key,label]) => `<label class="setting-item"><span>${label}</span><input type="checkbox" id="setting-${key}" ${data[key] ? 'checked' : ''}></label>`).join('')}
            ${numberFields.map(([key,label]) => `<div class="form-group"><label class="form-label">${label}</label><input class="form-input" type="number" id="setting-${key}" value="${esc(data[key])}"></div>`).join('')}
            <div class="form-group"><label class="form-label">强制订阅频道（逗号分隔）</label><input class="form-input" id="setting-force_sub_channels" value="${esc((data.force_sub_channels || []).join(','))}"></div>
            <div class="form-group"><label class="form-label">绑定频道（逗号分隔）</label><input class="form-input" id="setting-bound_channels" value="${esc((data.bound_channels || []).join(','))}"></div>
        </div></div><div class="card"><div class="card-header"><h3>文案设置</h3></div><div class="card-body">
            ${textFields.map(([key,label]) => `<div class="form-group"><label class="form-label">${label}</label><textarea class="form-textarea" id="setting-${key}">${esc(data[key] || '')}</textarea></div>`).join('')}
        </div></div></div><div class="page-actions"><button class="btn btn-accent" onclick="Tenant.saveSettings()">保存设置</button></div>`;
    }
    function channelValue(key) { return document.getElementById('setting-' + key).value.split(',').map(v => v.trim()).filter(Boolean).map(Number); }
    async function saveSettings() {
        const payload = {};
        boolFields.forEach(([key]) => payload[key] = document.getElementById('setting-' + key).checked);
        numberFields.forEach(([key]) => payload[key] = Number(document.getElementById('setting-' + key).value));
        textFields.forEach(([key]) => payload[key] = document.getElementById('setting-' + key).value);
        payload.force_sub_channels = channelValue('force_sub_channels'); payload.bound_channels = channelValue('bound_channels');
        try { await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) }); toast('设置已保存，Bot 将自动应用', 'success'); }
        catch (error) { toast(error.message, 'error'); }
    }

    async function health(container) {
        const data = await api('/api/system/health');
        container.innerHTML = `<div class="health-grid">${card('database', data.database, '数据库')}${card('robot', data.bot, 'Bot')}${card('memory', data.web_memory_mb + ' MB', 'Web 内存')}</div>${data.last_error ? `<div class="alert alert-danger">${esc(data.last_error)}</div>` : ''}`;
    }

    window.Tenant = { dashboard, users, usersPage, shares, sharesPage, searchShares, editShare, saveShare, forwardShare, runForward, removeShare, broadcast, sendBroadcast, banned, banModal, ban, unban, settings, saveSettings, health };
})();
