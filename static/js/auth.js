const API_BASE = '';
const ACCESS_TOKEN_KEY = 'access_token';

const el = (id) => document.getElementById(id);

function t(key, params) {
  return (window.I18n && window.I18n.t(key, params)) || key;
}

function googleErrorMessage(code) {
  const map = {
    config: t('js.googleConfig'),
    token: t('js.googleFail'),
    no_sub: t('js.googleNoSub'),
    db: t('js.googleDb'),
    deleted: t('profile.accountDeleted'),
    account_conflict: t('js.googleAccountConflict'),
  };
  return map[code] || t('js.googleFail');
}

function bindGoogleLoginForApp() {
  document.querySelectorAll('a[href*="/api/auth/google"]').forEach((a) => {
    if (a.dataset.authGoogleBound) return;
    a.dataset.authGoogleBound = '1';
    a.addEventListener('click', (e) => {
      if (!window.FlutterBridge) return;
      e.preventDefault();
      window.FlutterBridge.postMessage('GOOGLE_LOGIN');
    });
  });
}

function show(node) {
  if (node) node.classList.remove('hidden');
}
function hide(node) {
  if (node) node.classList.add('hidden');
}

function isGmailAddress(value) {
  return /^[a-zA-Z0-9](?:[a-zA-Z0-9._+-]*[a-zA-Z0-9])?@gmail\.com$/i.test(String(value || '').trim());
}

function getStoredUser() {
  try {
    const saved = localStorage.getItem('user');
    if (!saved) return null;
    const u = JSON.parse(saved);
    return u && u.id ? u : null;
  } catch (_) {
    return null;
  }
}

function saveUser(u) {
  try {
    if (u) localStorage.setItem('user', JSON.stringify(u));
    else localStorage.removeItem('user');
  } catch (_) {}
}

function initPasswordToggles(root) {
  (root || document).querySelectorAll('.auth-pwd-toggle').forEach((btn) => {
    if (btn.dataset.pwdToggleBound) return;
    btn.dataset.pwdToggleBound = '1';
    btn.addEventListener('click', () => {
      const wrap = btn.closest('.auth-input-wrap');
      const input = wrap && wrap.querySelector('input');
      if (!input) return;
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      btn.setAttribute('aria-pressed', visible ? 'true' : 'false');
      btn.setAttribute('aria-label', visible ? t('auth.hidePassword') : t('auth.showPassword'));
      const use = btn.querySelector('use');
      if (use) use.setAttribute('href', visible ? '#icon-eye-off' : '#icon-eye');
    });
  });
}

document.addEventListener('DOMContentLoaded', () => initPasswordToggles());
initPasswordToggles();

function redirectAfterAuth(user) {
  if (user && user.force_change_password) {
    window.location.href = '/change-password';
    return;
  }
  const params = new URLSearchParams(window.location.search);
  const next = (params.get('next') || '').trim();
  if (user && user.account_status === 'pending_delete') {
    window.location.href = '/';
    return;
  }
  if (!next) {
    window.location.href = '/?next=analyze';
    return;
  }
  const allowed = new Set(['analyze', 'packages', 'history', 'stats', 'admin', 'intro', 'profile']);
  if (allowed.has(next)) window.location.href = '/?next=' + encodeURIComponent(next);
  else window.location.href = '/';
}

(function initAuthPage() {
  try {
    const params = new URLSearchParams(window.location.search);
    const gErr = params.get('google_error');
    if (gErr) {
      if (window.history.replaceState) {
        window.history.replaceState({}, document.title, window.location.pathname);
      }
      alert(googleErrorMessage(gErr));
    }
  } catch (_) {}

  bindGoogleLoginForApp();
  document.addEventListener('DOMContentLoaded', bindGoogleLoginForApp);
})();

(function redirectIfAlreadyLoggedIn() {
  const path = window.location.pathname || '';
  if (path.startsWith('/change-password')) return;
  try {
    const params = new URLSearchParams(window.location.search);
    const force = (params.get('force') || '').trim();
    if (force === '1') {
      try { localStorage.removeItem('user'); } catch (_) {}
      try { localStorage.removeItem(ACCESS_TOKEN_KEY); } catch (_) {}
      return;
    }
  } catch (_) {}
  const user = getStoredUser();
  if (user && user.force_change_password) return;
  try {
    const tkn = localStorage.getItem(ACCESS_TOKEN_KEY);
    if (tkn && String(tkn).trim()) {
      redirectAfterAuth(user);
      return;
    }
  } catch (_) {}
  if (user) redirectAfterAuth(user);
})();

