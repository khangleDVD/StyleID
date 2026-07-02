// ---------- Payments (manual transfer + QR + polling) ----------
let paymentPollTimer = null;
let activePayment = null; // { id, hex_id }

function formatVnd(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  return new Intl.NumberFormat('vi-VN').format(v) + ' VND';
}

function setPaymentStatus(text, kind) {
  const st = el('paymentStatus');
  if (st) st.textContent = text || '';
  const err = el('paymentError');
  if (kind === 'error') {
    if (err) {
      err.textContent = text || t('common.error');
      show(err);
    }
  } else {
    if (err) {
      err.textContent = '';
      hide(err);
    }
  }
}

function stopPaymentPolling() {
  if (paymentPollTimer) {
    clearInterval(paymentPollTimer);
    paymentPollTimer = null;
  }
}

function closePaymentModal() {
  stopPaymentPolling();
  activePayment = null;
  paymentDoneEnabled = false;
  closeModal('modalPayment');
}

async function pollPaymentStatusOnce() {
  if (!activePayment || !activePayment.hex_id) return;
  try {
    const r = await fetch(API_BASE + '/api/v1/payment/status/' + encodeURIComponent(activePayment.hex_id));
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('js.errPaymentStatus'));

    const status = (d.status || '').toLowerCase();
    if (status === 'completed') {
      setPaymentStatus(t('js.paymentSuccess'), 'ok');
      const btnDone = el('btnPaymentDone');
      if (btnDone) {
        btnDone.disabled = false;
        paymentDoneEnabled = true;
        btnDone.textContent = t('common.close');
      }
      stopPaymentPolling();
      await refreshUserProfile();
      if (el('pagePackages') && el('pagePackages').classList.contains('active')) {
        try { await loadUserPaymentsHistory(); } catch (_) {}
      }
      return;
    }
    if (status === 'failed') {
      // failed vẫn có thể auto completed nếu tiền về sau → tiếp tục polling nhưng báo rõ
      setPaymentStatus(t('js.paymentExpired'), 'warn');
      return;
    }
    setPaymentStatus(t('js.paymentWaiting'), 'ok');
  } catch (e) {
    setPaymentStatus(e.message || t('js.errPaymentCheck'), 'error');
  }
}

function startPaymentPolling() {
  stopPaymentPolling();
  pollPaymentStatusOnce();
  paymentPollTimer = setInterval(pollPaymentStatusOnce, 7000);
}

async function createPaymentForPackage(packageId) {
  if (!currentUser || !currentUser.id) {
    window.location.href = '/login?next=packages';
    return;
  }
  paymentDoneEnabled = false;
  setPaymentStatus(t('js.paymentCreating'), 'ok');
  const btnDone = el('btnPaymentDone');
  if (btnDone) {
    btnDone.disabled = false;
    btnDone.textContent = t('modal.paidChecking');
  }
  const qrImg = el('paymentQrImg');
  const meta = el('paymentMeta');
  const amountEl = el('paymentAmount');
  const contentEl = el('paymentContent');
  if (qrImg) {
    qrImg.style.display = '';
    qrImg.removeAttribute('src');
  }
  if (meta) meta.textContent = '';
  if (amountEl) amountEl.textContent = '—';
  if (contentEl) contentEl.textContent = '—';

  openModal('modalPayment');
  try {
    const res = await fetch(API_BASE + '/api/v1/payment/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: currentUser.id,
        package_id: packageId,
      }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.error || t('js.errCreateInvoice'));

    activePayment = { id: d.id, hex_id: d.hex_id };
    if (amountEl) amountEl.textContent = formatVnd(d.amount_vnd);
    if (contentEl) contentEl.textContent = d.transfer_content || '—';
    if (qrImg) {
      if (d.qr_url) qrImg.src = d.qr_url;
      else qrImg.style.display = 'none';
    }
    if (meta) {
      const exp = d.expires_at ? new Date(d.expires_at).toLocaleString('vi-VN') : '';
      meta.textContent = exp ? t('js.invoiceExpires', { time: exp }) : '';
    }
    setPaymentStatus(t('js.paymentWaiting'), 'ok');
    startPaymentPolling();
  } catch (e) {
    setPaymentStatus(e.message || t('js.errCreateInvoice'), 'error');
  }
}

el('btnCopyPaymentContent')?.addEventListener('click', async () => {
  const c = el('paymentContent')?.textContent || '';
  if (!c || c === '—') return;
  try {
    await navigator.clipboard.writeText(c);
    setPaymentStatus(t('js.copied'), 'ok');
  } catch (_) {
    try {
      const ta = document.createElement('textarea');
      ta.value = c;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setPaymentStatus(t('js.copied'), 'ok');
    } catch (e) {
      setPaymentStatus(t('js.copyFail'), 'error');
    }
  }
});

el('btnPaymentDone')?.addEventListener('click', async () => {
  if (paymentDoneEnabled) {
    closePaymentModal();
    return;
  }
  if (!activePayment?.hex_id) return;
  const btnDone = el('btnPaymentDone');
  if (btnDone) btnDone.disabled = true;
  setPaymentStatus(t('js.paymentChecking'), 'ok');
  await pollPaymentStatusOnce();
  if (btnDone && !paymentDoneEnabled) {
    btnDone.disabled = false;
    btnDone.textContent = t('modal.paidChecking');
  }
});

// đóng modal payment khi bấm nút close (để stop polling)
el('modalPayment')?.querySelector('.modal-close')?.addEventListener('click', () => closePaymentModal());

function renderPackagesEmptyHistory() {
  return (
    '<div class="pkg-v2-empty">' +
    '<div class="pkg-v2-empty-icon"><span class="material-symbols-outlined" aria-hidden="true">receipt_long</span></div>' +
    '<h4>' +
    escapeHtml(t('packages.emptyHistoryTitle')) +
    '</h4>' +
    '<p>' +
    escapeHtml(t('packages.noInvoices')) +
    '</p>' +
    '<a class="pkg-v2-btn-outline" href="/payment-guide">' +
    escapeHtml(t('packages.paymentGuideBtn')) +
    '</a>' +
    '</div>'
  );
}

function buildPackageCard(pkg) {
  const card = document.createElement('article');
  const isPopular = !!pkg.popular;
  card.className = 'pkg-v2-card' + (isPopular ? ' pkg-v2-card--popular' : '');

  if (isPopular) {
    const badge = document.createElement('span');
    badge.className = 'pkg-v2-badge';
    badge.textContent = t('packages.popularBadge');
    card.appendChild(badge);
  }

  const name = (pkg.name || t('packages.pkgName', { key: pkg.key })).trim();
  const tagline = (pkg.tagline || t('packages.pkgTagline', { n: pkg.credits })).trim();
  const feats = Array.isArray(pkg.features) ? pkg.features : [];
  const priceNum = new Intl.NumberFormat('vi-VN').format(pkg.price_vnd || 0);

  const h = document.createElement('h3');
  h.className = 'pkg-v2-card-name';
  h.textContent = name;
  card.appendChild(h);

  const priceEl = document.createElement('div');
  priceEl.className = 'pkg-v2-card-price';
  priceEl.innerHTML =
    '<span class="pkg-v2-card-amount">' +
    escapeHtml(priceNum) +
    '</span><span class="pkg-v2-card-currency">VND</span>';
  card.appendChild(priceEl);

  const tag = document.createElement('span');
  tag.className = 'pkg-v2-card-tag';
  tag.textContent = tagline;
  card.appendChild(tag);

  const ul = document.createElement('ul');
  ul.className = 'pkg-v2-card-features';
  feats.forEach((line) => {
    const li = document.createElement('li');
    li.innerHTML =
      '<span class="material-symbols-outlined" aria-hidden="true">check_circle</span><span>' +
      escapeHtml(String(line)) +
      '</span>';
    ul.appendChild(li);
  });
  card.appendChild(ul);

  const btnPay = document.createElement('button');
  btnPay.type = 'button';
  btnPay.className = 'pkg-v2-btn';
  btnPay.innerHTML =
    '<span class="material-symbols-outlined" aria-hidden="true">bolt</span> ' +
    escapeHtml(t('packages.payBtn'));
  btnPay.disabled = !(currentUser && currentUser.id);
  btnPay.title = btnPay.disabled ? t('packages.payLogin') : t('packages.payTitle');
  btnPay.addEventListener('click', () => {
    if (btnPay.disabled) return;
    const original = btnPay.innerHTML;
    btnPay.classList.add('is-loading');
    btnPay.innerHTML =
      '<span class="material-symbols-outlined" aria-hidden="true">sync</span> ' +
      escapeHtml(t('packages.payLoading'));
    createPaymentForPackage(String(pkg.key || '').trim()).finally(() => {
      btnPay.classList.remove('is-loading');
      btnPay.innerHTML = original;
    });
  });
  card.appendChild(btnPay);

  return card;
}

