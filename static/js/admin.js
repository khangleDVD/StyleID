/**
 * admin.js — Trang quản trị: users, history, payments, policies, site config.
 * Depends on core.js.
 */
// ---------- Admin ----------
let adminActiveTab = 'users';
let adminUsersPage = 1;
let adminHistPage = 1;
let adminPaymentsPage = 1;
let adminPaymentStatus = '';
let adminPaymentUserFilter = '';
let adminPaymentSearch = '';
let adminUserSearch = '';
let adminUserStatusFilter = '';
let adminUsersCache = [];
let adminPolicySlug = 'privacy';
let adminPolicyEditLang = 'vi';
let adminPolicyViCache = '';
let adminPolicyEnCache = '';

function adminUrl(path) {
  const sep = path.includes('?') ? '&' : '?';
  return API_BASE + path + sep + 'admin_user_id=' + encodeURIComponent(currentUser.id);
}

async function adminGet(path) {
  const r = await fetch(adminUrl(path));
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || t('js.errAdminApi'));
  return d;
}

function hideAdminGlobalError() {
  const e = el('adminGlobalError');
  if (e) {
    e.textContent = '';
    hide(e);
  }
}

function showAdminGlobalError(msg) {
  const e = el('adminGlobalError');
  if (!e) return;
  e.textContent = msg;
  show(e);
}

function syncAdminShell(page) {
  qs('.app-shell')?.classList.toggle('app-shell--admin', page === 'admin');
}

function updateAdminTopbar() {
  const u = currentUser;
  const nameEl = el('adminV2DisplayName');
  const emailEl = el('adminV2Email');
  const avEl = el('adminV2Avatar');
  if (nameEl) nameEl.textContent = u?.full_name || u?.username || 'Admin';
  if (emailEl) emailEl.textContent = u?.email || u?.username || '—';
  if (avEl) renderUserAvatar(avEl, u);
}

function switchAdminTab(tab) {
  if (!['dash', 'users', 'history', 'payments', 'policies', 'site', 'api', 'landing'].includes(tab)) return;
  adminActiveTab = tab;
  qsAll('.admin-v2-tab, .admin-v2-nav-btn').forEach((b) => {
    b.classList.toggle('is-active', b.dataset.adtab === tab);
  });
  refreshAdminView();
}

function adminV2PaginationHtml(label, page, totalPages, prevId, nextId, prevDisabled, nextDisabled) {
  return (
    '<div class="admin-v2-pagination">' +
    '<span>' +
    label +
    '</span>' +
    '<div class="admin-v2-pagination-btns">' +
    '<button type="button" class="admin-v2-page-btn" id="' +
    prevId +
    '" ' +
    (prevDisabled ? 'disabled' : '') +
    '><span class="material-symbols-outlined">chevron_left</span></button>' +
    '<span class="admin-v2-page-num">' +
    page +
    '</span>' +
    '<button type="button" class="admin-v2-page-btn" id="' +
    nextId +
    '" ' +
    (nextDisabled ? 'disabled' : '') +
    '><span class="material-symbols-outlined">chevron_right</span></button>' +
    '</div></div>'
  );
}

function adminAccountStatusLabel(status) {
  const s = status || 'active';
  if (s === 'pending_delete') return t('admin.accountStatusPending');
  if (s === 'deleted') return t('admin.accountStatusDeleted');
  return t('admin.accountStatusActive');
}

function adminAccountStatusBadgeClass(status) {
  const s = status || 'active';
  if (s === 'pending_delete') return 'admin-badge-pending-delete';
  if (s === 'deleted') return 'admin-badge-deleted';
  return 'admin-badge-active';
}

