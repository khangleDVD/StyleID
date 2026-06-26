/** Trang pháp lý — đồng bộ khối nội dung song ngữ (legal-lang--vi / --en). */
(function () {
  'use strict';

  function syncLegalLangBlocks() {
    const lang = (window.I18n && window.I18n.getLang && window.I18n.getLang()) || 'vi';
    document.querySelectorAll('.legal-lang--vi').forEach((el) => {
      el.classList.toggle('hidden', lang !== 'vi');
    });
    document.querySelectorAll('.legal-lang--en').forEach((el) => {
      el.classList.toggle('hidden', lang !== 'en');
    });
  }

  document.addEventListener('langchange', syncLegalLangBlocks);
  document.addEventListener('DOMContentLoaded', syncLegalLangBlocks);
  if (document.readyState !== 'loading') syncLegalLangBlocks();
})();