async function loadUserPaymentsHistory() {
  const wrap = el('userPaymentsWrap');
  const hint = el('userPaymentsHint');
  const mount = el('userPaymentsTable');
  if (!wrap || !mount) return;
  if (!currentUser || !currentUser.id) {
    hide(wrap);
    return;
  }
  show(wrap);
  if (hint) hint.textContent = t('packages.loadingHistory');
  mount.innerHTML = '<p class="pkg-v2-loading">' + escapeHtml(t('js.loadingGeneric')) + '</p>';
  try {
    const r = await fetch(API_BASE + '/api/user/payments?user_id=' + encodeURIComponent(currentUser.id) + '&limit=50');
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('js.errLoadPayments'));
    const items = d.items || [];
    if (hint) hint.textContent = items.length ? t('packages.invoicesTotal', { n: items.length }) : t('packages.noInvoices');
    if (!items.length) {
      mount.innerHTML = renderPackagesEmptyHistory();
      return;
    }
    const rows = items.map((p) => {
      const st = (p.status || '').toString();
      const stClass = st === 'completed' ? 'admin-badge-admin' : (st === 'failed' ? 'admin-badge-user' : 'admin-badge-user');
      const created = p.created_at ? new Date(p.created_at).toLocaleString('vi-VN') : '';
      const completed = p.completed_at ? new Date(p.completed_at).toLocaleString('vi-VN') : '—';
      const sepay = p.sepay_tx_id ? escapeHtml(String(p.sepay_tx_id)) : '—';
      const memo = escapeHtml(p.transfer_content || '');
      return `<tr>
        <td>${p.id}</td>
        <td>${escapeHtml(String(p.package_id || ''))}</td>
        <td>${p.credits != null ? p.credits : '—'}</td>
        <td>${formatVnd(p.amount_vnd)}</td>
        <td><span class="admin-badge ${stClass}">${escapeHtml(st || '—')}</span></td>
        <td>${escapeHtml(created)}</td>
        <td>${escapeHtml(completed)}</td>
        <td>${sepay}</td>
        <td class="hint" style="max-width:360px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${memo}">${memo}</td>
      </tr>`;
    }).join('');
    mount.innerHTML =
      '<div class="pkg-v2-table-wrap"><table class="admin-table"><thead><tr>' +
      '<th>' +
      escapeHtml(t('packages.colId')) +
      '</th><th>' +
      escapeHtml(t('packages.colPlan')) +
      '</th><th>' +
      escapeHtml(t('packages.colCredits')) +
      '</th><th>' +
      escapeHtml(t('packages.colAmount')) +
      '</th><th>' +
      escapeHtml(t('packages.colStatus')) +
      '</th><th>' +
      escapeHtml(t('packages.colCreated')) +
      '</th><th>' +
      escapeHtml(t('packages.colCompleted')) +
      '</th><th>' +
      escapeHtml(t('packages.colSePay')) +
      '</th><th>' +
      escapeHtml(t('packages.colMemo')) +
      '</th>' +
      '</tr></thead><tbody>' +
      rows +
      '</tbody></table></div>';
  } catch (e) {
    if (hint) hint.textContent = '';
    mount.innerHTML = '<p class="pkg-v2-loading">' + escapeHtml(e.message || t('history.loadError')) + '</p>';
  }
}

async function loadPackagesPage() {
  const grid = el('packagesGrid');
  const hint = el('packagesPageHint');
  const hintWrap = el('packagesPageHintWrap');
  const errEl = el('packagesPageError');
  if (errEl) hide(errEl);
  if (!grid) return;
  grid.innerHTML = '<p class="pkg-v2-loading">' + escapeHtml(t('js.loadingGeneric')) + '</p>';
  try {
    const r = await fetch(API_BASE + '/api/packages');
    const data = await r.json();
    if (hint) {
      hint.textContent =
        currentUser && currentUser.id ? t('packages.hintLoggedIn') : t('packages.hintGuest');
    }
    if (hintWrap) {
      hintWrap.classList.toggle('is-guest', !(currentUser && currentUser.id));
    }
    grid.innerHTML = '';
    (data.packages || []).forEach((pkg) => {
      grid.appendChild(buildPackageCard(pkg));
    });
    await loadUserPaymentsHistory();
  } catch (_) {
    grid.innerHTML = '';
    if (errEl) {
      errEl.textContent = t('packages.loadError');
      show(errEl);
    }
  }
}

if (el('userMenuBuyCredits')) {
  el('userMenuBuyCredits').addEventListener('click', (e) => {
    e.stopPropagation();
    closeUserMenu();
    navigateToPage('packages', { urlMode: 'push' });
  });
}
if (el('userMenuChangePassword')) {
  el('userMenuChangePassword').addEventListener('click', (e) => {
    e.stopPropagation();
    closeUserMenu();
    openModal('modalChangePassword');
  });
}
if (el('userMenuLogout')) {
  el('userMenuLogout').addEventListener('click', (e) => {
    e.stopPropagation();
    closeUserMenu();
    logoutAndGoHome();
  });
}
el('btnSidebarLogout')?.addEventListener('click', () => {
  closeUserMenu();
  logoutAndGoHome();
});

qsAll('.modal-close').forEach(btn => {
  btn.addEventListener('click', () => {
    const m = btn.closest('.modal');
    if (m && m.id === 'modalImageLightbox') closeImageLightbox();
    else if (m) closeModal(m.id);
  });
});

el('modalImageLightbox')?.addEventListener('click', (e) => {
  if (e.target === el('modalImageLightbox')) closeImageLightbox();
});
el('modalImageLightboxInner')?.addEventListener('click', (e) => e.stopPropagation());

el('formLogin')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const errEl = el('loginError');
  hide(errEl);
  try {
    const res = await fetch(API_BASE + '/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || t('js.errLogin'));
    try { localStorage.removeItem(ACCESS_TOKEN_KEY); } catch (_) {}
    setUser(data);
    closeModal('modalLogin');
    form.reset();
    if (data.force_change_password) {
      window.location.href = '/change-password';
      return;
    }
  } catch (err) {
    errEl.textContent = err.message;
    show(errEl);
  }
});