function adminFormatDateTime(val) {
  if (!val) return '—';
  try {
    const d = new Date(val);
    if (Number.isNaN(d.getTime())) return String(val).slice(0, 16).replace('T', ' ');
    const lang = typeof getLang === 'function' ? getLang() : 'vi';
    return d.toLocaleString(lang === 'en' ? 'en-US' : 'vi-VN', {
      year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch (_) {
    return '—';
  }
}

async function refreshAdminView() {
  const pageEl = el('pageAdmin');
  const mount = el('adminTabContent');
  if (!pageEl || !mount || !pageEl.classList.contains('active') || !isAdmin()) return;
  hideAdminGlobalError();
  try {
    if (adminActiveTab === 'dash') {
      mount.innerHTML = '<p class="admin-v2-loading">' + escapeHtml(t('admin.loadingDash')) + '</p>';
      const d = await adminGet('/api/admin/dashboard');
      mount.innerHTML = `
        <div class="admin-v2-metrics">
          <article class="admin-v2-metric-card">
            <span class="material-symbols-outlined" aria-hidden="true">group</span>
            <span class="admin-v2-metric-value">${d.total_users}</span>
            <span class="admin-v2-metric-label">${escapeHtml(t('admin.metricUsers'))}</span>
          </article>
          <article class="admin-v2-metric-card">
            <span class="material-symbols-outlined" aria-hidden="true">history</span>
            <span class="admin-v2-metric-value">${d.total_history}</span>
            <span class="admin-v2-metric-label">${escapeHtml(t('admin.metricHistory'))}</span>
          </article>
          <article class="admin-v2-metric-card">
            <span class="material-symbols-outlined" aria-hidden="true">bolt</span>
            <span class="admin-v2-metric-value">${d.total_credits_in_system}</span>
            <span class="admin-v2-metric-label">${escapeHtml(t('admin.metricCredits'))}</span>
          </article>
          <article class="admin-v2-metric-card">
            <span class="material-symbols-outlined" aria-hidden="true">event_busy</span>
            <span class="admin-v2-metric-value">${d.pending_delete_users ?? 0}</span>
            <span class="admin-v2-metric-label">${escapeHtml(t('admin.metricPendingDelete'))}</span>
          </article>
        </div>
        <p class="admin-v2-hint">${escapeHtml(t('admin.dashHint'))}</p>`;
    } else if (adminActiveTab === 'users') {
      mount.innerHTML = '<p class="admin-v2-loading">' + escapeHtml(t('admin.loadingUsers')) + '</p>';
      const q = encodeURIComponent(adminUserSearch);
      const statusQs = adminUserStatusFilter ? ('&account_status=' + encodeURIComponent(adminUserStatusFilter)) : '';
      const d = await adminGet(`/api/admin/users?page=${adminUsersPage}&per_page=20&q=${q}${statusQs}`);
      adminUsersCache = d.users || [];
      const rows = (d.users || []).map((u) => {
        const roleClass = u.role === 'admin' ? 'admin-badge-admin' : 'admin-badge-user';
        const g = u.is_google_only ? t('admin.accountGoogle') : t('admin.accountPassword');
        const avCls = u.role === 'admin' ? '' : ' is-muted';
        const acctSt = u.account_status || 'active';
        const stBadge = adminAccountStatusBadgeClass(acctSt);
        const stLabel = adminAccountStatusLabel(acctSt);
        const sched = acctSt === 'pending_delete' ? adminFormatDateTime(u.delete_scheduled_at) : '—';
        const restoreBtn = acctSt === 'pending_delete' || acctSt === 'deleted'
          ? `<button type="button" class="admin-v2-link-btn admin-restore-user" data-uid="${u.id}">${escapeHtml(t('admin.restoreAccount'))}</button>`
          : '';
        const approveBtn = acctSt === 'pending_delete'
          ? `<button type="button" class="admin-v2-link-btn admin-v2-link-btn--danger admin-approve-delete-user" data-uid="${u.id}" ${u.id === currentUser.id ? 'disabled' : ''}>${escapeHtml(t('admin.approveDelete'))}</button>`
          : '';
        return `<tr>
          <td>${u.id}</td>
          <td><div class="admin-v2-user-cell"><span class="admin-v2-user-avatar${avCls}"><span class="material-symbols-outlined">person</span></span><span class="admin-v2-user-name">${escapeHtml(u.username || '')}</span></div></td>
          <td>${escapeHtml(u.full_name || '—')}</td>
          <td><span class="admin-badge ${stBadge}">${escapeHtml(stLabel)}</span></td>
          <td>${escapeHtml(sched)}</td>
          <td><span class="admin-badge ${roleClass}">${escapeHtml(u.role || 'user')}</span></td>
          <td>${u.analysis_credits != null ? u.analysis_credits : '—'}</td>
          <td>${escapeHtml(g)}</td>
          <td class="admin-v2-row-actions">
            <button type="button" class="admin-v2-link-btn admin-edit-user" data-uid="${u.id}">${escapeHtml(t('admin.edit'))}</button>
            ${restoreBtn}
            ${approveBtn}
            <button type="button" class="admin-v2-link-btn admin-v2-link-btn--danger admin-del-user" data-uid="${u.id}" ${u.id === currentUser.id ? 'disabled title="' + escapeHtml(t('admin.cannotDeleteSelf')) + '"' : ''}>${escapeHtml(t('common.delete'))}</button>
          </td>
        </tr>`;
      }).join('');
      const totalPages = Math.max(1, Math.ceil((d.total || 0) / (d.per_page || 20)));
      mount.innerHTML = `
        <div class="admin-v2-toolbar">
          <input type="search" class="admin-v2-input" id="adminUserSearchInput" placeholder="${escapeHtml(t('admin.searchUserPlaceholder'))}" value="${escapeHtml(adminUserSearch)}" style="flex:1;max-width:320px;">
          <select class="admin-v2-input" id="adminUserStatusFilter" style="max-width:220px;">
            <option value="" ${adminUserStatusFilter === '' ? 'selected' : ''}>${escapeHtml(t('admin.filterAllStatus'))}</option>
            <option value="active" ${adminUserStatusFilter === 'active' ? 'selected' : ''}>${escapeHtml(t('admin.accountStatusActive'))}</option>
            <option value="pending_delete" ${adminUserStatusFilter === 'pending_delete' ? 'selected' : ''}>${escapeHtml(t('admin.accountStatusPending'))}</option>
            <option value="deleted" ${adminUserStatusFilter === 'deleted' ? 'selected' : ''}>${escapeHtml(t('admin.accountStatusDeleted'))}</option>
          </select>
          <button type="button" class="admin-v2-btn" id="adminUserSearchBtn"><span class="material-symbols-outlined">search</span>${escapeHtml(t('admin.searchBtn'))}</button>
          <button type="button" class="admin-v2-btn-outline" id="adminUserAddBtn"><span class="material-symbols-outlined">person_add</span>${escapeHtml(t('admin.addUserBtn'))}</button>
        </div>
        <div class="admin-v2-glass-table"><table class="admin-table"><thead><tr>
          <th>${escapeHtml(t('admin.colId'))}</th><th>${escapeHtml(t('admin.colUsername'))}</th><th>${escapeHtml(t('admin.colFullName'))}</th><th>${escapeHtml(t('admin.colAccountStatus'))}</th><th>${escapeHtml(t('admin.deleteScheduledAt'))}</th><th>${escapeHtml(t('admin.colRole'))}</th><th>${escapeHtml(t('admin.colCredits'))}</th><th>${escapeHtml(t('admin.colAccountType'))}</th><th>${escapeHtml(t('admin.colActions'))}</th>
        </tr></thead><tbody>${rows || '<tr><td colspan="9" class="hint">' + escapeHtml(t('admin.noData')) + '</td></tr>'}</tbody></table></div>
        ${adminV2PaginationHtml(escapeHtml(t('admin.pageUsers', { page: d.page, total: totalPages, count: d.total })), d.page, totalPages, 'adminUsersPrev', 'adminUsersNext', d.page <= 1, d.page >= totalPages)}`;
      const searchIn = el('adminUserSearchInput');
      el('adminUserSearchBtn')?.addEventListener('click', () => {
        adminUserSearch = (searchIn && searchIn.value.trim()) || '';
        adminUserStatusFilter = el('adminUserStatusFilter')?.value || '';
        adminUsersPage = 1;
        refreshAdminView();
      });
      el('adminUserStatusFilter')?.addEventListener('change', () => {
        adminUserStatusFilter = el('adminUserStatusFilter')?.value || '';
        adminUsersPage = 1;
        refreshAdminView();
      });
      el('adminUserAddBtn')?.addEventListener('click', () => openAdminCreateUserModal());
      searchIn?.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') {
          adminUserSearch = searchIn.value.trim() || '';
          adminUsersPage = 1;
          refreshAdminView();
        }
      });
      el('adminUsersPrev')?.addEventListener('click', () => { adminUsersPage = Math.max(1, adminUsersPage - 1); refreshAdminView(); });
      el('adminUsersNext')?.addEventListener('click', () => { adminUsersPage = adminUsersPage + 1; refreshAdminView(); });
      mount.querySelectorAll('.admin-edit-user').forEach((b) => {
        b.addEventListener('click', () => openAdminUserModal(parseInt(b.getAttribute('data-uid'), 10)));
      });
      mount.querySelectorAll('.admin-del-user').forEach((b) => {
        b.addEventListener('click', () => adminDeleteUser(parseInt(b.getAttribute('data-uid'), 10)));
      });
      mount.querySelectorAll('.admin-restore-user').forEach((b) => {
        b.addEventListener('click', () => adminRestoreUser(parseInt(b.getAttribute('data-uid'), 10)));
      });
      mount.querySelectorAll('.admin-approve-delete-user').forEach((b) => {
        b.addEventListener('click', () => adminApproveDeleteUser(parseInt(b.getAttribute('data-uid'), 10)));
      });
    } else if (adminActiveTab === 'history') {
      mount.innerHTML = '<p class="admin-v2-loading">' + escapeHtml(t('admin.loadingHistory')) + '</p>';
      const d = await adminGet(`/api/admin/history?page=${adminHistPage}&per_page=25`);
      const items = d.items || [];
      const period = (window.__adminHistPeriod || '30d');
      let stats = null;
      try {
        stats = await adminGet(`/api/admin/history/stats?period=${encodeURIComponent(period)}`);
      } catch (_) {
        stats = null;
      }

      function _niceDayLabel(isoDay) {
        const s = String(isoDay || '');
        return s.length >= 10 ? s.slice(5) : s;
      }

      function buildAreaChartSvg(series, opts) {
        opts = opts || {};
        const title = opts.title || '';
        const w = 720;
        const h = 180;
        const padX = 18;
        const padY = 20;
        const data = Array.isArray(series) ? series : [];
        const vals = data.map(x => Number(x.count) || 0);
        const maxVal = Math.max(1, ...vals);
        const n = Math.max(2, data.length);
        const innerW = w - padX * 2;
        const innerH = h - padY * 2 - 18;
        const step = innerW / (n - 1);
        const pts = data.map((d, i) => {
          const v = Number(d.count) || 0;
          const x = padX + i * step;
          const y = padY + (innerH - (innerH * (v / maxVal)));
          return { x, y, v, label: _niceDayLabel(d.day) };
        });
        const pathLine = pts.map((p, i) => (i === 0 ? `M ${p.x} ${p.y}` : `L ${p.x} ${p.y}`)).join(' ');
        const area = `${pathLine} L ${padX + (pts.length - 1) * step} ${padY + innerH} L ${padX} ${padY + innerH} Z`;
        const grid = [0.25, 0.5, 0.75, 1].map((t) => {
          const y = padY + innerH - innerH * t;
          return `<line x1="${padX}" y1="${y}" x2="${w - padX}" y2="${y}" stroke="rgba(255,255,255,0.06)"/>`;
        }).join('');
        return `
          <div class="admin-chart-block" style="padding:1rem 1.1rem;">
            <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:0.75rem;flex-wrap:wrap;">
              <h3 style="margin:0;color:var(--adm-on-surface);font-size:1rem;font-weight:800;">${escapeHtml(title)}</h3>
              <span class="hint" style="margin:0;">Max: ${escapeHtml(String(maxVal))}</span>
            </div>
            <svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="${escapeHtml(title)}">
              <defs>
                <linearGradient id="histAreaGrad" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stop-color="rgba(109,75,217,0.45)"/>
                  <stop offset="100%" stop-color="rgba(109,75,217,0.05)"/>
                </linearGradient>
                <linearGradient id="histLineGrad" x1="0" x2="1" y1="0" y2="0">
                  <stop offset="0%" stop-color="rgba(109,75,217,1)"/>
                  <stop offset="100%" stop-color="rgba(59,130,246,0.95)"/>
                </linearGradient>
              </defs>
              ${grid}
              <path d="${area}" fill="url(#histAreaGrad)" stroke="none"/>
              <path d="${pathLine}" fill="none" stroke="url(#histLineGrad)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
              ${pts.map((p) => `<circle cx="${p.x}" cy="${p.y}" r="3.5" fill="rgba(232,197,71,0.95)" stroke="rgba(0,0,0,0.35)"/>`).join('')}
              ${pts.map((p, i) => (i === 0 || i === pts.length - 1 || i % 3 === 0)
                ? `<text x="${p.x}" y="${padY + innerH + 16}" text-anchor="middle" fill="var(--text-muted)" font-size="10">${escapeHtml(p.label)}</text>`
                : '').join('')}
            </svg>
          </div>
        `;
      }

      function buildHBarChart(list, opts) {
        opts = opts || {};
        const title = opts.title || '';
        const rows = Array.isArray(list) ? list.slice(0, 10) : [];
        const maxVal = Math.max(1, ...rows.map(x => Number(x.count) || 0));
        const bars = rows.map((r) => {
          const v = Number(r.count) || 0;
          const pct = Math.round((v / maxVal) * 100);
          return `
            <div style="display:grid;grid-template-columns:minmax(140px, 1fr) 5fr minmax(34px, 50px);gap:0.65rem;align-items:center;">
              <div style="min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text);font-weight:600;" title="${escapeHtml(r.style || '')}">
                ${escapeHtml(r.style || '—')}
              </div>
              <div style="height:10px;border-radius:999px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08);overflow:hidden;">
                <div style="height:100%;width:${pct}%;border-radius:999px;background:linear-gradient(90deg, rgba(109,75,217,0.95), rgba(59,130,246,0.9));"></div>
              </div>
              <div style="text-align:right;color:var(--text-muted);font-weight:700;">${escapeHtml(String(v))}</div>
            </div>
          `;
        }).join('');
        return `
          <div class="admin-chart-block" style="padding:1rem 1.1rem;">
            <h3 style="margin:0 0 0.75rem;color:var(--adm-on-surface);font-size:1rem;font-weight:800;">${escapeHtml(title)}</h3>
            <div style="display:grid;gap:0.55rem;">
              ${bars || '<p class="hint" style="margin:0;">' + escapeHtml(t('stats.noStyleData')) + '</p>'}
            </div>
          </div>
        `;
      }

      function renderAdminHistoryStatsBlock(statsObj) {
        const p = (statsObj && statsObj.period) ? statsObj.period : period;
        const byDay = (statsObj && Array.isArray(statsObj.by_day)) ? statsObj.by_day : [];
        const topStyles = (statsObj && Array.isArray(statsObj.top_styles)) ? statsObj.top_styles : [];
        const scanned = statsObj && statsObj.total_rows_scanned != null ? statsObj.total_rows_scanned : null;
        return `
          <div class="admin-chart-block" style="padding:1rem 1.1rem;margin-bottom:1rem;">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:0.75rem;flex-wrap:wrap;">
              <div>
                <h3 style="margin:0;color:var(--text);font-size:1rem;font-weight:900;">${escapeHtml(t('admin.histStatsTitle'))}</h3>
                <p class="hint" style="margin:0.25rem 0 0;">${scanned != null ? escapeHtml(t('admin.histScanned', { n: scanned })) : ''}</p>
              </div>
              <div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;">
                <label class="hint" style="margin:0;">${escapeHtml(t('admin.periodLabel'))}</label>
                <select id="adminHistPeriod" class="admin-v2-input" style="min-width:160px;">
                  <option value="7d" ${p === '7d' ? 'selected' : ''}>${escapeHtml(t('admin.period7d'))}</option>
                  <option value="30d" ${p === '30d' ? 'selected' : ''}>${escapeHtml(t('admin.period30d'))}</option>
                  <option value="all" ${p === 'all' ? 'selected' : ''}>${escapeHtml(t('admin.periodAll'))}</option>
                </select>
              </div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:1fr;gap:1rem;margin-bottom:1rem;">
            ${buildAreaChartSvg(byDay, { title: t('admin.chartAnalysesPerDay') })}
            ${buildHBarChart(topStyles, { title: t('admin.chartTopStyles') })}
          </div>
        `;
      }

      const rows = items.map((h) => {
        const timeStr = h.timestamp ? new Date(h.timestamp).toLocaleString('vi-VN') : '';
        return `<tr>
          <td>${h.id}</td>
          <td>${h.user_id != null ? h.user_id : '—'}</td>
          <td>${escapeHtml(h.username || '—')}</td>
          <td>${escapeHtml(timeStr)}</td>
          <td class="admin-v2-row-actions"><button type="button" class="admin-v2-link-btn admin-v2-link-btn--danger admin-del-hist" data-hid="${h.id}">${escapeHtml(t('common.delete'))}</button></td>
        </tr>`;
      }).join('');
      const totalPages = Math.max(1, Math.ceil((d.total || 0) / (d.per_page || 25)));
      mount.innerHTML = `
        ${renderAdminHistoryStatsBlock(stats)}
        <div class="admin-v2-glass-table"><table class="admin-table"><thead><tr>
          <th>${escapeHtml(t('admin.colId'))}</th><th>User ID</th><th>${escapeHtml(t('admin.colUsername'))}</th><th>${escapeHtml(t('admin.colTime'))}</th><th>${escapeHtml(t('admin.colActions'))}</th>
        </tr></thead><tbody>${rows || '<tr><td colspan="5" class="hint">' + escapeHtml(t('admin.noRecords')) + '</td></tr>'}</tbody></table></div>
        ${adminV2PaginationHtml(escapeHtml(t('admin.pageOf', { page: d.page, total: totalPages })), d.page, totalPages, 'adminHistPrev', 'adminHistNext', d.page <= 1, d.page >= totalPages)}`;
      el('adminHistPeriod')?.addEventListener('change', () => {
        window.__adminHistPeriod = el('adminHistPeriod').value || '30d';
        adminHistPage = 1;
        refreshAdminView();
      });
      el('adminHistPrev')?.addEventListener('click', () => { adminHistPage = Math.max(1, adminHistPage - 1); refreshAdminView(); });
      el('adminHistNext')?.addEventListener('click', () => { adminHistPage = adminHistPage + 1; refreshAdminView(); });
      mount.querySelectorAll('.admin-del-hist').forEach((b) => {
        b.addEventListener('click', () => adminDeleteHistory(parseInt(b.getAttribute('data-hid'), 10)));
      });
    } else if (adminActiveTab === 'payments') {
      mount.innerHTML = '<p class="admin-v2-loading">' + escapeHtml(t('admin.loadingPayments')) + '</p>';
      const qs = new URLSearchParams();
      qs.set('page', String(adminPaymentsPage));
      qs.set('per_page', '25');
      if (adminPaymentStatus) qs.set('status', adminPaymentStatus);
      if (adminPaymentUserFilter) qs.set('user_id', String(adminPaymentUserFilter));
      if (adminPaymentSearch) qs.set('q', adminPaymentSearch);
      const d = await adminGet(`/api/admin/payments?${qs.toString()}`);
      const items = d.items || [];
      const rows = items.map((p) => {
        const st = (p.status || '').toString();
        const stClass = st === 'completed' ? 'admin-badge-admin' : (st === 'failed' ? 'admin-badge-user' : 'admin-badge-user');
        const created = p.created_at ? new Date(p.created_at).toLocaleString('vi-VN') : '';
        const memo = escapeHtml(p.transfer_content || '');
        const sepay = p.sepay_tx_id ? escapeHtml(String(p.sepay_tx_id)) : '—';
        return `<tr>
          <td>${p.id}</td>
          <td>${p.user_id}</td>
          <td>${escapeHtml(p.username || '—')}</td>
          <td>${escapeHtml(String(p.package_id || ''))}</td>
          <td>${p.credits != null ? p.credits : '—'}</td>
          <td>${formatVnd(p.amount_vnd)}</td>
          <td><span class="admin-badge ${stClass}">${escapeHtml(st || '—')}</span></td>
          <td>${escapeHtml(created)}</td>
          <td>${sepay}</td>
          <td class="hint" style="max-width:360px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${memo}">${memo}</td>
        </tr>`;
      }).join('');
      const totalPages = Math.max(1, Math.ceil((d.total || 0) / (d.per_page || 25)));
      mount.innerHTML = `
        <div class="admin-v2-toolbar">
          <select id="adminPayStatus" class="admin-v2-input" style="min-width:170px;">
            <option value="" ${!adminPaymentStatus ? 'selected' : ''}>${escapeHtml(t('admin.payStatusAll'))}</option>
            <option value="pending" ${adminPaymentStatus === 'pending' ? 'selected' : ''}>pending</option>
            <option value="completed" ${adminPaymentStatus === 'completed' ? 'selected' : ''}>completed</option>
            <option value="failed" ${adminPaymentStatus === 'failed' ? 'selected' : ''}>failed</option>
          </select>
          <input type="number" class="admin-v2-input" id="adminPayUserId" placeholder="${escapeHtml(t('admin.payFilterUser'))}" value="${escapeHtml(adminPaymentUserFilter)}" style="width:150px;">
          <input type="search" class="admin-v2-input" id="adminPaySearch" placeholder="${escapeHtml(t('admin.paySearchPlaceholder'))}" value="${escapeHtml(adminPaymentSearch)}" style="flex:1;max-width:360px;">
          <button type="button" class="admin-v2-btn" id="adminPayFilterBtn"><span class="material-symbols-outlined">filter_alt</span>${escapeHtml(t('admin.payFilterBtn'))}</button>
        </div>
        <div class="admin-v2-glass-table"><table class="admin-table"><thead><tr>
          <th>${escapeHtml(t('admin.colId'))}</th><th>${escapeHtml(t('admin.colUser'))}</th><th>${escapeHtml(t('admin.colUsername'))}</th><th>${escapeHtml(t('admin.colPlan'))}</th><th>${escapeHtml(t('admin.colCredits'))}</th><th>${escapeHtml(t('admin.colAmount'))}</th><th>${escapeHtml(t('admin.colStatus'))}</th><th>${escapeHtml(t('admin.colCreated'))}</th><th>${escapeHtml(t('admin.colSePay'))}</th><th>${escapeHtml(t('admin.colMemo'))}</th>
        </tr></thead><tbody>${rows || '<tr><td colspan="10" class="hint">' + escapeHtml(t('admin.noRecords')) + '</td></tr>'}</tbody></table></div>
        ${adminV2PaginationHtml(escapeHtml(t('admin.pageInvoices', { page: d.page, total: totalPages, count: d.total })), d.page, totalPages, 'adminPayPrev', 'adminPayNext', d.page <= 1, d.page >= totalPages)}`;
      el('adminPayFilterBtn')?.addEventListener('click', () => {
        adminPaymentStatus = (el('adminPayStatus') && el('adminPayStatus').value) || '';
        adminPaymentUserFilter = (el('adminPayUserId') && el('adminPayUserId').value.trim()) || '';
        adminPaymentSearch = (el('adminPaySearch') && el('adminPaySearch').value.trim()) || '';
        adminPaymentsPage = 1;
        refreshAdminView();
      });
      el('adminPayPrev')?.addEventListener('click', () => { adminPaymentsPage = Math.max(1, adminPaymentsPage - 1); refreshAdminView(); });
      el('adminPayNext')?.addEventListener('click', () => { adminPaymentsPage = adminPaymentsPage + 1; refreshAdminView(); });
    } else if (adminActiveTab === 'policies') {
      mount.innerHTML = '<p class="admin-v2-loading">' + escapeHtml(t('admin.loadingPolicies')) + '</p>';
      const list = await adminGet('/api/admin/policies');
      const items = list.items || [];
      if (!items.some((p) => p.slug === adminPolicySlug)) {
        adminPolicySlug = items[0]?.slug || 'privacy';
      }
      const currentItem = items.find((p) => p.slug === adminPolicySlug) || items[0];
      const isContact = adminPolicySlug === 'site_contact';
      const lang = typeof getLang === 'function' ? getLang() : 'vi';
      const opts = items.map((p) => {
        const label = lang === 'en' ? (p.label_en || p.label_vi) : (p.label_vi || p.label_en);
        return '<option value="' + escapeHtml(p.slug) + '"' + (p.slug === adminPolicySlug ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
      }).join('');
      const previewUrl = currentItem?.url || '#';
      if (isContact) {
        const contact = await adminGet('/api/admin/site-contact');
        mount.innerHTML = adminPolicyContactPanelHtml(opts, contact, previewUrl);
        el('adminPolicySelect')?.addEventListener('change', () => {
          adminPolicySlug = el('adminPolicySelect').value || 'privacy';
          refreshAdminView();
        });
        el('adminPolicyReload')?.addEventListener('click', () => refreshAdminView());
        el('adminPolicySave')?.addEventListener('click', () => adminSaveSiteContact());
      } else {
        const detail = await adminGet('/api/admin/policies/' + encodeURIComponent(adminPolicySlug));
        mount.innerHTML = `
        <p class="admin-v2-hint admin-v2-policy-hint">${escapeHtml(t('admin.policiesHint'))}</p>
        <div class="admin-v2-toolbar admin-v2-policy-toolbar">
          <label class="admin-v2-policy-label" for="adminPolicySelect">${escapeHtml(t('admin.policySelect'))}</label>
          <select id="adminPolicySelect" class="admin-v2-input admin-v2-policy-select">${opts}</select>
          <div class="admin-v2-policy-lang" role="group" aria-label="${escapeHtml(t('admin.policyLang'))}">
            <button type="button" class="admin-v2-btn-outline admin-v2-policy-lang-btn is-active" data-policy-lang="vi">VI</button>
            <button type="button" class="admin-v2-btn-outline admin-v2-policy-lang-btn" data-policy-lang="en">EN</button>
          </div>
          <a href="${escapeHtml(detail.url || '#')}" target="_blank" rel="noopener noreferrer" class="admin-v2-btn-outline" id="adminPolicyOpenPage">
            <span class="material-symbols-outlined">open_in_new</span>${escapeHtml(t('admin.policyPreview'))}
          </a>
          <button type="button" class="admin-v2-btn-outline" id="adminPolicyToggleSource" title="${escapeHtml(t('admin.policySourceMode'))}">
            <span class="material-symbols-outlined">code</span>${escapeHtml(t('admin.policySourceMode'))}
          </button>
          <button type="button" class="admin-v2-btn-outline" id="adminPolicyReload">
            <span class="material-symbols-outlined">refresh</span>${escapeHtml(t('admin.policyReload'))}
          </button>
          <button type="button" class="admin-v2-btn" id="adminPolicySave">
            <span class="material-symbols-outlined">save</span>${escapeHtml(t('admin.save'))}
          </button>
        </div>
        <p id="adminPolicyStatus" class="admin-v2-policy-status hidden" role="status"></p>
        ${adminPolicyFormatToolbarHtml()}
        <div class="admin-v2-policy-editor-wrap" id="adminPolicyEditorWrap">
          <div id="adminPolicyVisualEditor" class="admin-v2-policy-visual legal-document" contenteditable="true" role="textbox" aria-multiline="true" aria-label="${escapeHtml(t('admin.policyVisualEdit'))}" spellcheck="true"></div>
          <textarea id="adminPolicyContent" class="admin-v2-policy-editor admin-v2-policy-editor--source" spellcheck="false" aria-label="${escapeHtml(t('admin.policyContent'))}"></textarea>
        </div>`;
        adminPolicyEditLang = 'vi';
        adminPolicyViCache = detail.vi_content || detail.content || '';
        adminPolicyEnCache = detail.en_content || '';
        adminPolicyInitEditor(adminPolicyViCache, { readonly: false });
        bindAdminPolicyLangTabs();
        el('adminPolicySelect')?.addEventListener('change', () => {
          adminPolicySlug = el('adminPolicySelect').value || 'privacy';
          refreshAdminView();
        });
        el('adminPolicyReload')?.addEventListener('click', () => refreshAdminView());
        el('adminPolicySave')?.addEventListener('click', () => adminSavePolicy());
      }
    } else if (adminActiveTab === 'site') {
      mount.innerHTML = '<p class="admin-v2-loading">' + escapeHtml(t('admin.loadingSite')) + '</p>';
      const cfg = await adminGet('/api/admin/site-config');
      mount.innerHTML = adminSiteConfigPanelHtml(cfg);
      el('adminSiteSave')?.addEventListener('click', () => adminSaveSiteConfig());
      el('adminSiteReload')?.addEventListener('click', () => refreshAdminView());
      el('adminSiteLogoUpload')?.addEventListener('click', () => adminUploadSiteLogo());
      el('adminSiteLogoRemove')?.addEventListener('click', () => adminRemoveSiteLogo());
      bindAdminSitePackageActions();
      el('adminSiteLogoFile')?.addEventListener('change', () => {
        const preview = el('adminSiteLogoPreview');
        const file = el('adminSiteLogoFile')?.files?.[0];
        if (preview && file) {
          preview.src = URL.createObjectURL(file);
          preview.classList.remove('hidden');
        }
      });
    } else if (adminActiveTab === 'api') {
      mount.innerHTML = '<p class="admin-v2-loading">' + escapeHtml(t('admin.loadingApi')) + '</p>';
      const cfg = await adminGet('/api/admin/api-config');
      mount.innerHTML = adminApiConfigPanelHtml(cfg);
      el('adminApiSave')?.addEventListener('click', () => adminSaveApiConfig());
      el('adminApiReload')?.addEventListener('click', () => refreshAdminView());
    } else if (adminActiveTab === 'landing') {
      mount.innerHTML = '<p class="admin-v2-loading">' + escapeHtml(t('admin.loadingLanding')) + '</p>';
      const cfg = await adminGet('/api/admin/landing-config');
      adminLandingCache = cfg;
      adminLandingEditLang = 'vi';
      mount.innerHTML = adminLandingConfigPanelHtml(cfg);
      bindAdminLandingPanel();
    }
  } catch (err) {
    showAdminGlobalError(err.message || t('admin.errLoad'));
    mount.innerHTML = '';
  }
}

function openAdminUserModal(userId) {
  const u = adminUsersCache.find((x) => x.id === userId);
  if (!u) return;
  el('adminEditUserId').value = String(u.id);
  el('adminEditUserLabel').textContent = u.username || ('ID ' + u.id);
  el('adminEditFullName').value = u.full_name || '';
  el('adminEditCredits').value = u.analysis_credits != null ? u.analysis_credits : 0;
  el('adminEditRole').value = u.role === 'admin' ? 'admin' : 'user';
  const deleteInfo = el('adminEditDeleteInfo');
  const acctSt = u.account_status || 'active';
  if (deleteInfo) {
    if (acctSt === 'pending_delete' || acctSt === 'deleted') {
      show(deleteInfo);
      const stEl = el('adminEditAccountStatus');
      if (stEl) stEl.textContent = adminAccountStatusLabel(acctSt);
      const reqEl = el('adminEditDeleteRequested');
      if (reqEl) reqEl.textContent = adminFormatDateTime(u.delete_requested_at);
      const schEl = el('adminEditDeleteScheduled');
      if (schEl) schEl.textContent = adminFormatDateTime(u.delete_scheduled_at);
      const reasonEl = el('adminEditDeleteReason');
      if (reasonEl) reasonEl.textContent = (u.delete_reason || '').trim() || '—';
      const restoreBtn = el('adminEditRestoreBtn');
      const approveBtn = el('adminEditApproveDeleteBtn');
      if (restoreBtn) restoreBtn.classList.toggle('hidden', false);
      if (approveBtn) {
        approveBtn.classList.toggle('hidden', acctSt !== 'pending_delete');
        approveBtn.disabled = u.id === currentUser.id;
      }
    } else {
      hide(deleteInfo);
    }
  }
  hide(el('adminEditError'));
  show(el('modalAdminUser'));
}

async function adminRestoreUser(userId) {
  if (!currentUser || !isAdmin() || !userId) return;
  if (!confirm(t('admin.restoreAccountConfirm', { id: userId }))) return;
  try {
    const r = await fetch(API_BASE + '/api/admin/users/' + userId + '/restore-account', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ admin_user_id: currentUser.id }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.restoreAccountError'));
    closeModal('modalAdminUser');
    alert(d.message || t('admin.restoreAccountOk'));
    await refreshAdminView();
  } catch (e) {
    alert(e.message || t('admin.restoreAccountError'));
  }
}

async function adminApproveDeleteUser(userId) {
  if (!currentUser || !isAdmin() || !userId) return;
  if (!confirm(t('admin.approveDeleteConfirm', { id: userId }))) return;
  try {
    const r = await fetch(API_BASE + '/api/admin/users/' + userId + '/approve-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ admin_user_id: currentUser.id }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.approveDeleteError'));
    closeModal('modalAdminUser');
    alert(d.message || t('admin.approveDeleteOk'));
    await refreshAdminView();
  } catch (e) {
    alert(e.message || t('admin.approveDeleteError'));
  }
}

