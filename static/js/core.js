/**
 * core.js — Shared constants, DOM helpers, auth state, navigation chrome.
 * Load before analyze.js, admin.js, app.js.
 */
const API_BASE = '';
const ACCESS_TOKEN_KEY = 'access_token';

function t(key, params) {
  return (window.I18n && window.I18n.t(key, params)) || key;
}

function getUiLang() {
  return (window.I18n && window.I18n.getLang && window.I18n.getLang()) || 'vi';
}

function looksVietnameseText(s) {
  return /[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]/i.test(String(s || ''));
}

let currentPage = 'intro';
let paymentDoneEnabled = false;
function resolveUploadSrc(url) {
  if (url == null || url === '') return '';
  const s = String(url).trim();
  if (!s) return '';
  try {
    if (s.startsWith('http://') || s.startsWith('https://')) {
      const u = new URL(s);
      const p = u.pathname || '';
      const idx = p.indexOf('/uploads/');
      if (idx >= 0) return API_BASE + p.slice(idx);
    }
  } catch (_) {}
  if (s.startsWith('/')) return API_BASE + s;
  const fn = s.replace(/^.*\//, '');
  return API_BASE + '/uploads/' + fn;
}

function bindUploadImage(img, url) {
  const src = resolveUploadSrc(url);
  if (!src) return false;
  img.src = src;
  img.loading = 'lazy';
  img.decoding = 'async';
  img.onerror = function () {
    this.style.display = 'none';
    this.classList.add('media-img--missing');
    this.alt = '';
    this.removeAttribute('src');
    if (this.dataset.fallback === '1') return;
    this.dataset.fallback = '1';
    const ph = document.createElement('span');
    ph.className = 'media-img-fallback';
    ph.textContent = t('js.noImage');
    ph.title = t('js.noImageTitle');
    this.parentNode.appendChild(ph);
  };
  return true;
}

let currentUser = null;

const el = (id) => document.getElementById(id);
const qs = (sel, parent = document) => parent.querySelector(sel);
const qsAll = (sel, parent = document) => parent.querySelectorAll(sel);

function show(el) {
  if (el) el.classList.remove('hidden');
}
function hide(el) {
  if (el) el.classList.add('hidden');
}

function isAdmin() {
  return !!(currentUser && currentUser.role === 'admin');
}

function setUser(u) {
  currentUser = u;
  if (u) {
    try { localStorage.setItem('user', JSON.stringify(u)); } catch (_) {}
  } else {
    try { localStorage.removeItem('user'); } catch (_) {}
    try { localStorage.removeItem(ACCESS_TOKEN_KEY); } catch (_) {}
    if (el('pageAdmin') && el('pageAdmin').classList.contains('active')) {
      qsAll('.nav-btn').forEach(b => b.classList.remove('active'));
      qs('.nav-btn[data-page="intro"]')?.classList.add('active');
      qsAll('.page').forEach(p => p.classList.remove('active'));
      el('pageIntro')?.classList.add('active');
      qs('.app-shell')?.classList.add('app-shell--landing');
    }
  }
  updateAuthUI();
  redirectToPendingDeleteIfNeeded();
}

async function refreshUserProfile() {
  if (!currentUser || !currentUser.id) return;
  const uid = currentUser.id;
  const now = Date.now();
  if (window.__profileRefreshAt && window.__profileRefreshAt.uid === uid && now - window.__profileRefreshAt.ts < 3000) {
    return;
  }
  window.__profileRefreshAt = { uid, ts: now };
  try {
    const r = await fetch(API_BASE + '/api/user/profile?user_id=' + encodeURIComponent(uid));
    if (!r.ok) return;
    const d = await r.json();
    if (d.id) setUser({ ...currentUser, ...d });
  } catch (_) {}
}

function initialsFromUser(u) {
  if (!u) return '?';
  const raw = (u.full_name || u.username || '?').trim();
  const parts = raw.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return raw.slice(0, 2).toUpperCase() || '?';
}

function renderUserAvatar(container, user) {
  if (!container) return;
  const url = user && user.avatar_url ? String(user.avatar_url).trim() : '';
  if (url) {
    const bust = url.includes('?') ? '&' : '?';
    container.innerHTML = '<img class="shell-v2-avatar-img" src="' + escapeHtml(url + bust + 't=' + Date.now()) + '" alt="">';
  } else {
    container.textContent = initialsFromUser(user);
  }
}

function renderProfileAvatarPreview(user) {
  const img = el('profileAvatarImg');
  const initials = el('profileAvatarInitials');
  const url = user && user.avatar_url ? String(user.avatar_url).trim() : '';
  if (img && initials) {
    if (url) {
      const bust = url.includes('?') ? '&' : '?';
      img.src = url + bust + 't=' + Date.now();
      img.alt = user.full_name || user.username || '';
      img.classList.remove('hidden');
      initials.classList.add('hidden');
    } else {
      img.removeAttribute('src');
      img.classList.add('hidden');
      initials.textContent = initialsFromUser(user);
      initials.classList.remove('hidden');
    }
  }
}

function formatProfileDate(val) {
  if (!val) return '—';
  try {
    const d = new Date(val);
    if (Number.isNaN(d.getTime())) return String(val).slice(0, 10);
    const lang = typeof getLang === 'function' ? getLang() : 'vi';
    return d.toLocaleDateString(lang === 'en' ? 'en-US' : 'vi-VN', { year: 'numeric', month: 'short', day: 'numeric' });
  } catch (_) {
    return '—';
  }
}

function isPendingDelete() {
  return !!(currentUser && currentUser.account_status === 'pending_delete');
}

function accountAuthHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  try {
    const tkn = localStorage.getItem(ACCESS_TOKEN_KEY);
    if (tkn && String(tkn).trim()) headers.Authorization = 'Bearer ' + tkn;
  } catch (_) {}
  return headers;
}