el('formRegister')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const errEl = el('registerError');
  hide(errEl);
  try {
    const res = await fetch(API_BASE + '/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: form.username.value.trim(),
        password: form.password.value,
        full_name: (form.full_name && form.full_name.value.trim()) || t('js.defaultUser'),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail ? data.error + '\n' + data.detail : (data.error || t('js.errRegister'));
      errEl.textContent = msg;
      show(errEl);
      return;
    }
    setUser(null);
    closeModal('modalRegister');
    form.reset();
    alert(t('js.registerOk'));
  } catch (err) {
    errEl.textContent = err.message || t('js.errRegister');
    show(errEl);
  }
});

el('formChangePassword')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const errEl = el('changePasswordError');
  hide(errEl);
  const newPw = form.new_password.value;
  const confirmPw = form.new_password_confirm.value;
  if (newPw !== confirmPw) {
    errEl.textContent = t('js.pwdMismatch');
    show(errEl);
    return;
  }
  if (!currentUser || !currentUser.id) {
    errEl.textContent = t('js.loginAgain');
    show(errEl);
    return;
  }
  try {
    const res = await fetch(API_BASE + '/api/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: currentUser.id,
        old_password: form.old_password.value,
        new_password: newPw,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || t('js.errChangePwd'));
    closeModal('modalChangePassword');
    form.reset();
    alert(t('js.pwdChanged'));
    // Bắt buộc đăng nhập lại sau khi đổi mật khẩu
    setUser(null);
    window.location.href = '/login';
  } catch (err) {
    errEl.textContent = err.message;
    show(errEl);
  }
});


const PAGE_TO_TITLE_KEY = {
  intro: 'meta.title',
  analyze: 'meta.titleAnalyze',
  history: 'meta.titleHistory',
  stats: 'meta.titleStats',
  packages: 'meta.titlePackages',
  profile: 'meta.titleProfile',
  admin: 'meta.titleAdmin',
};

const PAGE_NAV_TITLE_KEY = {
  intro: 'nav.home',
  analyze: 'nav.analyze',
  history: 'nav.history',
  stats: 'nav.stats',
  packages: 'nav.packages',
  profile: 'nav.profile',
  admin: 'nav.admin',
};

function getPageFromPathname() {
  const raw = (window.location.pathname || '/').toLowerCase().replace(/\/+$/, '');
  const seg = raw.startsWith('/') ? raw.slice(1) : raw;
  if (!seg) return 'intro';
  const allowed = new Set(['intro', 'analyze', 'packages', 'history', 'stats', 'profile', 'admin']);
  return allowed.has(seg) ? seg : null;
}

function pageToPath(page) {
  return page === 'intro' ? '/' : '/' + String(page || '').trim();
}

function setPageTitle(page) {
  const key = PAGE_TO_TITLE_KEY[page] || 'meta.title';
  const title = t(key);
  if (title) document.title = title;
  syncTopbarPageTitle(page);
}

function navigateToPage(page, opts) {
  opts = opts || {};
  const ok = showPage(page, opts);
  if (!ok) return false;

  const urlMode = opts.urlMode || 'push';
  const targetPath = pageToPath(page).replace(/\/+$/, '') || '/';
  const currentPath = (window.location.pathname || '/').replace(/\/+$/, '') || '/';
  if (targetPath === currentPath) return true;

  try {
    if (urlMode === 'replace' && window.history.replaceState) {
      window.history.replaceState({}, '', targetPath);
    } else if (window.history.pushState) {
      window.history.pushState({}, '', targetPath);
    }
  } catch (_) {}
  return true;
}

function isLandingMobileLayout() {
  return window.matchMedia('(max-width: 640px)').matches;
}

function restoreTopbarRightStructure() {
  const right = qs('.topbar-right');
  const langIcons = qs('.shell-v2-topbar-icons');
  const authBlock = el('topbarAuthBlock');
  if (!right) return;
  if (langIcons && langIcons.parentElement !== right) {
    right.insertBefore(langIcons, right.firstChild);
  }
  if (authBlock && authBlock.parentElement !== right) {
    right.appendChild(authBlock);
  }
}

function syncLandingAuthSlot() {
  const shell = qs('.app-shell');
  const slot = el('landingNavAuthSlot');
  const langSlot = el('landingNavLangSlot');
  const sidebarAuthSlot = el('sidebarAuthSlot');
  const topbar = qs('.app-topbar');
  const right = qs('.topbar-right');
  const langIcons = qs('.shell-v2-topbar-icons');
  const authBlock = el('topbarAuthBlock');
  if (!topbar || !right) return;

  restoreTopbarRightStructure();

  const onLanding = shell && shell.classList.contains('app-shell--landing');
  const mobileLanding = onLanding && isLandingMobileLayout();

  if (mobileLanding) {
    if (langSlot && langIcons) langSlot.appendChild(langIcons);
    if (sidebarAuthSlot && authBlock) sidebarAuthSlot.appendChild(authBlock);
    return;
  }

  if (onLanding) {
    if (slot) slot.appendChild(right);
    return;
  }

  topbar.appendChild(right);
}

function showPage(page, opts) {
  opts = opts || {};
  currentPage = page;
  closeMobileNav();
  if (redirectToChangePasswordIfNeeded()) return false;
  if (redirectToPendingDeleteIfNeeded()) return false;
  if (isProtectedPage(page) && (!currentUser || !currentUser.id)) {
    redirectToLogin(page);
    return false;
  }
  if (page === 'admin' && !isAdmin()) return false;
  const shell = qs('.app-shell');
  if (shell) {
    if (page === 'intro') shell.classList.add('app-shell--landing');
    else shell.classList.remove('app-shell--landing');
    syncAdminShell(page);
  }
  qsAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.page === page));
  qsAll('.page').forEach(p => p.classList.remove('active'));
  if (page === 'intro') {
    el('pageIntro').classList.add('active');
    const landScroll = qs('.landing-scroll');
    if (landScroll && !opts.scrollToId) landScroll.scrollTop = 0;
    if (opts.scrollToId) {
      const id = opts.scrollToId;
      setTimeout(() => {
        const t = document.getElementById(id);
        if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 80);
    }
  } else if (page === 'analyze') {
    el('pageAnalyze').classList.add('active');
  } else if (page === 'packages') {
    el('pagePackages').classList.add('active');
    loadPackagesPage();
  } else if (page === 'history') {
    el('pageHistory').classList.add('active');
    loadHistory();
  } else if (page === 'stats') {
    el('pageStats').classList.add('active');
    loadStats();
  } else if (page === 'profile') {
    el('pageProfile').classList.add('active');
    loadProfilePage();
  } else if (page === 'admin') {
    el('pageAdmin').classList.add('active');
    updateAdminTopbar();
    qsAll('.admin-v2-tab, .admin-v2-nav-btn').forEach((b) => {
      b.classList.toggle('is-active', b.dataset.adtab === adminActiveTab);
    });
    refreshAdminView();
  }
  syncLandingAuthSlot();
  setPageTitle(page);
  try {
    document.dispatchEvent(new CustomEvent('pagechange', { detail: { page } }));
  } catch (_) {}
  return true;
}

function handleNextFromUrl(params) {
  try {
    const next = (params && params.get ? (params.get('next') || '') : '').trim();
    const allowed = new Set(['intro', 'analyze', 'packages', 'history', 'stats', 'profile', 'admin']);
    if (!next || !allowed.has(next)) return false;
    // Chỉ xử lý sau khi đã khôi phục phiên (currentUser) để tránh loop /login <-> /
    setTimeout(() => navigateToPage(next, { urlMode: 'replace' }), 0);
    return true;
  } catch (_) {}
  return false;
}