function openAdminCreateUserModal() {
  if (!isAdmin()) return;
  const errEl = el('adminCreateError');
  if (errEl) {
    errEl.textContent = '';
    hide(errEl);
  }
  if (el('adminCreateUsername')) el('adminCreateUsername').value = '';
  if (el('adminCreateFullName')) el('adminCreateFullName').value = '';
  if (el('adminCreatePassword')) el('adminCreatePassword').value = '';
  if (el('adminCreateCredits')) {
    const init = (currentUser && Number.isFinite(Number(currentUser.analysis_credits)))
      ? 0
      : 0;
    el('adminCreateCredits').value = String(init);
  }
  if (el('adminCreateRole')) el('adminCreateRole').value = 'user';
  show(el('modalAdminCreateUser'));
  setTimeout(() => el('adminCreateUsername')?.focus(), 50);
}

async function adminCreateUser() {
  const errEl = el('adminCreateError');
  if (errEl) {
    errEl.textContent = '';
    hide(errEl);
  }
  if (!currentUser || !isAdmin()) return;

  const username = (el('adminCreateUsername')?.value || '').trim();
  const fullName = (el('adminCreateFullName')?.value || '').trim();
  const password = el('adminCreatePassword')?.value || '';
  const role = (el('adminCreateRole')?.value || 'user').trim();
  const creditsRaw = el('adminCreateCredits')?.value;

  let creditsVal = null;
  try {
    creditsVal = creditsRaw == null || String(creditsRaw).trim() === ''
      ? null
      : parseInt(String(creditsRaw), 10);
    if (creditsVal != null && (Number.isNaN(creditsVal) || creditsVal < 0)) throw new Error(t('admin.invalidCredits'));
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message || t('admin.invalidCreditsShort');
      show(errEl);
    }
    return;
  }

  if (!username) {
    if (errEl) { errEl.textContent = t('admin.needUsername'); show(errEl); }
    return;
  }
  if (!password || String(password).length < 6) {
    if (errEl) { errEl.textContent = t('admin.pwdMin6'); show(errEl); }
    return;
  }

  try {
    const r = await fetch(API_BASE + '/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        admin_user_id: currentUser.id,
        username,
        password,
        full_name: fullName || null,
        role,
        analysis_credits: creditsVal,
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errCreateUser'));
    closeModal('modalAdminCreateUser');
    adminUsersPage = 1;
    await refreshAdminView();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message || t('common.error');
      show(errEl);
    } else {
      alert(e.message || t('common.error'));
    }
  }
}