async function accountApiPost(path, payload) {
  const body = { user_id: currentUser && currentUser.id, ...(payload || {}) };
  const r = await fetch(API_BASE + path, {
    method: 'POST',
    headers: accountAuthHeaders(),
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  return { r, d };
}

function formatDeleteScheduledDate(val) {
  if (!val) return '—';
  try {
    const d = new Date(val);
    if (Number.isNaN(d.getTime())) return String(val).slice(0, 10);
    const lang = typeof getLang === 'function' ? getLang() : 'vi';
    return d.toLocaleString(lang === 'en' ? 'en-US' : 'vi-VN', {
      year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch (_) {
    return '—';
  }
}

function showPendingDeleteOverlay() {
  const overlay = el('pendingDeleteOverlay');
  const msg = el('pendingDeleteMessage');
  if (!overlay) return;
  const dateStr = formatDeleteScheduledDate(currentUser && currentUser.delete_scheduled_at);
  if (msg) msg.textContent = t('profile.pendingDeleteMessage', { date: dateStr });
  overlay.classList.remove('hidden');
  document.body.classList.add('pending-delete-active');
}

function hidePendingDeleteOverlay() {
  const overlay = el('pendingDeleteOverlay');
  if (overlay) overlay.classList.add('hidden');
  document.body.classList.remove('pending-delete-active');
}

function redirectToPendingDeleteIfNeeded() {
  if (isPendingDelete()) {
    showPendingDeleteOverlay();
    return true;
  }
  hidePendingDeleteOverlay();
  return false;
}

function syncProfileDeleteZone(u) {
  const btn = el('profileDeleteAccountBtn');
  const reasonWrap = el('profileDeleteReason')?.closest('.profile-v2-field--optional');
  const notice = el('profilePendingDeleteNotice');
  const noticeText = el('profilePendingDeleteText');
  const pending = u && u.account_status === 'pending_delete';
  if (btn) btn.classList.toggle('hidden', !!pending);
  if (reasonWrap) reasonWrap.classList.toggle('hidden', !!pending);
  if (notice && noticeText) {
    if (pending) {
      noticeText.textContent = t('profile.pendingDeleteNotice', {
        date: formatDeleteScheduledDate(u.delete_scheduled_at),
      });
      show(notice);
    } else {
      hide(notice);
    }
  }
}

async function requestDeleteAccountOtp() {
  if (!currentUser || !currentUser.id) return;
  const errEl = el('deleteAccountConfirmError');
  hide(errEl);
  const reason = (el('profileDeleteReason')?.value || '').trim();
  const btn = el('deleteAccountConfirmProceed');
  if (btn) btn.disabled = true;
  try {
    const { r, d } = await accountApiPost('/api/account/delete/request', { delete_reason: reason });
    if (!r.ok) throw new Error(d.error || t('profile.deleteError'));
    closeModal('modalDeleteAccountConfirm');
    if (el('deleteAccountSessionToken')) el('deleteAccountSessionToken').value = d.session_token || '';
    const hint = el('deleteAccountOtpHint');
    if (hint && d.email_masked) {
      hint.textContent = t('auth.otpSentTo', { email: d.email_masked });
    }
    hide(el('deleteAccountOtpError'));
    hide(el('deleteAccountOtpSuccess'));
    openModal('modalDeleteAccountOtp');
    el('deleteAccountOtpInput')?.focus();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message || t('profile.deleteError');
      show(errEl);
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function verifyDeleteAccountOtp(ev) {
  if (ev) ev.preventDefault();
  if (!currentUser || !currentUser.id) return;
  const errEl = el('deleteAccountOtpError');
  const okEl = el('deleteAccountOtpSuccess');
  hide(errEl);
  hide(okEl);
  const session_token = el('deleteAccountSessionToken')?.value || '';
  const otp = (el('deleteAccountOtpInput')?.value || '').trim();
  try {
    const { r, d } = await accountApiPost('/api/account/delete/verify', { session_token, otp });
    if (!r.ok) throw new Error(d.error || t('profile.deleteError'));
    setUser({
      ...currentUser,
      account_status: 'pending_delete',
      delete_requested_at: d.delete_requested_at,
      delete_scheduled_at: d.delete_scheduled_at,
    });
    if (okEl) {
      okEl.textContent = d.message || t('profile.deleteSuccess');
      show(okEl);
    }
    closeModal('modalDeleteAccountOtp');
    loadProfilePage();
    redirectToPendingDeleteIfNeeded();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message || t('profile.deleteError');
      show(errEl);
    }
  }
}

async function resendDeleteAccountOtp() {
  const errEl = el('deleteAccountOtpError');
  hide(errEl);
  try {
    const session_token = el('deleteAccountSessionToken')?.value || '';
    const { r, d } = await accountApiPost('/api/account/delete/resend', { session_token });
    if (!r.ok) throw new Error(d.error || t('js.errResendOtp'));
    alert(d.message || t('js.otpResent'));
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message || t('js.errResendOtp');
      show(errEl);
    }
  }
}

async function startRestoreAccountFlow() {
  if (!currentUser || !currentUser.id) return;
  const errEl = el('restoreAccountOtpError');
  hide(errEl);
  try {
    const { r, d } = await accountApiPost('/api/account/restore/request', {});
    if (!r.ok) throw new Error(d.error || t('profile.restoreError'));
    if (el('restoreAccountSessionToken')) el('restoreAccountSessionToken').value = d.session_token || '';
    const hint = el('restoreAccountOtpHint');
    if (hint && d.email_masked) {
      hint.textContent = t('auth.otpSentTo', { email: d.email_masked });
    }
    hidePendingDeleteOverlay();
    openModal('modalRestoreAccountOtp');
    el('restoreAccountOtpInput')?.focus();
  } catch (e) {
    alert(e.message || t('profile.restoreError'));
  }
}

async function verifyRestoreAccountOtp(ev) {
  if (ev) ev.preventDefault();
  if (!currentUser || !currentUser.id) return;
  const errEl = el('restoreAccountOtpError');
  hide(errEl);
  const session_token = el('restoreAccountSessionToken')?.value || '';
  const otp = (el('restoreAccountOtpInput')?.value || '').trim();
  try {
    const { r, d } = await accountApiPost('/api/account/restore/verify', { session_token, otp });
    if (!r.ok) throw new Error(d.error || t('profile.restoreError'));
    setUser({
      ...currentUser,
      account_status: 'active',
      delete_requested_at: null,
      delete_scheduled_at: null,
    });
    closeModal('modalRestoreAccountOtp');
    hidePendingDeleteOverlay();
    alert(d.message || t('profile.restoreSuccess'));
    loadProfilePage();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message || t('profile.restoreError');
      show(errEl);
    }
  }
}

async function resendRestoreAccountOtp() {
  const errEl = el('restoreAccountOtpError');
  hide(errEl);
  try {
    const session_token = el('restoreAccountSessionToken')?.value || '';
    const { r, d } = await accountApiPost('/api/account/restore/resend', { session_token });
    if (!r.ok) throw new Error(d.error || t('js.errResendOtp'));
    alert(d.message || t('js.otpResent'));
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message || t('js.errResendOtp');
      show(errEl);
    }
  }
}

function openProfilePage() {
  if (!currentUser || !currentUser.id) {
    redirectToLogin('profile');
    return;
  }
  navigateToPage('profile', { urlMode: 'push' });
}

async function loadProfilePage() {
  const hint = el('profileLoginHint');
  const content = el('profileContent');
  if (!hint || !content) return;
  if (!currentUser || !currentUser.id) {
    show(hint);
    hide(content);
    return;
  }
  hide(hint);
  show(content);
  await refreshUserProfile();
  const u = currentUser;
  if (!u) return;
  const fullName = el('profileFullName');
  const username = el('profileUsername');
  const email = el('profileEmail');
  const displayName = el('profileDisplayName');
  const roleLabel = el('profileRoleLabel');
  const credits = el('profileCredits');
  const memberSince = el('profileMemberSince');
  const accountType = el('profileAccountType');
  const emailHint = el('profileEmailHint');
  const changePwdLink = el('profileChangePwdLink');
  const removeAvatarBtn = el('profileRemoveAvatarBtn');
  if (fullName) fullName.value = u.full_name || '';
  if (username) username.value = u.username || '';
  if (email) email.value = u.email || u.username || '';
  if (displayName) displayName.textContent = u.full_name || u.username || '—';
  if (roleLabel) roleLabel.textContent = isAdmin() ? t('shell.roleAdmin') : t('shell.roleUser');
  if (credits) credits.textContent = u.analysis_credits != null ? String(u.analysis_credits) : '—';
  if (memberSince) memberSince.textContent = formatProfileDate(u.created_at);
  if (accountType) {
    accountType.textContent = u.is_google_only ? t('profile.accountGoogle') : t('profile.accountLocal');
  }
  if (emailHint) emailHint.classList.toggle('hidden', !u.is_google_only);
  if (changePwdLink) changePwdLink.classList.toggle('hidden', !!u.is_google_only);
  if (removeAvatarBtn) removeAvatarBtn.disabled = !u.avatar_url;
  renderProfileAvatarPreview(u);
  syncProfileDeleteZone(u);
  hide(el('profileError'));
  hide(el('profileSuccess'));
}

async function saveProfileForm(ev) {
  if (ev) ev.preventDefault();
  if (!currentUser || !currentUser.id) return;
  const errEl = el('profileError');
  const okEl = el('profileSuccess');
  const saveBtn = el('profileSaveBtn');
  hide(errEl);
  hide(okEl);
  const fullName = (el('profileFullName')?.value || '').trim();
  if (!fullName) {
    if (errEl) errEl.textContent = t('profile.saveError');
    show(errEl);
    return;
  }
  if (saveBtn) saveBtn.disabled = true;
  try {
    const r = await fetch(API_BASE + '/api/user/profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: currentUser.id, full_name: fullName }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('profile.saveError'));
    if (d.user) setUser({ ...currentUser, ...d.user });
    if (okEl) okEl.textContent = t('profile.saved');
    show(okEl);
    loadProfilePage();
  } catch (e) {
    if (errEl) errEl.textContent = e.message || t('profile.saveError');
    show(errEl);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function uploadProfileAvatar(file) {
  if (!currentUser || !currentUser.id || !file) return;
  const errEl = el('profileError');
  const okEl = el('profileSuccess');
  hide(errEl);
  hide(okEl);
  const form = new FormData();
  form.append('user_id', String(currentUser.id));
  form.append('avatar', file);
  try {
    const r = await fetch(API_BASE + '/api/user/avatar', { method: 'POST', body: form });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('profile.avatarInvalid'));
    setUser({ ...currentUser, avatar_url: d.avatar_url || '' });
    if (okEl) okEl.textContent = t('profile.avatarUpdated');
    show(okEl);
    loadProfilePage();
  } catch (e) {
    if (errEl) errEl.textContent = e.message || t('profile.avatarInvalid');
    show(errEl);
  }
}

async function removeProfileAvatar() {
  if (!currentUser || !currentUser.id || !currentUser.avatar_url) return;
  const errEl = el('profileError');
  const okEl = el('profileSuccess');
  hide(errEl);
  hide(okEl);
  try {
    const r = await fetch(API_BASE + '/api/user/avatar', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: currentUser.id }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || t('profile.saveError'));
    setUser({ ...currentUser, avatar_url: '' });
    if (okEl) okEl.textContent = t('profile.avatarRemoved');
    show(okEl);
    loadProfilePage();
  } catch (e) {
    if (errEl) errEl.textContent = e.message || t('profile.saveError');
    show(errEl);
  }
}

function bindProfilePage() {
  el('profileForm')?.addEventListener('submit', saveProfileForm);
  const fileInput = el('profileAvatarInput');
  const openPicker = () => fileInput?.click();
  el('profileAvatarBtn')?.addEventListener('click', openPicker);
  el('profileChangeAvatarBtn')?.addEventListener('click', openPicker);
  fileInput?.addEventListener('change', () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) uploadProfileAvatar(f);
    fileInput.value = '';
  });
  el('profileRemoveAvatarBtn')?.addEventListener('click', () => removeProfileAvatar());
  el('profileLogoutBtn')?.addEventListener('click', () => logoutAndGoHome());
  el('profileDeleteAccountBtn')?.addEventListener('click', () => {
    hide(el('deleteAccountConfirmError'));
    openModal('modalDeleteAccountConfirm');
  });
  el('deleteAccountConfirmCancel')?.addEventListener('click', () => closeModal('modalDeleteAccountConfirm'));
  el('deleteAccountConfirmProceed')?.addEventListener('click', () => requestDeleteAccountOtp());
  el('formDeleteAccountOtp')?.addEventListener('submit', verifyDeleteAccountOtp);
  el('deleteAccountResendOtp')?.addEventListener('click', () => resendDeleteAccountOtp());
  el('pendingDeleteRestoreBtn')?.addEventListener('click', () => startRestoreAccountFlow());
  el('pendingDeleteLogoutBtn')?.addEventListener('click', () => logoutAndGoHome());
  el('formRestoreAccountOtp')?.addEventListener('submit', verifyRestoreAccountOtp);
  el('restoreAccountResendOtp')?.addEventListener('click', () => resendRestoreAccountOtp());
  el('userBadge')?.addEventListener('click', () => openProfilePage());
  el('topbarAvatar')?.addEventListener('click', (e) => {
    e.stopPropagation();
    openProfilePage();
  });
}

bindProfilePage();

function syncLandingCta() {
  const reg = el('btnIntroRegister');
  const dash = el('btnIntroDashboard');
  if (!reg && !dash) return;
  const loggedIn = !!(currentUser && currentUser.id);
  if (reg) reg.classList.toggle('hidden', loggedIn);
  if (dash) dash.classList.toggle('hidden', !loggedIn);
}

function updateAuthUI() {
  const btnLogin = el('btnLogin');
  const btnRegister = el('btnRegister');
  const userBadge = el('userBadge');
  const topbarGuest = el('topbarGuest');
  const topbarDisplayName = el('topbarDisplayName');
  const topbarEmail = el('topbarEmail');
  const topbarAvatar = el('topbarAvatar');
  const btnSidebarLogout = el('btnSidebarLogout');
  const btnSidebarAdmin = el('btnSidebarAdmin');
  const topbarUserRole = el('topbarUserRole');
  const analyzeBalanceHint = el('analyzeBalanceHint');
  if (!btnLogin) return;
  if (currentUser) {
    hide(btnLogin);
    hide(btnRegister);
    if (topbarGuest) hide(topbarGuest);
    if (userBadge) show(userBadge);
    const displayName = currentUser.full_name || currentUser.username || '';
    if (topbarDisplayName) topbarDisplayName.textContent = displayName;
    if (topbarEmail) {
      const mail = (currentUser.email && String(currentUser.email).trim()) || '';
      if (mail) {
        topbarEmail.textContent = mail;
        topbarEmail.classList.remove('hidden');
      } else if (currentUser.username) {
        topbarEmail.textContent = currentUser.username;
        topbarEmail.classList.remove('hidden');
      } else {
        topbarEmail.textContent = '';
        topbarEmail.classList.add('hidden');
      }
    }
    if (topbarAvatar) renderUserAvatar(topbarAvatar, currentUser);
    if (topbarUserRole) {
      topbarUserRole.textContent = isAdmin() ? t('shell.roleAdmin') : t('shell.roleUser');
    }
    if (analyzeBalanceHint) {
      const c = currentUser.analysis_credits;
      if (c !== undefined && c !== null) {
        analyzeBalanceHint.textContent = t('analyze.balanceCredits', { n: c });
      } else {
        analyzeBalanceHint.textContent = t('analyze.balancePending');
      }
    }
    if (btnSidebarLogout) show(btnSidebarLogout);
    if (btnSidebarAdmin) btnSidebarAdmin.classList.toggle('hidden', !isAdmin());
    closeUserMenu();
  } else {
    show(btnLogin);
    show(btnRegister);
    if (topbarGuest) show(topbarGuest);
    if (userBadge) hide(userBadge);
    if (analyzeBalanceHint) {
      analyzeBalanceHint.textContent = t('analyze.balanceGuest');
    }
    if (btnSidebarLogout) hide(btnSidebarLogout);
    if (btnSidebarAdmin) btnSidebarAdmin.classList.add('hidden');
    closeUserMenu();
  }
  syncLandingCta();
}

function syncTopbarPageTitle(page) {
  const titleEl = el('topbarPageTitle');
  if (!titleEl) return;
  const key = PAGE_NAV_TITLE_KEY[page] || PAGE_NAV_TITLE_KEY.intro;
  titleEl.textContent = t(key);
  titleEl.dataset.i18n = key;
}

function mustChangePassword() {
  return !!(currentUser && currentUser.force_change_password);
}

function redirectToChangePasswordIfNeeded() {
  if (mustChangePassword()) {
    window.location.href = '/change-password';
    return true;
  }
  return false;
}

function redirectToLogin(nextPage) {
  const n = (nextPage || '').trim();
  try {
    if (localStorage.getItem(ACCESS_TOKEN_KEY)) {
      loadSessionFromAccessToken().then((ok) => {
        if (ok) {
          const page = n || 'analyze';
          showPage(page, {});
          if (window.history.replaceState) {
            window.history.replaceState({}, document.title, '/?next=' + encodeURIComponent(page));
          }
        } else {
          window.location.href = n ? ('/login?next=' + encodeURIComponent(n)) : '/login';
        }
      });
      return;
    }
  } catch (_) {}
  window.location.href = n ? ('/login?next=' + encodeURIComponent(n)) : '/login';
}

function logoutAndGoHome() {
  setUser(null);
  try {
    if (window.FlutterBridge) window.FlutterBridge.postMessage('LOGOUT');
  } catch (_) {}
  window.location.href = '/';
}

function isProtectedPage(page) {
  // Các chức năng yêu cầu đăng nhập trước khi vào giao diện
  return ['analyze', 'history', 'stats', 'packages', 'profile', 'admin'].includes(String(page || '').trim());
}

function toggleUserMenu() {
  const dropdown = el('userMenuDropdown');
  const trigger = el('userMenuTrigger');
  if (!dropdown || !trigger) return;
  const isOpen = !dropdown.classList.contains('hidden');
  dropdown.classList.toggle('hidden', isOpen);
  trigger.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
}

function closeUserMenu() {
  const dropdown = el('userMenuDropdown');
  const trigger = el('userMenuTrigger');
  if (dropdown) dropdown.classList.add('hidden');
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
}

function closeMobileNav() {
  const shell = qs('.app-shell');
  if (shell) shell.classList.remove('nav-drawer-open');
  const backdrop = el('navBackdrop');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'true');
  const openBtn = el('btnNavOpen');
  if (openBtn) openBtn.setAttribute('aria-expanded', 'false');
  const landingMenu = el('btnLandingMenu');
  if (landingMenu) landingMenu.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('nav-drawer-no-scroll');
  closeAdminDrawer();
}

function closeAdminDrawer() {
  const page = el('pageAdmin');
  if (!page) return;
  page.classList.remove('admin-drawer-open');
  const backdrop = el('adminNavBackdrop');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'true');
  const openBtn = el('btnAdminNavOpen');
  if (openBtn) openBtn.setAttribute('aria-expanded', 'false');
  if (!qs('.app-shell')?.classList.contains('nav-drawer-open')) {
    document.body.classList.remove('nav-drawer-no-scroll');
  }
}