window.addEventListener('popstate', () => {
  const p = getPageFromPathname();
  if (p) showPage(p);
});

qsAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    closeMobileNav();
    navigateToPage(btn.dataset.page, { urlMode: 'push' });
  });
});

el('btnNavOpen')?.addEventListener('click', (e) => {
  e.stopPropagation();
  openMobileNav();
});
el('btnNavClose')?.addEventListener('click', () => closeMobileNav());
el('navBackdrop')?.addEventListener('click', () => closeMobileNav());
window.addEventListener('resize', () => {
  if (window.innerWidth > 900) {
    closeMobileNav();
    closeAdminDrawer();
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const lb = el('modalImageLightbox');
  if (lb && !lb.classList.contains('hidden')) {
    closeImageLightbox();
    e.preventDefault();
    return;
  }
  if (qs('.app-shell')?.classList.contains('nav-drawer-open')) closeMobileNav();
  if (el('pageAdmin')?.classList.contains('admin-drawer-open')) closeAdminDrawer();
});

document.body.addEventListener('click', (e) => {
  const t = e.target;
  if (!(t instanceof HTMLImageElement)) return;
  if (!t.classList.contains('img-lightbox')) return;
  const src = t.currentSrc || t.src;
  if (!src) return;
  e.preventDefault();
  e.stopPropagation();
  openImageLightbox(src, t.alt || 'Ảnh');
});

qsAll('.logo-home').forEach((btn) => {
  btn.addEventListener('click', () => navigateToPage('intro', { urlMode: 'push' }));
});
el('btnIntroAnalyze')?.addEventListener('click', () => navigateToPage('analyze', { urlMode: 'push' }));
function bindLandingDashboardClicks() {
  qsAll('.js-landing-dashboard').forEach((btn) => {
    btn.addEventListener('click', () => navigateToPage('analyze', { urlMode: 'push' }));
  });
}

function bindGlobalFooterLinks() {
  document.querySelectorAll('.js-landing-nav-page').forEach((link) => {
    if (link.dataset.footerBound) return;
    link.dataset.footerBound = '1';
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const page = link.dataset.page;
      if (page && typeof navigateToPage === 'function') {
        navigateToPage(page, { urlMode: 'push' });
      }
    });
  });
  document.querySelectorAll('.js-footer-intro-anchor').forEach((link) => {
    if (link.dataset.footerBound) return;
    link.dataset.footerBound = '1';
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const sectionId = link.dataset.section || 'landing-features';
      const introEl = el('pageIntro');
      if (introEl && introEl.classList.contains('active')) {
        document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
      navigateToPage('intro', { scrollToId: sectionId, urlMode: 'push' });
    });
  });
}
bindLandingDashboardClicks();
bindGlobalFooterLinks();
syncLandingAuthSlot();
syncLandingCta();
window.matchMedia('(max-width: 640px)').addEventListener('change', () => syncLandingAuthSlot());
el('btnLandingMenu')?.addEventListener('click', (e) => {
  e.stopPropagation();
  openMobileNav();
});
el('btnIntroHow')?.addEventListener('click', () => {
  const introEl = el('pageIntro');
  if (introEl && introEl.classList.contains('active')) {
    el('intro-how')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else {
    navigateToPage('intro', { scrollToId: 'intro-how', urlMode: 'replace' });
  }
});


// ---------- Lịch sử ----------
async function loadHistory() {
  const hint = el('historyLoginHint');
  const list = el('historyList');
  list.innerHTML = '';
  if (!currentUser || !currentUser.id) {
    show(hint);
    return;
  }
  hide(hint);
  list.innerHTML = '<p class="hint">' + escapeHtml(t('js.loadingGeneric')) + '</p>';
  try {
    const res = await fetch(API_BASE + '/api/history/' + currentUser.id);
    const rows = await res.json();
    list.innerHTML = '';
    if (!res.ok || !Array.isArray(rows)) {
      list.innerHTML = '<p class="hint">' + escapeHtml(t('history.loadError')) + '</p>';
      return;
    }
    if (rows.length === 0) {
      list.innerHTML = '<p class="hint">' + escapeHtml(t('history.empty')) + '</p>';
      return;
    }
    rows.forEach(row => {
      const resData = row.analysis_result || {};
      const item = document.createElement('div');
      item.className = 'history-item';
      const thumbs = document.createElement('div');
      thumbs.className = 'thumbs';
      const urls = (resData.image_urls && resData.image_urls.length) ? resData.image_urls : (row.image_url ? [row.image_url] : []);
      urls.forEach(url => {
        const img = document.createElement('img');
        img.alt = t('js.analyzedImage');
        img.classList.add('img-lightbox');
        img.title = t('js.viewLarge');
        if (bindUploadImage(img, url)) thumbs.appendChild(img);
      });
      const meta = document.createElement('div');
      meta.className = 'meta';
      const dateStr = row.timestamp ? new Date(row.timestamp).toLocaleString('vi-VN') : '';
      const nPhotos = resData.image_count
        || (resData.image_urls && resData.image_urls.length)
        || (resData.results && resData.results.length)
        || (urls.length ? urls.length : 0);
      const metaLabel = nPhotos > 0
        ? t('history.photos', { n: nPhotos })
        : ((resData.items || []).length ? t('history.items', { n: (resData.items || []).length }) : t('history.analysis'));
      meta.textContent = `${metaLabel} · ${dateStr}`;
      const styleBadge = document.createElement('span');
      styleBadge.className = 'style-badge';
      styleBadge.textContent = overallStyleDisplayEn(
        (resData.results && resData.results[0])
          ? resData.results[0].overall_style
          : (resData.overall_style || ''),
      ) || '—';
      const btnDelete = document.createElement('button');
      btnDelete.type = 'button';
      btnDelete.className = 'btn-delete-history';
      btnDelete.textContent = t('common.delete');
      btnDelete.title = t('history.deleteTitle');
      btnDelete.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (!confirm(t('history.deleteConfirm'))) return;
        deleteHistoryRow(row.id);
      });
      const actionsCell = document.createElement('div');
      actionsCell.className = 'history-item-actions';
      actionsCell.appendChild(styleBadge);
      actionsCell.appendChild(btnDelete);
      item.appendChild(thumbs);
      item.appendChild(meta);
      item.appendChild(actionsCell);
      item.style.cursor = 'pointer';
      item.addEventListener('click', async () => {
        const payload = { ...resData };
        payload.image_urls = resData.image_urls || (row.image_url ? [row.image_url] : []);
        await displayResult(payload);
        show(result);
        syncAnalyzeResultEmpty();
        closeMobileNav();
        navigateToPage('analyze', { urlMode: 'push' });
      });
      list.appendChild(item);
    });
  } catch (err) {
    list.innerHTML = '<p class="hint">' + escapeHtml(t('history.loadError')) + '</p>';
  }
}

async function deleteHistoryRow(historyId) {
  if (!currentUser || !currentUser.id) return;
  try {
    const res = await fetch(API_BASE + '/api/history/' + historyId + '?user_id=' + currentUser.id, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || t('js.errDelete'));
    loadHistory();
  } catch (err) {
    alert(err.message || t('js.errDeleteRow'));
  }
}

