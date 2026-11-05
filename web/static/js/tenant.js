(function () {
  const { api, esc, toast, modal, closeModal, pagination, pollJob } = window.FSB;

  const JELLY_MAP = {
    'users': 'j-users',
    'share-nodes': 'j-sha(function () {
  const { api, esc, toast, openModal, closeModal, navigate, pagination, pollJob } = window.FSB;

  // 所有布尔型可选项开关
  const boolFields = [
    ['is_verify', '启用短链验证', '用户获取资源前需先完成一次短链跳转验证'],
    ['protect_content', '默认禁止转发与另存', '向用户下发消息时开启 Telegram 原生防转发保护'],
    ['show_promo', '显示底部推广语', '在机器人下发的说明末尾追加推广文案'],
    ['disable_channel_button', '隐藏原始频道按钮', '下发文件时不附带默认频道跳转按钮'],
    // 需求 1 可选项：强制订阅频道发送一条任意消息才能解锁
    ['force_sub_require_msg', '需要在强制订阅的频道发送一条任意消息才能解锁（可选项）', '勾选后：用户除了加入强制订阅频道外，还必须在该频道或关联讨论群内发送至少一条任意消息才能解锁；不勾选则只需加入频道即可解锁'],
    // 需求 2 可选项：不绑定强制订阅频道时的付费解锁总开关 + 4 种独立可选付费方式
    ['pay_to_unlock', '不绑定强制订阅频道时启用「需要付费才能解锁」（可选项）', '当上方强制订阅频道列表为空时生效：勾选后用户需通过下方任一已勾选的付费方式完成解锁'],
    ['pay_enable_stars', '方式一：Telegram Stars 官方星币支付（可选项）', '调用 Telegram 原生星币账单（XTR），用户支付后自动秒级解锁'],
    ['pay_enable_usdt', '方式二：USDT 加密货币链上转账核销（可选项）', '向用户展示 USDT 收款网络与地址，支持发送 /tx <交易哈希> 自助核验解锁'],
    ['pay_enable_cashier', '方式三：第三方在线收银台 / 聚合支付链接（可选项）', '跳转至发卡网或微信/支付宝聚合收银台，支持 Webhook 自动入账或购卡兑换'],
    ['pay_enable_cdkey', '方式四：卡密 / 激活码 (CDKey) 兑换解锁（可选项）', '允许用户在对话框直接发送 CDK-XXXX 激活码或 /redeem 命令核销解锁']
  ];

  const numberFields = [
    ['verify_expire', '短链验证有效期 (秒)'],
    ['auto_delete_time', '自动销毁秒数 (0=不销毁)'],
    ['share_code_length', '分享码长度 (6-12)'],
    ['rate_limit_max', '单用户限流次数'],
    ['rate_limit_window', '限流时间窗口 (秒)'],
    ['pay_unlock_expire', '付费解锁有效期 (秒，0=永久有效)'],
    ['pay_amount_stars', 'Telegram Stars 星币解锁价格 (整数，如 50)']
  ];

  const paymentTextFields = [
    ['pay_amount_usdt', 'USDT 解锁价格 (如 5.00)'],
    ['pay_usdt_network', 'USDT 收款网络 (如 TRC20 / BEP20 / Arbitrum)'],
    ['pay_usdt_address', 'USDT 收款钱包地址'],
    ['pay_custom_url', '第三方在线收银台/发卡网 URL (支持 {user_id} 与 {tenant_id} 变量)']
  ];

  const textFields = [
    ['shortlink_url', '短链服务域名 (如 example.com)', 1],
    ['shortlink_api', '短链服务 API Key', 1],
    ['tutorial_video', '验证教程链接 (URL)', 1],
    ['promo_text', '底部推广语', 2],
    ['custom_caption', '自定义文件说明模板', 2],
    ['custom_buttons', '自定义底部按钮 (每行：按钮文字 - https://链接)', 2],
    ['start_message', '欢迎语 (/start)', 3],
    ['force_sub_message', '强制订阅提示语', 3],
    ['force_sub_msg_prompt', '强制订阅附加「发送一条消息解锁」提示语', 2],
    ['pay_unlock_message', '付费解锁提示语', 3],
    ['auto_delete_message', '自动销毁提示语模板 (支持 {time})', 2],
    ['user_reply_text', '非指令私聊默认回复文案', 2],
    ['help_text', '普通用户帮助文案', 3],
    ['admin_help_text', '管理员帮助文案', 3],
    ['about_text', '关于文案 (/about)', 2],
    ['keyword_button_text', '关键词按钮文字', 1]
  ];

  function channelValue(key) {
    const el = document.getElementById('setting-' + key);
    if (!el) return [];
    return el.value
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => Number(s))
      .filter((n) => !Number.isNaN(n) && n !== 0);
  }

  // 1. 运行总览 Dashboard（100% 对齐 web/tenant_api.py 的 api_dashboard 返回结构）
  async function dashboard(container) {
    const data = await api('/api/dashboard');
    const ctxEl = document.getElementById('sidebar-context');
    if (ctxEl && data.tenant) {
      ctxEl.textContent = data.tenant.display_name || data.tenant.slug || '';
    }
    const usersStat = data.users || {};
    const sharesStat = data.shares || {};
    const sys = data.system || {};
    const botRunning = sys.status === 'running' || Boolean(sys.bot);

    container.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">累计总用户 (7日新增: ${esc(String(usersStat.week ?? 0))})</div>
          <div class="stat-value">${esc(String(usersStat.total ?? 0))}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">受控分享总数</div>
          <div class="stat-value">${esc(String(sharesStat.total ?? 0))}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">累计分享访问次数</div>
          <div class="stat-value">${esc(String(sharesStat.share_accessed ?? sharesStat.views ?? 0))}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">累计投递文件数</div>
          <div class="stat-value">${esc(String(sharesStat.files_shared ?? sharesStat.downloads ?? 0))}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3>运行状态监控</h3>
        </div>
        <div class="card-body">
          <div class="health-grid">
            <div class="health-item">
              <div class="health-label">Bot 进程状态</div>
              <div class="health-value">
                <span class="badge ${botRunning ? 'badge-success' : 'badge-neutral'}">
                  ${botRunning ? '正常运行' : '未运行'}
                </span>
              </div>
            </div>
            <div class="health-item">
              <div class="health-label">Bot 用户名</div>
              <div class="health-value code-text">${sys.bot_username ? '@' + esc(sys.bot_username) : '-'}</div>
            </div>
            <div class="health-item">
              <div class="health-label">数据库连接</div>
              <div class="health-value code-text">${esc(sys.database || 'connected')}</div>
            </div>
          </div>
          ${sys.last_error ? `<div class="alert alert-danger margin-top-md">${esc(sys.last_error)}</div>` : ''}
        </div>
      </div>
    `;
  }

  // 2. 用户列表 Users（对齐 /api/users 返回结构并支持查看发言/付费解锁状态与手动授权）
  async function users(container, page = 1) {
    const data = await api(`/api/users?page=${page}`);
    const list = data.users || data.items || [];
    container.innerHTML = `
      <div class="card table-card">
        <div class="card-header">
          <h3>访问用户列表</h3>
          <span class="text-muted">共 ${esc(String(data.total ?? list.length))} 名用户</span>
        </div>
        <div class="card-body" style="padding:0">
          <table class="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Telegram User ID</th>
                <th>频道订阅 / 发言状态</th>
                <th>付费解锁状态</th>
                <th style="text-align:right">操作</th>
              </tr>
            </thead>
            <tbody>
              ${list.map((u, i) => {
                const fsub = u.force_sub_status || {};
                const paid = u.paid_status || {};
                const msgCount = (fsub.msg_sent_channels || []).length;
                return `
                  <tr>
                    <td class="code-text">${(page - 1) * (data.per_page || 20) + i + 1}</td>
                    <td><code class="code-text">${esc(String(u.user_id))}</code></td>
                    <td>
                      <span class="badge ${fsub.verified ? 'badge-success' : 'badge-neutral'}">
                        ${fsub.verified ? '已通过' : '未验证'}
                      </span>
                      ${msgCount > 0 ? `<span class="badge badge-info">已发言 (${msgCount})</span>` : ''}
                    </td>
                    <td>
                      <span class="badge ${paid.is_unlocked ? 'badge-success' : 'badge-neutral'}">
                        ${paid.is_unlocked ? `已解锁 (${esc(paid.method || 'manual')})` : '未解锁'}
                      </span>
                    </td>
                    <td style="text-align:right">
                      <div class="table-actions">
                        <button type="button" class="btn btn-ghost btn-sm" onclick="Tenant.toggleUserUnlock('${esc(String(u.user_id))}', ${!paid.is_unlocked})">
                          ${paid.is_unlocked ? '撤销付费解锁' : '手动授予解锁'}
                        </button>
                        <button type="button" class="btn btn-danger btn-sm" onclick="Tenant.banModal('${esc(String(u.user_id))}')">封禁</button>
                      </div>
                    </td>
                  </tr>
                `;
              }).join('') || '<tr><td colspan="5" class="empty-state">暂无用户数据</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
      ${pagination ? pagination(data.page || page, data.total_pages || 1, 'Tenant.usersPage') : ''}
    `;
  }

  function usersPage(page) {
    return users(document.getElementById('page-content'), page);
  }

  async function toggleUserUnlock(userId, unlock) {
    try {
      await api(`/api/users/${encodeURIComponent(userId)}/unlock`, {
        method: 'POST',
        body: JSON.stringify({ unlocked: Boolean(unlock), method: 'admin_manual' })
      });
      toast(unlock ? `已为用户 ${userId} 授予解锁权限` : `已撤销用户 ${userId} 的解锁权限`, 'success');
      usersPage(1);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  // 3. 分享管理 Shares（100% 对齐 /api/shares 与 /api/shares/{code}）
  async function shares(container, page = 1, search = '') {
    const data = await api(`/api/shares?page=${page}&search=${encodeURIComponent(search)}`);
    const list = data.shares || data.items || [];
    container.innerHTML = `
      <div class="page-actions" style="margin-bottom:14px">
        <div class="search-box">
          <input class="form-input" id="share-search" placeholder="按标题或分享 Key 搜索..." value="${esc(search)}" onkeydown="if(event.key==='Enter')Tenant.searchShares()">
          <button type="button" class="btn btn-ghost" onclick="Tenant.searchShares()">搜索</button>
        </div>
      </div>
      <div class="card table-card">
        <div class="card-header">
          <h3>受控分享列表</h3>
          <span class="text-muted">共 ${esc(String(data.total ?? list.length))} 条记录</span>
        </div>
        <div class="card-body" style="padding:0">
          <table class="data-table">
            <thead>
              <tr>
                <th>标题 / 文件名</th>
                <th>Key</th>
                <th>文件数</th>
                <th>防转发</th>
                <th>访问次数</th>
                <th style="text-align:right">操作</th>
              </tr>
            </thead>
            <tbody>
              ${list.map((s) => {
                const sid = s.code || s.id || s.key;
                return `
                  <tr>
                    <td><strong>${esc(s.title || s.file_name || 'Untitled')}</strong></td>
                    <td><span class="code-text">${esc(sid)}</span></td>
                    <td class="code-text">${esc(String(s.files_count ?? (s.message_ids || []).length ?? 1))}</td>
                    <td>
                      <span class="badge ${s.protect_content ? 'badge-info' : 'badge-neutral'}">
                        ${s.protect_content ? '禁止转发' : '允许'}
                      </span>
                    </td>
                    <td class="code-text">${esc(String(s.access_count ?? s.views ?? 0))}</td>
                    <td style="text-align:right">
                      <div class="table-actions">
                        <button type="button" class="btn btn-ghost btn-sm" onclick="Tenant.editShare('${esc(sid)}')">编辑</button>
                        <button type="button" class="btn btn-ghost btn-sm" onclick="Tenant.forwardShare('${esc(sid)}')">频道群发</button>
                        <button type="button" class="btn btn-danger btn-sm" onclick="Tenant.removeShare('${esc(sid)}')">删除</button>
                      </div>
                    </td>
                  </tr>
                `;
              }).join('') || '<tr><td colspan="6" class="empty-state">暂无受控分享数据</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
      ${pagination ? pagination(data.page || page, data.total_pages || 1, 'Tenant.sharesPage') : ''}
    `;
  }

  function sharesPage(page) {
    const q = document.getElementById('share-search')?.value || '';
    return shares(document.getElementById('page-content'), page, q);
  }

  function searchShares() {
    const q = document.getElementById('share-search')?.value || '';
    return shares(document.getElementById('page-content'), 1, q);
  }

  async function editShare(id) {
    const item = await api(`/api/shares/${encodeURIComponent(id)}`);
    openModal(
      '编辑分享设置',
      `
        <div class="form-group">
          <label class="form-label">标题 / 名称</label>
          <input class="form-input" id="edit-title" value="${esc(item.title || item.file_name || '')}">
        </div>
        <div class="form-group">
          <label class="form-label">触发关键词（逗号分隔）</label>
          <input class="form-input" id="edit-keywords" value="${esc((item.keywords || []).join(','))}">
        </div>
        <div class="form-group">
          <label class="form-label">群发说明文案</label>
          <textarea class="form-textarea" id="edit-grouptext" rows="3">${esc(item.group_text || '')}</textarea>
        </div>
        <label class="setting-item">
          <div class="setting-item-text">
            <span class="setting-item-title">禁止转发与另存</span>
          </div>
          <input type="checkbox" id="edit-protect" ${item.protect_content ? 'checked' : ''}>
        </label>
      `,
      `<button type="button" class="btn btn-ghost" onclick="FSB.closeModal()">取消</button>
       <button type="button" class="btn btn-accent" onclick="Tenant.saveShare('${esc(id)}')">保存</button>`
    );
  }

  async function saveShare(id) {
    const payload = {
      title: document.getElementById('edit-title')?.value?.trim() || '',
      keywords: document.getElementById('edit-keywords')?.value || '',
      group_text: document.getElementById('edit-grouptext')?.value || '',
      protect_content: Boolean(document.getElementById('edit-protect')?.checked)
    };
    try {
      await api(`/api/shares/${encodeURIComponent(id)}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      });
      toast('分享设置已保存', 'success');
      closeModal();
      if (navigate) navigate('shares');
      else sharesPage(1);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function forwardShare(id) {
    openModal(
      '推送分享到绑定频道',
      `
        <label class="setting-item">
          <div class="setting-item-text">
            <span class="setting-item-title">推送全部文件作为预览</span>
            <span class="setting-item-desc">不勾选则按下方预览数量抽取前几条</span>
          </div>
          <input type="checkbox" id="forward-all">
        </label>
        <div class="form-group">
          <label class="form-label">抽取预览文件数量</label>
          <input class="form-input" type="number" id="forward-count" value="2" min="1" max="10">
        </div>
      `,
      `<button type="button" class="btn btn-ghost" onclick="FSB.closeModal()">取消</button>
       <button type="button" class="btn btn-accent" onclick="Tenant.runForward('${esc(id)}')">立即推送</button>`
    );
  }

  async function runForward(id) {
    const forward_all = Boolean(document.getElementById('forward-all')?.checked);
    const forward_count = Number(document.getElementById('forward-count')?.value || 2);
    try {
      await api(`/api/shares/${encodeURIComponent(id)}/forward`, {
        method: 'POST',
        body: JSON.stringify({ forward_all, forward_count })
      });
      toast('已提交频道推送任务', 'success');
      closeModal();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function removeShare(id) {
    openModal(
      '确认删除分享',
      `<p style="color:var(--text-secondary)">确定要删除分享码 <code class="code-text">${esc(id)}</code> 吗？删除后对应链接将立即失效。</p>`,
      `<button type="button" class="btn btn-ghost" onclick="FSB.closeModal()">取消</button>
       <button type="button" class="btn btn-danger" onclick="Tenant.confirmRemoveShare('${esc(id)}')">确认删除</button>`
    );
  }

  async function confirmRemoveShare(id) {
    try {
      await api(`/api/shares/${encodeURIComponent(id)}`, { method: 'DELETE' });
      toast('分享已删除', 'success');
      closeModal();
      if (navigate) navigate('shares');
      else sharesPage(1);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  // 4. 广播消息 Broadcast（对齐 POST /api/broadcast 的 { message } 字段）
  function broadcast(container) {
    container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <h3>全员广播消息</h3>
          <span class="text-muted">由后台异步队列限流发送</span>
        </div>
        <div class="card-body">
          <div class="form-group">
            <label class="form-label">广播内容（支持 HTML 格式）</label>
            <textarea class="form-textarea" id="broadcast-message" rows="5" placeholder="输入广播消息内容..."></textarea>
          </div>
          <button type="button" class="btn btn-accent" onclick="Tenant.sendBroadcast()">提交广播任务</button>
          <div id="broadcast-progress" class="code-text margin-top-md"></div>
        </div>
      </div>
    `;
  }

  async function sendBroadcast() {
    const message = document.getElementById('broadcast-message')?.value?.trim();
    if (!message) return toast('请输入广播内容', 'error');
    try {
      const res = await api('/api/broadcast', {
        method: 'POST',
        body: JSON.stringify({ message })
      });
      toast('广播任务已提交至后台队列', 'success');
      document.getElementById('broadcast-message').value = '';
      if (res.job_id && pollJob) {
        pollJob(res.job_id, document.getElementById('broadcast-progress'));
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  // 5. 封禁名单 Banned（100% 对齐 GET /api/banned、POST /api/ban、POST /api/unban）
  async function banned(container, page = 1) {
    const data = await api(`/api/banned?page=${page}`);
    const list = data.banned_users || data.items || data.users || [];
    container.innerHTML = `
      <div class="page-actions" style="margin-bottom:14px">
        <button type="button" class="btn btn-accent" onclick="Tenant.banModal()">新增封禁用户</button>
      </div>
      <div class="card table-card">
        <div class="card-header">
          <h3>已封禁用户列表</h3>
          <span class="text-muted">共 ${esc(String(data.total ?? list.length))} 人</span>
        </div>
        <div class="card-body" style="padding:0">
          <table class="data-table">
            <thead>
              <tr>
                <th>User ID</th>
                <th>封禁原因</th>
                <th>封禁时间</th>
                <th style="text-align:right">操作</th>
              </tr>
            </thead>
            <tbody>
              ${list.map((u) => `
                <tr>
                  <td><code class="code-text">${esc(String(u.user_id))}</code></td>
                  <td>${esc(u.reason || '无')}</td>
                  <td class="text-muted">${u.banned_at ? new Date(Number(u.banned_at) * 1000).toLocaleString() : '-'}</td>
                  <td style="text-align:right">
                    <button type="button" class="btn btn-ghost btn-sm" onclick="Tenant.unban('${esc(String(u.user_id))}')">解封</button>
                  </td>
                </tr>
              `).join('') || '<tr><td colspan="4" class="empty-state">暂无封禁用户</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  function banModal(userId = '') {
    openModal(
      '新增封禁用户',
      `
        <div class="form-group">
          <label class="form-label">Telegram User ID</label>
          <input class="form-input" id="ban-userid" value="${esc(String(userId))}" placeholder="例如: 123456789">
        </div>
        <div class="form-group">
          <label class="form-label">封禁原因</label>
          <input class="form-input" id="ban-reason" placeholder="违规访问或恶意刷请求">
        </div>
      `,
      `<button type="button" class="btn btn-ghost" onclick="FSB.closeModal()">取消</button>
       <button type="button" class="btn btn-danger" onclick="Tenant.ban()">确认封禁</button>`
    );
  }

  async function ban() {
    const userId = document.getElementById('ban-userid')?.value?.trim();
    const reason = document.getElementById('ban-reason')?.value?.trim();
    if (!userId) return toast('请输入 User ID', 'error');
    try {
      await api('/api/ban', {
        method: 'POST',
        body: JSON.stringify({ user_id: Number(userId), reason })
      });
      toast('用户已封禁', 'success');
      closeModal();
      if (navigate) navigate('banned');
      else banned(document.getElementById('page-content'));
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function unban(userId) {
    try {
      await api('/api/unban', {
        method: 'POST',
        body: JSON.stringify({ user_id: Number(userId) })
      });
      toast('用户已解封', 'success');
      if (navigate) navigate('banned');
      else banned(document.getElementById('page-content'));
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  // 6. 参数设置 Settings（支持「发送一条消息解锁」可选项 & 「多种付费方式各自独立可选」联动）
  function syncAccessGateState() {
    const fsubInput = document.getElementById('setting-force_sub_channels');
    const hasChannels = Boolean(fsubInput && fsubInput.value.trim().length > 0);
    const requireMsgChecked = Boolean(document.getElementById('setting-force_sub_require_msg')?.checked);
    const payUnlockChecked = Boolean(document.getElementById('setting-pay_to_unlock')?.checked);

    const fsubBadge = document.getElementById('gate-mode-fsub-badge');
    const payBadge = document.getElementById('gate-mode-pay-badge');

    if (fsubBadge) {
      fsubBadge.className = hasChannels ? 'badge badge-success' : 'badge badge-neutral';
      fsubBadge.textContent = hasChannels
        ? (requireMsgChecked ? '当前生效：强制订阅 + 需发一条消息解锁' : '当前生效：仅强制订阅即可解锁')
        : '未绑定强制订阅频道';
    }
    if (payBadge) {
      payBadge.className = (!hasChannels && payUnlockChecked) ? 'badge badge-info' : 'badge badge-neutral';
      payBadge.textContent = !hasChannels
        ? (payUnlockChecked ? '当前生效：已启用多方式付费解锁' : '可选：未启用付费解锁')
        : '已绑定强制订阅频道（付费门禁自动挂起）';
    }

    // 动态展开/隐藏「发送一条消息解锁」的提示语配置框
    const msgPromptBox = document.getElementById('fsub-msg-prompt-wrapper');
    if (msgPromptBox) {
      msgPromptBox.classList.toggle('hidden', !requireMsgChecked);
    }

    // 动态展开/隐藏「付费解锁」子选项面板及 4 种可选付费方式各自的参数面板
    const payMethodsPanel = document.getElementById('pay-methods-panel');
    if (payMethodsPanel) {
      payMethodsPanel.classList.toggle('hidden', !payUnlockChecked);
    }

    const toggleSubBox = (checkboxId, boxId) => {
      const checked = Boolean(document.getElementById(checkboxId)?.checked);
      const box = document.getElementById(boxId);
      if (box) box.classList.toggle('hidden', !checked);
    };

    toggleSubBox('setting-pay_enable_stars', 'pay-box-stars');
    toggleSubBox('setting-pay_enable_usdt', 'pay-box-usdt');
    toggleSubBox('setting-pay_enable_cashier', 'pay-box-cashier');
    toggleSubBox('setting-pay_enable_cdkey', 'pay-box-cdkey');
  }

  async function settings(container) {
    const data = await api('/api/settings');
    const fsubChannelsStr = (data.force_sub_channels || []).join(',');
    const boundChannelsStr = (data.bound_channels || []).join(',');

    const renderToggle = (key, onChangeSync = false) => {
      const found = boolFields.find((f) => f[0] === key);
      if (!found) return '';
      const [, label, desc] = found;
      const changeAttr = onChangeSync ? 'onchange="Tenant.syncAccessGateState()"' : '';
      return `
        <label class="setting-item">
          <div class="setting-item-text">
            <span class="setting-item-title">${esc(label)}</span>
            ${desc ? `<span class="setting-item-desc">${esc(desc)}</span>` : ''}
          </div>
          <input type="checkbox" id="setting-${key}" ${data[key] ? 'checked' : ''} ${changeAttr}>
        </label>
      `;
    };

    const renderNumber = (key) => {
      const found = numberFields.find((f) => f[0] === key);
      if (!found) return '';
      return `
        <div class="form-group">
          <label class="form-label">${esc(found[1])}</label>
          <input type="number" id="setting-${key}" class="form-input" value="${data[key] ?? 0}">
        </div>
      `;
    };

    const renderPaymentText = (key) => {
      const found = paymentTextFields.find((f) => f[0] === key);
      if (!found) return '';
      return `
        <div class="form-group">
          <label class="form-label">${esc(found[1])}</label>
          <input class="form-input" id="setting-${key}" value="${esc(data[key] || '')}">
        </div>
      `;
    };

    container.innerHTML = `
      <div class="settings-grid">
        <!-- 模块 1：强制订阅频道与可选「发送一条任意消息解锁」 -->
        <div class="card">
          <div class="card-header">
            <h3>门禁一：强制订阅频道与发言解锁</h3>
            <span id="gate-mode-fsub-badge" class="badge badge-neutral">检测中</span>
          </div>
          <div class="card-body">
            <div class="form-group">
              <label class="form-label">强制订阅频道 / 群组 ID（多个以英文逗号分隔，如 -100123456789；留空代表不绑定）</label>
              <input class="form-input code-text" id="setting-force_sub_channels"
                     value="${esc(fsubChannelsStr)}"
                     placeholder="留空则不绑定强制订阅频道"
                     oninput="Tenant.syncAccessGateState()">
            </div>

            ${renderToggle('force_sub_require_msg', true)}

            <div id="fsub-msg-prompt-wrapper" class="${data.force_sub_require_msg ? '' : 'hidden'}" style="padding:10px 12px;border:1px solid var(--border-color);border-radius:var(--radius-md);margin-bottom:12px">
              <div class="form-group" style="margin-bottom:0">
                <label class="form-label">强制订阅附加「频道发言解锁」提示语</label>
                <textarea id="setting-force_sub_msg_prompt" class="form-input" rows="2">${esc(data.force_sub_msg_prompt || '')}</textarea>
              </div>
            </div>

            <div class="form-group" style="margin-top:12px">
              <label class="form-label">绑定下发频道 ID（多个以英文逗号分隔）</label>
              <input class="form-input code-text" id="setting-bound_channels" value="${esc(boundChannelsStr)}">
            </div>
          </div>
        </div>

        <!-- 模块 2：不绑定强制订阅频道时的可选「付费解锁」与 4 种可选付费方式 -->
        <div class="card">
          <div class="card-header">
            <h3>门禁二：不绑定强制订阅时的付费解锁（可选项）</h3>
            <span id="gate-mode-pay-badge" class="badge badge-neutral">检测中</span>
          </div>
          <div class="card-body">
            ${renderToggle('pay_to_unlock', true)}

            <div id="pay-methods-panel" class="${data.pay_to_unlock ? '' : 'hidden'}">
              ${renderNumber('pay_unlock_expire')}

              <!-- 可选方式 1：Telegram Stars -->
              ${renderToggle('pay_enable_stars', true)}
              <div id="pay-box-stars" class="${data.pay_enable_stars ? '' : 'hidden'}" style="padding:10px 12px;border:1px solid var(--border-color);border-radius:var(--radius-md);margin-bottom:10px">
                ${renderNumber('pay_amount_stars')}
              </div>

              <!-- 可选方式 2：USDT 转账 -->
              ${renderToggle('pay_enable_usdt', true)}
              <div id="pay-box-usdt" class="${data.pay_enable_usdt ? '' : 'hidden'}" style="padding:10px 12px;border:1px solid var(--border-color);border-radius:var(--radius-md);margin-bottom:10px">
                <div class="form-row-2">
                  ${renderPaymentText('pay_amount_usdt')}
                  ${renderPaymentText('pay_usdt_network')}
                </div>
                ${renderPaymentText('pay_usdt_address')}
              </div>

              <!-- 可选方式 3：第三方在线收银台 -->
              ${renderToggle('pay_enable_cashier', true)}
              <div id="pay-box-cashier" class="${data.pay_enable_cashier ? '' : 'hidden'}" style="padding:10px 12px;border:1px solid var(--border-color);border-radius:var(--radius-md);margin-bottom:10px">
                ${renderPaymentText('pay_custom_url')}
              </div>

              <!-- 可选方式 4：卡密 / 激活码 (CDKey) -->
              ${renderToggle('pay_enable_cdkey', true)}
              <div id="pay-box-cdkey" class="${data.pay_enable_cdkey !== false ? '' : 'hidden'}" style="padding:10px 12px;border:1px solid var(--border-color);border-radius:var(--radius-md);margin-bottom:10px">
                <div class="cdkey-toolbar" style="border-top:none;padding-top:0">
                  <div class="cdkey-toolbar-left">
                    <input type="number" id="cdkey-gen-count" class="form-input" style="width:100px" value="10" min="1" max="200">
                    <button type="button" class="btn btn-ghost" onclick="Tenant.generateCdkeys()">批量生成激活码</button>
                    <button type="button" class="btn btn-ghost" onclick="Tenant.loadCdkeys()">查看可用卡密</button>
                  </div>
                </div>
                <div id="cdkey-output-box" class="hidden" style="margin-top:10px"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- 模块 3：短链验证、防转发与限流参数 -->
        <div class="card">
          <div class="card-header">
            <h3>安全验证、防转发与访问限流</h3>
          </div>
          <div class="card-body">
            ${renderToggle('is_verify')}
            ${renderToggle('protect_content')}
            ${renderToggle('show_promo')}
            ${renderToggle('disable_channel_button')}
            <div class="form-row-2" style="margin-top:12px">
              ${renderNumber('verify_expire')}
              ${renderNumber('auto_delete_time')}
              ${renderNumber('share_code_length')}
              ${renderNumber('rate_limit_max')}
              ${renderNumber('rate_limit_window')}
            </div>
          </div>
        </div>

        <!-- 模块 4：消息文案与模板设置 -->
        <div class="card">
          <div class="card-header">
            <h3>消息文案与模板设置</h3>
          </div>
          <div class="card-body">
            ${textFields.filter(([k]) => k !== 'force_sub_msg_prompt').map(([key, label, rows]) => `
              <div class="form-group">
                <label class="form-label">${esc(label)}</label>
                <textarea id="setting-${key}" class="form-input" rows="${rows || 2}">${esc(data[key] || '')}</textarea>
              </div>
            `).join('')}
          </div>
        </div>
      </div>

      <div class="page-actions" style="margin-top:20px">
        <button type="button" class="btn btn-accent" onclick="Tenant.saveSettings()">保存配置</button>
      </div>
    `;

    syncAccessGateState();
  }

  async function saveSettings() {
    const payload = {};
    boolFields.forEach(([key]) => {
      const el = document.getElementById('setting-' + key);
      if (el) payload[key] = el.checked;
    });
    numberFields.forEach(([key]) => {
      const el = document.getElementById('setting-' + key);
      if (el) payload[key] = Number(el.value);
    });
    paymentTextFields.forEach(([key]) => {
      const el = document.getElementById('setting-' + key);
      if (el) payload[key] = el.value.trim();
    });
    textFields.forEach(([key]) => {
      const el = document.getElementById('setting-' + key);
      if (el) payload[key] = el.value;
    });
    payload.force_sub_channels = channelValue('force_sub_channels');
    payload.bound_channels = channelValue('bound_channels');

    try {
      await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify(payload)
      });
      toast('设置已保存，Worker 将自动同步生效', 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function generateCdkeys() {
    const count = Number(document.getElementById('cdkey-gen-count')?.value || 10);
    const box = document.getElementById('cdkey-output-box');
    try {
      const res = await api('/api/cdkeys', {
        method: 'POST',
        body: JSON.stringify({ count })
      });
      const codes = res.codes || [];
      if (box) {
        box.classList.remove('hidden');
        box.innerHTML = `
          <label class="form-label">新生成激活码（共 ${codes.length} 个，可直接复制）</label>
          <textarea class="form-input code-text" rows="5" readonly>${esc(codes.join('\n'))}</textarea>
        `;
      }
      toast(`已生成 ${codes.length} 个激活码`, 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function loadCdkeys() {
    const box = document.getElementById('cdkey-output-box');
    try {
      const res = await api('/api/cdkeys');
      const items = res.items || [];
      if (box) {
        box.classList.remove('hidden');
        const unused = items.filter((i) => !i.used_by).map((i) => i.code);
        box.innerHTML = `
          <label class="form-label">当前未核销激活码（可用 ${unused.length} 个 / 总计 ${items.length} 个）</label>
          <textarea class="form-input code-text" rows="5" readonly>${esc(unused.join('\n') || '暂无未使用的激活码')}</textarea>
        `;
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  // 7. 健康诊断 Health（100% 对齐 GET /api/system/health）
  async function health(container) {
    const data = await api('/api/system/health');
    container.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">MongoDB 状态</div>
          <div class="stat-value">${esc(String(data.database || '-'))}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">租户 Bot 状态</div>
          <div class="stat-value">${esc(String(data.bot || '-'))}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">控制面板内存占用</div>
          <div class="stat-value code-text">${esc(String(data.web_memory_mb ?? 0))} MB</div>
        </div>
      </div>
      ${data.last_error ? `<div class="alert alert-danger">${esc(data.last_error)}</div>` : ''}
    `;
  }

  window.Tenant = {
    dashboard,
    users,
    usersPage,
    toggleUserUnlock,
    shares,
    sharesPage,
    searchShares,
    editShare,
    saveShare,
    forwardShare,
    runForward,
    removeShare,
    confirmRemoveShare,
    broadcast,
    sendBroadcast,
    banned,
    banModal,
    ban,
    unban,
    settings,
    saveSettings,
    syncAccessGateState,
    generateCdkeys,
    loadCdkeys,
    health
  };
})();re',
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