async function postJson(url, payload) {
  const res = await fetch(API_BASE + url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  return { res, data };
}

function showRegisterOtpStep(sessionToken, emailMasked) {
  hide(el('registerStepForm'));
  show(el('registerStepOtp'));
  if (el('registerSessionToken')) el('registerSessionToken').value = sessionToken;
  const hint = el('registerOtpHint');
  if (hint && emailMasked) {
    hint.textContent = t('auth.otpSentTo', { email: emailMasked });
  }
  hide(el('registerOtpError'));
  el('reg_otp')?.focus();
}

el('btnRegisterBack')?.addEventListener('click', () => {
  show(el('registerStepForm'));
  hide(el('registerStepOtp'));
  hide(el('registerOtpError'));
});

el('formRegister')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const errEl = el('registerError');
  const submitBtn = el('registerSubmitBtn');
  hide(errEl);
  const originalBtnHtml = submitBtn ? submitBtn.innerHTML : '';
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="register-v2-submit-spinner"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" aria-hidden="true"><circle opacity="0.25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><span>' + t('js.processing') + '</span></span>';
  }
  try {
    const full_name = form.full_name.value.trim();
    const username = form.username.value.trim();
    const email = form.email.value.trim().toLowerCase();
    const password = form.password.value;
    const password_confirm = form.password_confirm.value;
    const terms_accepted = !!form.terms_accepted?.checked;

    if (!full_name) throw new Error(t('js.fullNameRequired'));
    if (!username) throw new Error(t('js.usernameRequired'));
    if (!email || !isGmailAddress(email)) throw new Error(t('js.gmailOnly'));
    if (!password || password.length < 6) throw new Error(t('js.passwordMin'));
    if (password !== password_confirm) throw new Error(t('js.confirmPwdMismatch'));
    if (!terms_accepted) throw new Error(t('js.termsRequired'));

    const { res, data } = await postJson('/api/register', {
      username,
      email,
      password,
      password_confirm,
      full_name,
      terms_accepted: true,
    });
    if (!res.ok) {
      const msg = data.detail ? (data.error + '\n' + data.detail) : (data.error || t('js.errRegister'));
      throw new Error(msg);
    }
    showRegisterOtpStep(data.session_token, data.email_masked);
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message || t('js.errRegister');
      show(errEl);
    } else {
      alert(err.message || t('js.errRegister'));
    }
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalBtnHtml;
    }
  }
});

el('formRegisterOtp')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = el('registerOtpError');
  hide(errEl);
  try {
    const session_token = el('registerSessionToken')?.value || '';
    const otp = el('reg_otp')?.value.trim() || '';
    const { res, data } = await postJson('/api/register/verify', { session_token, otp });
    if (!res.ok) throw new Error(data.error || t('js.errOtp'));
    alert(data.message || t('js.registerOk'));
    window.location.href = '/login';
  } catch (err) {
    errEl.textContent = err.message || t('js.errOtp');
    show(errEl);
  }
});

el('btnRegisterResendOtp')?.addEventListener('click', async () => {
  const errEl = el('registerOtpError');
  hide(errEl);
  try {
    const session_token = el('registerSessionToken')?.value || '';
    const { res, data } = await postJson('/api/register/resend', { session_token });
    if (!res.ok) throw new Error(data.error || t('js.errResendOtp'));
    alert(data.message || t('js.otpResent'));
  } catch (err) {
    errEl.textContent = err.message || t('js.errResendOtp');
    show(errEl);
  }
});

el('formLogin')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const errEl = el('loginError');
  hide(errEl);
  try {
    const username = form.username.value.trim();
    const password = form.password.value;
    const remember = !!form.remember?.checked;

    const { res, data } = await postJson('/api/login', { username, password });
    if (!res.ok) throw new Error(data.error || t('js.errLogin'));

    if (remember) {
      try {
        localStorage.setItem('styleid_remember_login', username);
        localStorage.setItem('styleid_remember_me', '1');
      } catch (_) {}
    } else {
      try {
        localStorage.removeItem('styleid_remember_login');
        localStorage.removeItem('styleid_remember_me');
      } catch (_) {}
    }

    try { localStorage.removeItem(ACCESS_TOKEN_KEY); } catch (_) {}
    saveUser(data);
    if (data.account_status === 'pending_delete') {
      window.location.href = '/';
      return;
    }
    redirectAfterAuth(data);
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message || t('js.errLogin');
      show(errEl);
    } else {
      alert(err.message || t('js.errLogin'));
    }
  }
});