// ---------- Thống kê ----------
function renderStatsRankList(items, emptyKey, nameField, labelFn) {
  if (!items || !items.length) {
    return '<p class="stats-v2-empty">' + escapeHtml(t(emptyKey)) + '</p>';
  }
  const max = Math.max(...items.map((x) => Number(x.count) || 0), 1);
  return (
    '<div class="stats-v2-rank-list">' +
    items
      .map((row, i) => {
        const count = Number(row.count) || 0;
        const pct = Math.round((count / max) * 100);
        const raw = row[nameField] != null ? String(row[nameField]) : '—';
        const name = labelFn ? labelFn(raw, row) : raw;
        return (
          '<div class="stats-v2-rank-row">' +
          '<span class="stats-v2-rank-idx">' +
          (i + 1) +
          '</span>' +
          '<div class="stats-v2-rank-body">' +
          '<div class="stats-v2-rank-top">' +
          '<span class="stats-v2-rank-name">' +
          escapeHtml(name) +
          '</span>' +
          '<span class="stats-v2-rank-count">' +
          count +
          '</span>' +
          '</div>' +
          '<div class="stats-v2-rank-bar-track"><div class="stats-v2-rank-bar" style="width:' +
          pct +
          '%"></div></div>' +
          '</div>' +
          '</div>'
        );
      })
      .join('') +
    '</div>'
  );
}