function openAdminDrawer() {
  const page = el('pageAdmin');
  if (!page || !page.classList.contains('active')) return;
  page.classList.add('admin-drawer-open');
  const backdrop = el('adminNavBackdrop');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'false');
  const openBtn = el('btnAdminNavOpen');
  if (openBtn) openBtn.setAttribute('aria-expanded', 'true');
  document.body.classList.add('nav-drawer-no-scroll');
}

function openMobileNav() {
  const shell = qs('.app-shell');
  if (!shell) return;
  shell.classList.add('nav-drawer-open');
  const backdrop = el('navBackdrop');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'false');
  const openBtn = el('btnNavOpen');
  if (openBtn) openBtn.setAttribute('aria-expanded', 'true');
  const landingMenu = el('btnLandingMenu');
  if (landingMenu) landingMenu.setAttribute('aria-expanded', 'true');
  document.body.classList.add('nav-drawer-no-scroll');
  const sidebar = el('appSidebar');
  if (sidebar) sidebar.scrollTop = 0;
  const nav = qs('.shell-v2-nav');
  if (nav) nav.scrollTop = 0;
}
function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function openImageLightbox(src, alt) {
  const modal = el('modalImageLightbox');
  const img = el('lightboxImage');
  if (!modal || !img || !src) return;
  img.alt = alt || 'Ảnh';
  img.src = src;
  modal.classList.remove('hidden');
}

function closeImageLightbox() {
  const modal = el('modalImageLightbox');
  const img = el('lightboxImage');
  if (img) {
    img.removeAttribute('src');
    img.alt = '';
  }
  if (modal) modal.classList.add('hidden');
}

// ---------- Modals ----------
function openModal(id) {
  const m = el(id);
  if (m) m.classList.remove('hidden');
}
function closeModal(id) {
  const m = el(id);
  if (m) m.classList.add('hidden');
  if (id === 'modalRestoreAccountOtp' && isPendingDelete()) {
    showPendingDeleteOverlay();
  }
}
if (el('userMenuTrigger')) {
  el('userMenuTrigger').addEventListener('click', (e) => { e.stopPropagation(); toggleUserMenu(); });
}
document.addEventListener('click', () => closeUserMenu());
if (el('userMenuDropdown')) {
  el('userMenuDropdown').addEventListener('click', (e) => e.stopPropagation());
}
