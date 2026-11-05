(function () {
  window.FSB = window.FSB || {};
  const FSB = window.FSB;

  async function refreshAdminTenantSelector() {
    if (FSB.sessionRole !== "platform") return;
    try {
      const data = await FSB.api("/api/platform/tenants");
      const list = data.tenants || [];
      FSB.cachedTenants = list;
      const sel = document.getElementById("admin-tenant-select");
      if (!sel) return;
      if (!FSB.selectedTenantId && list.length > 0) {
        FSB.selectedTenantId = list[0].id;
        FSB.safeSetStorage("fsb_selected_tenant_id", FSB.selectedTenantId);
      }
      const options = ['<option value="">-- 请选择要管理的租户 --</option>'];
      list.forEach(function (t) {
        const selected = t.id === FSB.selectedTenantId ? "selected" : "";
        options.push(
          `<option value="${FSB.esc(t.id)}" ${selected}>${FSB.esc(t.display_name || t.slug)} (${FSB.esc(t.slug)})</option>`
        );
      });
      sel.innerHTML = options.join("");
      updateSelectedTenantBadge();
    } catch (_) {}
  }

  function updateSelectedTenantBadge() {
    const ind = document.getElementById("current-tenant-indicator");
    if (!ind) return;
    if (FSB.sessionRole !== "platform" || !FSB.selectedTenantId) {
      ind.classList.add("hidden");
      return;
    }
    const found = (FSB.cachedTenants || []).find(function (t) {
      return t.id === FSB.selectedTenantId;
    });
    if (found) {
      ind.textContent = "正在管理租户: " + (found.display_name || found.slug) + " (" + found.slug + ")";
      ind.classList.remove("hidden");
    } else {
      ind.classList.add("hidden");
    }
  }

  function onAdminTenantSelect(tenantId) {
    FSB.selectedTenantId = tenantId || "";
    FSB.safeSetStorage("fsb_selected_tenant_id", FSB.selectedTenantId);
    updateSelectedTenantBadge();
    if (FSB.currentPage && FSB.currentPage !== "admin_tenants" && FSB.currentPage !== "admin_security") {
      FSB.loadPage(FSB.currentPage);
    }
  }

  function selectAndManageTenant(tenantId, targetPage) {
    FSB.selectedTenantId = tenantId;
    FSB.safeSetStorage("fsb_selected_tenant_id", tenantId);
    const sel = document.getElementById("admin-tenant-select");
    if (sel) sel.value = tenantId;
    updateSelectedTenantBadge();
    FSB.loadPage(targetPage || "settings");
  }

  async function renderAdminTenantsOverview() {
    const container = document.getElementById("page-content");
    await refreshAdminTenantSelector();
    const list = FSB.cachedTenants || [];

    let totalUsers = 0;
    let totalShares = 0;
    let totalCdkeys = 0;
    list.forEach(function (t) {
      const c = t.counts || {};
      totalUsers += Number(c.users || 0);
      totalShares += Number(c.shares || 0);
      totalCdkeys += Number(c.cdkeys || 0);
    });

    const rows = list
      .map(function (t) {
        const c = t.counts || {};
        const rt = t.runtime || {};
        const isCurrent = t.id === FSB.selectedTenantId;
        const statusBadge =
          rt.status === "running"
            ? '<span class="badge badge-success">运行中</span>'
            : t.enabled
            ? '<span class="badge badge-warning">' + FSB.esc(rt.status || "启动中") + "</span>"
            : '<span class="badge badge-secondary">已停用</span>';

        return `
          <tr style="${isCurrent ? "background:rgba(37,99,235,0.1);" : ""}">
            <td>
              <strong>${FSB.esc(t.display_name || t.slug)}</strong>
              <div class="text-muted">标识: <code>${FSB.esc(t.slug)}</code></div>
            </td>
            <td>
              <div>Bot: ${t.bot_username ? "@" + FSB.esc(t.bot_username) : "未设置"}</div>
              <div class="text-muted">Owner: ${FSB.esc(t.owner_id || "未设置")} | 频道: ${FSB.esc(t.db_channel_id || "未设置")}</div>
            </td>
            <td>${statusBadge}</td>
            <td>${FSB.formatNumber(c.users || 0)}</td>
            <td>${FSB.formatNumber(c.shares || 0)}</td>
            <td>${FSB.formatNumber(c.cdkeys || 0)}</td>
            <td>${FSB.formatNumber(c.banned || 0)}</td>
            <td>
              <div class="btn-group">
                <button class="btn btn-sm btn-primary" onclick="window.FSB.selectAndManageTenant('${FSB.esc(t.id)}', 'settings')">管理全部设置</button>
                <button class="btn btn-sm btn-secondary" onclick="window.FSB.selectAndManageTenant('${FSB.esc(t.id)}', 'shares')">管理分享数据</button>
                <button class="btn btn-sm btn-secondary" onclick="window.FSB.selectAndManageTenant('${FSB.esc(t.id)}', 'users')">管理用户</button>
                <button class="btn btn-sm btn-secondary" onclick="window.FSB.selectAndManageTenant('${FSB.esc(t.id)}', 'cdkeys')">管理卡密</button>
                ${
                  t.enabled
                    ? `<button class="btn btn-sm btn-warning" onclick="window.FSB.toggleTenantEnable('${FSB.esc(t.id)}', false)">停用</button>`
                    : `<button class="btn btn-sm btn-success" onclick="window.FSB.toggleTenantEnable('${FSB.esc(t.id)}', true)">启用</button>`
                }
                <button class="btn btn-sm btn-danger" onclick="window.FSB.deleteTenantByAdmin('${FSB.esc(t.id)}', '${FSB.esc(t.slug)}')">删除租户</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">注册租户总数</div>
          <div class="stat-value">${FSB.formatNumber(list.length)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">全平台总用户数</div>
          <div class="stat-value">${FSB.formatNumber(totalUsers)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">全平台总分享数据</div>
          <div class="stat-value">${FSB.formatNumber(totalShares)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">全平台总卡密数</div>
          <div class="stat-value">${FSB.formatNumber(totalCdkeys)}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div class="card-title">所有租户列表 — 点击对应按钮即可直接管理该租户的所有设置项与所有数据</div>
        </div>
        <div class="card-body">
          <div class="table-responsive">
            <table class="table">
              <thead>
                <tr>
                  <th>租户名称 / 账号标识</th>
                  <th>机器人与频道参数</th>
                  <th>运行状态</th>
                  <th>用户数</th>
                  <th>分享数</th>
                  <th>卡密数</th>
                  <th>封禁数</th>
                  <th>全方位管理操作</th>
                </tr>
              </thead>
              <tbody>
                ${rows || '<tr><td colspan="8" class="text-muted">暂无已注册的租户（租户可在主域名首页自行注册）</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }

  async function toggleTenantEnable(tenantId, enable) {
    try {
      const action = enable ? "enable" : "disable";
      await FSB.api("/api/platform/tenants/" + encodeURIComponent(tenantId) + "/" + action, {
        method: "POST",
      });
      FSB.toast(enable ? "已启用该租户" : "已停用该租户", "success");
      await renderAdminTenantsOverview();
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function deleteTenantByAdmin(tenantId, slug) {
    if (!confirm("确定要彻底删除租户 " + slug + " 及其名下所有用户、分享、卡密数据吗？此操作不可恢复！")) {
      return;
    }
    try {
      await FSB.api("/api/platform/tenants/" + encodeURIComponent(tenantId), {
        method: "DELETE",
      });
      if (FSB.selectedTenantId === tenantId) {
        FSB.selectedTenantId = "";
        FSB.safeSetStorage("fsb_selected_tenant_id", "");
      }
      FSB.toast("已删除租户 " + slug, "success");
      await renderAdminTenantsOverview();
    } catch (err) {
      FSB.toast(err.message, "error");
    }
  }

  async function renderAdminSecuritySettings() {
    const container = document.getElementById("page-content");
    const cfg = await FSB.api("/api/admin/config");
    const currentOrigin = window.location.origin;

    container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <div class="card-title">管理员系统安全与入口设置</div>
        </div>
        <div class="card-body">
          <form id="admin-config-form">
            <div class="form-group">
              <label>当前管理员专属登录入口地址</label>
              <input type="text" class="form-control" value="${FSB.esc(currentOrigin + "/" + (cfg.admin_path || "admin"))}" disabled />
              <div class="form-hint">主域名首页 (/) 仅显示租户登录与注册，管理员需通过上方带后缀的地址进入管理员登录界面。</div>
            </div>
            <div class="form-group">
              <label for="adm-cfg-path">管理员登录入口路径后缀 (默认 admin)</label>
              <input type="text" id="adm-cfg-path" class="form-control" value="${FSB.esc(cfg.admin_path || "admin")}" required />
              <div class="form-hint">例如填写 <code>superadmin</code>，则管理员登录地址变为 <code>${FSB.esc(currentOrigin)}/superadmin</code></div>
            </div>
            <div class="form-group">
              <label for="adm-cfg-id">管理员登录 ID (默认 admin)</label>
              <input type="text" id="adm-cfg-id" class="form-control" value="${FSB.esc(cfg.admin_id || "admin")}" required />
            </div>
            <div class="form-group">
              <label for="adm-cfg-pw">修改管理员登录密码 (留空则保持原密码不变)</label>
              <input type="password" id="adm-cfg-pw" class="form-control" placeholder="输入新管理员密码（留空不修改）" />
            </div>
            <button type="submit" class="btn btn-primary">保存管理员设置</button>
          </form>
        </div>
      </div>
    `;

    const form = document.getElementById("admin-config-form");
    if (form) {
      form.addEventListener("submit", async function (e) {
        e.preventDefault();
        const newPath = document.getElementById("adm-cfg-path").value.trim().replace(/^\/+|\/+$/g, "");
        const newId = document.getElementById("adm-cfg-id").value.trim();
        const newPw = document.getElementById("adm-cfg-pw").value;
        const body = { admin_path: newPath, admin_id: newId };
        if (newPw && newPw !== "") {
          body.password = newPw;
        }
        try {
          const res = await FSB.api("/api/admin/config", { method: "PUT", body: body });
          FSB.toast("管理员设置已更新！新入口: /" + res.admin_path, "success");
          if (res.admin_path && window.location.pathname.replace(/^\/+|\/+$/g, "") !== res.admin_path) {
            window.history.replaceState({}, "", "/" + res.admin_path);
          }
          await renderAdminSecuritySettings();
        } catch (err) {
          FSB.toast(err.message, "error");
        }
      });
    }
  }

  Object.assign(window.FSB, {
    refreshAdminTenantSelector: refreshAdminTenantSelector,
    onAdminTenantSelect: onAdminTenantSelect,
    selectAndManageTenant: selectAndManageTenant,
    renderAdminTenantsOverview: renderAdminTenantsOverview,
    toggleTenantEnable: toggleTenantEnable,
    deleteTenantByAdmin: deleteTenantByAdmin,
    renderAdminSecuritySettings: renderAdminSecuritySettings,
  });
})();