function formatStatsCategoryLabel(raw) {
  const s = (raw || '').trim().toLowerCase();
  if (!s || s === 'other') return t('stats.categoryOther');
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function statsNiceDayLabel(isoDay) {
  const s = String(isoDay || '');
  return s.length >= 10 ? s.slice(5) : s;
}

function buildStatsAreaChart(series, opts) {
  opts = opts || {};
  const title = opts.title || '';
  const emptyKey = opts.emptyKey || 'stats.chartNoDayData';
  const data = Array.isArray(series) ? series : [];
  if (!data.length) {
    return '<p class="stats-v2-empty">' + escapeHtml(t(emptyKey)) + '</p>';
  }
  const w = 720;
  const h = 180;
  const padX = 18;
  const padY = 20;
  const vals = data.map((x) => Number(x.count) || 0);
  const maxVal = Math.max(1, ...vals);
  const n = Math.max(2, data.length);
  const innerW = w - padX * 2;
  const innerH = h - padY * 2 - 18;
  const step = innerW / (n - 1);
  const pts = data.map((d, i) => {
    const v = Number(d.count) || 0;
    const x = padX + i * step;
    const y = padY + (innerH - innerH * (v / maxVal));
    return { x, y, v, label: statsNiceDayLabel(d.day) };
  });
  const pathLine = pts.map((p, i) => (i === 0 ? `M ${p.x} ${p.y}` : `L ${p.x} ${p.y}`)).join(' ');
  const area = `${pathLine} L ${padX + (pts.length - 1) * step} ${padY + innerH} L ${padX} ${padY + innerH} Z`;
  const grid = [0.25, 0.5, 0.75, 1]
    .map((tVal) => {
      const y = padY + innerH - innerH * tVal;
      return `<line x1="${padX}" y1="${y}" x2="${w - padX}" y2="${y}" stroke="rgba(255,255,255,0.06)"/>`;
    })
    .join('');
  return (
    '<div class="stats-v2-chart">' +
    '<svg viewBox="0 0 ' +
    w +
    ' ' +
    h +
    '" width="100%" height="' +
    h +
    '" role="img" aria-label="' +
    escapeHtml(title) +
    '">' +
    '<defs>' +
    '<linearGradient id="statsAreaGrad" x1="0" x2="0" y1="0" y2="1">' +
    '<stop offset="0%" stop-color="rgba(255,209,101,0.35)"/>' +
    '<stop offset="100%" stop-color="rgba(255,209,101,0.03)"/>' +
    '</linearGradient>' +
    '<linearGradient id="statsLineGrad" x1="0" x2="1" y1="0" y2="0">' +
    '<stop offset="0%" stop-color="rgba(255,209,101,1)"/>' +
    '<stop offset="100%" stop-color="rgba(234,179,8,0.85)"/>' +
    '</linearGradient>' +
    '</defs>' +
    grid +
    '<path d="' +
    area +
    '" fill="url(#statsAreaGrad)" stroke="none"/>' +
    '<path d="' +
    pathLine +
    '" fill="none" stroke="url(#statsLineGrad)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>' +
    pts
      .map(
        (p) =>
          '<circle cx="' +
          p.x +
          '" cy="' +
          p.y +
          '" r="3.5" fill="rgba(255,209,101,0.95)" stroke="rgba(0,0,0,0.35)"/>',
      )
      .join('') +
    pts
      .map((p, i) =>
        i === 0 || i === pts.length - 1 || i % 3 === 0
          ? '<text x="' +
            p.x +
            '" y="' +
            (padY + innerH + 16) +
            '" text-anchor="middle" fill="#9b8f79" font-size="10">' +
            escapeHtml(p.label) +
            '</text>'
          : '',
      )
      .join('') +
    '</svg></div>'
  );
}

function renderStatsPrimaryStyle(primary) {
  const card = el('statsPrimaryStyle');
  const nameEl = el('statsPrimaryStyleName');
  const metaEl = el('statsPrimaryStyleMeta');
  if (!card || !nameEl || !metaEl) return;
  if (!primary || !primary.style) {
    hide(card);
    return;
  }
  show(card);
  nameEl.textContent = primary.style;
  metaEl.textContent = t('stats.primaryStyleMeta', {
    style: primary.style,
    count: primary.count ?? 0,
    percent: primary.percent ?? 0,
  });
}

function formatStatsDelta(delta, pct) {
  const n = Number(delta) || 0;
  const sign = n > 0 ? '+' : '';
  if (pct != null && Number.isFinite(Number(pct))) {
    return t('stats.deltaFmt', { sign, n, pct });
  }
  return t('stats.deltaFmtNoPct', { sign, n });
}

function statsCompareWindowKey(period) {
  if (period === '7d') return 'stats.compare7d';
  if (period === '30d') return 'stats.compare30d';
  return 'stats.compareAll';
}

function renderStatsPeriodComparison(comparison, period) {
  const card = el('statsPeriodComparison');
  const labelEl = el('statsCompareWindowLabel');
  const bodyEl = el('statsCompareBody');
  if (!card || !bodyEl) return;
  if (!comparison) {
    hide(card);
    return;
  }
  show(card);
  if (labelEl) labelEl.textContent = t(statsCompareWindowKey(period));

  const prevA = Number(comparison.previous_analyses) || 0;
  const deltaA = Number(comparison.delta_analyses) || 0;
  const deltaAPct = comparison.delta_analyses_pct;
  const prevI = Number(comparison.previous_images) || 0;
  const deltaI = Number(comparison.delta_images) || 0;
  const deltaIPct = comparison.delta_images_pct;
  const curPs = comparison.current_primary_style;
  const prevPs = comparison.previous_primary_style;

  let styleHtml = '';
  if (curPs && prevPs && comparison.primary_style_changed) {
    styleHtml =
      '<p class="stats-v2-compare-style">' +
      escapeHtml(t('stats.compareStyleChanged', { prev: prevPs, cur: curPs })) +
      '</p>';
  } else if (curPs) {
    styleHtml =
      '<p class="stats-v2-compare-style">' +
      escapeHtml(t('stats.compareStyleSame', { style: curPs })) +
      '</p>';
  } else {
    styleHtml = '<p class="stats-v2-compare-style stats-v2-compare-muted">' + escapeHtml(t('stats.compareNoPrev')) + '</p>';
  }

  const deltaClass = (d) => (d > 0 ? 'up' : d < 0 ? 'down' : 'flat');

  bodyEl.innerHTML =
    '<div class="stats-v2-compare-metric">' +
    '<span class="stats-v2-compare-label">' +
    escapeHtml(t('stats.compareAnalyses')) +
    '</span>' +
    '<span class="stats-v2-compare-values">' +
    '<strong>' +
    (comparison.current_analyses ?? 0) +
    '</strong>' +
    '<span class="stats-v2-compare-vs">vs ' +
    prevA +
    '</span>' +
    '</span>' +
    '<span class="stats-v2-compare-delta stats-v2-compare-delta--' +
    deltaClass(deltaA) +
    '">' +
    escapeHtml(formatStatsDelta(deltaA, prevA > 0 ? deltaAPct : null)) +
    '</span>' +
    '</div>' +
    '<div class="stats-v2-compare-metric">' +
    '<span class="stats-v2-compare-label">' +
    escapeHtml(t('stats.compareImages')) +
    '</span>' +
    '<span class="stats-v2-compare-values">' +
    '<strong>' +
    (comparison.current_images ?? 0) +
    '</strong>' +
    '<span class="stats-v2-compare-vs">vs ' +
    prevI +
    '</span>' +
    '</span>' +
    '<span class="stats-v2-compare-delta stats-v2-compare-delta--' +
    deltaClass(deltaI) +
    '">' +
    escapeHtml(formatStatsDelta(deltaI, prevI > 0 ? deltaIPct : null)) +
    '</span>' +
    '</div>' +
    '<div class="stats-v2-compare-metric stats-v2-compare-metric--wide">' +
    '<span class="stats-v2-compare-label">' +
    escapeHtml(t('stats.comparePrimaryStyle')) +
    '</span>' +
    styleHtml +
    '</div>';
}

function renderStatsAvgConfidence(avg, samples) {
  const valEl = el('statAvgConfidence');
  const hintEl = el('statAvgConfidenceHint');
  if (!valEl) return;
  if (avg == null || !Number.isFinite(Number(avg))) {
    valEl.textContent = '—';
    if (hintEl) {
      hintEl.textContent = t('stats.noConfidenceData');
      hintEl.classList.remove('hidden');
    }
    return;
  }
  valEl.textContent = Math.round(Number(avg)) + '%';
  if (hintEl) {
    const n = Number(samples) || 0;
    hintEl.textContent = n > 0 ? t('stats.avgConfidenceHint', { n }) : '';
    hintEl.classList.toggle('hidden', n <= 0);
  }
}

let lastStatsSnapshot = null;

function statsPeriodLabel(period) {
  if (period === '7d') return t('stats.period7d');
  if (period === '30d') return t('stats.period30d');
  return t('common.all');
}

function buildStatsExportPayload(data, period) {
  const user = currentUser || {};
  return {
    report_type: 'styleid_personal_stats',
    generated_at: new Date().toISOString(),
    period,
    period_label: statsPeriodLabel(period),
    user: {
      id: user.id || null,
      username: user.username || null,
      full_name: user.full_name || null,
    },
    stats: data,
  };
}

function updateStatsExportControls() {
  const btnJson = el('statsExportJson');
  const btnPrint = el('statsExportPrint');
  const hint = el('statsExportHint');
  const hasData = !!(
    lastStatsSnapshot &&
    lastStatsSnapshot.stats &&
    Number(lastStatsSnapshot.stats.total_analyses) > 0
  );
  [btnJson, btnPrint].forEach((btn) => {
    if (btn) btn.disabled = !hasData;
  });
  if (hint) hint.classList.toggle('hidden', hasData);
}

function statsReportFilename(ext) {
  const user = (currentUser && currentUser.username) || 'user';
  const period = (lastStatsSnapshot && lastStatsSnapshot.period) || 'all';
  const stamp = new Date().toISOString().slice(0, 10);
  return 'styleid-stats-' + user + '-' + period + '-' + stamp + '.' + ext;
}

function downloadBlobFile(blob, filename) {
  try {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (_) {}
}

function downloadStatsReportJson() {
  if (!lastStatsSnapshot) return;
  const json = JSON.stringify(lastStatsSnapshot, null, 2);
  downloadBlobFile(new Blob([json], { type: 'application/json;charset=utf-8' }), statsReportFilename('json'));
}

function statsReportTableHtml(title, rows, nameField, labelFn, emptyKey) {
  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    return (
      '<section class="block"><h2>' +
      escapeHtml(title) +
      '</h2><p class="empty">' +
      escapeHtml(t(emptyKey)) +
      '</p></section>'
    );
  }
  const body = items
    .map((row, i) => {
      const raw = row[nameField] != null ? String(row[nameField]) : '—';
      const name = labelFn ? labelFn(raw, row) : raw;
      return (
        '<tr><td>' +
        (i + 1) +
        '</td><td>' +
        escapeHtml(name) +
        '</td><td>' +
        (Number(row.count) || 0) +
        '</td></tr>'
      );
    })
    .join('');
  return (
    '<section class="block"><h2>' +
    escapeHtml(title) +
    '</h2><table><thead><tr><th>#</th><th>' +
    escapeHtml(title) +
    '</th><th>' +
    escapeHtml(t('stats.colCount')) +
    '</th></tr></thead><tbody>' +
    body +
    '</tbody></table></section>'
  );
}

function buildStatsReportPrintDocument(payload) {
  const stats = (payload && payload.stats) || {};
  const user = payload && payload.user;
  const displayName = (user && (user.full_name || user.username)) || '—';
  const generated = payload && payload.generated_at ? new Date(payload.generated_at) : new Date();
  const generatedText = generated.toLocaleString();
  const period = payload && payload.period_label ? payload.period_label : statsPeriodLabel(payload.period);
  const primary = stats.primary_style;
  const cmp = stats.period_comparison;
  const avgConf =
    stats.avg_confidence != null && Number.isFinite(Number(stats.avg_confidence))
      ? Math.round(Number(stats.avg_confidence)) + '%'
      : '—';

  let compareHtml = '';
  if (cmp) {
    compareHtml =
      '<section class="block"><h2>' +
      escapeHtml(t('stats.reportSectionCompare')) +
      '</h2><p class="sub">' +
      escapeHtml(t(statsCompareWindowKey(payload.period))) +
      '</p><table><tbody>' +
      '<tr><td>' +
      escapeHtml(t('stats.compareAnalyses')) +
      '</td><td>' +
      (cmp.current_analyses ?? 0) +
      ' vs ' +
      (cmp.previous_analyses ?? 0) +
      ' (' +
      escapeHtml(formatStatsDelta(Number(cmp.delta_analyses) || 0, cmp.previous_analyses > 0 ? cmp.delta_analyses_pct : null)) +
      ')</td></tr>' +
      '<tr><td>' +
      escapeHtml(t('stats.compareImages')) +
      '</td><td>' +
      (cmp.current_images ?? 0) +
      ' vs ' +
      (cmp.previous_images ?? 0) +
      ' (' +
      escapeHtml(formatStatsDelta(Number(cmp.delta_images) || 0, cmp.previous_images > 0 ? cmp.delta_images_pct : null)) +
      ')</td></tr>' +
      '<tr><td>' +
      escapeHtml(t('stats.comparePrimaryStyle')) +
      '</td><td>' +
      escapeHtml(
        cmp.primary_style_changed && cmp.current_primary_style && cmp.previous_primary_style
          ? t('stats.compareStyleChanged', { prev: cmp.previous_primary_style, cur: cmp.current_primary_style })
          : cmp.current_primary_style
            ? t('stats.compareStyleSame', { style: cmp.current_primary_style })
            : t('stats.compareNoPrev'),
      ) +
      '</td></tr></tbody></table></section>';
  }

  const dailyRows = (stats.by_day || [])
    .map(
      (d) =>
        '<tr><td>' +
        escapeHtml(String(d.day || '')) +
        '</td><td>' +
        (Number(d.count) || 0) +
        '</td></tr>',
    )
    .join('');
  const dailyHtml =
    '<section class="block"><h2>' +
    escapeHtml(t('stats.reportSectionDaily')) +
    '</h2>' +
    (dailyRows
      ? '<table><thead><tr><th>' +
        escapeHtml(t('stats.reportSectionDaily')) +
        '</th><th>' +
        escapeHtml(t('stats.colCount')) +
        '</th></tr></thead><tbody>' +
        dailyRows +
        '</tbody></table>'
      : '<p class="empty">' + escapeHtml(t('stats.chartNoDayData')) + '</p>') +
    '</section>';

  return (
    '<!DOCTYPE html><html lang="' +
    (document.documentElement.lang || 'vi') +
    '"><head><meta charset="utf-8"><title>' +
    escapeHtml(t('stats.reportTitle')) +
    '</title><style>' +
    'body{font-family:Segoe UI,system-ui,sans-serif;color:#111;margin:24px;line-height:1.45}' +
    'h1{font-size:1.45rem;margin:0 0 6px} .meta{color:#555;font-size:.9rem;margin:0 0 18px}' +
    '.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:0 0 18px}' +
    '.kpi{border:1px solid #ddd;border-radius:8px;padding:10px 12px;background:#fafafa}' +
    '.kpi strong{display:block;font-size:1.2rem;color:#111}' +
    '.kpi span{font-size:.78rem;color:#666;text-transform:uppercase;letter-spacing:.04em}' +
    'h2{font-size:1rem;margin:0 0 8px;border-bottom:1px solid #eee;padding-bottom:4px}' +
    '.block{margin:0 0 18px;break-inside:avoid}' +
    'table{width:100%;border-collapse:collapse;font-size:.9rem}' +
    'th,td{border:1px solid #e5e5e5;padding:6px 8px;text-align:left}' +
    'th{background:#f5f5f5;font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}' +
    '.sub{margin:-4px 0 8px;color:#666;font-size:.85rem}' +
    '.empty{color:#777;font-size:.9rem}' +
    '.footer{margin-top:24px;padding-top:10px;border-top:1px solid #eee;font-size:.78rem;color:#777}' +
    '@media print{body{margin:12mm} .kpis{grid-template-columns:repeat(3,1fr)}}' +
    '</style></head><body>' +
    '<h1>' +
    escapeHtml(t('stats.reportTitle')) +
    '</h1>' +
    '<p class="meta">' +
    escapeHtml(t('stats.reportUser')) +
    ': <strong>' +
    escapeHtml(displayName) +
    '</strong> · ' +
    escapeHtml(t('stats.reportPeriod')) +
    ': <strong>' +
    escapeHtml(period) +
    '</strong> · ' +
    escapeHtml(t('stats.reportGenerated')) +
    ': ' +
    escapeHtml(generatedText) +
    '</p>' +
    '<section class="block"><h2>' +
    escapeHtml(t('stats.reportSummary')) +
    '</h2><div class="kpis">' +
    '<div class="kpi"><strong>' +
    (stats.total_analyses ?? 0) +
    '</strong><span>' +
    escapeHtml(t('stats.analyses')) +
    '</span></div>' +
    '<div class="kpi"><strong>' +
    (stats.total_images ?? 0) +
    '</strong><span>' +
    escapeHtml(t('stats.images')) +
    '</span></div>' +
    '<div class="kpi"><strong>' +
    escapeHtml(String(avgConf)) +
    '</strong><span>' +
    escapeHtml(t('stats.avgConfidence')) +
    '</span></div>' +
    '</div>' +
    (primary && primary.style
      ? '<p><strong>' +
        escapeHtml(t('stats.primaryStyleLabel')) +
        ':</strong> ' +
        escapeHtml(primary.style) +
        ' (' +
        escapeHtml(
          t('stats.primaryStyleMeta', {
            style: primary.style,
            count: primary.count ?? 0,
            percent: primary.percent ?? 0,
          }),
        ) +
        ')</p>'
      : '<p class="empty">' + escapeHtml(t('stats.noPrimaryStyle')) + '</p>') +
    '</section>' +
    compareHtml +
    dailyHtml +
    statsReportTableHtml(t('stats.reportSectionStyles'), stats.top_styles, 'style', null, 'stats.noStyleData') +
    statsReportTableHtml(t('stats.reportSectionItems'), stats.top_items, 'item', null, 'stats.noItemData') +
    statsReportTableHtml(t('stats.reportSectionOccasions'), stats.top_occasions, 'occasion', null, 'stats.noOccasionData') +
    statsReportTableHtml(
      t('stats.reportSectionCategories'),
      stats.top_categories,
      'category',
      formatStatsCategoryLabel,
      'stats.noCategoryData',
    ) +
    '<p class="footer">' +
    escapeHtml(t('stats.reportFooter')) +
    '</p>' +
    '</body></html>'
  );
}

function getStatsPrintFrame() {
  let frame = el('statsPrintFrame');
  if (!frame) {
    frame = document.createElement('iframe');
    frame.id = 'statsPrintFrame';
    frame.setAttribute('title', t('stats.exportPrintAria'));
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText =
      'position:fixed;right:0;bottom:0;width:0;height:0;border:0;opacity:0;pointer-events:none;';
    document.body.appendChild(frame);
  }
  return frame;
}

function printStatsReport() {
  if (!lastStatsSnapshot) return;
  const html = buildStatsReportPrintDocument(lastStatsSnapshot);
  const frame = getStatsPrintFrame();
  const win = frame.contentWindow;
  if (!win) return;
  win.document.open();
  win.document.write(html);
  win.document.close();
  setTimeout(function () {
    try {
      win.focus();
      win.print();
    } catch (_) {}
  }, 300);
}

async function loadStats() {
  const hint = el('statsLoginHint');
  const content = el('statsContent');
  if (!content) return;
  if (!currentUser || !currentUser.id) {
    hide(content);
    show(hint);
    return;
  }
  hide(hint);
  show(content);
  const period = (el('statsPeriod') && el('statsPeriod').value) || 'all';
  const totalAnalysesEl = el('statTotalAnalyses');
  const totalImagesEl = el('statTotalImages');
  const stylesWrap = el('statsStylesTable');
  const itemsWrap = el('statsItemsTable');
  const occasionsWrap = el('statsOccasionsTable');
  const categoriesWrap = el('statsCategoriesTable');
  const chartWrap = el('statsByDayChart');
  if (totalAnalysesEl) totalAnalysesEl.textContent = '—';
  if (totalImagesEl) totalImagesEl.textContent = '—';
  hide(el('statsPrimaryStyle'));
  hide(el('statsPeriodComparison'));
  renderStatsAvgConfidence(null, 0);
  if (chartWrap) chartWrap.innerHTML = '<p class="stats-v2-empty">' + escapeHtml(t('js.loadingGeneric')) + '</p>';
  if (stylesWrap) stylesWrap.innerHTML = '<p class="stats-v2-empty">' + escapeHtml(t('js.loadingGeneric')) + '</p>';
  if (itemsWrap) itemsWrap.innerHTML = '';
  if (occasionsWrap) occasionsWrap.innerHTML = '';
  if (categoriesWrap) categoriesWrap.innerHTML = '';
  lastStatsSnapshot = null;
  updateStatsExportControls();
  try {
    const url = API_BASE + '/api/stats/' + currentUser.id + (period !== 'all' ? '?period=' + period : '');
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) {
      if (stylesWrap) stylesWrap.innerHTML = '<p class="stats-v2-empty">' + escapeHtml(t('stats.loadError')) + '</p>';
      if (chartWrap) chartWrap.innerHTML = '<p class="stats-v2-empty">' + escapeHtml(t('stats.loadError')) + '</p>';
      lastStatsSnapshot = null;
      updateStatsExportControls();
      return;
    }
    if (totalAnalysesEl) totalAnalysesEl.textContent = data.total_analyses ?? 0;
    if (totalImagesEl) totalImagesEl.textContent = data.total_images ?? 0;
    renderStatsPrimaryStyle(data.primary_style);
    renderStatsPeriodComparison(data.period_comparison, period);
    renderStatsAvgConfidence(data.avg_confidence, data.confidence_samples);
    if (chartWrap) {
      chartWrap.innerHTML = buildStatsAreaChart(data.by_day || [], {
        title: t('stats.chartAnalysesPerDay'),
      });
    }
    if (stylesWrap) {
      stylesWrap.innerHTML = renderStatsRankList(data.top_styles || [], 'stats.noStyleData', 'style');
    }
    if (itemsWrap) {
      itemsWrap.innerHTML = renderStatsRankList(data.top_items || [], 'stats.noItemData', 'item');
    }
    if (occasionsWrap) {
      occasionsWrap.innerHTML = renderStatsRankList(
        data.top_occasions || [],
        'stats.noOccasionData',
        'occasion',
      );
    }
    if (categoriesWrap) {
      categoriesWrap.innerHTML = renderStatsRankList(
        data.top_categories || [],
        'stats.noCategoryData',
        'category',
        formatStatsCategoryLabel,
      );
    }
    lastStatsSnapshot = buildStatsExportPayload(data, period);
    updateStatsExportControls();
  } catch (err) {
    if (stylesWrap) stylesWrap.innerHTML = '<p class="stats-v2-empty">' + escapeHtml(t('stats.loadError')) + '</p>';
    if (chartWrap) chartWrap.innerHTML = '<p class="stats-v2-empty">' + escapeHtml(t('stats.loadError')) + '</p>';
    lastStatsSnapshot = null;
    updateStatsExportControls();
  }
}

