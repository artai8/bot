(function () {
  const { api, esc, toast, modal, closeModal, pagination, pollJob } = window.FSB;

  const JELLY_MAP = {
    'users': 'j-users',
    'share-nodes': 'j-share',
    'eye': 'j-eye',
    'download': 'j-download',
    'database': 'j-building',
    'robot': 'j-bolt',
    'memory': 'j-gear'
  };

  function card(icon, value, label) {
    const svgId = JELLY_MAP[icon] || 'j-bolt';
    return `<div class="stat-card">
      <svg class="jelly-svg md"><use href="#${svgId}"/></svg>
      <div class="stat-content">
        <div class="stat-value">${esc(value)}</div>
        <div class="stat-label">${esc(label)}</div>
      </div>
    </div>`;
  }

  async function dashboard(container) {
    const data = await api('/api/dashboard');
    document.getElementById('sidebar-context').textContent = data.tenant.display_name;
    container.innerHTML = `<div class="stats-grid">
      ${card('users', data.users.total, '总用户')}
      ${card('share-nodes', data.shares.total, '受控分享')}
      ${card('eye', data.shares.views, '总浏览')}
      ${card('download', data.shares.downloads, '总下载')}
    </div>
    <div class="card">
      <div class="card-header"><h3>运行状态监控</h3></div>
      <div class="card-body">
        <div class="health-grid">
          <div class="health-item"><div class="health-label">Bot 进程</div><div class="health-value">${data.system.bot ? '🟢 正常运行' : '🔴 异常'}</div></div>
          <div class="health-item"><div class="health-label">Bot 用户名</div><div class="health-value">${esc(data.system.bot_username || '-')}</div></div>
          <div class="health-item"><div class="health-label">存储频道</div><div class="health-value">${esc(data.system.db_channel_status || '-')}</div></div>
        </div>
        ${data.system.last_error ? `<div class="alert alert-danger">${esc(data.system.last_error)}</div>` : ''}
      </div>
    </div>`;
  }

  async function users(container, page = 1) {
    const data = await api(`/api/users?page=${page}`);
    const list = data.items || data.users || [];
    container.innerHTML = `<div class="table-card"><table><thead><tr><th>#</th><th>Telegram User ID</th><th>操作</th></tr></thead><tbody>${list.map((u, i) => `<tr><td>${(page - 1) * 10 + i + 1}</td><td><code>${esc(u.user_id)}</code></td><td><button type="button" class="btn btn-danger btn-sm" onclick="Tenant.banModal('${esc(u.user_id)}')">封禁</button></td></tr>`).join('') || '<tr><td colspan="3" class="empty-state">暂无用户</td></tr>'}</tbody></table></div>${pagination(data.page, data.total_pages, 'Tenant.usersPage')}`;
  }
  function usersPage(page) { window.App.loadPage('users', page); }

  async function shares(container, page = 1, search = '') {
    const data = await api(`/api/shares?page=${page}&search=${encodeURIComponent(search)}`);
    const list = data.items || data.shares || [];
    container.innerHTML = `<div class="page-actions"><div class="search-box"><input class="form-input" id="share-search" placeholder="搜索文件名或 Key" value="${esc(search)}"><button type="button" class="btn btn-ghost" onclick="Tenant.searchShares()"><i class="fas fa-search"></i></button></div></div>
      <div class="table-card"><table><thead><tr><th>文件名</th><th>Key</th><th>访问密码</th><th>类型</th><th>下载数</th><th>浏览数</th><th>操作</th></tr></thead><tbody>${list.map(s => {
        const sid = s.id || s.code || s.key;
        return `<tr>
          <td><strong>${esc(s.file_name || s.title || '-')}</strong></td>
          <td><span class="code-text">${esc(s.key || s.code)}</span></td>
          <td>${esc(s.password || '-')}</td>
          <td>${esc(s.media_type || s.file_type || '-')}</td>
          <td>${esc(s.download_count ?? s.downloads ?? 0)}</td>
          <td>${esc(s.view_count ?? s.views ?? 0)}</td>
          <td><div class="table-actions">
            <button type="button" class="btn btn-ghost btn-sm" onclick="Tenant.editShare('${esc(sid)}')">编辑</button>
            <button type="button" class="btn btn-ghost btn-sm" onclick="Tenant.forwardShare('${esc(sid)}')">补发文件</button>
            <button type="button" class="btn btn-danger btn-sm" onclick="Tenant.removeShare('${esc(sid)}')">删除</button>
          </div></td>
        </tr>`;
      }).join('') || '<tr><td colspan="7" class="empty-state">暂无分享</td></tr>'}</tbody></table></div>${pagination(data.page, data.total_pages, 'Tenant.sharesPage')}`;
  }
  function sharesPage(page) { window.App.loadPage('shares', page, document.getElementById('share-search')?.value || ''); }
  function searchShares() { window.App.loadPage('shares', 1, document.getElementById('share-search')?.value || ''); }

  async function editShare(id) {
    try {
      const share = await api('/api/shares/' + encodeURIComponent(id));
      modal('编辑分享', `<div class="form-group"><label class="form-label">Key</label><input class="form-input" value="${esc(share.key || share.code || id)}" disabled></div>
        <div class="form-group"><label class="form-label">文件名</label><input class="form-input" id="share-filename" value="${esc(share.file_name || share.title || '')}"></div>
        <div class="form-group"><label class="form-label">访问密码</label><input class="form-input" id="share-password" value="${esc(share.password || '')}"></div>`, `<button type="button" class="btn btn-ghost" onclick="FSB.closeModal()">取消</button><button type="button" class="btn btn-accent" onclick="Tenant.saveShare('${esc(id)}')">保存</button>`);
    } catch (error) { toast(error.message, 'error'); }
  }

  async function saveShare(id) {
    try {
      await api('/api/shares/' + encodeURIComponent(id), {
        method: 'PUT',
        body: JSON.stringify({
          file_name: document.getElementById('share-filename').value.trim(),
          password: document.getElementById('share-password').value.trim()
        })
      });
      closeModal(); toast('分享已更新', 'success'); window.App.navigate('shares');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function forwardShare(id) {
    modal('补发文件', `<div class="form-group"><label class="form-label">接收者 Telegram User ID</label><input class="form-input" type="number" id="forward-target"></div>`, `<button type="button" class="btn btn-ghost" onclick="FSB.closeModal()">取消</button><button type="button" class="btn btn-accent" onclick="Tenant.runForward('${esc(id)}')">立即发送</button>`);
  }

  async function runForward(id) {
    const target = Number(document.getElementById('forward-target').value);
    if (!target) return toast('请输入目标 User ID', 'error');
    try {
      await api(`/api/shares/${encodeURIComponent(id)}/forward`, { method: 'POST', body: JSON.stringify({ target_user_id: target }) });
      closeModal(); toast('补发任务已提交', 'success');
    } catch (error) { toast(error.message, 'error'); }
  }

  function removeShare(id) {
    modal(
      '确认删除分享',
      `<p>确定删除分享 <span class="code-text">${esc(id)}</span> 吗？删除后用户无法继续通过该 Key 提取文件。</p>`,
      `<button type="button" class="btn btn-ghost" onclick="FSB.closeModal()">取消</button><button type="button" class="btn btn-danger" onclick="Tenant.confirmRemoveShare('${esc(id)}')">确认删除</button>`
    );
  }

  async function confirmRemoveShare(id) {
    try {
      await api('/api/shares/' + encodeURIComponent(id), { method: 'DELETE' });
      closeModal(); toast('分享已删除', 'success'); window.App.navigate('shares');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function broadcast(container) {
    container.innerHTML = `<div class="card"><div class="card-header"><h3>新增全员广播</h3></div><div class="card-body">
      <div class="form-group"><label class="form-label">消息内容（支持 HTML 标签排版）</label><textarea class="form-textarea" id="broadcast-message" rows="6" placeholder="<b>📢 频道更新通知</b>"></textarea></div>
      <button type="button" class="btn btn-accent" id="broadcast-send" onclick="Tenant.sendBroadcast()">🚀 发送广播</button>
      <div id="broadcast-progress" class="code-text margin-top-md">准备就绪</div>
    </div></div>`;
  }

  async function sendBroadcast() {
    const message = document.getElementById('broadcast-message').value.trim();
    if (!message) return toast('请输入消息内容', 'error');
    try {
      const result = await api('/api/broadcast', { method: 'POST', headers: { 'Idempotency-Key': String(Date.now()) }, body: JSON.stringify({ message }) });
      document.getElementById('broadcast-send').disabled = true;
      const job = await pollJob(result.job_id, jobState => {
        const p = jobState.progress || {};
        document.getElementById('broadcast-progress').textContent = `${jobState.state} ${p.current || 0}/${p.total || 0}, 成功 ${p.successful || 0}, 失败 ${p.failed || 0}`;
      });
      toast(job.state === 'completed' ? '广播发送完成' : `广播失败: ${job.last_error}`, job.state === 'completed' ? 'success' : 'error');
    } catch (error) { toast(error.message, 'error'); }
    finally {
      const btn = document.getElementById('broadcast-send');
      if (btn) btn.disabled = false;
    }
  }

  async function banned(container) {
    const data = await api('/api/banned');
    const list = data.banned_users || data.items || [];
    container.innerHTML = `<div class="page-actions"><button type="button" class="btn btn-accent" onclick="Tenant.banModal()"><i class="fas fa-user-slash"></i> 封禁用户</button></div><div class="table-card"><table><thead><tr><th>User ID</th><th>封禁原因</th><th>操作</th></tr></thead><tbody>${list.map(u => `<tr><td><code>${esc(u.user_id)}</code></td><td>${esc(u.reason)}</td><td><button type="button" class="btn btn-success btn-sm" onclick="Tenant.unban(${Number(u.user_id)})">解封</button></td></tr>`).join('') || '<tr><td colspan="3" class="empty-state">当前无封禁用户</td></tr>'}</tbody></table></div>`;
  }

  function banModal(userId = '') {
    modal('封禁用户', `<div class="form-group"><label class="form-label">Telegram User ID</label><input class="form-input" id="ban-id" type="number" value="${esc(userId)}"></div><div class="form-group"><label class="form-label">封禁原因</label><input class="form-input" id="ban-reason" placeholder="例如：恶意刷取资源"></div>`, '<button type="button" class="btn btn-ghost" onclick="FSB.closeModal()">取消</button><button type="button" class="btn btn-danger" onclick="Tenant.ban()">确认封禁</button>');
  }

  async function ban() {
    try {
      await api('/api/ban', { method: 'POST', body: JSON.stringify({ user_id: Number(document.getElementById('ban-id').value), reason: document.getElementById('ban-reason').value }) });
      closeModal(); toast('用户已封禁', 'success'); window.App.navigate('banned');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function unban(id) {
    try {
      await api('/api/unban', { method: 'POST', body: JSON.stringify({ user_id: id }) });
      toast('用户已解封', 'success'); window.App.navigate('banned');
    } catch (error) { toast(error.message, 'error'); }
  }

  const boolFields = [['is_verify', '启用短链验证'], ['protect_content', '默认禁止转发/另存'], ['show_promo', '显示底部推广语'], ['disable_channel_button', '隐藏原始频道按钮']];
  const numberFields = [['verify_expire', '验证有效期 (秒)'], ['auto_delete_time', '自动销毁秒数 (0=不销毁)'], ['share_code_length', '分享码长度'], ['rate_limit_max', '单用户限流次数'], ['rate_limit_window', '限流时间窗口 (秒)']];
  const textFields = [['promo_text', '底部推广语'], ['custom_caption', '自定义文件说明模板'], ['custom_buttons', '自定义底部按钮'], ['start_message', '欢迎语 (/start)'], ['force_sub_message', '强制订阅提示语'], ['help_text', '普通用户帮助文案'], ['admin_help_text', '管理员帮助文案'], ['keyword_button_text', '关键词按钮文字']];

  async function settings(container) {
    const data = await api('/api/settings');
    container.innerHTML = `<div class="settings-grid"><div class="card"><div class="card-header"><h3>⚙️ 行为与限流设置</h3></div><div class="card-body">
      ${boolFields.map(([key, label]) => `<label class="setting-item"><span>${esc(label)}</span><input type="checkbox" id="setting-${key}" ${data[key] ? 'checked' : ''}></label>`).join('')}
      ${numberFields.map(([key, label]) => `<div class="form-group"><label class="form-label">${esc(label)}</label><input class="form-input" type="number" id="setting-${key}" value="${esc(data[key] ?? 0)}"></div>`).join('')}
      <div class="form-group"><label class="form-label">强制订阅频道 ID（逗号分隔）</label><input class="form-input" id="setting-force_sub_channels" value="${esc((data.force_sub_channels || []).join(','))}"></div>
      <div class="form-group"><label class="form-label">绑定下发频道 ID（逗号分隔）</label><input class="form-input" id="setting-bound_channels" value="${esc((data.bound_channels || []).join(','))}"></div>
    </div></div><div class="card"><div class="card-header"><h3>📝 消息文案与模板设置</h3></div><div class="card-body">
      ${textFields.map(([key, label]) => `<div class="form-group"><label class="form-label">${esc(label)}</label><textarea class="form-textarea" id="setting-${key}">${esc(data[key] || '')}</textarea></div>`).join('')}
    </div></div></div><div class="page-actions"><button type="button" class="btn btn-accent" onclick="Tenant.saveSettings()">💾 保存全部设置</button></div>`;
  }

  function channelValue(key) {
    return document.getElementById('setting-' + key).value.split(',').map(v => v.trim()).filter(Boolean).map(Number);
  }

  async function saveSettings() {
    const payload = {};
    boolFields.forEach(([key]) => { payload[key] = document.getElementById('setting-' + key).checked; });
    numberFields.forEach(([key]) => { payload[key] = Number(document.getElementById('setting-' + key).value); });
    textFields.forEach(([key]) => { payload[key] = document.getElementById('setting-' + key).value; });
    payload.force_sub_channels = channelValue('force_sub_channels');
    payload.bound_channels = channelValue('bound_channels');
    try {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
      toast('设置已保存，Worker 将自动热更新应用', 'success');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function health(container) {
    const data = await api('/api/system/health');
    container.innerHTML = `<div class="stats-grid">
      ${card('database', data.database, 'MongoDB 状态')}
      ${card('robot', data.bot, '租户 Bot 状态')}
      ${card('memory', data.web_memory_mb + ' MB', '控制面内存占用')}
    </div>${data.last_error ? `<div class="alert alert-danger">${esc(data.last_error)}</div>` : ''}`;
  }

  window.Tenant = {
    dashboard, users, usersPage, shares, sharesPage, searchShares,
    editShare, saveShare, forwardShare, runForward, removeShare, confirmRemoveShare,
    broadcast, sendBroadcast, banned, banModal, ban, unban, settings, saveSettings, health
  };
})();