(function initLoginRemember() {
  const form = el('formLogin');
  if (!form) return;
  try {
    if (localStorage.getItem('styleid_remember_me') === '1') {
      const saved = localStorage.getItem('styleid_remember_login') || '';
      if (saved && form.username) form.username.value = saved;
      const remember = el('login_remember');
      if (remember) remember.checked = true;
    }
  } catch (_) {}
})();

function showForgotOtpStep(sessionToken) {
  hide(el('forgotStepRequest'));
  show(el('forgotStepOtp'));
  if (el('forgotSessionToken')) el('forgotSessionToken').value = sessionToken || '';
  hide(el('forgotOtpError'));
  hide(el('forgotOtpInfo'));
  el('forgot_otp')?.focus();
}

el('btnForgotBack')?.addEventListener('click', () => {
  show(el('forgotStepRequest'));
  hide(el('forgotStepOtp'));
});

el('formForgotPassword')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const errEl = el('forgotError');
  const infoEl = el('forgotInfo');
  hide(errEl);
  hide(infoEl);
  try {
    const { res, data } = await postJson('/api/forgot-password', {
      identifier: form.identifier.value.trim(),
    });
    if (!res.ok) throw new Error(data.error || t('js.errForgot'));
    if (infoEl) {
      infoEl.textContent = data.message || t('js.forgotGeneric');
      show(infoEl);
    }
    showForgotOtpStep(data.session_token || '');
  } catch (err) {
    errEl.textContent = err.message || t('js.errForgot');
    show(errEl);
  }
});

el('formForgotOtp')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = el('forgotOtpError');
  const infoEl = el('forgotOtpInfo');
  hide(errEl);
  hide(infoEl);
  try {
    const session_token = el('forgotSessionToken')?.value || '';
    const otp = el('forgot_otp')?.value.trim() || '';
    const { res, data } = await postJson('/api/forgot-password/verify', { session_token, otp });
    if (!res.ok) throw new Error(data.error || t('js.errOtp'));
    if (infoEl) {
      infoEl.textContent = data.message || t('js.forgotSuccess');
      show(infoEl);
    } else {
      alert(data.message || t('js.forgotSuccess'));
    }
    setTimeout(() => { window.location.href = '/login'; }, 1200);
  } catch (err) {
    errEl.textContent = err.message || t('js.errOtp');
    show(errEl);
  }
});

el('btnForgotResendOtp')?.addEventListener('click', async () => {
  const errEl = el('forgotOtpError');
  hide(errEl);
  try {
    const session_token = el('forgotSessionToken')?.value || '';
    const { res, data } = await postJson('/api/forgot-password/resend', { session_token });
    if (!res.ok) throw new Error(data.error || t('js.errResendOtp'));
    alert(data.message || t('js.otpResent'));
  } catch (err) {
    errEl.textContent = err.message || t('js.errResendOtp');
    show(errEl);
  }
});

(function initChangePasswordPage() {
  const form = el('formChangePasswordPage');
  if (!form) return;

  const user = getStoredUser();
  if (!user || !user.id) {
    window.location.href = '/login?next=analyze';
    return;
  }

  const force = !!user.force_change_password;
  const oldWrap = el('changePwdOldWrap');
  const oldInput = el('cp_old_password');
  const subtitle = el('changePwdSubtitle');

  if (force) {
    if (oldWrap) hide(oldWrap);
    if (oldInput) oldInput.removeAttribute('required');
    if (subtitle) subtitle.textContent = t('authPage.changePwdForceSub');
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = el('changePwdError');
    hide(errEl);
    const newPw = form.new_password.value;
    const confirmPw = form.new_password_confirm.value;
    if (newPw !== confirmPw) {
      errEl.textContent = t('js.pwdMismatch');
      show(errEl);
      return;
    }
    try {
      const payload = {
        user_id: user.id,
        new_password: newPw,
      };
      if (force) payload.force_change = true;
      else payload.old_password = form.old_password.value;

      const { res, data } = await postJson('/api/change-password', payload);
      if (!res.ok) throw new Error(data.error || t('js.errChangePwd'));

      const updated = { ...user, force_change_password: false };
      saveUser(updated);
      alert(data.message || t('js.pwdChanged'));
      window.location.href = '/?next=analyze';
    } catch (err) {
      errEl.textContent = err.message || t('js.errChangePwd');
      show(errEl);
    }
  });
})();