if (el('statsPeriod')) {
  el('statsPeriod').addEventListener('change', () => { if (currentUser && currentUser.id) loadStats(); });
}

if (el('statsExportJson')) {
  el('statsExportJson').addEventListener('click', downloadStatsReportJson);
}
if (el('statsExportPrint')) {
  el('statsExportPrint').addEventListener('click', printStatsReport);
}

syncAnalyzeResultEmpty();

async function loadSessionFromAccessToken() {
  if (window.__authMeInFlight) return window.__authMeInFlight;
  window.__authMeInFlight = (async () => {
    const t = localStorage.getItem(ACCESS_TOKEN_KEY);
    if (!t) return false;
    try {
      const res = await fetch(API_BASE + '/api/auth/me', {
        headers: { Authorization: 'Bearer ' + t },
      });
      const data = await res.json();
      if (!res.ok || !data.id || !data.username) {
        try { localStorage.removeItem(ACCESS_TOKEN_KEY); } catch (_) {}
        return false;
      }
      if (data.account_status === 'deleted') {
        try { localStorage.removeItem(ACCESS_TOKEN_KEY); } catch (_) {}
        setUser(null);
        alert(t('profile.accountDeleted'));
        return false;
      }
      setUser(data);
      if (data.force_change_password) {
        window.location.href = '/change-password';
        return true;
      }
      return true;
    } catch (_) {
      try { localStorage.removeItem(ACCESS_TOKEN_KEY); } catch (_) {}
      return false;
    }
  })();
  try {
    return await window.__authMeInFlight;
  } finally {
    window.__authMeInFlight = null;
  }
}

