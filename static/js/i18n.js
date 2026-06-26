/**
 * StyleID — i18n (Vietnamese / English)
 * Static: data-i18n* attributes + /static/data/i18n.json
 * Dynamic: I18n.translateText() → POST /api/translate (cached)
 */
(function (global) {
  'use strict';

  const LANG_KEY = 'language';
  const LANG_KEY_LEGACY = 'styleid_lang';
  const I18N_JSON_URL = '/static/data/i18n.json';
  const TRANSLATE_CACHE_PREFIX = 'styleid_translate_';

  const LANDING_HTML_I18N_KEYS = new Set([
    'intro.note',
    'intro.galleryLead',
    'intro.ctaFootAlt',
  ]);

  const LANDING_SHORT_TEXT_KEYS = new Set([
    'intro.kicker',
    'intro.heroTitleLine1',
    'intro.heroTitleLine2',
    'intro.howTitle',
    'intro.featuresTitle',
    'intro.galleryTitle',
    'intro.techTitle',
    'intro.faqTitle',
    'intro.ctaEyebrow',
    'intro.ctaTitle',
    'intro.ctaRegister',
    'intro.step1',
    'intro.step2',
    'intro.step3',
    'intro.wf1Title',
    'intro.wf2Title',
    'intro.wf3Title',
    'intro.tech1Title',
    'intro.tech2Title',
    'intro.tech3Title',
    'intro.how1Title',
    'intro.how2Title',
    'intro.how3Title',
    'intro.f1Title',
    'intro.f2Title',
    'intro.f3Title',
    'intro.f4Title',
    'intro.faq1Q',
    'intro.faq2Q',
    'intro.faq3Q',
  ]);


  const LANDING_I18N_KEYS = [
    'intro.kicker',
    'intro.heroTitleLine1',
    'intro.heroTitleLine2',
    'intro.lead',
    'intro.note',
    'intro.featuresTitle',
    'intro.featuresLead',
    'intro.f1Title',
    'intro.f1Desc',
    'intro.f2Title',
    'intro.f2Desc',
    'intro.f3Title',
    'intro.f3Desc',
    'intro.f4Title',
    'intro.f4Desc',
    'intro.galleryTitle',
    'intro.galleryLead',
    'intro.step1',
    'intro.wf1Title',
    'intro.step2',
    'intro.wf2Title',
    'intro.step3',
    'intro.wf3Title',
    'intro.techTitle',
    'intro.techLead',
    'intro.tech1Title',
    'intro.tech1Desc',
    'intro.tech2Title',
    'intro.tech2Desc',
    'intro.tech3Title',
    'intro.tech3Desc',
    'intro.howTitle',
    'intro.how1Title',
    'intro.how1Desc',
    'intro.how2Title',
    'intro.how2Desc',
    'intro.how3Title',
    'intro.how3Desc',
    'intro.faqTitle',
    'intro.faq1Q',
    'intro.faq1A',
    'intro.faq2Q',
    'intro.faq2A',
    'intro.faq3Q',
    'intro.faq3A',
    'intro.ctaEyebrow',
    'intro.ctaTitle',
    'intro.ctaRegister',
    'intro.ctaFootAlt',
  ];

  let STRINGS = { vi: {}, en: {} };
  let currentLang = 'vi';
  let ready = false;
  let translateInFlight = 0;
  let loadingOverlay = null;

  function getLang() {
    return currentLang;
  }

  function getStoredLang() {
    try {
      const v = localStorage.getItem(LANG_KEY) || localStorage.getItem(LANG_KEY_LEGACY);
      if (v === 'en' || v === 'vi') return v;
    } catch (_) {}
    return 'vi';
  }

  function setStoredLang(lang) {
    try {
      localStorage.setItem(LANG_KEY, lang);
      localStorage.removeItem(LANG_KEY_LEGACY);
    } catch (_) {}
  }

  function hashText(text, targetLang) {
    let h = 0;
    const s = String(text || '') + '|' + targetLang;
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    }
    return 'h' + (h >>> 0).toString(16);
  }

  function getTranslateCacheKey(text, targetLang) {
    return TRANSLATE_CACHE_PREFIX + hashText(text, targetLang);
  }

  function readTranslateCache(text, targetLang) {
    try {
      const v = localStorage.getItem(getTranslateCacheKey(text, targetLang));
      if (v != null) return v;
    } catch (_) {}
    return null;
  }

  function writeTranslateCache(text, targetLang, translated) {
    try {
      localStorage.setItem(getTranslateCacheKey(text, targetLang), translated);
    } catch (_) {}
  }

  function interpolate(str, params) {
    if (!params || typeof str !== 'string') return str || '';
    return str.replace(/\{(\w+)\}/g, (_, k) => {
      return params[k] !== undefined && params[k] !== null ? String(params[k]) : '';
    });
  }

  function t(key, params) {
    const bucket = STRINGS[currentLang] || STRINGS.vi || {};
    const fallback = STRINGS.vi || {};
    const raw = bucket[key] ?? fallback[key] ?? key;
    return interpolate(raw, params);
  }

  function ensureLoadingOverlay() {
    if (loadingOverlay) return loadingOverlay;
    const el = document.createElement('div');
    el.id = 'i18nTranslatingOverlay';
    el.className = 'i18n-translating-overlay hidden';
    el.setAttribute('aria-live', 'polite');
    el.innerHTML =
      '<div class="i18n-translating-box">' +
      '<span class="i18n-translating-spinner" aria-hidden="true"></span>' +
      '<span class="i18n-translating-text" data-i18n="lang.translating">Đang dịch...</span>' +
      '</div>';
    document.body.appendChild(el);
    loadingOverlay = el;
    return el;
  }

  function setTranslatingVisible(visible) {
    const overlay = ensureLoadingOverlay();
    const label = overlay.querySelector('.i18n-translating-text');
    if (label) label.textContent = t('lang.translating');
    overlay.classList.toggle('hidden', !visible);
    document.documentElement.classList.toggle('i18n-is-translating', visible);
  }

  function bumpTranslateLock(delta) {
    translateInFlight = Math.max(0, translateInFlight + delta);
    setTranslatingVisible(translateInFlight > 0);
  }

  async function translateText(text, targetLang) {
    const original = (text == null ? '' : String(text)).trim();
    const lang = targetLang === 'en' ? 'en' : targetLang === 'vi' ? 'vi' : null;
    if (!original || !lang) return original;

    const cached = readTranslateCache(original, lang);
    if (cached != null) return cached;

    bumpTranslateLock(1);
    try {
      const res = await fetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: original, target_language: lang }),
      });
      const data = await res.json().catch(() => ({}));
      const translated = (data.translated_text != null ? String(data.translated_text) : original).trim() || original;
      writeTranslateCache(original, lang, translated);
      return translated;
    } catch (_) {
      return original;
    } finally {
      bumpTranslateLock(-1);
    }
  }

  async function translateBatch(texts, targetLang) {
    const list = Array.isArray(texts) ? texts : [];
    const lang = targetLang || currentLang;
    const unique = [];
    const seen = new Set();
    list.forEach((txt) => {
      const s = (txt == null ? '' : String(txt)).trim();
      if (!s || seen.has(s)) return;
      seen.add(s);
      unique.push(s);
    });
    const map = new Map();
    await Promise.all(unique.map(async (s) => {
      map.set(s, await translateText(s, lang));
    }));
    return map;
  }

  async function translateDynamicElements(root) {
    const scope = root || document;
    const lang = currentLang;
    const nodes = scope.querySelectorAll('[data-i18n-dynamic]');
    if (!nodes.length) return;

    const jobs = [];
    nodes.forEach((el) => {
      const source = el.getAttribute('data-i18n-source') || el.textContent || '';
      const src = source.trim();
      if (!src) return;
      if (lang === 'vi') {
        el.textContent = src;
        return;
      }
      jobs.push({ el, src });
    });

    if (!jobs.length) return;
    bumpTranslateLock(1);
    try {
      const texts = jobs.map((j) => j.src);
      const map = await translateBatch(texts, lang);
      jobs.forEach(({ el, src }) => {
        el.textContent = map.get(src) || src;
      });
    } finally {
      bumpTranslateLock(-1);
    }
  }

  function applyI18n(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((el) => {
      const key = el.getAttribute('data-i18n');
      if (!key) return;
      let val = t(key);
      if (!LANDING_HTML_I18N_KEYS.has(key) && /<[a-z][\s\S]*>/i.test(val)) {
        val = normalizeLandingFieldValue(key, val);
      }
      if (el.tagName === 'TITLE') document.title = val;
      else el.textContent = val;
    });
    scope.querySelectorAll('[data-i18n-html]').forEach((el) => {
      const key = el.getAttribute('data-i18n-html');
      if (key) el.innerHTML = t(key);
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
      const key = el.getAttribute('data-i18n-placeholder');
      if (key) el.placeholder = t(key);
    });
    scope.querySelectorAll('[data-i18n-title]').forEach((el) => {
      const key = el.getAttribute('data-i18n-title');
      if (key) el.title = t(key);
    });
    scope.querySelectorAll('[data-i18n-aria]').forEach((el) => {
      const key = el.getAttribute('data-i18n-aria');
      if (key) el.setAttribute('aria-label', t(key));
    });
    const titleKey = document.documentElement.getAttribute('data-i18n-title-key');
    if (titleKey) document.title = t(titleKey);

    document.querySelectorAll('[data-lang-switcher]').forEach(updateLangSwitcherUI);
    syncLegalLangBlocks();
  }

  function updateLangSwitcherUI(wrapper) {
    if (!wrapper) return;
    wrapper.querySelectorAll('.lang-switcher-code[data-lang-code]').forEach((codeEl) => {
      const code = codeEl.getAttribute('data-lang-code');
      const isActive = code === currentLang;
      codeEl.classList.toggle('is-active', isActive);
    });
    wrapper.querySelectorAll('.lang-switcher-option').forEach((opt) => {
      const isActive = opt.getAttribute('data-lang') === currentLang;
      opt.classList.toggle('is-active', isActive);
      opt.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    const btn = wrapper.querySelector('.lang-switcher-btn');
    if (btn) btn.setAttribute('aria-label', t('lang.dropdownAria'));
  }

  function closeAllLangMenus(except) {
    document.querySelectorAll('[data-lang-switcher]').forEach((w) => {
      if (except && w === except) return;
      const menu = w.querySelector('.lang-switcher-menu');
      const btn = w.querySelector('.lang-switcher-btn');
      if (menu) menu.classList.add('hidden');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    });
  }

  function bindLangSwitcher(wrapper) {
    if (!wrapper || wrapper.dataset.bound) return;
    wrapper.dataset.bound = '1';
    const btn = wrapper.querySelector('.lang-switcher-btn');
    const menu = wrapper.querySelector('.lang-switcher-menu');
    if (!btn || !menu) return;

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = menu.classList.contains('hidden');
      closeAllLangMenus(wrapper);
      menu.classList.toggle('hidden', !open);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    menu.querySelectorAll('.lang-switcher-option').forEach((opt) => {
      opt.addEventListener('click', (e) => {
        e.stopPropagation();
        const lang = opt.getAttribute('data-lang');
        if (lang) setLang(lang);
        menu.classList.add('hidden');
        btn.setAttribute('aria-expanded', 'false');
      });
    });

    updateLangSwitcherUI(wrapper);
  }

  function upgradeLegacyLangToggles() {
    document.querySelectorAll('.lang-toggle').forEach((btn) => {
      if (btn.closest('[data-lang-switcher]')) return;
      const parent = btn.parentElement;
      if (!parent) return;
      const wrapper = document.createElement('div');
      wrapper.className = 'lang-switcher';
      wrapper.setAttribute('data-lang-switcher', '');
      wrapper.innerHTML =
        '<button type="button" class="lang-switcher-btn" aria-haspopup="listbox" aria-expanded="false">' +
        '<span class="material-symbols-outlined lang-switcher-icon" aria-hidden="true">language</span>' +
        '<span class="lang-switcher-codes" aria-hidden="true">' +
        '<span class="lang-switcher-code" data-lang-code="vi">VI</span>' +
        '<span class="lang-switcher-sep">|</span>' +
        '<span class="lang-switcher-code" data-lang-code="en">EN</span>' +
        '</span>' +
        '<span class="material-symbols-outlined lang-switcher-chevron" aria-hidden="true">expand_more</span>' +
        '</button>' +
        '<ul class="lang-switcher-menu hidden" role="listbox">' +
        '<li class="lang-switcher-option" role="option" data-lang="vi" data-i18n="lang.optionVi">Tiếng Việt</li>' +
        '<li class="lang-switcher-option" role="option" data-lang="en" data-i18n="lang.optionEn">English</li>' +
        '</ul>';
      parent.replaceChild(wrapper, btn);
      bindLangSwitcher(wrapper);
    });
  }

  function initLangSwitchers() {
    document.querySelectorAll('[data-lang-switcher]').forEach(bindLangSwitcher);
    upgradeLegacyLangToggles();
  }

  async function setLang(lang, skipEvent) {
    const next = lang === 'en' ? 'en' : 'vi';
    if (next === currentLang && !skipEvent) return;
    currentLang = next;
    setStoredLang(next);
    document.documentElement.lang = next;
    if (landingViSource) Object.assign(STRINGS.vi, landingViSource);
    if (next === 'en' && landingEnSource) Object.assign(STRINGS.en, landingEnSource);
    applyI18n();
    applyLandingExtrasLocalized(next, document);
    await translateDynamicElements(document);
    if (!skipEvent) {
      document.dispatchEvent(new CustomEvent('langchange', { detail: { lang: next } }));
    }
  }

  function toggleLang() {
    setLang(currentLang === 'vi' ? 'en' : 'vi');
  }

  function syncLegalLangBlocks() {
    const lang = currentLang;
    document.querySelectorAll('.legal-lang--vi').forEach((el) => {
      el.classList.toggle('hidden', lang !== 'vi');
    });
    document.querySelectorAll('.legal-lang--en').forEach((el) => {
      el.classList.toggle('hidden', lang !== 'en');
    });
  }

  async function loadStrings() {
    try {
      const res = await fetch(I18N_JSON_URL, { cache: 'no-cache' });
      if (res.ok) {
        const data = await res.json();
        if (data && data.vi && data.en) STRINGS = data;
      }
    } catch (_) {}
  }

  let landingExtras = null;
  let landingViSource = null;
  let landingEnSource = null;

  function stripHtmlToText(value) {
    const s = String(value || '').trim();
    if (!s) return '';
    if (!/<[a-z][\s\S]*>/i.test(s)) return s;
    const tmp = document.createElement('div');
    tmp.innerHTML = s;
    return (tmp.textContent || tmp.innerText || '').replace(/\s+/g, ' ').trim();
  }

  function normalizeLandingFieldValue(key, value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    if (LANDING_HTML_I18N_KEYS.has(key)) return raw;
    let text = stripHtmlToText(raw);
    if (LANDING_SHORT_TEXT_KEYS.has(key) && text.length > 160) {
      const firstLine = text.split(/\n+/).find((line) => line.trim()) || text;
      text = firstLine.length <= 160 ? firstLine : firstLine.slice(0, 160).trim();
    }
    return text;
  }

  function sanitizeLandingLangSource(block) {
    const out = { ...block };
    LANDING_I18N_KEYS.forEach((key) => {
      if (out[key] != null) out[key] = normalizeLandingFieldValue(key, out[key]);
    });
    return out;
  }

  async function loadLandingOverrides() {
    try {
      const res = await fetch('/api/site/landing', { cache: 'no-cache' });
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.vi && typeof data.vi === 'object') {
        landingViSource = sanitizeLandingLangSource(data.vi);
        Object.assign(STRINGS.vi, landingViSource);
      }
      if (data && data.en && typeof data.en === 'object') {
        landingEnSource = sanitizeLandingLangSource(data.en);
        Object.assign(STRINGS.en, landingEnSource);
      }
      landingExtras = {
        hero_image_url: data.hero_image_url || '',
        hero_marker_a: data.hero_marker_a || '',
        hero_marker_b: data.hero_marker_b || '',
        hero_marker_a_en: data.hero_marker_a_en || '',
        hero_marker_b_en: data.hero_marker_b_en || '',
      };
    } catch (_) {}
  }

  function applyLandingExtrasLocalized(lang, root) {
    if (!landingExtras) return;
    const scope = root || document;
    const img = scope.querySelector('#landingHeroImg');
    if (img && landingExtras.hero_image_url) img.src = landingExtras.hero_image_url;
    const ma = scope.querySelector('#landingHeroMarkerA');
    const mb = scope.querySelector('#landingHeroMarkerB');
    if (ma) {
      const viMarker = stripHtmlToText(landingExtras.hero_marker_a);
      const enMarker = stripHtmlToText(landingExtras.hero_marker_a_en) || viMarker;
      ma.textContent = lang === 'en' ? enMarker : viMarker;
    }
    if (mb) {
      const viMarker = stripHtmlToText(landingExtras.hero_marker_b);
      const enMarker = stripHtmlToText(landingExtras.hero_marker_b_en) || viMarker;
      mb.textContent = lang === 'en' ? enMarker : viMarker;
    }
  }

  function applyLandingExtras(root) {
    applyLandingExtrasLocalized(currentLang, root);
  }

  async function mergeLandingStrings(vi, en, extras) {
    if (vi && typeof vi === 'object') {
      landingViSource = sanitizeLandingLangSource(vi);
      Object.assign(STRINGS.vi, landingViSource);
    }
    if (en && typeof en === 'object') {
      landingEnSource = sanitizeLandingLangSource(en);
      Object.assign(STRINGS.en, landingEnSource);
    }
    if (extras && typeof extras === 'object') {
      landingExtras = {
        hero_image_url: extras.hero_image_url || '',
        hero_marker_a: extras.hero_marker_a || '',
        hero_marker_b: extras.hero_marker_b || '',
        hero_marker_a_en: extras.hero_marker_a_en || '',
        hero_marker_b_en: extras.hero_marker_b_en || '',
      };
    }
    const introRoot = document.getElementById('pageIntro');
    const lang = currentLang;
    if (introRoot) {
      applyI18n(introRoot);
      applyLandingExtrasLocalized(lang, introRoot);
    } else {
      applyI18n();
      applyLandingExtrasLocalized(lang, document);
    }
  }

  async function init() {
    currentLang = getStoredLang();
    document.documentElement.lang = currentLang;
    await loadStrings();
    await loadLandingOverrides();
    ready = true;
    applyI18n();
    applyLandingExtrasLocalized(currentLang, document);
    initLangSwitchers();
    await translateDynamicElements(document);
    document.dispatchEvent(new CustomEvent('i18nready', { detail: { lang: currentLang } }));
  }

  document.addEventListener('click', () => closeAllLangMenus());

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.I18n = {
    t,
    getLang,
    setLang,
    toggleLang,
    applyI18n,
    init,
    translateText,
    translateBatch,
    translateDynamicElements,
    mergeLandingStrings,
    applyLandingExtras,
    isReady: () => ready,
  };
  global.syncLegalLangBlocks = syncLegalLangBlocks;
  global.toggleLang = toggleLang;
})(typeof window !== 'undefined' ? window : globalThis);
