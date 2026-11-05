(function () {
  const { api, esc, toast, modal, closeModal, pagination, pollJob } = window.FSB;

  const boolFields = [
    ['is_verify', '启用短链验证', '用户获取资源前需先完成一次短链跳转验证'],
    ['protect_content', '默认禁止转发与另存', '向用户下发消息时开启 Telegram 原生防转发保护'],
    ['disable_channel_button', '隐藏消息底部频道按钮', '下发文件时不附带默认频道跳转按钮'],
    ['show_promo', '显示底部推广语', '在机器人下发的说明末尾追加推广文案'],
    ['force_sub_require_msg', '需在强制订阅频道发送一条任意消息才可解锁', '绑定强制订阅频道后生效：用户除了加入频道/关联群组外，还需在其中发送至少一条消息'],
    ['pay_to_unlock', '不绑定强制订阅频道时启用「付费解锁」', '当上方强制订阅频道列表为空时生效：用户需通过任一付费方式解锁后才可获取资源'],
    ['pay_enable_cdkey', '启用卡密 / 激活码 (CDKey) 兑换解锁', '允许用户在机器人对话框直接发送 CDK-XXXX 激活码或 /redeem 命令核销解锁']
  ];

  const numberFields = [
    ['verify_expire', '短链验证有效期（秒）'],
    ['auto_delete_time', '自动销毁秒数（0 = 不销毁）'],
    ['share_code_length', '分享码长度（6 - 12）'],
    ['rate_limit_max', '限流窗口最大请求次数'],
    ['rate_limit_window', '限流时间窗口（秒）'],
    ['pay_unlock_expire', '付费解锁有效期（秒，0 = 永久有效）'],
    ['pay_amount_stars', 'Telegram Stars 星币解锁价格（0 = 关闭星币支付）']
  ];

  const paymentTextFields = [
    ['pay_amount_usdt', 'USDT 解锁价格（如 5.00，留空则关闭 USDT 支付）'],
    ['pay_usdt_network', 'USDT 收款网络（如 TRC20 / BEP20 / Arbitrum）'],
    ['pay_usdt_address', 'USDT 收款钱包地址'],
    ['pay_custom_url', '第三方在线收银台/发卡网链接（支持 {user_id} 与 {tenant_id} 变量）']
  ];

  const textFields = [
    ['shortlink_url', '短链服务域名（如 example.com）', 1],
    ['shortlink_api', '短链服务 API Key', 1],
    ['tutorial_video', '验证教程链接（URL）', 1],
    ['promo_text', '底部推广语文案（支持 HTML）', 2],
    ['custom_caption', '自定义文件说明模板', 2],
    ['custom_buttons', '自定义底部按钮（每行格式：按钮文字 - https://链接 | 按钮2 - https://链接）', 2],
    ['start_message', '/start 欢迎词模板（支持 {first} {mention} {id}）', 3],
    ['force_sub_message', '强制订阅拦截提示语模板', 3],
    ['force_sub_msg_prompt', '强制订阅附加「频道发言解锁」提示语', 2],
    ['pay_unlock_message', '付费解锁拦截提示语模板', 3],
    ['auto_delete_message', '自动销毁提示语模板（支持 {time}）', 2],
    ['user_reply_text', '非指令私聊默认回复文案', 2],
    ['help_text', '普通用户 /help 帮助文案', 3],
    ['admin_help_text', '管理员 /help 帮助文案', 3],
    ['about_text', '/about 关于文案', 2],
    ['keyword_button_text', '资源获取按钮文案', 1]
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

  async function dashboard(container) {
    const data = await api('/api/dashboard');
    const stats = data.stats || {};
    const bot = data.bot || {};
    container.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-content">
            <div class="stat-label">累计注册用户</div>
            <div class="stat-value">${esc(String(stats.users ?? 0))}</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-content">
            <div class="stat-label">有效分享资源</div>
            <div class="stat-value">${esc(String(stats.shares ?? 0))}</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-content">
            <div class="stat-label">封禁用户名单</div>
            <div class="stat-value">${esc(String(stats.banned ?? 0))}</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-content">
            <div class="stat-label">实例在线状态</div>
            <div class="stat-value">${bot.online ? '运行中' : '离线'}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3>实例运行摘要</h3>
        </div>
        <div class="card-body">
          <div class="health-grid">
            <div class="health-item">
              <span class="health-label">Bot 用户名</span>
              <span class="health-value code-text">${bot.username ? '@' + esc(bot.username) : '未连接'}</span>
            </div>
            <div class="health-item">
              <span class="health-label">分配 Worker 节点</span>
              <span class="health-value code-text">${esc(bot.worker_id || '待分配')}</span>
            </div>
            <div class="health-item">
              <span class="health-label">配置版本</span>
              <span class="health-value code-text">v${esc(String(data.config_version ?? 1))}</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  async function users(container, page = 1) {
    const data = await api(`/api/users?page=${page}`);
    const items = data.items || [];
    container.innerHTML = `
      <div class="card table-card">
        <div class="card-header">
          <h3>访问用户列表</h3>
          <span class="text-muted">共 ${esc(String(data.total ?? items.length))} 名用户</span>
        </div>
        <div class="card-body" style="padding:0">
          ${items.length === 0 ? '<div class="empty-state">暂无用户记录</div>' : `
            <table class="data-table">
              <thead>
                <tr>
                  <th>用户 ID</th>
                  <th>加入时间</th>
                  <th>订阅/发言校验</th>
                  <th>付费解锁状态</th>
                  <th style="text-align:right">操作</th>
                </tr>
              </thead>
              <tbody>
                ${items.map((u) => {
                  const fsub = u.force_sub_status || {};
                  const paid = u.paid_status || {};
                  const msgCount = (fsub.msg_sent_channels || []).length;
                  return `
                    <tr>
                      <td class="code-text">${esc(String(u.user_id))}</td>
                      <td class="text-muted">${u.joined_at ? new Date(u.joined_at * 1000).toLocaleString() : '-'}</td>
                      <td>
                        <span class="badge ${fsub.verified ? 'badge-success' : 'badge-neutral'}">
                          ${fsub.verified ? '已通过' : '未通过'}
                        </span>
                        ${msgCount > 0 ? `<span class="badge badge-info">已发言 (${msgCount})</span>` : ''}
                      </td>
                      <td>
                        <span class="badge ${paid.is_unlocked ? 'badge-success' : 'badge-neutral'}">
                          ${paid.is_unlocked ? `已解锁 (${esc(paid.method || 'manual')})` : '未解锁'}
                        </span>
                      </td>
                      <td class="table-actions" style="text-align:right">
                        <button class="btn btn-ghost btn-sm" onclick="Tenant.toggleUserUnlock(${Number(u.user_id)}, ${!paid.is_unlocked})">
                          ${paid.is_unlocked ? '撤销解锁' : '手动解锁'}
                        </button>
                        <button class="btn btn-danger btn-sm" onclick="Tenant.banModal(${Number(u.user_id)})">封禁</button>
                      </td>
                    </tr>
                  `;
                }).join('')}
              </tbody>
            </table>
          `}
        </div>
      </div>
      ${pagination ? pagination(data.page || page, data.pages || 1, 'Tenant.usersPage') : ''}
    `;
  }

  function usersPage(page) {
    return users(document.getElementById('page-content'), page);
  }

  async function toggleUserUnlock(userId, unlock) {
    try {
      await api(`/api/users/${userId}/unlock`, {
        method: 'POST',
        body: JSON.stringify({ unlocked: Boolean(unlock), method: 'admin_manual' })
      });
      toast(unlock ? `已为用户 ${userId} 开启付费解锁权限` : `已撤销用户 ${userId} 的解锁权限`, 'success');
      usersPage(1);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function shares(container, page = 1, q = '') {
    const query = q ? `&q=${encodeURIComponent(q)}` : '';
    const data = await api(`/api/shares?page=${page}${query}`);
    const items = data.items || [];
    container.innerHTML = `
      <div class="page-actions" style="justify-content:space-between;margin-bottom:16px">
        <div class="search-box">
          <input id="share-search-input" class="form-input" placeholder="按分享码或备注搜索..." value="${esc(q)}" onkeydown="if(event.key==='Enter')Tenant.searchShares()">
          <button class="btn btn-ghost" onclick="Tenant.searchShares()">查询</button>
        </div>
      </div>
      <div class="card table-card">
        <div class="card-header">
          <h3>受控分享列表</h3>
          <span class="text-muted">共 ${esc(String(data.total ?? items.length))} 条记录</span>
        </div>
        <div class="card-body" style="padding:0">
          ${items.length === 0 ? '<div class="empty-state">暂无分享记录</div>' : `
            <table class="data-table">
              <thead>
                <tr>
                  <th>分享码</th>
                  <th>文件数量</th>
                  <th>防转发保护</th>
                  <th>访问次数</th>
                  <th style="text-align:right">操作</th>
                </tr>
              </thead>
              <tbody>
                ${items.map((item) => `
                  <tr>
                    <td class="code-text">${esc(item.code || '')}</td>
                    <td>${esc(String((item.message_ids || []).length || item.file_count || 1))}</td>
                    <td>
                      <span class="badge ${item.protect_content ? 'badge-info' : 'badge-neutral'}">
                        ${item.protect_content ? '开启' : '关闭'}
                      </span>
                    </td>
                    <td class="code-text">${esc(String(item.views ?? item.access_count ?? 0))}</td>
                    <td class="table-actions" style="text-align:right">
                      <button class="btn btn-ghost btn-sm" onclick="Tenant.editShare('${esc(item.code)}')">编辑</button>
                      <button class="btn btn-ghost btn-sm" onclick="Tenant.forwardShare('${esc(item.code)}')">补发</button>
                      <button class="btn btn-danger btn-sm" onclick="Tenant.removeShare('${esc(item.code)}')">删除</button>
                    </td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          `}
        </div>
      </div>
      ${pagination ? pagination(data.page || page, data.pages || 1, 'Tenant.sharesPage') : ''}
    `;
  }

  function sharesPage(page) {
    const q = document.getElementById('share-search-input')?.value || '';
    return shares(document.getElementById('page-content'), page, q);
  }

  function searchShares() {
    const q = document.getElementById('share-search-input')?.value || '';
    return shares(document.getElementById('page-content'), 1, q);
  }

  function editShare(code) {
    modal(
      `编辑分享 ${code}`,
      `
        <div class="form-group">
          <label class="form-label">访问密码（留空表示不设密码）</label>
          <input id="edit-share-password" class="form-input" placeholder="输入新密码或留空">
        </div>
        <label class="setting-item">
          <div class="setting-item-text">
            <span class="setting-item-title">禁止转发与另存</span>
            <span class="setting-item-desc">开启后用户无法转发或下载该分享批次中的文件</span>
          </div>
          <input type="checkbox" id="edit-share-protect">
        </label>
      `,
      `<button class="btn btn-ghost" onclick="FSB.closeModal()">取消</button>
       <button class="btn btn-accent" onclick="Tenant.saveShare('${esc(code)}')">保存</button>`
    );
  }

  async function saveShare(code) {
    const password = document.getElementById('edit-share-password')?.value || '';
    const protect_content = Boolean(document.getElementById('edit-share-protect')?.checked);
    try {
      await api(`/api/shares/${encodeURIComponent(code)}`, {
        method: 'PUT',
        body: JSON.stringify({ password, protect_content })
      });
      closeModal();
      toast('分享配置已更新', 'success');
      sharesPage(1);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function forwardShare(code) {
    modal(
      `定向补发分享 ${code}`,
      `
        <div class="form-group">
          <label class="form-label">目标 Telegram 用户 ID</label>
          <input id="forward-user-id" type="number" class="form-input" placeholder="例如 123456789">
        </div>
      `,
      `<button class="btn btn-ghost" onclick="FSB.closeModal()">取消</button>
       <button class="btn btn-accent" onclick="Tenant.runForward('${esc(code)}')">立即下发</button>`
    );
  }

  async function runForward(code) {
    const userId = Number(document.getElementById('forward-user-id')?.value || 0);
    if (!userId) {
      toast('请输入有效的目标用户 ID', 'error');
      return;
    }
    try {
      await api(`/api/shares/${encodeURIComponent(code)}/deliver`, {
        method: 'POST',
        body: JSON.stringify({ user_id: userId })
      });
      closeModal();
      toast('下发任务已提交至队列', 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function removeShare(code) {
    modal(
      '删除分享确认',
      `<p>确定要删除分享码 <code class="code-text">${esc(code)}</code> 吗？删除后原分享链接将立即失效。</p>`,
      `<button class="btn btn-ghost" onclick="FSB.closeModal()">取消</button>
       <button class="btn btn-danger" onclick="Tenant.confirmRemoveShare('${esc(code)}')">确认删除</button>`
    );
  }

  async function confirmRemoveShare(code) {
    try {
      await api(`/api/shares/${encodeURIComponent(code)}`, { method: 'DELETE' });
      closeModal();
      toast('分享码已删除', 'success');
      sharesPage(1);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function broadcast(container) {
    container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <h3>全员广播推送</h3>
          <span class="text-muted">由后台异步队列限流发送，自动清理失效或封禁账号</span>
        </div>
        <div class="card-body">
          <div class="form-group">
            <label class="form-label">广播正文（支持 Telegram HTML 标签）</label>
            <textarea id="broadcast-text" class="form-textarea" rows="5" placeholder="输入要推送给全体用户的消息内容..."></textarea>
          </div>
          <div class="page-actions">
            <button id="broadcast-send" class="btn btn-accent" onclick="Tenant.sendBroadcast()">提交广播任务</button>
          </div>
          <div id="broadcast-progress" class="code-text margin-top-md"></div>
        </div>
      </div>
    `;
  }

  async function sendBroadcast() {
    const text = document.getElementById('broadcast-text')?.value?.trim();
    if (!text) {
      toast('请输入广播内容', 'error');
      return;
    }
    try {
      const res = await api('/api/broadcast', {
        method: 'POST',
        body: JSON.stringify({ text })
      });
      toast('广播任务已启动', 'success');
      if (res.job_id && pollJob) {
        pollJob(res.job_id, document.getElementById('broadcast-progress'));
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function banned(container) {
    const data = await api('/api/banned');
    const items = data.items || [];
    container.innerHTML = `
      <div class="page-actions" style="margin-bottom:16px">
        <button class="btn btn-accent" onclick="Tenant.banModal()">添加封禁用户</button>
      </div>
      <div class="card table-card">
        <div class="card-header">
          <h3>黑名单列表</h3>
          <span class="text-muted">共 ${esc(String(items.length))} 条记录</span>
        </div>
        <div class="card-body" style="padding:0">
          ${items.length === 0 ? '<div class="empty-state">当前无封禁用户</div>' : `
            <table class="data-table">
              <thead>
                <tr>
                  <th>用户 ID</th>
                  <th>封禁原因</th>
                  <th>操作时间</th>
                  <th style="text-align:right">操作</th>
                </tr>
              </thead>
              <tbody>
                ${items.map((item) => `
                  <tr>
                    <td class="code-text">${esc(String(item.user_id))}</td>
                    <td>${esc(item.reason || '管理员手动封禁')}</td>
                    <td class="text-muted">${item.banned_at ? new Date(item.banned_at * 1000).toLocaleString() : '-'}</td>
                    <td class="table-actions" style="text-align:right">
                      <button class="btn btn-ghost btn-sm" onclick="Tenant.unban(${Number(item.user_id)})">解除封禁</button>
                    </td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          `}
        </div>
      </div>
    `;
  }

  function banModal(prefillId = '') {
    modal(
      '封禁 Telegram 用户',
      `
        <div class="form-group">
          <label class="form-label">目标用户 ID</label>
          <input id="ban-user-id" type="number" class="form-input" value="${esc(String(prefillId || ''))}">
        </div>
        <div class="form-group">
          <label class="form-label">封禁备注</label>
          <input id="ban-reason" class="form-input" placeholder="选填，如：恶意刷接口">
        </div>
      `,
      `<button class="btn btn-ghost" onclick="FSB.closeModal()">取消</button>
       <button class="btn btn-danger" onclick="Tenant.ban()">确认封禁</button>`
    );
  }

  async function ban() {
    const user_id = Number(document.getElementById('ban-user-id')?.value || 0);
    const reason = document.getElementById('ban-reason')?.value || '';
    if (!user_id) {
      toast('请填写有效的用户 ID', 'error');
      return;
    }
    try {
      await api('/api/banned', {
        method: 'POST',
        body: JSON.stringify({ user_id, reason })
      });
      closeModal();
      toast('已加入封禁名单', 'success');
      banned(document.getElementById('page-content'));
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function unban(userId) {
    try {
      await api(`/api/banned/${userId}`, { method: 'DELETE' });
      toast('已解除封禁', 'success');
      banned(document.getElementById('page-content'));
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function syncAccessGateState() {
    const fsubInput = document.getElementById('setting-force_sub_channels');
    const hasChannels = Boolean(fsubInput && fsubInput.value.trim().length > 0);
    const fsubBadge = document.getElementById('gate-mode-fsub-badge');
    const payBadge = document.getElementById('gate-mode-pay-badge');

    if (fsubBadge) {
      fsubBadge.className = hasChannels ? 'badge badge-success' : 'badge badge-neutral';
      fsubBadge.textContent = hasChannels ? '当前生效：已绑定强制订阅频道' : '未配置频道';
    }
    if (payBadge) {
      payBadge.className = !hasChannels ? 'badge badge-info' : 'badge badge-neutral';
      payBadge.textContent = !hasChannels ? '当前生效：未绑定强制订阅频道，可启用付费解锁' : '已绑定强制订阅频道时自动跳过付费门禁';
    }
  }

  async function settings(container) {
    const data = await api('/api/settings');
    const fsubChannelsStr = (data.force_sub_channels || []).join(',');
    const boundChannelsStr = (data.bound_channels || []).join(',');

    const renderToggle = (key) => {
      const found = boolFields.find((f) => f[0] === key);
      if (!found) return '';
      const [, label, desc] = found;
      return `
        <label class="setting-item">
          <div class="setting-item-text">
            <span class="setting-item-title">${esc(label)}</span>
            ${desc ? `<span class="setting-item-desc">${esc(desc)}</span>` : ''}
          </div>
          <input type="checkbox" id="setting-${key}" ${data[key] ? 'checked' : ''}>
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

    container.innerHTML = `
      <div class="settings-grid">
        <!-- 模块 1：强制订阅频道与频道发言解锁 -->
        <div class="card">
          <div class="card-header">
            <h3>门禁策略一：强制订阅与频道发言解锁</h3>
            <span id="gate-mode-fsub-badge" class="badge badge-neutral">检测中</span>
          </div>
          <div class="card-body">
            <div class="form-group">
              <label class="form-label">强制订阅频道 / 群组 ID（多个以英文逗号分隔，如 -100123456789）</label>
              <input class="form-input code-text" id="setting-force_sub_channels"
                     value="${esc(fsubChannelsStr)}"
                     placeholder="留空则不启用强制订阅，转由下方付费解锁门禁控制"
                     oninput="Tenant.syncAccessGateState()">
            </div>
            ${renderToggle('force_sub_require_msg')}
            <div class="form-group" style="margin-top:12px">
              <label class="form-label">绑定资源存储/下发频道 ID（多个以逗号分隔）</label>
              <input class="form-input code-text" id="setting-bound_channels" value="${esc(boundChannelsStr)}">
            </div>
          </div>
        </div>

        <!-- 模块 2：未绑定强制订阅频道时的付费解锁配置 -->
        <div class="card">
          <div class="card-header">
            <h3>门禁策略二：无强制订阅时的多方式付费解锁</h3>
            <span id="gate-mode-pay-badge" class="badge badge-neutral">检测中</span>
          </div>
          <div class="card-body">
            ${renderToggle('pay_to_unlock')}
            ${renderToggle('pay_enable_cdkey')}
            <div class="form-row-2">
              ${renderNumber('pay_unlock_expire')}
              ${renderNumber('pay_amount_stars')}
            </div>
            ${paymentTextFields.map(([key, label]) => `
              <div class="form-group">
                <label class="form-label">${esc(label)}</label>
                <input class="form-input" id="setting-${key}" value="${esc(data[key] || '')}">
              </div>
            `).join('')}
            <div class="cdkey-toolbar">
              <div class="cdkey-toolbar-left">
                <input type="number" id="cdkey-gen-count" class="form-input" style="width:110px" value="10" min="1" max="200">
                <button type="button" class="btn btn-ghost" onclick="Tenant.generateCdkeys()">批量生成激活码</button>
                <button type="button" class="btn btn-ghost" onclick="Tenant.loadCdkeys()">查看可用卡密</button>
              </div>
            </div>
            <div id="cdkey-output-box" class="hidden" style="margin-top:12px"></div>
          </div>
        </div>

        <!-- 模块 3：短链验证、防转发与限流控制 -->
        <div class="card">
          <div class="card-header">
            <h3>安全防护、短链验证与访问限流</h3>
          </div>
          <div class="card-body">
            ${renderToggle('is_verify')}
            ${renderToggle('protect_content')}
            ${renderToggle('disable_channel_button')}
            ${renderToggle('show_promo')}
            <div class="form-row-2" style="margin-top:12px">
              ${renderNumber('verify_expire')}
              ${renderNumber('auto_delete_time')}
              ${renderNumber('share_code_length')}
              ${renderNumber('rate_limit_max')}
              ${renderNumber('rate_limit_window')}
            </div>
          </div>
        </div>

        <!-- 模块 4：消息文案与模板配置 -->
        <div class="card">
          <div class="card-header">
            <h3>消息模板与提示文案</h3>
          </div>
          <div class="card-body">
            ${textFields.map(([key, label, rows]) => `
              <div class="form-group">
                <label class="form-label">${esc(label)}</label>
                <textarea id="setting-${key}" class="form-input" rows="${rows || 2}">${esc(data[key] || '')}</textarea>
              </div>
            `).join('')}
          </div>
        </div>
      </div>

      <div class="page-actions" style="margin-top:20px">
        <button type="button" class="btn btn-accent" onclick="Tenant.saveSettings()">保存全部配置</button>
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
      toast('配置已保存，Worker 实例将自动同步生效', 'success');
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
          <label class="form-label">新生成激活码（共 ${codes.length} 个，可直接复制至发卡网）</label>
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
          <label class="form-label">当前未核销激活码（共 ${unused.length} 个可用 / 总计 ${items.length} 个）</label>
          <textarea class="form-input code-text" rows="5" readonly>${esc(unused.join('\n') || '暂无未使用的激活码')}</textarea>
        `;
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function health(container) {
    const data = await api('/api/health');
    container.innerHTML = `
      <div class="stats-grid">
        <div class="card">
          <div class="card-header"><h3>运行时健康诊断</h3></div>
          <div class="card-body">
            <div class="health-grid">
              <div class="health-item">
                <span class="health-label">数据库状态</span>
                <span class="health-value">${data.db_ok !== false ? '正常' : '异常'}</span>
              </div>
              <div class="health-item">
                <span class="health-label">Bot 会话状态</span>
                <span class="health-value">${data.bot_online ? '在线' : '待机/离线'}</span>
              </div>
              <div class="health-item">
                <span class="health-label">待处理投递队列</span>
                <span class="health-value code-text">${esc(String(data.pending_deliveries ?? 0))}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
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
})();