// Khôi phục phiên: Google JWT (?token= hoặc access_token), hoặc user localStorage (đăng nhập mật khẩu)
(async function initAuthSession() {
  const params = new URLSearchParams(window.location.search);
  const err = params.get('google_error');
  if (err) {
    const msg = {
      config: t('js.googleConfig'),
      token: t('js.googleFail'),
      no_sub: t('js.googleNoSub'),
      db: t('js.googleDb'),
      deleted: t('profile.accountDeleted'),
      account_conflict: t('js.googleAccountConflict'),
    }[err] || t('js.googleFail');
    if (window.history.replaceState) window.history.replaceState({}, '', window.location.pathname);
    alert(msg);
    updateAuthUI();
    handleNextFromUrl(params);
    return;
  }
  const tokenFromUrl = params.get('token');
  if (tokenFromUrl) {
    try { localStorage.setItem(ACCESS_TOKEN_KEY, tokenFromUrl); } catch (_) {}
    if (window.history.replaceState) window.history.replaceState({}, document.title, window.location.pathname);
    const ok = await loadSessionFromAccessToken();
    if (!ok) alert(t('js.googleTokenFail'));
    else {
      updateAuthUI();
      const next = params.get('next') || 'analyze';
      const allowed = new Set(['intro', 'analyze', 'packages', 'history', 'stats', 'profile', 'admin']);
      const page = allowed.has(next) ? next : 'analyze';
      showPage(page, {});
      if (window.history.replaceState) {
        window.history.replaceState({}, document.title, '/?next=' + encodeURIComponent(page));
      }
    }
    if (currentUser && currentUser.id) refreshUserProfile();
    return;
  }
  if (localStorage.getItem(ACCESS_TOKEN_KEY)) {
    await loadSessionFromAccessToken();
    updateAuthUI();
    if (redirectToChangePasswordIfNeeded()) return;
    if (redirectToPendingDeleteIfNeeded()) return;
    handleNextFromUrl(params);
    if (currentUser && currentUser.id) refreshUserProfile();
    return;
  }
  try {
    const saved = localStorage.getItem('user');
    if (saved) {
      const u = JSON.parse(saved);
      if (u && u.id && u.username) {
        if (!u.role) u.role = 'user';
        setUser(u);
      }
    }
  } catch (_) {}
  if (!currentUser) updateAuthUI();
  loadSiteBranding();
  if (redirectToChangePasswordIfNeeded()) return;
  if (redirectToPendingDeleteIfNeeded()) return;
  const handledNext = handleNextFromUrl(params);
  if (!handledNext) {
    const fromPath = getPageFromPathname();
    if (fromPath) showPage(fromPath);
  }
  if (currentUser && currentUser.id) refreshUserProfile();
})();

function refreshUIOnLangChange() {
  updateAuthUI();
  setPageTitle(currentPage);
  if (typeof window.syncLegalLangBlocks === 'function') window.syncLegalLangBlocks();
  if (lastDisplayedResult && result && !result.classList.contains('hidden')) {
    displayResult(lastDisplayedResult);
  }
  if (currentPage === 'packages') loadPackagesPage();
  else if (currentPage === 'history') loadHistory();
  else if (currentPage === 'stats') loadStats();
  else if (currentPage === 'profile') loadProfilePage();
  else if (currentPage === 'admin') {
    updateAdminTopbar();
    refreshAdminView();
  }
  if (isPendingDelete()) showPendingDeleteOverlay();
}

document.addEventListener('langchange', refreshUIOnLangChange);
