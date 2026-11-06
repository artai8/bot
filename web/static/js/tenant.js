(function () {
  window.FSB = window.FSB || {};
  const FSB = window.FSB;

  const BOOL_FIELDS = [
    { key: "is_verify", label: "开启短链验证 (is_verify)", hint: "要求用户完成短链广告验证后才可获取资源" },
    { key: "protect_content", label: "全局防转发保护 (protect_content)", hint: "开启后机器人发送给用户的消息禁止转发和另存" },
    { key: "disable_channel_button", label: "隐藏频道跳转按钮 (disable_channel_button)", hint: "隐藏资源消息下方的默认频道按钮" },
    { key: "show_promo", label: "显示底部推广文案 (show_promo)", hint: "在发送的消息底部附加推广签名文案" },
    { key: "force_sub_require_msg", label: "强关后需发言解锁 (force_sub_require_msg)", hint: "加入强关频道/群组后还需发送一条消息才可解锁" },
    { key: "pay_to_unlock", label: "开启付费/卡密解锁 (pay_to_unlock)", hint: "要求用户完成Stars、USDT或卡密兑换后才可访问资源" },
    { key: "pay_enable_stars", label: "启用 Telegram Stars 支付 (pay_enable_stars)", hint: "允许用户使用 Telegram Stars 解锁" },
    { key: "pay_enable_usdt", label: "启用 USDT 支付验证 (pay_enable_usdt)", hint: "允许用户转账 USDT 并提交交易哈希解锁" },
    { key: "pay_enable_cashier", label: "启用第三方收银台链接 (pay_enable_cashier)", hint: "显示自定义外部购买/收银台按钮" },
    { key: "pay_enable_cdkey", label: "启用卡密 (CDKey) 兑换解锁 (pay_enable_cdkey)", hint: "允许用户使用卡密激活访问权限" },
  ];

  const NUMBER_FIELDS = [
    { key: "verify_expire", label: "短链验证有效期 (秒)", hint: "默认 86400 秒 (24小时)" },
    { key: "auto_delete_time", label: "消息自动销毁时间 (秒)", hint: "0 表示不自动删除" },
    { key: "share_code_length", label: "分享码随机长度 (4-32)", hint: "默认 8 位" },
    { key: "rate_limit_max", label: "频率限制次数 (每窗口)", hint: "默认 10 次" },
    { key: "rate_limit_window", label: "频率限制时间窗口 (秒)", hint: "默认 60 秒" },
    { key: "pay_unlock_expire", label: "付费解锁有效期 (秒)", hint: "0 表示永久有效" },
    { key: "pay_amount_stars", label: "Telegram Stars 价格 (颗)", hint: "默认 50" },
  ];

  const TEXT_FIELDS = [
    { key: "shortlink_url", label: "短网址平台域名 (shortlink_url)", multiline: false },
    { key: "shortlink_api", label: "短网址 API Key (shortlink_api)", multiline: false },
    { key: "tutorial_video", label: "验证教程视频链接 (tutorial_video)", multiline: false },
    { key: "keyword_button_text", label: "关键词按钮文字 (keyword_button_text)", multiline: false },
    { key: "pay_amount_usdt", label: "USDT 价格金额 (pay_amount_usdt)", multiline: false },
    { key: "pay_usdt_network", label: "USDT 网络类型 (如 TRC20 / BEP20)", multiline: false },
    { key: "pay_usdt_address", label: "USDT 收款钱包地址 (pay_usdt_address)", multiline: false },
    { key: "pay_custom_url", label: "发卡网/收银台链接 (pay_custom_url)", multiline: false },
    { key: "pay_webhook_secret", label: "支付回调签名密钥 (pay_webhook_secret，选填，防伪造回调)", multiline: false },
    { key: "custom_caption", label: "自定义附加说明文案 (custom_caption)", multiline: true },
    { key: "custom_buttons", label: "自定义底部按钮配置 (每行: 按钮名 | 链接)", multiline: true },
    { key: "promo_text", label: "推广签名文案 (promo_text)", multiline: true },
    { key: "start_message", label: "欢迎语模板 (start_message)", multiline: true },
    { key: "force_sub_message", label: "强制关注提示文案 (force_sub_message)", multiline: true },
    { key: "force_sub_msg_prompt", label: "强制发言附加提示 (force_sub_msg_prompt)", multiline: true },
    { key: "pay_unlock_message", label: "付费解锁提示文案 (pay_unlock_message)", multiline: true },
    { key: "auto_delete_message", label: "自动删除倒计时提示文案 (auto_delete_message)", multiline: true },
    { key: "user_reply_text", label: "普通用户私聊自动回复 (user_reply_text)", multiline: true },
    { key: "about_text", label: "关于说明文案 (about_text)", multiline: true },
    { key: "help_text", label: "帮助说明文案 (help_text)", multiline: true },
    { key: "admin_help_text", label: "管理员指令说明文案 (admin_help_text)", multiline: true },
  ];

  function ensureTenantSelected() {
    if (FSB.sessionRole === "platform" && !FSB.selectedTenantId) {
      const container = document.getElementById("page-content");
      if (container) {
        container.innerHTML =
          '<div class="card"><div class="card-body"><p class="text-muted">请先在左侧顶部下拉框中选择一个租户，或在「全部租户总览」中点击某个租户进行管理。</p></div></div>';
      }
      return false;
    }
    return true;
  }

  async function renderDashboard() {
    if (!ensureTenantSelected()) return;
    const container = document.getElementById("page-content");
    const data = await FSB.api("/api/dashboard");
    const t = data.tenant || {};
    const u = data.users || {};
    const s = data.shares || {};
    const rt = data.runtime || {};
    const rtStatus = rt.status || (t.enabled ? "starting" : "stopped");
    const badgeClass =
      rtStatus === "running" ? "badge-success" : t.enabled ? "badge-warning" : "badge-secondary";

    container.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">总用户数</div>
          <div class="stat-value">${FSB.formatNumber(u.total)}</div>
          <div class="stat-sub">今日新增: ${FSB.formatNumber(u.today)} | 7日: ${FSB.formatNumber(u.week)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">总分享合集/链接</div>
          <div class="stat-value">${FSB.formatNumber(s.total)}</div>
          <div class="stat-sub">今日新增: ${FSB.formatNumber(s.today)} | 7日: ${FSB.formatNumber(s.week)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">已付费/卡密解锁用户</div>
          <div class="stat-value">${FSB.formatNumber(u.unlocked || 0)}</div>
          <div class="stat-sub">卡密总数: ${FSB.formatNumber(data.cdkeys_count || 0)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">封禁黑名单用户</div>
          <div class="stat-value">${FSB.formatNumber(data.banned || 0)}</div>
          <div class="stat-sub">机器人状态: <span class="badge ${badgeClass}">${FSB.esc(rtStatus)}</span></div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div class="card-title">当前租户核心信息 (${FSB.esc(t.display_name || t.slug)})</div>
          <div class="btn-group">
            <button class="btn btn-sm btn-primary" onclick="window.FSB.loadPage('settings')">修改配置与Bot参数</button>
            <button class="btn btn-sm btn-secondary" onclick="window.FSB.restartTenantBot()">重启机器人实例</button>
          </div>
        </div>
        <div class="card-body">
          <div class="form-row">
            <div><strong>租户账号 (Slug):</strong> ${FSB.esc(t.slug)}</div>
            <div><strong>Bot 用户名:</strong> ${t.bot_username ? "@" + FSB.esc(t.bot_username) : "未获取"}</div>
            <div><strong>Bot Token:</strong> ${FSB.esc(t.bot_token_hint || "尚未配置")}</div>
            <div><strong>Owner Telegram ID:</strong> ${FSB.esc(t.owner_id || "尚未配置")}</div>
            <div><strong>存储频道 ID:</strong> ${FSB.esc(t.db_channel_id || "尚未配置")}</div>
            <div><strong>启用状态:</strong> ${t.enabled ? '<span class="badge badge-success">已启用</span>' : '<span class="badge badge-warning">未启用</span>'}</div>
          </div>
          ${rt.last_error ? `<div style="margin-top:12px;color:#f87171;">最近运行错误: ${FSB.esc(rt.last_error)}</div>` : ""}
        </div>
      </div>
    `;
  }

  async function restartTenantBot() {
    try {
      await FSB.api("/api/tenant/restart", { method: "POST" });
      FSB.toast("已发送重启机器人指令", "success");
      setTimeout(renderDashboard, 1000);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function renderSettings() {
    if (!ensureTenantSelected()) return;
    const container = document.getElementById("page-content");
    const cfg = await FSB.api("/api/settings");

    const boolHtml = BOOL_FIELDS.map(function (f) {
      const checked = cfg[f.key] ? "checked" : "";
      return `
        <div class="form-check">
          <input type="checkbox" id="cfg-${f.key}" data-key="${f.key}" ${checked} />
          <div>
            <label for="cfg-${f.key}" style="margin:0;cursor:pointer;">${FSB.esc(f.label)}</label>
            <div class="form-hint">${FSB.esc(f.hint)}</div>
          </div>
        </div>
      `;
    }).join("");

    const numHtml = NUMBER_FIELDS.map(function (f) {
      const val = cfg[f.key] !== undefined ? cfg[f.key] : 0;
      return `
        <div class="form-group">
          <label for="cfg-${f.key}">${FSB.esc(f.label)}</label>
          <input type="number" id="cfg-${f.key}" class="form-control" value="${FSB.esc(val)}" />
          <div class="form-hint">${FSB.esc(f.hint)}</div>
        </div>
      `;
    }).join("");

    const textHtml = TEXT_FIELDS.map(function (f) {
      const val = cfg[f.key] !== undefined ? cfg[f.key] : "";
      if (f.multiline) {
        return `
          <div class="form-group">
            <label for="cfg-${f.key}">${FSB.esc(f.label)}</label>
            <textarea id="cfg-${f.key}" class="form-control">${FSB.esc(val)}</textarea>
          </div>
        `;
      }
      return `
        <div class="form-group">
          <label for="cfg-${f.key}">${FSB.esc(f.label)}</label>
          <input type="text" id="cfg-${f.key}" class="form-control" value="${FSB.esc(val)}" />
        </div>
      `;
    }).join("");

    const forceSubStr = Array.isArray(cfg.force_sub_channels) ? cfg.force_sub_channels.join(", ") : "";
    const boundStr = Array.isArray(cfg.bound_channels) ? cfg.bound_channels.join(", ") : "";
    const adminIdsStr = Array.isArray(cfg.admin_ids) ? cfg.admin_ids.join(", ") : "";

    container.innerHTML = `
      <form id="tenant-full-settings-form">
        <div class="card">
          <div class="card-header">
            <div class="card-title">1. 租户基础信息与机器人核心配置</div>
            <button type="submit" class="btn btn-primary">保存全部设置</button>
          </div>
          <div class="card-body">
            <div class="form-row">
              <div class="form-group">
                <label>租户登录账号标识 (Slug)</label>
                <input type="text" id="core-slug" class="form-control" value="${FSB.esc(cfg.slug || "")}" />
              </div>
              <div class="form-group">
                <label>租户显示名称</label>
                <input type="text" id="core-display-name" class="form-control" value="${FSB.esc(cfg.display_name || "")}" />
              </div>
              <div class="form-group">
                <label>修改租户登录密码 (留空则不修改)</label>
                <input type="password" id="core-web-password" class="form-control" placeholder="输入新密码以修改" />
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label>Telegram Bot Token (当前: ${FSB.esc(cfg.bot_token_hint || "未设置")})</label>
                <input type="text" id="core-bot-token" class="form-control" placeholder="留空保持不变，输入新 Token 则实时校验并覆盖" />
              </div>
              <div class="form-group">
                <label>Bot 用户名 (保存 Token 时会自动识别，也可手动填)</label>
                <input type="text" id="core-bot-username" class="form-control" value="${FSB.esc(cfg.bot_username || "")}" />
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label>Owner Telegram 用户 ID (必填以启用)</label>
                <input type="number" id="core-owner-id" class="form-control" value="${FSB.esc(cfg.owner_id || 0)}" />
              </div>
              <div class="form-group">
                <label>存储数据库频道 ID (必填，如 -100123456789)</label>
                <input type="number" id="core-db-channel-id" class="form-control" value="${FSB.esc(cfg.db_channel_id || 0)}" />
              </div>
              <div class="form-group">
                <label>附加管理员 Telegram ID 列表 (逗号分隔)</label>
                <input type="text" id="core-admin-ids" class="form-control" value="${FSB.esc(adminIdsStr)}" />
              </div>
            </div>
            <div class="form-check">
              <input type="checkbox" id="core-enabled" ${cfg.enabled ? "checked" : ""} />
              <label for="core-enabled" style="margin:0;cursor:pointer;"><strong>启用并运行该租户的 Telegram 机器人实例</strong>（保存时会自动校验 Bot Token 与存储频道管理员权限）</label>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">2. 频道绑定与强制关注设置</div>
          </div>
          <div class="card-body">
            <div class="form-row">
              <div class="form-group">
                <label>强制关注频道/群组 ID 列表 (force_sub_channels，多个用逗号分隔)</label>
                <input type="text" id="cfg-force_sub_channels" class="form-control" value="${FSB.esc(forceSubStr)}" placeholder="-1001111111, -1002222222" />
              </div>
              <div class="form-group">
                <label>自动监听并生成链接的绑定频道 ID 列表 (bound_channels，逗号分隔)</label>
                <input type="text" id="cfg-bound_channels" class="form-control" value="${FSB.esc(boundStr)}" placeholder="-1003333333" />
              </div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">3. 功能开关配置</div>
          </div>
          <div class="card-body">
            ${boolHtml}
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">4. 数值与限流参数配置</div>
          </div>
          <div class="card-body">
            <div class="form-row">
              ${numHtml}
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">5. 消息文案、短链与支付收款详细设置</div>
            <button type="submit" class="btn btn-primary">保存全部设置</button>
          </div>
          <div class="card-body">
            ${textHtml}
          </div>
        </div>
      </form>
    `;

    const form = document.getElementById("tenant-full-settings-form");
    if (form) {
      form.addEventListener("submit", async function (e) {
        e.preventDefault();
        await saveAllSettings();
      });
    }
  }

  async function saveAllSettings() {
    const payload = {
      slug: document.getElementById("core-slug").value.trim(),
      display_name: document.getElementById("core-display-name").value.trim(),
      owner_id: Number(document.getElementById("core-owner-id").value || 0),
      db_channel_id: Number(document.getElementById("core-db-channel-id").value || 0),
      admin_ids: document.getElementById("core-admin-ids").value,
      bot_username: document.getElementById("core-bot-username").value.trim(),
      enabled: document.getElementById("core-enabled").checked,
      force_sub_channels: document.getElementById("cfg-force_sub_channels").value,
      bound_channels: document.getElementById("cfg-bound_channels").value,
    };

    const newPw = document.getElementById("core-web-password").value;
    if (newPw && newPw.trim()) payload.web_password = newPw.trim();

    const newTok = document.getElementById("core-bot-token").value;
    if (newTok && newTok.trim()) payload.bot_token = newTok.trim();

    BOOL_FIELDS.forEach(function (f) {
      const el = document.getElementById("cfg-" + f.key);
      if (el) payload[f.key] = el.checked;
    });

    NUMBER_FIELDS.forEach(function (f) {
      const el = document.getElementById("cfg-" + f.key);
      if (el) payload[f.key] = Number(el.value || 0);
    });

    TEXT_FIELDS.forEach(function (f) {
      const el = document.getElementById("cfg-" + f.key);
      if (el) payload[f.key] = el.value;
    });

    try {
      FSB.showLoading(true);
      await FSB.api("/api/settings", { method: "PUT", body: payload });
      FSB.toast("所有设置已成功保存", "success");
      if (FSB.sessionRole === "platform" && FSB.refreshAdminTenantSelector) {
        await FSB.refreshAdminTenantSelector();
      }
      await renderSettings();
    } catch (err) {
      FSB.toast(err.message, "error");
    } finally {
      FSB.showLoading(false);
    }
  }

  async function renderShares(page, search) {
    if (!ensureTenantSelected()) return;
    page = page || 1;
    search = search !== undefined ? search : "";
    const container = document.getElementById("page-content");
    const q = new URLSearchParams({ page: String(page), per_page: "20" });
    if (search) q.set("search", search);
    const data = await FSB.api("/api/shares?" + q.toString());
    const items = data.shares || [];

    const rows = items
      .map(function (s) {
        const kw = (s.keywords || []).join(", ");
        return `
          <tr>
            <td><code>${FSB.esc(s.code)}</code></td>
            <td>${FSB.esc(s.title || "-")}</td>
            <td>${FSB.formatNumber(s.file_count)} (${FSB.esc((s.message_ids || []).slice(0, 5).join(","))})</td>
            <td>${FSB.esc(kw || "-")}</td>
            <td>${s.protect_content ? '<span class="badge badge-warning">防转发</span>' : '<span class="badge badge-secondary">普通</span>'}</td>
            <td>${FSB.formatNumber(s.access_count)}</td>
            <td>${FSB.formatDate(s.created_at)}</td>
            <td>
              <div class="btn-group">
                <button class="btn btn-sm btn-secondary" onclick="window.FSB.openEditShareModal('${FSB.esc(s.code)}')">编辑</button>
                <button class="btn btn-sm btn-danger" onclick="window.FSB.deleteShare('${FSB.esc(s.code)}')">删除</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <div class="card-title">分享链接与合集数据 (${FSB.formatNumber(data.total || 0)})</div>
          <div class="btn-group">
            <input type="text" id="share-search-input" class="form-control form-control-sm" style="width:200px;" placeholder="搜索分享码/标题/关键词" value="${FSB.esc(search)}" />
            <button class="btn btn-sm btn-secondary" onclick="window.FSB.searchShares()">搜索</button>
            <button class="btn btn-sm btn-primary" onclick="window.FSB.openCreateShareModal()">+ 新增分享</button>
          </div>
        </div>
        <div class="card-body">
          <div class="table-responsive">
            <table class="table">
              <thead>
                <tr>
                  <th>分享码 (Code)</th>
                  <th>标题</th>
                  <th>文件数 / 消息ID</th>
                  <th>搜索关键词</th>
                  <th>保护状态</th>
                  <th>访问次数</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                ${rows || '<tr><td colspan="8" class="text-muted">暂无分享数据</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }

  function searchShares() {
    const val = (document.getElementById("share-search-input") || {}).value || "";
    renderShares(1, val.trim());
  }

  function openCreateShareModal() {
    const body = `
      <div class="form-group">
        <label>存储频道中的消息 ID 列表 (必填，多个用逗号或空格分隔)</label>
        <input type="text" id="modal-share-mids" class="form-control" placeholder="例如: 101, 102, 103" />
      </div>
      <div class="form-group">
        <label>自定义分享码 (选填，留空自动随机生成)</label>
        <input type="text" id="modal-share-code" class="form-control" placeholder="例如: vippack01" />
      </div>
      <div class="form-group">
        <label>合集标题</label>
        <input type="text" id="modal-share-title" class="form-control" placeholder="资源标题" />
      </div>
      <div class="form-group">
        <label>搜索关键词 (逗号分隔)</label>
        <input type="text" id="modal-share-keywords" class="form-control" placeholder="关键词1, 关键词2" />
      </div>
      <div class="form-group">
        <label>群组展示文案 (group_text)</label>
        <textarea id="modal-share-group-text" class="form-control"></textarea>
      </div>
      <div class="form-check">
        <input type="checkbox" id="modal-share-protect" />
        <label for="modal-share-protect" style="margin:0;">开启防转发保护 (protect_content)</label>
      </div>
    `;
    const footer = `
      <button class="btn btn-secondary" onclick="window.FSB.closeModal()">取消</button>
      <button class="btn btn-primary" onclick="window.FSB.submitCreateShare()">创建分享</button>
    `;
    FSB.renderModal("新增分享资源", body, footer);
  }

  async function submitCreateShare() {
    const payload = {
      message_ids: document.getElementById("modal-share-mids").value,
      code: document.getElementById("modal-share-code").value.trim(),
      title: document.getElementById("modal-share-title").value.trim(),
      keywords: document.getElementById("modal-share-keywords").value,
      group_text: document.getElementById("modal-share-group-text").value,
      protect_content: document.getElementById("modal-share-protect").checked,
    };
    try {
      await FSB.api("/api/shares", { method: "POST", body: payload });
      FSB.closeModal();
      FSB.toast("分享创建成功", "success");
      await renderShares(1);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function openEditShareModal(code) {
    try {
      const s = await FSB.api("/api/shares/" + encodeURIComponent(code));
      const body = `
        <div class="form-group">
          <label>分享码</label>
          <input type="text" class="form-control" value="${FSB.esc(s.code)}" disabled />
        </div>
        <div class="form-group">
          <label>消息 ID 列表 (逗号分隔)</label>
          <input type="text" id="edit-share-mids" class="form-control" value="${FSB.esc((s.message_ids || []).join(", "))}" />
        </div>
        <div class="form-group">
          <label>标题</label>
          <input type="text" id="edit-share-title" class="form-control" value="${FSB.esc(s.title || "")}" />
        </div>
        <div class="form-group">
          <label>搜索关键词 (逗号分隔)</label>
          <input type="text" id="edit-share-keywords" class="form-control" value="${FSB.esc((s.keywords || []).join(", "))}" />
        </div>
        <div class="form-group">
          <label>群组展示文案</label>
          <textarea id="edit-share-group-text" class="form-control">${FSB.esc(s.group_text || "")}</textarea>
        </div>
        <div class="form-check">
          <input type="checkbox" id="edit-share-protect" ${s.protect_content ? "checked" : ""} />
          <label for="edit-share-protect" style="margin:0;">开启防转发保护</label>
        </div>
      `;
      const footer = `
        <button class="btn btn-secondary" onclick="window.FSB.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="window.FSB.submitEditShare('${FSB.esc(s.code)}')">保存修改</button>
      `;
      FSB.renderModal("编辑分享: " + s.code, body, footer);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function submitEditShare(code) {
    const payload = {
      message_ids: document.getElementById("edit-share-mids").value,
      title: document.getElementById("edit-share-title").value,
      keywords: document.getElementById("edit-share-keywords").value,
      group_text: document.getElementById("edit-share-group-text").value,
      protect_content: document.getElementById("edit-share-protect").checked,
    };
    try {
      await FSB.api("/api/shares/" + encodeURIComponent(code), { method: "PUT", body: payload });
      FSB.closeModal();
      FSB.toast("分享已更新", "success");
      await renderShares(1);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function deleteShare(code) {
    if (!confirm("确定要删除分享码 " + code + " 吗？")) return;
    try {
      await FSB.api("/api/shares/" + encodeURIComponent(code), { method: "DELETE" });
      FSB.toast("分享已删除", "success");
      await renderShares(1);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function renderUsers(page, search) {
    if (!ensureTenantSelected()) return;
    page = page || 1;
    search = search || "";
    const container = document.getElementById("page-content");
    const q = new URLSearchParams({ page: String(page), per_page: "20" });
    if (search) q.set("search", search);
    const data = await FSB.api("/api/users?" + q.toString());
    const users = data.users || [];

    const rows = users
      .map(function (u) {
        const unlocked = u.paid_status && u.paid_status.is_unlocked;
        return `
          <tr>
            <td><code>${FSB.esc(u.user_id)}</code></td>
            <td>${FSB.formatDate(u.joined_at)}</td>
            <td>${unlocked ? '<span class="badge badge-success">已解锁 (' + FSB.esc(u.paid_status.method || "manual") + ')</span>' : '<span class="badge badge-secondary">未解锁</span>'}</td>
            <td>${u.is_banned ? '<span class="badge badge-danger">已封禁: ' + FSB.esc(u.ban_reason) + '</span>' : '<span class="badge badge-success">正常</span>'}</td>
            <td>
              <div class="btn-group">
                <button class="btn btn-sm ${unlocked ? "btn-warning" : "btn-success"}" onclick="window.FSB.toggleUserUnlock(${u.user_id}, ${!unlocked})">${unlocked ? "取消解锁" : "手动解锁"}</button>
                ${
                  u.is_banned
                    ? `<button class="btn btn-sm btn-secondary" onclick="window.FSB.unbanUser(${u.user_id})">解封</button>`
                    : `<button class="btn btn-sm btn-danger" onclick="window.FSB.banUser(${u.user_id})">封禁</button>`
                }
                <button class="btn btn-sm btn-danger" onclick="window.FSB.deleteUserRecord(${u.user_id})">删除数据</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <div class="card-title">用户数据与权限管理 (${FSB.formatNumber(data.total || 0)})</div>
          <div class="btn-group">
            <input type="text" id="user-search-input" class="form-control form-control-sm" style="width:180px;" placeholder="按 Telegram ID 搜索" value="${FSB.esc(search)}" />
            <button class="btn btn-sm btn-secondary" onclick="window.FSB.searchUsers()">搜索</button>
            <button class="btn btn-sm btn-primary" onclick="window.FSB.openAddUserModal()">+ 手动添加用户</button>
          </div>
        </div>
        <div class="card-body">
          <div class="table-responsive">
            <table class="table">
              <thead>
                <tr>
                  <th>Telegram 用户 ID</th>
                  <th>加入时间</th>
                  <th>付费/卡密解锁状态</th>
                  <th>账号状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                ${rows || '<tr><td colspan="5" class="text-muted">暂无用户数据</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }

  function searchUsers() {
    const val = (document.getElementById("user-search-input") || {}).value || "";
    renderUsers(1, val.trim());
  }

  function openAddUserModal() {
    const body = `
      <div class="form-group">
        <label>Telegram 用户 ID *</label>
        <input type="number" id="modal-add-uid" class="form-control" placeholder="例如 123456789" />
      </div>
      <div class="form-check">
        <input type="checkbox" id="modal-add-unlocked" checked />
        <label for="modal-add-unlocked" style="margin:0;">同时赋予付费解锁权限</label>
      </div>
    `;
    const footer = `
      <button class="btn btn-secondary" onclick="window.FSB.closeModal()">取消</button>
      <button class="btn btn-primary" onclick="window.FSB.submitAddUser()">确认添加</button>
    `;
    FSB.renderModal("手动添加用户数据", body, footer);
  }

  async function submitAddUser() {
    const uid = Number(document.getElementById("modal-add-uid").value || 0);
    const unlocked = document.getElementById("modal-add-unlocked").checked;
    try {
      await FSB.api("/api/users", { method: "POST", body: { user_id: uid, unlocked: unlocked } });
      FSB.closeModal();
      FSB.toast("用户已添加", "success");
      await renderUsers(1);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function toggleUserUnlock(uid, unlocked) {
    try {
      await FSB.api("/api/users/" + uid + "/unlock", {
        method: "POST",
        body: { unlocked: unlocked },
      });
      FSB.toast("用户解锁状态已更新", "success");
      await renderUsers(1);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function banUser(uid) {
    const reason = prompt("请输入封禁原因（选填）：", "违反规则");
    if (reason === null) return;
    try {
      await FSB.api("/api/ban", { method: "POST", body: { user_id: uid, reason: reason } });
      FSB.toast("已封禁用户 " + uid, "success");
      await renderUsers(1);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function unbanUser(uid) {
    try {
      await FSB.api("/api/unban", { method: "POST", body: { user_id: uid } });
      FSB.toast("已解封用户 " + uid, "success");
      await renderUsers(1);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function deleteUserRecord(uid) {
    if (!confirm("确定要彻底删除用户 " + uid + " 的记录吗？")) return;
    try {
      await FSB.api("/api/users/" + uid, { method: "DELETE" });
      FSB.toast("已删除用户记录", "success");
      await renderUsers(1);
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function renderCDKeys() {
    if (!ensureTenantSelected()) return;
    const container = document.getElementById("page-content");
    const data = await FSB.api("/api/cdkeys");
    const items = data.items || [];

    const rows = items
      .map(function (c) {
        const used = c.used_by !== null && c.used_by !== undefined;
        return `
          <tr>
            <td><code>${FSB.esc(c.code)}</code></td>
            <td>${FSB.esc(c.note || "-")}</td>
            <td>${used ? '<span class="badge badge-secondary">已使用 (' + FSB.esc(c.used_by) + ")</span>" : '<span class="badge badge-success">未使用</span>'}</td>
            <td>${FSB.formatDate(c.created_at)}</td>
            <td>${c.used_at ? FSB.formatDate(c.used_at) : "-"}</td>
            <td>
              <button class="btn btn-sm btn-danger" onclick="window.FSB.deleteCDKey('${FSB.esc(c.code)}')">删除</button>
            </td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <div class="card-title">卡密 (CDKey) 管理 (${FSB.formatNumber(items.length)})</div>
          <div class="btn-group">
            <input type="number" id="cdk-gen-count" class="form-control form-control-sm" style="width:100px;" value="10" min="1" max="200" />
            <input type="text" id="cdk-gen-note" class="form-control form-control-sm" style="width:160px;" placeholder="备注(选填)" />
            <button class="btn btn-sm btn-primary" onclick="window.FSB.generateCDKeys()">批量生成卡密</button>
          </div>
        </div>
        <div class="card-body">
          <div class="table-responsive">
            <table class="table">
              <thead>
                <tr>
                  <th>卡密代码 (Code)</th>
                  <th>备注</th>
                  <th>状态 / 使用者</th>
                  <th>创建时间</th>
                  <th>使用时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                ${rows || '<tr><td colspan="6" class="text-muted">暂无卡密数据</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }

  async function generateCDKeys() {
    const count = Number((document.getElementById("cdk-gen-count") || {}).value || 10);
    const note = ((document.getElementById("cdk-gen-note") || {}).value || "").trim();
    try {
      const res = await FSB.api("/api/cdkeys", { method: "POST", body: { count: count, note: note } });
      FSB.toast("成功生成 " + (res.codes || []).length + " 张卡密", "success");
      await renderCDKeys();
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function deleteCDKey(code) {
    try {
      await FSB.api("/api/cdkeys/" + encodeURIComponent(code), { method: "DELETE" });
      FSB.toast("卡密已删除", "success");
      await renderCDKeys();
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function renderBroadcast() {
    if (!ensureTenantSelected()) return;
    const container = document.getElementById("page-content");
    container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <div class="card-title">向租户全体用户广播消息</div>
        </div>
        <div class="card-body">
          <div class="form-group">
            <label>广播内容 (支持 HTML 格式标签)</label>
            <textarea id="broadcast-msg-input" class="form-control" style="min-height:140px;" placeholder="请输入要广播给所有用户的消息内容..."></textarea>
          </div>
          <button class="btn btn-primary" onclick="window.FSB.submitBroadcast()">立即发送广播</button>
          <div id="broadcast-status-box" style="margin-top:16px;"></div>
        </div>
      </div>
    `;
  }

  async function submitBroadcast() {
    const msg = (document.getElementById("broadcast-msg-input") || {}).value || "";
    if (!msg.trim()) {
      FSB.toast("请输入广播内容", "error");
      return;
    }
    try {
      const res = await FSB.api("/api/broadcast", { method: "POST", body: { message: msg } });
      FSB.toast("广播任务已启动: " + res.job_id, "success");
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  const _tenantExports = {
    dashboard: renderDashboard,
    settings: renderSettings,
    shares: renderShares,
    users: renderUsers,
    cdkeys: renderCDKeys,
    broadcast: renderBroadcast,
    renderDashboard: renderDashboard,
    restartTenantBot: restartTenantBot,
    renderSettings: renderSettings,
    renderShares: renderShares,
    searchShares: searchShares,
    openCreateShareModal: openCreateShareModal,
    submitCreateShare: submitCreateShare,
    openEditShareModal: openEditShareModal,
    submitEditShare: submitEditShare,
    deleteShare: deleteShare,
    renderUsers: renderUsers,
    searchUsers: searchUsers,
    openAddUserModal: openAddUserModal,
    submitAddUser: submitAddUser,
    toggleUserUnlock: toggleUserUnlock,
    banUser: banUser,
    unbanUser: unbanUser,
    deleteUserRecord: deleteUserRecord,
    renderCDKeys: renderCDKeys,
    generateCDKeys: generateCDKeys,
    deleteCDKey: deleteCDKey,
    renderBroadcast: renderBroadcast,
    submitBroadcast: submitBroadcast,
  };
  Object.assign(window.FSB, _tenantExports);
  window.Tenant = Object.assign(window.Tenant || {}, _tenantExports);
})();