async function adminDeleteUser(userId) {
  if (!isAdmin() || userId === currentUser.id) return;
  if (!confirm(t('admin.deleteUserConfirm', { id: userId }))) return;
  try {
    const r = await fetch(API_BASE + '/api/admin/users/' + userId, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ admin_user_id: currentUser.id }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('js.errDelete'));
    await refreshAdminView();
  } catch (e) {
    alert(e.message || t('common.error'));
  }
}

async function adminDeleteHistory(hid) {
  if (!isAdmin()) return;
  if (!confirm(t('admin.deleteHistConfirm', { id: hid }))) return;
  try {
    const r = await fetch(adminUrl('/api/admin/history/' + hid), { method: 'DELETE' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('js.errDelete'));
    await refreshAdminView();
  } catch (e) {
    alert(e.message || t('common.error'));
  }
}

const ADMIN_POLICY_JINJA_RE = /\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/g;
const ADMIN_POLICY_JINJA_PLACEHOLDER_RE = /\[\[JINJA:(\d+)\]\]/g;
let adminPolicyJinjaTokens = [];

function adminPolicyContactField(id, labelKey, value, opts) {
  opts = opts || {};
  const type = opts.type || 'text';
  const full = opts.full ? ' admin-v2-contact-item--full' : '';
  const rows = opts.rows ? ' rows="' + opts.rows + '"' : '';
  let control = '';
  if (type === 'textarea') {
    control =
      '<textarea id="' + id + '" class="admin-v2-input admin-v2-contact-input"' + rows + '>' +
      escapeHtml(value || '') +
      '</textarea>';
  } else {
    control =
      '<input type="' + type + '" id="' + id + '" class="admin-v2-input admin-v2-contact-input" value="' +
      escapeHtml(value || '') +
      '">';
  }
  return (
    '<div class="admin-v2-contact-item' + full + '">' +
    '<label class="admin-v2-contact-field" for="' + id + '">' +
    escapeHtml(t(labelKey)) +
    '</label>' +
    control +
    '</div>'
  );
}

function adminPolicyContactPanelHtml(opts, contact, previewUrl) {
  contact = contact || {};
  return (
    '<p class="admin-v2-hint admin-v2-policy-hint">' +
    escapeHtml(t('admin.contactHint')) +
    '</p>' +
    '<div class="admin-v2-toolbar admin-v2-policy-toolbar">' +
    '<label class="admin-v2-policy-label" for="adminPolicySelect">' +
    escapeHtml(t('admin.policySelect')) +
    '</label>' +
    '<select id="adminPolicySelect" class="admin-v2-input admin-v2-policy-select">' +
    opts +
    '</select>' +
    '<a href="' +
    escapeHtml(previewUrl || '/support#contact') +
    '" target="_blank" rel="noopener noreferrer" class="admin-v2-btn-outline" id="adminPolicyOpenPage">' +
    '<span class="material-symbols-outlined">open_in_new</span>' +
    escapeHtml(t('admin.contactPreview')) +
    '</a>' +
    '<button type="button" class="admin-v2-btn-outline" id="adminPolicyReload">' +
    '<span class="material-symbols-outlined">refresh</span>' +
    escapeHtml(t('admin.policyReload')) +
    '</button>' +
    '<button type="button" class="admin-v2-btn" id="adminPolicySave">' +
    '<span class="material-symbols-outlined">save</span>' +
    escapeHtml(t('admin.save')) +
    '</button>' +
    '</div>' +
    '<p id="adminPolicyStatus" class="admin-v2-policy-status hidden" role="status"></p>' +
    '<form class="admin-v2-contact-form" id="adminContactForm" onsubmit="return false;">' +
    '<div class="admin-v2-contact-grid">' +
    adminPolicyContactField('adminContactEmail', 'admin.contactEmail', contact.support_email, { type: 'email' }) +
    adminPolicyContactField('adminContactPhone', 'admin.contactPhone', contact.support_phone, { type: 'tel' }) +
    adminPolicyContactField('adminContactTaxId', 'admin.contactTaxId', contact.company_tax_id) +
    adminPolicyContactField('adminContactRep', 'admin.contactRep', contact.company_representative) +
    adminPolicyContactField('adminContactCompanyVi', 'admin.contactCompanyVi', contact.company_name_vi) +
    adminPolicyContactField('adminContactCompanyEn', 'admin.contactCompanyEn', contact.company_name_en) +
    adminPolicyContactField('adminContactAddress', 'admin.contactAddress', contact.company_address, { type: 'textarea', rows: 3, full: true }) +
    '</div>' +
    '</form>'
  );
}

async function adminSaveSiteContact() {
  if (!isAdmin()) return;
  const statusEl = el('adminPolicyStatus');
  const saveBtn = el('adminPolicySave');
  if (statusEl) {
    statusEl.textContent = '';
    hide(statusEl);
  }
  const payload = {
    admin_user_id: currentUser.id,
    support_email: el('adminContactEmail')?.value?.trim() || '',
    support_phone: el('adminContactPhone')?.value?.trim() || '',
    company_tax_id: el('adminContactTaxId')?.value?.trim() || '',
    company_representative: el('adminContactRep')?.value?.trim() || '',
    company_name_vi: el('adminContactCompanyVi')?.value?.trim() || '',
    company_name_en: el('adminContactCompanyEn')?.value?.trim() || '',
    company_address: el('adminContactAddress')?.value?.trim() || '',
  };
  if (saveBtn) saveBtn.disabled = true;
  try {
    const r = await fetch(API_BASE + '/api/admin/site-contact', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errSave'));
    if (statusEl) {
      statusEl.textContent = t('admin.contactSaved');
      statusEl.classList.remove('hidden', 'admin-v2-policy-status--error');
      statusEl.classList.add('admin-v2-policy-status--ok');
      show(statusEl);
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = e.message || t('admin.errSave');
      statusEl.classList.remove('hidden', 'admin-v2-policy-status--ok');
      statusEl.classList.add('admin-v2-policy-status--error');
      show(statusEl);
    } else {
      alert(e.message || t('admin.errSave'));
    }
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

let siteBranding = { app_display_name: 'StyleID', logo_url: '' };
let adminSitePkgSeq = 0;

function adminSitePkgCardHtml(pkg, canRemove) {
  adminSitePkgSeq += 1;
  const id = 'adminPkg' + adminSitePkgSeq;
  pkg = pkg || { key: '', name: '', credits: 10, price_vnd: 5000, popular: false };
  return (
    '<article class="admin-v2-site-pkg-card">' +
    '<div class="admin-v2-site-pkg-card-head">' +
    '<h3 class="admin-v2-site-pkg-title">' + escapeHtml(t('admin.sitePkgCard')) + '</h3>' +
    '<button type="button" class="admin-v2-link-btn admin-v2-link-btn--danger admin-pkg-remove"' +
    (canRemove ? '' : ' disabled') +
    ' title="' + escapeHtml(t('admin.sitePkgRemove')) + '">' +
    '<span class="material-symbols-outlined">delete</span>' + escapeHtml(t('admin.sitePkgRemove')) +
    '</button></div>' +
    '<div class="admin-v2-site-pkg-grid">' +
    '<div class="admin-v2-site-pkg-field">' +
    '<label class="admin-v2-site-pkg-label" for="' + id + 'Key">' + escapeHtml(t('admin.sitePkgKey')) + '</label>' +
    '<input type="text" id="' + id + 'Key" class="admin-v2-input admin-pkg-key" value="' + escapeHtml(pkg.key || '') + '" pattern="[-a-z0-9_]{1,32}" maxlength="32" placeholder="vd: pro100">' +
    '<p class="hint admin-v2-site-pkg-key-hint">' + escapeHtml(t('admin.sitePkgKeyHint')) + '</p>' +
    '</div>' +
    '<div class="admin-v2-site-pkg-field">' +
    '<label class="admin-v2-site-pkg-label" for="' + id + 'Name">' + escapeHtml(t('admin.sitePkgName')) + '</label>' +
    '<input type="text" id="' + id + 'Name" class="admin-v2-input admin-pkg-name" value="' + escapeHtml(pkg.name || '') + '">' +
    '</div>' +
    '<div class="admin-v2-site-pkg-field">' +
    '<label class="admin-v2-site-pkg-label" for="' + id + 'Credits">' + escapeHtml(t('admin.sitePkgCredits')) + '</label>' +
    '<input type="number" id="' + id + 'Credits" class="admin-v2-input admin-pkg-credits" min="1" step="1" value="' + escapeHtml(String(pkg.credits ?? 10)) + '">' +
    '</div>' +
    '<div class="admin-v2-site-pkg-field">' +
    '<label class="admin-v2-site-pkg-label" for="' + id + 'Price">' + escapeHtml(t('admin.sitePkgPrice')) + '</label>' +
    '<input type="number" id="' + id + 'Price" class="admin-v2-input admin-pkg-price" min="0" step="1000" value="' + escapeHtml(String(pkg.price_vnd ?? 0)) + '">' +
    '</div>' +
    '<label class="admin-v2-site-pkg-popular">' +
    '<input type="checkbox" class="admin-pkg-popular"' + (pkg.popular ? ' checked' : '') + '>' +
    '<span>' + escapeHtml(t('admin.sitePkgPopular')) + '</span></label>' +
    '</div></article>'
  );
}

function bindAdminSitePackageActions() {
  const list = el('adminSitePkgList');
  if (!list) return;
  list.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.admin-pkg-remove');
    if (!btn || btn.disabled) return;
    const cards = list.querySelectorAll('.admin-v2-site-pkg-card');
    if (cards.length <= 1) return;
    btn.closest('.admin-v2-site-pkg-card')?.remove();
    adminSyncSitePkgRemoveButtons();
  });
  list.addEventListener('change', (ev) => {
    if (!ev.target.classList.contains('admin-pkg-popular')) return;
    if (!ev.target.checked) return;
    list.querySelectorAll('.admin-pkg-popular').forEach((cb) => {
      if (cb !== ev.target) cb.checked = false;
    });
  });
  el('adminSitePkgAdd')?.addEventListener('click', () => {
    const n = list.querySelectorAll('.admin-v2-site-pkg-card').length;
    list.insertAdjacentHTML('beforeend', adminSitePkgCardHtml({ key: 'goi' + (n + 1), name: '', credits: 10, price_vnd: 5000, popular: false }, true));
    adminSyncSitePkgRemoveButtons();
  });
  adminSyncSitePkgRemoveButtons();
}

function adminSyncSitePkgRemoveButtons() {
  const list = el('adminSitePkgList');
  if (!list) return;
  const cards = list.querySelectorAll('.admin-v2-site-pkg-card');
  cards.forEach((card) => {
    const btn = card.querySelector('.admin-pkg-remove');
    if (btn) btn.disabled = cards.length <= 1;
  });
}

function adminSiteConfigPanelHtml(cfg) {
  cfg = cfg || {};
  adminSitePkgSeq = 0;
  const pkgs = Array.isArray(cfg.packages) ? cfg.packages : [];
  const pkgBlocks = pkgs.map((pkg, i) => adminSitePkgCardHtml(pkg, pkgs.length > 1)).join('');
  const logoPreview = cfg.logo_url
    ? '<img id="adminSiteLogoPreview" class="admin-v2-site-logo-preview" src="' + escapeHtml(cfg.logo_url) + '" alt="Logo">'
    : '<img id="adminSiteLogoPreview" class="admin-v2-site-logo-preview hidden" alt="Logo">';
  return (
    '<p class="admin-v2-hint admin-v2-policy-hint">' + escapeHtml(t('admin.siteHint')) + '</p>' +
    '<div class="admin-v2-toolbar admin-v2-policy-toolbar">' +
    '<button type="button" class="admin-v2-btn-outline" id="adminSiteReload"><span class="material-symbols-outlined">refresh</span>' + escapeHtml(t('admin.policyReload')) + '</button>' +
    '<button type="button" class="admin-v2-btn" id="adminSiteSave"><span class="material-symbols-outlined">save</span>' + escapeHtml(t('admin.save')) + '</button>' +
    '</div>' +
    '<p id="adminSiteStatus" class="admin-v2-policy-status hidden" role="status"></p>' +
    '<section class="admin-v2-site-section">' +
    '<h3 class="admin-v2-site-section-title">' + escapeHtml(t('admin.siteNameSection')) + '</h3>' +
    '<label class="admin-v2-contact-field" for="adminSiteAppName">' + escapeHtml(t('admin.siteAppName')) + '</label>' +
    '<input type="text" id="adminSiteAppName" class="admin-v2-input" value="' + escapeHtml(cfg.app_display_name || '') + '">' +
    '</section>' +
    '<section class="admin-v2-site-section">' +
    '<h3 class="admin-v2-site-section-title">' + escapeHtml(t('admin.siteLogoSection')) + '</h3>' +
    '<div class="admin-v2-site-logo-row">' + logoPreview +
    '<div class="admin-v2-site-logo-actions">' +
    '<input type="file" id="adminSiteLogoFile" accept="image/png,image/jpeg,image/webp" class="admin-v2-site-logo-file">' +
    '<button type="button" class="admin-v2-btn-outline" id="adminSiteLogoUpload"><span class="material-symbols-outlined">upload</span>' + escapeHtml(t('admin.siteLogoUpload')) + '</button>' +
    '<button type="button" class="admin-v2-btn-outline" id="adminSiteLogoRemove"' + (cfg.has_logo ? '' : ' disabled') + '><span class="material-symbols-outlined">delete</span>' + escapeHtml(t('admin.siteLogoRemove')) + '</button>' +
    '<p class="hint">' + escapeHtml(t('admin.siteLogoHint')) + '</p>' +
    '</div></div></section>' +
    '<section class="admin-v2-site-section">' +
    '<div class="admin-v2-site-section-head">' +
    '<h3 class="admin-v2-site-section-title">' + escapeHtml(t('admin.sitePackagesSection')) + '</h3>' +
    '<button type="button" class="admin-v2-btn-outline" id="adminSitePkgAdd"><span class="material-symbols-outlined">add</span>' + escapeHtml(t('admin.sitePkgAdd')) + '</button>' +
    '</div>' +
    '<p class="hint admin-v2-site-pkg-section-hint">' + escapeHtml(t('admin.sitePkgSectionHint')) + '</p>' +
    '<div class="admin-v2-site-pkg-list" id="adminSitePkgList">' + pkgBlocks + '</div>' +
    '</section>'
  );
}

function adminSiteStatus(msg, ok) {
  const statusEl = el('adminSiteStatus');
  if (!statusEl) return;
  statusEl.textContent = msg;
  statusEl.classList.remove('hidden', 'admin-v2-policy-status--ok', 'admin-v2-policy-status--error');
  statusEl.classList.add(ok ? 'admin-v2-policy-status--ok' : 'admin-v2-policy-status--error');
  show(statusEl);
}

function adminCollectPackagesFromDom() {
  const list = el('adminSitePkgList');
  if (!list) return [];
  return Array.from(list.querySelectorAll('.admin-v2-site-pkg-card')).map((card) => ({
    key: (card.querySelector('.admin-pkg-key')?.value || '').trim().toLowerCase(),
    name: (card.querySelector('.admin-pkg-name')?.value || '').trim(),
    credits: parseInt(card.querySelector('.admin-pkg-credits')?.value, 10),
    price_vnd: parseInt(card.querySelector('.admin-pkg-price')?.value, 10),
    popular: !!card.querySelector('.admin-pkg-popular')?.checked,
  }));
}

function adminCollectSiteConfigPayload() {
  return {
    admin_user_id: currentUser.id,
    app_display_name: el('adminSiteAppName')?.value?.trim() || '',
    packages: adminCollectPackagesFromDom(),
  };
}

async function adminSaveSiteConfig() {
  if (!isAdmin()) return;
  const saveBtn = el('adminSiteSave');
  if (saveBtn) saveBtn.disabled = true;
  try {
    const r = await fetch(API_BASE + '/api/admin/site-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(adminCollectSiteConfigPayload()),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errSave'));
    if (d.config) {
      siteBranding.app_display_name = d.config.app_display_name || siteBranding.app_display_name;
      siteBranding.logo_url = d.config.logo_url || siteBranding.logo_url;
      applySiteBranding();
    }
    adminSiteStatus(t('admin.siteSaved'), true);
    if (currentPage === 'packages') loadPackagesPage();
  } catch (e) {
    adminSiteStatus(e.message || t('admin.errSave'), false);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function adminUploadSiteLogo() {
  if (!isAdmin()) return;
  const fileInput = el('adminSiteLogoFile');
  const file = fileInput?.files?.[0];
  if (!file) {
    adminSiteStatus(t('admin.siteLogoPick'), false);
    return;
  }
  const fd = new FormData();
  fd.append('logo', file);
  fd.append('admin_user_id', String(currentUser.id));
  try {
    const r = await fetch(API_BASE + '/api/admin/site-config/logo?admin_user_id=' + encodeURIComponent(currentUser.id), { method: 'POST', body: fd });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errSave'));
    siteBranding.logo_url = d.logo_url || '';
    applySiteBranding();
    adminSiteStatus(t('admin.siteLogoSaved'), true);
    if (el('adminSiteLogoRemove')) el('adminSiteLogoRemove').disabled = !d.has_logo;
    refreshAdminView();
  } catch (e) {
    adminSiteStatus(e.message || t('admin.errSave'), false);
  }
}

async function adminRemoveSiteLogo() {
  if (!isAdmin()) return;
  if (!confirm(t('admin.siteLogoRemoveConfirm'))) return;
  try {
    const r = await fetch(API_BASE + '/api/admin/site-config/logo?admin_user_id=' + encodeURIComponent(currentUser.id), {
      method: 'DELETE',
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errSave'));
    siteBranding.logo_url = '';
    applySiteBranding();
    adminSiteStatus(t('admin.siteLogoRemoved'), true);
    refreshAdminView();
  } catch (e) {
    adminSiteStatus(e.message || t('admin.errSave'), false);
  }
}

function adminApiConfigPanelHtml(cfg) {
  cfg = cfg || {};
  const keyPlaceholder = cfg.has_api_key
    ? (cfg.api_key_masked || t('admin.apiKeySet'))
    : t('admin.apiKeyEmpty');
  return (
    '<div class="admin-v2-api-panel">' +
    '<p class="admin-v2-hint admin-v2-policy-hint">' + escapeHtml(t('admin.apiHint')) + '</p>' +
    '<div class="admin-v2-toolbar admin-v2-policy-toolbar">' +
    '<button type="button" class="admin-v2-btn-outline" id="adminApiReload"><span class="material-symbols-outlined">refresh</span>' + escapeHtml(t('admin.policyReload')) + '</button>' +
    '<button type="button" class="admin-v2-btn" id="adminApiSave"><span class="material-symbols-outlined">save</span>' + escapeHtml(t('admin.save')) + '</button>' +
    '</div>' +
    '<p id="adminApiStatus" class="admin-v2-policy-status hidden" role="status"></p>' +
    '<section class="admin-v2-site-section admin-v2-api-section">' +
    '<h3 class="admin-v2-site-section-title">' + escapeHtml(t('admin.apiKeySection')) + '</h3>' +
    '<div class="admin-v2-api-field">' +
    '<label class="admin-v2-contact-field" for="adminApiKey">' + escapeHtml(t('admin.apiKeyLabel')) + '</label>' +
    '<input type="password" id="adminApiKey" class="admin-v2-input admin-v2-landing-input" autocomplete="off" placeholder="' + escapeHtml(keyPlaceholder) + '">' +
    '<p class="hint">' + escapeHtml(t('admin.apiKeyHint')) + '</p>' +
    '</div>' +
    '</section>' +
    '<section class="admin-v2-site-section admin-v2-api-section">' +
    '<h3 class="admin-v2-site-section-title">' + escapeHtml(t('admin.apiModelSection')) + '</h3>' +
    '<div class="admin-v2-api-field">' +
    '<label class="admin-v2-contact-field" for="adminApiVisionModels">' + escapeHtml(t('admin.apiVisionModels')) + '</label>' +
    '<textarea id="adminApiVisionModels" class="admin-v2-input admin-v2-landing-input admin-v2-api-textarea" rows="3">' + escapeHtml(cfg.openrouter_vision_models || '') + '</textarea>' +
    '<p class="hint">' + escapeHtml(t('admin.apiVisionModelsHint')) + '</p>' +
    '</div>' +
    '<div class="admin-v2-api-field">' +
    '<label class="admin-v2-contact-field" for="adminApiVisionWeights">' + escapeHtml(t('admin.apiVisionWeights')) + '</label>' +
    '<input type="text" id="adminApiVisionWeights" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(cfg.openrouter_vision_model_weights || '') + '">' +
    '<p class="hint">' + escapeHtml(t('admin.apiVisionWeightsHint')) + '</p>' +
    '</div>' +
    '<div class="admin-v2-api-field">' +
    '<label class="admin-v2-contact-field" for="adminApiModel">' + escapeHtml(t('admin.apiModel')) + '</label>' +
    '<input type="text" id="adminApiModel" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(cfg.openrouter_model || '') + '">' +
    '<p class="hint">' + escapeHtml(t('admin.apiModelHint')) + '</p>' +
    '</div>' +
    '<div class="admin-v2-api-field">' +
    '<label class="admin-v2-contact-field" for="adminApiTranslateModel">' + escapeHtml(t('admin.apiTranslateModel')) + '</label>' +
    '<input type="text" id="adminApiTranslateModel" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(cfg.openrouter_translate_model || '') + '">' +
    '<p class="hint">' + escapeHtml(t('admin.apiTranslateModelHint')) + '</p>' +
    '</div>' +
    '</section>' +
    '<section class="admin-v2-site-section admin-v2-api-section">' +
    '<h3 class="admin-v2-site-section-title">' + escapeHtml(t('admin.apiAdvancedSection')) + '</h3>' +
    '<div class="admin-v2-api-advanced-grid">' +
    '<div class="admin-v2-api-field">' +
    '<label class="admin-v2-contact-field" for="adminApiTimeout">' + escapeHtml(t('admin.apiTimeout')) + '</label>' +
    '<input type="number" id="adminApiTimeout" class="admin-v2-input admin-v2-landing-input" min="1" step="1" value="' + escapeHtml(String(cfg.openrouter_timeout ?? 90)) + '">' +
    '</div>' +
    '<div class="admin-v2-api-field">' +
    '<label class="admin-v2-contact-field" for="adminApiMaxTokens">' + escapeHtml(t('admin.apiMaxTokens')) + '</label>' +
    '<input type="number" id="adminApiMaxTokens" class="admin-v2-input admin-v2-landing-input" min="1" step="1" value="' + escapeHtml(String(cfg.openrouter_max_tokens ?? 512)) + '">' +
    '</div>' +
    '</div>' +
    '</section>' +
    '</div>'
  );
}

function adminApiStatus(msg, ok) {
  const statusEl = el('adminApiStatus');
  if (!statusEl) return;
  statusEl.textContent = msg;
  statusEl.classList.remove('hidden', 'admin-v2-policy-status--ok', 'admin-v2-policy-status--error');
  statusEl.classList.add(ok ? 'admin-v2-policy-status--ok' : 'admin-v2-policy-status--error');
  show(statusEl);
}

function adminCollectApiConfigPayload() {
  const payload = {
    admin_user_id: currentUser.id,
    openrouter_vision_models: el('adminApiVisionModels')?.value?.trim() || '',
    openrouter_vision_model_weights: el('adminApiVisionWeights')?.value?.trim() || '',
    openrouter_model: el('adminApiModel')?.value?.trim() || '',
    openrouter_translate_model: el('adminApiTranslateModel')?.value?.trim() || '',
    openrouter_timeout: parseInt(el('adminApiTimeout')?.value, 10),
    openrouter_max_tokens: parseInt(el('adminApiMaxTokens')?.value, 10),
  };
  const keyVal = el('adminApiKey')?.value?.trim() || '';
  if (keyVal) payload.openrouter_api_key = keyVal;
  return payload;
}

async function adminSaveApiConfig() {
  if (!isAdmin()) return;
  const saveBtn = el('adminApiSave');
  if (saveBtn) saveBtn.disabled = true;
  try {
    const r = await fetch(API_BASE + '/api/admin/api-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(adminCollectApiConfigPayload()),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errSave'));
    if (el('adminApiKey')) el('adminApiKey').value = '';
    adminApiStatus(t('admin.apiSaved'), true);
    if (d.config) {
      const keyInput = el('adminApiKey');
      if (keyInput) {
        keyInput.placeholder = d.config.has_api_key
          ? (d.config.api_key_masked || t('admin.apiKeySet'))
          : t('admin.apiKeyEmpty');
      }
    }
  } catch (e) {
    adminApiStatus(e.message || t('admin.errSave'), false);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

let adminLandingCache = null;
let adminLandingEditLang = 'vi';

const ADMIN_LANDING_HTML_KEYS = new Set(['intro.note', 'intro.galleryLead', 'intro.ctaFootAlt']);

const ADMIN_LANDING_SECTIONS = [
  {
    titleKey: 'admin.landingHeroSection',
    fields: ['intro.kicker', 'intro.heroTitleLine1', 'intro.heroTitleLine2', 'intro.lead', 'intro.note'],
  },
  {
    titleKey: 'admin.landingFeaturesSection',
    fields: ['intro.featuresTitle', 'intro.featuresLead', 'intro.f1Title', 'intro.f1Desc', 'intro.f2Title', 'intro.f2Desc', 'intro.f3Title', 'intro.f3Desc', 'intro.f4Title', 'intro.f4Desc'],
  },
  {
    titleKey: 'admin.landingWorkflowSection',
    fields: ['intro.galleryTitle', 'intro.galleryLead', 'intro.step1', 'intro.wf1Title', 'intro.step2', 'intro.wf2Title', 'intro.step3', 'intro.wf3Title'],
  },
  {
    titleKey: 'admin.landingTechSection',
    fields: ['intro.techTitle', 'intro.techLead', 'intro.tech1Title', 'intro.tech1Desc', 'intro.tech2Title', 'intro.tech2Desc', 'intro.tech3Title', 'intro.tech3Desc'],
  },
  {
    titleKey: 'admin.landingHowSection',
    fields: ['intro.howTitle', 'intro.how1Title', 'intro.how1Desc', 'intro.how2Title', 'intro.how2Desc', 'intro.how3Title', 'intro.how3Desc'],
  },
  {
    titleKey: 'admin.landingFaqSection',
    fields: ['intro.faqTitle', 'intro.faq1Q', 'intro.faq1A', 'intro.faq2Q', 'intro.faq2A', 'intro.faq3Q', 'intro.faq3A'],
  },
  {
    titleKey: 'admin.landingCtaSection',
    fields: ['intro.ctaEyebrow', 'intro.ctaTitle', 'intro.ctaRegister', 'intro.ctaFootAlt'],
  },
];

function adminLandingFieldId(key) {
  return 'adminLanding_' + String(key).replace(/\./g, '_');
}

function adminLandingSyncViFromDom() {
  if (!adminLandingCache || adminLandingEditLang !== 'vi') return;
  if (!adminLandingCache.vi) adminLandingCache.vi = {};
  ADMIN_LANDING_SECTIONS.forEach((sec) => {
    sec.fields.forEach((key) => {
      const node = el(adminLandingFieldId(key));
      if (node) adminLandingCache.vi[key] = node.value;
    });
  });
  const heroImg = el('adminLandingHeroImage');
  if (heroImg) adminLandingCache.hero_image_url = heroImg.value.trim();
  const markerA = el('adminLandingHeroMarkerA');
  if (markerA) adminLandingCache.hero_marker_a = markerA.value.trim();
  const markerB = el('adminLandingHeroMarkerB');
  if (markerB) adminLandingCache.hero_marker_b = markerB.value.trim();
}

function adminLandingSyncFieldsFromDom() {
  adminLandingSyncViFromDom();
}

function adminLandingFieldHtml(key, value) {
  const id = adminLandingFieldId(key);
  const isHtml = ADMIN_LANDING_HTML_KEYS.has(key);
  const label = key.replace(/^intro\./, '');
  const ro = adminLandingEditLang === 'en' ? ' readonly' : '';
  const input = isHtml
    ? '<textarea id="' + id + '" class="admin-v2-input admin-v2-landing-input admin-v2-landing-textarea" rows="5"' + ro + '>' + escapeHtml(value || '') + '</textarea>'
    : '<input type="text" id="' + id + '" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(value || '') + '"' + ro + '>';
  const hint = isHtml ? '<p class="hint">' + escapeHtml(t('admin.landingHtmlHint')) + '</p>' : '';
  return (
    '<div class="admin-v2-landing-field">' +
    '<label class="admin-v2-contact-field" for="' + id + '">' + escapeHtml(label) + '</label>' +
    input + hint +
    '</div>'
  );
}

function adminLandingConfigPanelHtml(cfg) {
  cfg = cfg || {};
  const lang = adminLandingEditLang;
  const bucket = cfg[lang] || {};
  const isEn = lang === 'en';
  const markerAVal = isEn ? (cfg.hero_marker_a_en || cfg.hero_marker_a || '') : (cfg.hero_marker_a || '');
  const markerBVal = isEn ? (cfg.hero_marker_b_en || cfg.hero_marker_b || '') : (cfg.hero_marker_b || '');
  const sections = ADMIN_LANDING_SECTIONS.map((sec) => {
    const fields = sec.fields.map((key) => adminLandingFieldHtml(key, bucket[key] || '')).join('');
    return (
      '<section class="admin-v2-site-section admin-v2-landing-section">' +
      '<h3 class="admin-v2-site-section-title">' + escapeHtml(t(sec.titleKey)) + '</h3>' +
      fields +
      '</section>'
    );
  }).join('');
  return (
    '<div class="admin-v2-landing-panel">' +
    '<p class="admin-v2-hint admin-v2-policy-hint">' + escapeHtml(t('admin.landingHint')) + '</p>' +
    '<div class="admin-v2-toolbar admin-v2-policy-toolbar admin-v2-landing-toolbar">' +
    '<div class="admin-v2-landing-lang" role="group" aria-label="' + escapeHtml(t('admin.landingLang')) + '">' +
    '<button type="button" class="admin-v2-btn-outline admin-v2-landing-lang-btn' + (lang === 'vi' ? ' is-active' : '') + '" data-landing-lang="vi">VI</button>' +
    '<button type="button" class="admin-v2-landing-lang-btn admin-v2-btn-outline' + (lang === 'en' ? ' is-active' : '') + '" data-landing-lang="en">EN</button>' +
    '</div>' +
    '<a href="/" target="_blank" rel="noopener noreferrer" class="admin-v2-btn-outline"><span class="material-symbols-outlined">open_in_new</span>' + escapeHtml(t('admin.landingPreview')) + '</a>' +
    '<button type="button" class="admin-v2-btn-outline" id="adminLandingReload"><span class="material-symbols-outlined">refresh</span>' + escapeHtml(t('admin.policyReload')) + '</button>' +
    '<button type="button" class="admin-v2-btn" id="adminLandingSave"><span class="material-symbols-outlined">save</span>' + escapeHtml(t('admin.save')) + '</button>' +
    '</div>' +
    '<p id="adminLandingStatus" class="admin-v2-policy-status hidden" role="status"></p>' +
    (isEn ? '' : (
    '<section class="admin-v2-site-section admin-v2-landing-section">' +
    '<h3 class="admin-v2-site-section-title">' + escapeHtml(t('admin.landingVisualSection')) + '</h3>' +
    '<div class="admin-v2-landing-field">' +
    '<label class="admin-v2-contact-field" for="adminLandingHeroImage">' + escapeHtml(t('admin.landingHeroImage')) + '</label>' +
    '<input type="url" id="adminLandingHeroImage" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(cfg.hero_image_url || '') + '">' +
    '<p class="hint">' + escapeHtml(t('admin.landingHeroImageHint')) + '</p>' +
    '</div>' +
    '<div class="admin-v2-landing-field">' +
    '<label class="admin-v2-contact-field" for="adminLandingHeroMarkerA">' + escapeHtml(t('admin.landingHeroMarkerA')) + '</label>' +
    '<input type="text" id="adminLandingHeroMarkerA" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(cfg.hero_marker_a || '') + '">' +
    '</div>' +
    '<div class="admin-v2-landing-field">' +
    '<label class="admin-v2-contact-field" for="adminLandingHeroMarkerB">' + escapeHtml(t('admin.landingHeroMarkerB')) + '</label>' +
    '<input type="text" id="adminLandingHeroMarkerB" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(cfg.hero_marker_b || '') + '">' +
    '</div>' +
    '</section>'
    )) +
    (isEn ? (
    '<section class="admin-v2-site-section admin-v2-landing-section admin-v2-landing-section--preview">' +
    '<h3 class="admin-v2-site-section-title">' + escapeHtml(t('admin.landingVisualSection')) + ' (EN)</h3>' +
    '<div class="admin-v2-landing-field">' +
    '<label class="admin-v2-contact-field">' + escapeHtml(t('admin.landingHeroMarkerA')) + '</label>' +
    '<input type="text" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(markerAVal) + '" readonly>' +
    '</div>' +
    '<div class="admin-v2-landing-field">' +
    '<label class="admin-v2-contact-field">' + escapeHtml(t('admin.landingHeroMarkerB')) + '</label>' +
    '<input type="text" class="admin-v2-input admin-v2-landing-input" value="' + escapeHtml(markerBVal) + '" readonly>' +
    '</div>' +
    '</section>'
    ) : '') +
    sections +
    '</div>'
  );
}

function adminLandingStatus(msg, ok) {
  const statusEl = el('adminLandingStatus');
  if (!statusEl) return;
  statusEl.textContent = msg;
  statusEl.classList.remove('hidden', 'admin-v2-policy-status--ok', 'admin-v2-policy-status--error');
  statusEl.classList.add(ok ? 'admin-v2-policy-status--ok' : 'admin-v2-policy-status--error');
  show(statusEl);
}

function bindAdminLandingPanel() {
  el('adminLandingSave')?.addEventListener('click', () => adminSaveLandingConfig());
  el('adminLandingReload')?.addEventListener('click', () => refreshAdminView());
  document.querySelectorAll('[data-landing-lang]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const next = btn.getAttribute('data-landing-lang');
      if (!next || next === adminLandingEditLang) return;
      adminLandingSyncViFromDom();
      adminLandingEditLang = next;
      const mount = el('adminTabContent');
      if (mount && adminLandingCache) {
        mount.innerHTML = adminLandingConfigPanelHtml(adminLandingCache);
        bindAdminLandingPanel();
      }
    });
  });
}

function adminCollectLandingPayload() {
  adminLandingSyncViFromDom();
  return {
    admin_user_id: currentUser.id,
    vi: adminLandingCache?.vi || {},
    hero_image_url: adminLandingCache?.hero_image_url || '',
    hero_marker_a: adminLandingCache?.hero_marker_a || '',
    hero_marker_b: adminLandingCache?.hero_marker_b || '',
  };
}

async function adminSaveLandingConfig() {
  if (!isAdmin()) return;
  const saveBtn = el('adminLandingSave');
  if (saveBtn) saveBtn.disabled = true;
  if (adminLandingEditLang === 'vi') {
    adminLandingSyncViFromDom();
  }
  adminLandingStatus(t('admin.landingSaving'), true);
  try {
    const r = await fetch(API_BASE + '/api/admin/landing-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(adminCollectLandingPayload()),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errSave'));
    const prevLang = adminLandingEditLang;
    if (d.config) {
      adminLandingCache = d.config;
      const mount = el('adminTabContent');
      if (mount) {
        mount.innerHTML = adminLandingConfigPanelHtml(adminLandingCache);
        adminLandingEditLang = prevLang;
        bindAdminLandingPanel();
      }
    }
    if (window.I18n && window.I18n.mergeLandingStrings && d.config) {
      await window.I18n.mergeLandingStrings(d.config.vi, d.config.en, {
        hero_image_url: d.config.hero_image_url,
        hero_marker_a: d.config.hero_marker_a,
        hero_marker_b: d.config.hero_marker_b,
        hero_marker_a_en: d.config.hero_marker_a_en,
        hero_marker_b_en: d.config.hero_marker_b_en,
      });
    }
    adminLandingStatus(t('admin.landingSaved'), true);
  } catch (e) {
    adminLandingStatus(e.message || t('admin.errSave'), false);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function loadSiteBranding() {
  try {
    const r = await fetch(API_BASE + '/api/site/config');
    const d = await r.json().catch(() => ({}));
    if (r.ok && d) {
      siteBranding = {
        app_display_name: d.app_display_name || 'StyleID',
        logo_url: d.logo_url || '',
      };
      applySiteBranding();
    }
  } catch (_) {}
}

function applySiteBranding() {
  const name = siteBranding.app_display_name || 'StyleID';
  const logo = siteBranding.logo_url || '';
  document.querySelectorAll('.shell-v2-logo-text').forEach((node) => {
    node.textContent = name;
  });
  document.querySelectorAll('.logo-home').forEach((btn) => {
    let img = btn.querySelector('.site-brand-logo');
    if (logo) {
      if (!img) {
        btn.innerHTML = '';
        img = document.createElement('img');
        img.className = 'site-brand-logo';
        btn.appendChild(img);
      }
      img.src = logo;
      img.alt = name;
    } else if (btn.classList.contains('shell-v2-logo')) {
      btn.innerHTML = '<span class="shell-v2-logo-text">' + escapeHtml(name) + '</span>';
    } else if (btn.classList.contains('landing-v2-logo')) {
      btn.innerHTML = escapeHtml(name);
    } else {
      btn.textContent = name;
    }
  });
}

function adminPolicyVisualEditor() {
  return el('adminPolicyVisualEditor');
}

function adminPolicyTextarea() {
  return el('adminPolicyContent');
}

function adminPolicyIsSourceMode() {
  return el('adminPolicyEditorWrap')?.classList.contains('is-source') === true;
}

function adminPolicyProtectJinja(html) {
  adminPolicyJinjaTokens = [];
  return String(html || '').replace(ADMIN_POLICY_JINJA_RE, (match) => {
    const idx = adminPolicyJinjaTokens.length;
    adminPolicyJinjaTokens.push(match);
    return '[[JINJA:' + idx + ']]';
  });
}

function adminPolicyRestoreJinjaPlaceholders(html) {
  return String(html || '').replace(ADMIN_POLICY_JINJA_PLACEHOLDER_RE, (_, idx) => {
    const i = parseInt(idx, 10);
    return adminPolicyJinjaTokens[i] != null ? adminPolicyJinjaTokens[i] : '';
  });
}

function adminPolicyRestoreJinjaFromRoot(root) {
  root.querySelectorAll('.admin-policy-jinja[data-jinja-idx]').forEach((node) => {
    const i = parseInt(node.getAttribute('data-jinja-idx') || '', 10);
    const token = adminPolicyJinjaTokens[i];
    node.replaceWith(document.createTextNode(token != null ? token : (node.textContent || '')));
  });
  root.querySelectorAll('.admin-policy-jinja[data-jinja]').forEach((node) => {
    let raw = '';
    try {
      raw = decodeURIComponent(node.getAttribute('data-jinja') || '');
    } catch (_) {
      raw = node.textContent || '';
    }
    node.replaceWith(document.createTextNode(raw));
  });
  return root.innerHTML;
}

function adminPolicyDecorateJinjaPlaceholders(root) {
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  textNodes.forEach((textNode) => {
    const text = textNode.nodeValue || '';
    if (!ADMIN_POLICY_JINJA_PLACEHOLDER_RE.test(text)) return;
    ADMIN_POLICY_JINJA_PLACEHOLDER_RE.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    let m;
    while ((m = ADMIN_POLICY_JINJA_PLACEHOLDER_RE.exec(text)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const idx = parseInt(m[1], 10);
      const span = document.createElement('span');
      span.className = 'admin-policy-jinja';
      span.contentEditable = 'false';
      span.setAttribute('data-jinja-idx', String(idx));
      span.title = t('admin.jinjaTokenHint');
      span.textContent = adminPolicyJinjaTokens[idx] || m[0];
      frag.appendChild(span);
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    textNode.parentNode?.replaceChild(frag, textNode);
  });
}

function adminPolicyVisualToStorageHtml() {
  const editor = adminPolicyVisualEditor();
  if (!editor) return '';
  const clone = editor.cloneNode(true);
  let html = adminPolicyRestoreJinjaFromRoot(clone);
  html = adminPolicyRestoreJinjaPlaceholders(html);
  return html.replace(/\u200B/g, '');
}

function adminPolicyLoadVisual(content) {
  const editor = adminPolicyVisualEditor();
  if (!editor) return;
  editor.innerHTML = adminPolicyProtectJinja(content);
  adminPolicyDecorateJinjaPlaceholders(editor);
}

function adminPolicyGetContentForSave() {
  if (adminPolicyIsSourceMode()) return adminPolicyTextarea()?.value || '';
  return adminPolicyVisualToStorageHtml();
}

function adminPolicySyncViFromDom() {
  if (adminPolicyEditLang !== 'vi') return;
  adminPolicyViCache = adminPolicyGetContentForSave();
}

function adminPolicyUpdateLangTabUi() {
  document.querySelectorAll('[data-policy-lang]').forEach((btn) => {
    const lang = btn.getAttribute('data-policy-lang');
    btn.classList.toggle('is-active', lang === adminPolicyEditLang);
  });
}

function adminPolicySwitchEditLang(next) {
  if (!next || next === adminPolicyEditLang) return;
  if (adminPolicyEditLang === 'vi') adminPolicySyncViFromDom();
  adminPolicyEditLang = next;
  const readonly = next === 'en';
  adminPolicyInitEditor(readonly ? adminPolicyEnCache : adminPolicyViCache, { readonly });
  adminPolicyUpdateLangTabUi();
}

function bindAdminPolicyLangTabs() {
  document.querySelectorAll('[data-policy-lang]').forEach((btn) => {
    btn.addEventListener('click', () => {
      adminPolicySwitchEditLang(btn.getAttribute('data-policy-lang'));
    });
  });
  adminPolicyUpdateLangTabUi();
}

function adminPolicyInitEditor(content, opts) {
  opts = opts || {};
  const readonly = !!opts.readonly;
  const ta = adminPolicyTextarea();
  const wrap = el('adminPolicyEditorWrap');
  const editor = adminPolicyVisualEditor();
  const fmtBar = el('adminPolicyFormatBar');
  const toggleSrc = el('adminPolicyToggleSource');
  if (ta) {
    ta.value = content;
    ta.readOnly = readonly;
  }
  if (wrap) wrap.classList.remove('is-source');
  if (editor) {
    editor.contentEditable = readonly ? 'false' : 'true';
    editor.classList.toggle('is-readonly', readonly);
    editor.setAttribute('aria-readonly', readonly ? 'true' : 'false');
  }
  if (fmtBar) fmtBar.classList.toggle('hidden', readonly);
  if (toggleSrc) toggleSrc.disabled = readonly;
  adminPolicyLoadVisual(content);
  adminPolicySyncSourceToggleLabel();
  if (!readonly) bindAdminPolicyFormatToolbar();
}

function adminPolicySyncSourceToggleLabel() {
  const btn = el('adminPolicyToggleSource');
  if (!btn) return;
  const source = adminPolicyIsSourceMode();
  const label = source ? t('admin.policyVisualMode') : t('admin.policySourceMode');
  const icon = source ? 'edit_note' : 'code';
  btn.title = label;
  btn.innerHTML = '<span class="material-symbols-outlined">' + icon + '</span>' + escapeHtml(label);
}

function adminPolicyToggleSourceMode() {
  if (adminPolicyEditLang === 'en') return;
  const wrap = el('adminPolicyEditorWrap');
  const ta = adminPolicyTextarea();
  const editor = adminPolicyVisualEditor();
  if (!wrap || !ta || !editor) return;
  const toSource = !wrap.classList.contains('is-source');
  if (toSource) {
    ta.value = adminPolicyVisualToStorageHtml();
    wrap.classList.add('is-source');
  } else {
    adminPolicyLoadVisual(ta.value);
    wrap.classList.remove('is-source');
    editor.focus();
  }
  const btn = el('adminPolicyToggleSource');
  if (btn) btn.classList.toggle('is-active', toSource);
  adminPolicySyncSourceToggleLabel();
  if (toSource) ta.focus();
}

function adminPolicyFocusVisual() {
  adminPolicyVisualEditor()?.focus();
}

function adminPolicyGetSel() {
  const ta = adminPolicyTextarea();
  if (!ta) return null;
  return {
    ta,
    start: ta.selectionStart,
    end: ta.selectionEnd,
    text: ta.value.slice(ta.selectionStart, ta.selectionEnd),
  };
}

function adminPolicyInsertAt(replacement, selStart, selEnd, selectFrom, selectTo) {
  const ta = adminPolicyTextarea();
  if (!ta) return;
  const s = selStart ?? ta.selectionStart;
  const e = selEnd ?? ta.selectionEnd;
  ta.value = ta.value.slice(0, s) + replacement + ta.value.slice(e);
  ta.focus();
  if (selectFrom != null && selectTo != null) {
    ta.setSelectionRange(selectFrom, selectTo);
  } else {
    const caret = s + replacement.length;
    ta.setSelectionRange(caret, caret);
  }
}

function adminPolicyWrapInline(before, after, placeholder) {
  const sel = adminPolicyGetSel();
  if (!sel) return;
  const inner = sel.text || placeholder || '';
  const wrapped = before + inner + after;
  const innerStart = sel.start + before.length;
  const innerEnd = innerStart + inner.length;
  adminPolicyInsertAt(wrapped, sel.start, sel.end, innerStart, innerEnd);
}

function adminPolicyWrapBlock(tag, attrs, placeholder) {
  const sel = adminPolicyGetSel();
  if (!sel) return;
  const attrStr = attrs ? ' ' + attrs : '';
  const open = '<' + tag + attrStr + '>';
  const close = '</' + tag + '>';
  const inner = sel.text || placeholder || '';
  const multiline = inner.includes('\n');
  const wrapped = multiline ? open + '\n' + inner + '\n' + close : open + inner + close;
  const innerStart = sel.start + open.length + (multiline ? 1 : 0);
  const innerEnd = innerStart + inner.length;
  adminPolicyInsertAt(wrapped, sel.start, sel.end, innerStart, innerEnd);
}

function adminPolicyApplyFontSize(size) {
  if (!size) return;
  if (adminPolicyIsSourceMode()) {
    adminPolicyApplyFontSizeSource(size);
  } else {
    adminPolicyApplyFontSizeVisual(size);
  }
}

function adminPolicyApplyFontSizeSource(size) {
  const sel = adminPolicyGetSel();
  if (!sel) return;
  const ph = t('admin.policyPlaceholder');
  const inner = sel.text || ph;
  const open = '<span style="font-size: ' + size + '">';
  const wrapped = open + inner + '</span>';
  const innerStart = sel.start + open.length;
  adminPolicyInsertAt(wrapped, sel.start, sel.end, innerStart, innerStart + inner.length);
}

function adminPolicyApplyFontSizeVisual(size) {
  adminPolicyFocusVisual();
  const sel = window.getSelection();
  const ph = t('admin.policyPlaceholder');
  const text = sel && sel.rangeCount ? sel.getRangeAt(0).toString() : '';
  const inner = text || ph;
  document.execCommand('insertHTML', false, '<span style="font-size:' + size + '">' + inner + '</span>');
}

function adminPolicyApplyFormatVisual(fmt) {
  adminPolicyFocusVisual();
  const ph = t('admin.policyPlaceholder');
  const heading = t('admin.policyHeading');
  switch (fmt) {
    case 'bold':
      document.execCommand('bold');
      break;
    case 'italic':
      document.execCommand('italic');
      break;
    case 'underline':
      document.execCommand('underline');
      break;
    case 'strike':
      document.execCommand('strikeThrough');
      break;
    case 'h2':
      document.execCommand('formatBlock', false, 'h2');
      break;
    case 'h3':
      document.execCommand('formatBlock', false, 'h3');
      break;
    case 'p':
      document.execCommand('formatBlock', false, 'p');
      break;
    case 'ul':
      document.execCommand('insertUnorderedList');
      break;
    case 'ol':
      document.execCommand('insertOrderedList');
      break;
    case 'link': {
      const url = prompt(t('admin.policyLinkUrl'), 'https://');
      if (url == null || !String(url).trim()) return;
      document.execCommand('createLink', false, String(url).trim());
      break;
    }
    case 'section':
      document.execCommand(
        'insertHTML',
        false,
        '<section><h2>' + heading + '</h2><p>' + ph + '</p></section>'
      );
      break;
    case 'alignLeft':
      document.execCommand('justifyLeft');
      break;
    case 'alignCenter':
      document.execCommand('justifyCenter');
      break;
    case 'alignRight':
      document.execCommand('justifyRight');
      break;
    case 'alignJustify':
      document.execCommand('justifyFull');
      break;
    default:
      break;
  }
}

function adminPolicyApplyFormatSource(fmt) {
  const ph = t('admin.policyPlaceholder');
  const heading = t('admin.policyHeading');
  switch (fmt) {
    case 'bold':
      adminPolicyWrapInline('<strong>', '</strong>', ph);
      break;
    case 'italic':
      adminPolicyWrapInline('<em>', '</em>', ph);
      break;
    case 'underline':
      adminPolicyWrapInline('<u>', '</u>', ph);
      break;
    case 'strike':
      adminPolicyWrapInline('<s>', '</s>', ph);
      break;
    case 'h2':
      adminPolicyWrapBlock('h2', null, heading);
      break;
    case 'h3':
      adminPolicyWrapBlock('h3', null, heading);
      break;
    case 'p':
      adminPolicyWrapBlock('p', null, ph);
      break;
    case 'ul': {
      const sel = adminPolicyGetSel();
      if (!sel) return;
      const items = sel.text
        ? sel.text.split('\n').map((line) => line.trim()).filter(Boolean)
        : [ph];
      const html = '<ul>\n' + items.map((item) => '  <li>' + item + '</li>').join('\n') + '\n</ul>';
      adminPolicyInsertAt(html, sel.start, sel.end);
      break;
    }
    case 'ol': {
      const sel = adminPolicyGetSel();
      if (!sel) return;
      const items = sel.text
        ? sel.text.split('\n').map((line) => line.trim()).filter(Boolean)
        : [ph];
      const html = '<ol>\n' + items.map((item) => '  <li>' + item + '</li>').join('\n') + '\n</ol>';
      adminPolicyInsertAt(html, sel.start, sel.end);
      break;
    }
    case 'link': {
      const url = prompt(t('admin.policyLinkUrl'), 'https://');
      if (url == null || !String(url).trim()) return;
      const safeUrl = String(url).trim().replace(/"/g, '&quot;');
      adminPolicyWrapInline('<a href="' + safeUrl + '">', '</a>', ph);
      break;
    }
    case 'section': {
      const sel = adminPolicyGetSel();
      if (!sel) return;
      const open = '<section>\n  <h2>';
      const snippet = open + heading + '</h2>\n  <p>' + ph + '</p>\n</section>';
      adminPolicyInsertAt(snippet, sel.start, sel.end, sel.start + open.length, sel.start + open.length + heading.length);
      break;
    }
    case 'alignLeft':
      adminPolicyWrapBlock('p', 'style="text-align: left"', ph);
      break;
    case 'alignCenter':
      adminPolicyWrapBlock('p', 'style="text-align: center"', ph);
      break;
    case 'alignRight':
      adminPolicyWrapBlock('p', 'style="text-align: right"', ph);
      break;
    case 'alignJustify':
      adminPolicyWrapBlock('p', 'style="text-align: justify"', ph);
      break;
    default:
      break;
  }
}

function adminPolicyApplyFormat(fmt) {
  if (adminPolicyIsSourceMode()) {
    adminPolicyApplyFormatSource(fmt);
  } else {
    adminPolicyApplyFormatVisual(fmt);
  }
}

function adminPolicyFmtBtn(fmt, icon, titleKey) {
  return (
    '<button type="button" class="admin-v2-policy-fmt-btn" data-fmt="' +
    fmt +
    '" title="' +
    escapeHtml(t(titleKey)) +
    '" aria-label="' +
    escapeHtml(t(titleKey)) +
    '"><span class="material-symbols-outlined">' +
    icon +
    '</span></button>'
  );
}

function adminPolicyFormatToolbarHtml() {
  return (
    '<div class="admin-v2-policy-format" id="adminPolicyFormatBar" role="toolbar" aria-label="' +
    escapeHtml(t('admin.policyFormatBar')) +
    '">' +
    '<div class="admin-v2-policy-format-group">' +
    adminPolicyFmtBtn('bold', 'format_bold', 'admin.fmtBold') +
    adminPolicyFmtBtn('italic', 'format_italic', 'admin.fmtItalic') +
    adminPolicyFmtBtn('underline', 'format_underlined', 'admin.fmtUnderline') +
    adminPolicyFmtBtn('strike', 'strikethrough_s', 'admin.fmtStrike') +
    '</div>' +
    '<span class="admin-v2-policy-format-sep" aria-hidden="true"></span>' +
    '<div class="admin-v2-policy-format-group">' +
    adminPolicyFmtBtn('h2', 'title', 'admin.fmtH2') +
    adminPolicyFmtBtn('h3', 'view_headline', 'admin.fmtH3') +
    adminPolicyFmtBtn('p', 'notes', 'admin.fmtParagraph') +
    adminPolicyFmtBtn('section', 'article', 'admin.fmtSection') +
    '</div>' +
    '<span class="admin-v2-policy-format-sep" aria-hidden="true"></span>' +
    '<div class="admin-v2-policy-format-group">' +
    adminPolicyFmtBtn('ul', 'format_list_bulleted', 'admin.fmtUl') +
    adminPolicyFmtBtn('ol', 'format_list_numbered', 'admin.fmtOl') +
    adminPolicyFmtBtn('link', 'link', 'admin.fmtLink') +
    '</div>' +
    '<span class="admin-v2-policy-format-sep" aria-hidden="true"></span>' +
    '<div class="admin-v2-policy-format-group">' +
    adminPolicyFmtBtn('alignLeft', 'format_align_left', 'admin.fmtAlignLeft') +
    adminPolicyFmtBtn('alignCenter', 'format_align_center', 'admin.fmtAlignCenter') +
    adminPolicyFmtBtn('alignRight', 'format_align_right', 'admin.fmtAlignRight') +
    adminPolicyFmtBtn('alignJustify', 'format_align_justify', 'admin.fmtAlignJustify') +
    '</div>' +
    '<span class="admin-v2-policy-format-sep" aria-hidden="true"></span>' +
    '<div class="admin-v2-policy-format-group admin-v2-policy-format-group--select">' +
    '<label class="admin-v2-policy-fmt-label" for="adminPolicyFontSize">' +
    escapeHtml(t('admin.fmtFontSize')) +
    '</label>' +
    '<select id="adminPolicyFontSize" class="admin-v2-input admin-v2-policy-fmt-select">' +
    '<option value="">' +
    escapeHtml(t('admin.fmtFontSizePick')) +
    '</option>' +
    '<option value="0.875rem">' +
    escapeHtml(t('admin.fmtSizeSm')) +
    '</option>' +
    '<option value="1rem">' +
    escapeHtml(t('admin.fmtSizeMd')) +
    '</option>' +
    '<option value="1.125rem">' +
    escapeHtml(t('admin.fmtSizeLg')) +
    '</option>' +
    '<option value="1.25rem">' +
    escapeHtml(t('admin.fmtSizeXl')) +
    '</option>' +
    '<option value="1.5rem">' +
    escapeHtml(t('admin.fmtSizeXxl')) +
    '</option>' +
    '</select>' +
    '</div>' +
    '</div>'
  );
}

function bindAdminPolicyFormatToolbar() {
  const bar = el('adminPolicyFormatBar');
  if (!bar || bar.dataset.bound) return;
  bar.dataset.bound = '1';
  bar.addEventListener('mousedown', (ev) => {
    if (ev.target.closest('.admin-v2-policy-fmt-btn, .admin-v2-policy-fmt-select')) ev.preventDefault();
  });
  bar.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-fmt]');
    if (!btn) return;
    ev.preventDefault();
    adminPolicyApplyFormat(btn.dataset.fmt);
  });
  el('adminPolicyFontSize')?.addEventListener('change', (ev) => {
    const size = ev.target.value;
    if (size) adminPolicyApplyFontSize(size);
    ev.target.value = '';
  });
  el('adminPolicyToggleSource')?.addEventListener('click', () => adminPolicyToggleSourceMode());
}

async function adminSavePolicy() {
  if (!isAdmin()) return;
  if (adminPolicySlug === 'site_contact') {
    await adminSaveSiteContact();
    return;
  }
  const statusEl = el('adminPolicyStatus');
  const saveBtn = el('adminPolicySave');
  if (!adminPolicyVisualEditor() && !adminPolicyTextarea()) return;
  if (statusEl) {
    statusEl.textContent = '';
    hide(statusEl);
  }
  const slug = adminPolicySlug || 'privacy';
  adminPolicySyncViFromDom();
  const vi_content = adminPolicyViCache;
  if (!String(vi_content || '').trim()) {
    if (statusEl) {
      statusEl.textContent = t('admin.policyViRequired');
      statusEl.classList.remove('hidden', 'admin-v2-policy-status--ok');
      statusEl.classList.add('admin-v2-policy-status--error');
      show(statusEl);
    }
    return;
  }
  if (saveBtn) saveBtn.disabled = true;
  if (statusEl) {
    statusEl.textContent = t('admin.policySaving');
    statusEl.classList.remove('hidden', 'admin-v2-policy-status--error');
    statusEl.classList.add('admin-v2-policy-status--ok');
    show(statusEl);
  }
  try {
    const r = await fetch(API_BASE + '/api/admin/policies/' + encodeURIComponent(slug), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ admin_user_id: currentUser.id, vi_content }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errSave'));
    if (d.vi_content != null) adminPolicyViCache = d.vi_content;
    if (d.en_content != null) adminPolicyEnCache = d.en_content;
    if (adminPolicyEditLang === 'en') {
      adminPolicyInitEditor(adminPolicyEnCache, { readonly: true });
    } else {
      adminPolicyInitEditor(adminPolicyViCache, { readonly: false });
    }
    bindAdminPolicyLangTabs();
    if (statusEl) {
      statusEl.textContent = t('admin.policySaved');
      statusEl.classList.remove('hidden', 'admin-v2-policy-status--error');
      statusEl.classList.add('admin-v2-policy-status--ok');
      show(statusEl);
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = e.message || t('admin.errSave');
      statusEl.classList.remove('hidden', 'admin-v2-policy-status--ok');
      statusEl.classList.add('admin-v2-policy-status--error');
      show(statusEl);
    } else {
      alert(e.message || t('admin.errSave'));
    }
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

function bindAdminV2Shell() {
  el('btnAdminExit')?.addEventListener('click', () => {
    closeAdminDrawer();
    navigateToPage('analyze', { urlMode: 'push' });
  });
  el('btnAdminNavOpen')?.addEventListener('click', (e) => {
    e.stopPropagation();
    openAdminDrawer();
  });
  el('btnAdminNavClose')?.addEventListener('click', () => closeAdminDrawer());
  el('adminNavBackdrop')?.addEventListener('click', () => closeAdminDrawer());
  el('adminV2QuickSearch')?.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    const q = ev.target.value.trim();
    if (!q) return;
    adminUserSearch = q;
    adminUsersPage = 1;
    switchAdminTab('users');
  });
  el('pageAdmin')?.addEventListener('click', (e) => {
    const tabBtn = e.target.closest('.admin-v2-tab, .admin-v2-nav-btn');
    if (!tabBtn || !tabBtn.dataset.adtab) return;
    if (tabBtn.dataset.adtab !== adminActiveTab) switchAdminTab(tabBtn.dataset.adtab);
    if (tabBtn.classList.contains('admin-v2-nav-btn')) closeAdminDrawer();
  });
}
bindAdminV2Shell();

el('adminEditRestoreBtn')?.addEventListener('click', () => {
  const uid = parseInt(el('adminEditUserId')?.value || '', 10);
  if (uid) adminRestoreUser(uid);
});
el('adminEditApproveDeleteBtn')?.addEventListener('click', () => {
  const uid = parseInt(el('adminEditUserId')?.value || '', 10);
  if (uid) adminApproveDeleteUser(uid);
});

el('adminEditSave')?.addEventListener('click', async () => {
  const uid = parseInt(el('adminEditUserId').value, 10);
  const errEl = el('adminEditError');
  hide(errEl);
  if (!currentUser || !isAdmin()) return;
  let creditsVal;
  try {
    creditsVal = parseInt(el('adminEditCredits').value, 10);
    if (Number.isNaN(creditsVal) || creditsVal < 0) throw new Error(t('admin.invalidCredits'));
  } catch (e) {
    errEl.textContent = e.message || t('admin.invalidCreditsShort');
    show(errEl);
    return;
  }
  try {
    const r = await fetch(API_BASE + '/api/admin/users/' + uid, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        admin_user_id: currentUser.id,
        full_name: el('adminEditFullName').value.trim(),
        analysis_credits: creditsVal,
        role: el('adminEditRole').value,
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('admin.errSave'));
    closeModal('modalAdminUser');
    if (uid === currentUser.id && d.user) {
      setUser({
        ...currentUser,
        role: d.user.role,
        full_name: d.user.full_name || currentUser.full_name,
        analysis_credits: d.user.analysis_credits,
      });
      if (!isAdmin()) {
        navigateToPage('intro', { urlMode: 'push' });
        return;
      }
    }
    await refreshAdminView();
  } catch (e) {
    errEl.textContent = e.message || t('common.error');
    show(errEl);
  }
});

el('adminCreateSave')?.addEventListener('click', async () => {
  await adminCreateUser();
});
