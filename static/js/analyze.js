/**
 * analyze.js — Upload ảnh, gọi API phân tích, hiển thị kết quả, trace modal.
 * Depends on core.js (and i18n.js).
 */
let lastDisplayedResult = null;

async function localizeAnalysisReasonsToVi(data) {
  if (!data || getUiLang() !== 'vi' || !window.I18n || !window.I18n.translateBatch) {
    return data;
  }
  const texts = new Set();
  function collectReasons(val) {
    if (!Array.isArray(val)) return;
    val.forEach((r) => {
      const s = String(r || '').trim();
      if (s && !looksVietnameseText(s)) texts.add(s);
    });
  }
  function walkResult(r) {
    if (!r || typeof r !== 'object') return;
    (r.items || []).forEach((it) => collectReasons(it.reason));
  }
  walkResult(data);
  (data.results || []).forEach(walkResult);
  (data.items || []).forEach((it) => collectReasons(it.reason));
  if (!texts.size) return data;

  const map = await window.I18n.translateBatch(Array.from(texts), 'vi');
  const trReasons = (val) => {
    if (!Array.isArray(val)) return val;
    return val.map((r) => {
      const s = String(r || '').trim();
      if (!s || looksVietnameseText(s)) return r;
      return map.get(s) || r;
    });
  };
  const copy = JSON.parse(JSON.stringify(data));
  function applyResult(r) {
    if (!r || typeof r !== 'object') return;
    (r.items || []).forEach((it) => {
      if (it.reason) it.reason = trReasons(it.reason);
    });
  }
  applyResult(copy);
  (copy.results || []).forEach(applyResult);
  (copy.items || []).forEach((it) => {
    if (it.reason) it.reason = trReasons(it.reason);
  });
  return copy;
}

/** Dịch nội dung động từ kết quả AI (chỉ giao diện, không đổi dữ liệu gốc). */
async function localizeAnalysisData(data) {
  if (!data || getUiLang() !== 'en' || !window.I18n || !window.I18n.translateBatch) {
    return data;
  }
  const texts = new Set();
  function collect(val) {
    if (val == null) return;
    if (Array.isArray(val)) {
      val.forEach(collect);
      return;
    }
    const s = String(val).trim();
    if (s) texts.add(s);
  }
  function walkResult(r) {
    if (!r || typeof r !== 'object') return;
    collect(r.overall_style_description);
    collect(r.suggested_occasions);
    collect(r.mix_suggestions);
    collect(r.analysis_error);
    (r.items || []).forEach((it) => {
      collect(it.category_display);
      collect(it.reason);
    });
  }
  walkResult(data);
  (data.results || []).forEach(walkResult);
  collect(data.overall_style_description);
  collect(data.suggested_occasions);
  collect(data.mix_suggestions);
  collect(data.analysis_error);
  (data.items || []).forEach((it) => {
    collect(it.category_display);
    collect(it.reason);
  });

  if (!texts.size) return data;

  const map = await window.I18n.translateBatch(Array.from(texts), 'en');
  const tr = (val) => {
    if (val == null) return val;
    if (Array.isArray(val)) return val.map(tr);
    const s = String(val).trim();
    return map.get(s) || val;
  };
  const copy = JSON.parse(JSON.stringify(data));
  function applyResult(r) {
    if (!r || typeof r !== 'object') return;
    if (r.overall_style_description) r.overall_style_description = tr(r.overall_style_description);
    if (r.suggested_occasions) r.suggested_occasions = tr(r.suggested_occasions);
    if (r.mix_suggestions) r.mix_suggestions = tr(r.mix_suggestions);
    if (r.analysis_error) r.analysis_error = tr(r.analysis_error);
    (r.items || []).forEach((it) => {
      if (it.category_display) it.category_display = tr(it.category_display);
      if (it.reason) it.reason = tr(it.reason);
    });
  }
  applyResult(copy);
  (copy.results || []).forEach(applyResult);
  return copy;
}
// ---------- Upload ----------
const fileInput = el('fileInput');
const uploadZone = el('uploadZone');
const uploadPlaceholder = el('uploadPlaceholder');
const previewList = el('previewList');
const btnAnalyze = el('btnAnalyze');
const loading = el('loading');
const result = el('result');
const resultEmptyState = el('resultEmptyState');

function syncAnalyzeResultEmpty() {
  if (!resultEmptyState) return;
  const hasResult = result && !result.classList.contains('hidden');
  const isLoading = loading && !loading.classList.contains('hidden');
  if (hasResult || isLoading) {
    resultEmptyState.classList.add('hidden');
    resultEmptyState.setAttribute('aria-hidden', 'true');
  } else {
    resultEmptyState.classList.remove('hidden');
    resultEmptyState.setAttribute('aria-hidden', 'false');
  }
}

let selectedFiles = [];

function isAllowedImageFile(file) {
  const allowed = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp'];
  if (file.type && allowed.includes(file.type)) return true;
  const name = (file.name || '').toLowerCase();
  return /\.(png|jpe?g|webp)$/.test(name);
}

function addFiles(files) {
  for (const f of files) {
    if (!isAllowedImageFile(f)) continue;
    if (selectedFiles.some(x => x.name === f.name && x.size === f.size)) continue;
    selectedFiles.push(f);
  }
  renderPreviews();
  btnAnalyze.disabled = selectedFiles.length === 0;
}

function removeFile(index) {
  selectedFiles.splice(index, 1);
  renderPreviews();
  btnAnalyze.disabled = selectedFiles.length === 0;
}

function renderPreviews() {
  previewList.innerHTML = '';
  if (selectedFiles.length > 0) {
    hide(uploadPlaceholder);
    uploadZone?.classList.add('has-previews');
  } else {
    show(uploadPlaceholder);
    uploadZone?.classList.remove('has-previews');
  }
  selectedFiles.forEach((file, i) => {
    const div = document.createElement('div');
    div.className = 'preview-item';
    const img = document.createElement('img');
    img.src = URL.createObjectURL(file);
    img.classList.add('img-lightbox');
    img.title = t('js.viewLarge');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'remove';
    btn.textContent = '×';
    btn.addEventListener('click', () => removeFile(i));
    div.appendChild(img);
    div.appendChild(btn);
    previewList.appendChild(div);
  });
}

uploadZone.addEventListener('click', (e) => {
  if (e.target.closest('.preview-item')) return;
  fileInput.click();
});

fileInput.addEventListener('change', () => {
  addFiles(Array.from(fileInput.files));
  fileInput.value = '';
});

uploadZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  uploadZone.classList.add('dragover');
});

uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('dragover');
  addFiles(Array.from(e.dataTransfer.files));
});

// ---------- Phân tích ----------
btnAnalyze.addEventListener('click', async () => {
  if (selectedFiles.length === 0) return;
  hide(result);
  show(loading);
  syncAnalyzeResultEmpty();
  btnAnalyze.disabled = true;
  const formData = new FormData();
  selectedFiles.forEach(f => formData.append('images', f));
  if (currentUser && currentUser.id) formData.append('user_id', currentUser.id);
  try {
    const res = await fetch(API_BASE + '/api/analyze', { method: 'POST', body: formData });
    const data = await res.json();
    if (res.status === 402) {
      if (currentUser && typeof data.credits_remaining === 'number') {
        setUser({ ...currentUser, analysis_credits: data.credits_remaining });
      }
      alert(data.error || t('js.errCredits'));
      return;
    }
    if (!res.ok) throw new Error(data.error || t('js.errAnalyze'));
    lastDisplayedResult = data;
    await displayResult(data);
    if (currentUser && typeof data.credits_remaining === 'number') {
      setUser({ ...currentUser, analysis_credits: data.credits_remaining });
    }
    if (currentUser) showSavedNotice();
    show(result);
    syncAnalyzeResultEmpty();
    el('analyzeResultsPanel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    alert(err.message || t('js.errGeneric'));
  } finally {
    hide(loading);
    syncAnalyzeResultEmpty();
    btnAnalyze.disabled = false;
  }
});

function showSavedNotice() {
  const resultContent = el('resultContent');
  if (!resultContent) return;
  let notice = resultContent.querySelector('.saved-notice');
  if (!notice) {
    notice = document.createElement('p');
    notice.className = 'saved-notice hint';
    notice.textContent = t('js.savedHistory');
    resultContent.insertBefore(notice, resultContent.firstChild);
  }
}

/** Phong cách tổng thể: chỉ hiển thị nhãn EN (bỏ hậu tố " / …" từ bản ghi lịch sử cũ). */
function overallStyleDisplayEn(raw) {
  const s = (raw == null ? '' : String(raw)).trim();
  if (!s) return '';
  const sep = ' / ';
  const i = s.indexOf(sep);
  if (i >= 0) return s.slice(0, i).trim() || s;
  return s;
}

/** Tên món tiếng Anh (banner «Từng món»). */
function itemDisplayNameEn(it) {
  return (it.item_en || it.item || it.item_type || it.design_name || it.description || '').trim() || '—';
}

function _num01(v, fallback) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function pickBannerItems(items, opts) {
  opts = opts || {};
  const input = Array.isArray(items) ? items : [];
  const uniq = new Map(); // name -> best item (highest confidence)

  input.forEach((it) => {
    const name = itemDisplayNameEn(it);
    if (!name || name === '—') return;
    const conf = _num01(it && it.confidence, 0);
    const prev = uniq.get(name);
    if (!prev || conf > _num01(prev.confidence, 0)) {
      uniq.set(name, it);
    }
  });

  let arr = Array.from(uniq.values())
    .map((it) => ({ it, conf: _num01(it.confidence, 0) }))
    .sort((a, b) => b.conf - a.conf);

  const picked = arr.map((x) => x.it);

  const totalUnique = uniq.size;
  return { items: picked, totalUnique };
}

/** Tên món: luôn EN / VI khi có hai nhãn khác nhau (plain text). */
function itemNameBilingualPlain(it) {
  const en = (it.item_en || it.item || it.item_type || '').trim() || '—';
  const vi = (it.item_vi || '').trim();
  if (!vi || vi === en) return en;
  return `${en} / ${vi}`;
}

/** Tên món cho innerHTML (đã escape). */
function itemNameBilingualHtml(it) {
  const en = (it.item_en || it.item || it.item_type || '').trim() || '—';
  const vi = (it.item_vi || '').trim();
  if (!vi || vi === en) return escapeHtml(en);
  return `${escapeHtml(en)} / ${escapeHtml(vi)}`;
}

function itemConfidencePct(it) {
  const c = it && (it.confidence != null ? it.confidence : it.detection_confidence);
  if (c == null) return null;
  const n = Number(c);
  if (!Number.isFinite(n)) return null;
  return n <= 1 ? Math.round(n * 100) : Math.round(n);
}

function itemColorDisplay(it) {
  const ct = it && it.color_tone;
  if (!ct) return null;
  const raw = Array.isArray(ct) ? (ct[0] || '') : ct;
  const label = String(raw || '').trim();
  if (!label) return null;
  const hexMap = {
    black: '#121212', dark: '#1a1a1a', white: '#f5f5f5', neutral: '#8a8a8a',
    monochrome: '#555555', muted: '#6b6b6b', bright: '#ffd165', neon: '#39ff14',
    metallic: '#b8b8b8', red: '#c62828', blue: '#1565c0', green: '#2e7d32',
  };
  const hex = hexMap[label.toLowerCase()] || '#333535';
  const pretty = label.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return { label: pretty, hex };
}

function downloadAnalysisReport(data) {
  try {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'styleid-analysis-' + Date.now() + '.json';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (_) {}
}

function openSimilarProductsSearch(overallStyle, items) {
  const names = (items || []).slice(0, 3).map((it) => itemDisplayNameEn(it)).filter(Boolean).join(' ');
  const q = encodeURIComponent((overallStyleDisplayEn(overallStyle) + ' fashion outfit ' + names).trim());
  window.open('https://www.google.com/search?q=' + q + '&tbm=shop', '_blank', 'noopener,noreferrer');
}

function buildAnalyzeV2HotspotsHtml(items, blockId) {
  const offsets = (window.analyzeV2HotspotOffsets || [
    { top: 15, left: 45 }, { top: 45, left: 50 }, { top: 75, left: 40 },
  ]);
  const list = Array.isArray(items) ? items : [];
  return list.slice(0, 8).map((it, i) => {
    const pos = offsets[i % offsets.length];
    const title = itemDisplayNameEn(it);
    const itemId = blockId + '-item-' + i;
    return '<div class="analyze-v2-hotspot" style="top:' + pos.top + '%;left:' + pos.left + '%;" title="' +
      escapeHtml(title) + '" data-item-id="' + escapeHtml(itemId) + '" aria-hidden="true"></div>';
  }).join('');
}

function buildAnalyzeV2ItemCard(it, idx, blockId) {
  const itemId = blockId + '-item-' + idx;
  const itemStr = itemNameBilingualHtml(it);
  const categoryRaw = (it.category || '').replace(/_/g, ' ');
  const categoryEn = categoryRaw ? categoryRaw.replace(/\b\w/g, (c) => c.toUpperCase()) : '—';
  const categoryVi = it.category_display || '';
  const categoryLabel = categoryVi
    ? escapeHtml(t('js.category')) + ': ' + escapeHtml(categoryEn) + ' / ' + escapeHtml(categoryVi)
    : escapeHtml(t('js.category')) + ': ' + escapeHtml(categoryEn);

  const confPct = itemConfidencePct(it);
  const accuracyBadge = confPct != null
    ? '<span class="analyze-v2-accuracy-badge">' + escapeHtml(t('analyze.accuracy', { n: confPct })) + '</span>'
    : '';

  const modelDetected = Array.isArray(it.model_detected_styles) ? it.model_detected_styles : [];
  let modelLines = '';
  if (modelDetected.length > 0) {
    modelLines = modelDetected.slice(0, 3).map((m, i) => {
      const ts = (m && m.top_style ? String(m.top_style) : '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
      const tc = (m && typeof m.top_confidence === 'number') ? Math.round(m.top_confidence * 100) + '%' : '—';
      const muted = !ts ? ' is-muted' : '';
      return '<div class="analyze-v2-model-line' + muted + '"><span class="analyze-v2-model-dot"></span><span>Model ' +
        (i + 1) + ': ' + escapeHtml(ts || '—') + ' (' + escapeHtml(tc) + ')</span></div>';
    }).join('');
  } else {
    const detectedStyles = it.detected_styles || [];
    const finalStyleRaw = (it.final_style || it.style || 'casual').toString();
    const finalStyleEn = finalStyleRaw.replace(/\b\w/g, (c) => c.toUpperCase());
    modelLines = '<div class="analyze-v2-model-line"><span class="analyze-v2-model-dot"></span><span>' +
      escapeHtml(finalStyleEn) + '</span></div>';
    if (detectedStyles.length > 1) {
      modelLines += detectedStyles.slice(1, 3).map((s) => {
        const name = (s.style || s.name || '').toString().replace(/\b\w/g, (c) => c.toUpperCase());
        return '<div class="analyze-v2-model-line is-muted"><span class="analyze-v2-model-dot"></span><span>' +
          escapeHtml(name || '—') + '</span></div>';
      }).join('');
    }
  }

  const reasons = Array.isArray(it.reason) ? it.reason : [];
  const reasonHtml = reasons.length > 0
    ? '<p class="analyze-v2-reason">' + escapeHtml(t('js.reason')) + ' ' + escapeHtml(reasons.join(' ')) + '</p>'
    : '';

  const color = itemColorDisplay(it);
  const colorHtml = color
    ? '<div class="analyze-v2-color-box"><p class="analyze-v2-color-kicker">' + escapeHtml(t('analyze.colorAnalysis')) +
      '</p><div class="analyze-v2-color-row"><span class="analyze-v2-color-swatch" style="background:' +
      escapeHtml(color.hex) + '"></span><span>' + escapeHtml(color.label) + '</span></div></div>'
    : '';

  const openCls = idx === 0 ? ' is-open' : '';
  return (
    '<article class="analyze-v2-item-card' + openCls + '" id="' + escapeHtml(itemId) + '">' +
    '<button type="button" class="analyze-v2-item-head" aria-expanded="' + (idx === 0 ? 'true' : 'false') + '">' +
    '<div><div class="analyze-v2-item-title-row"><span class="analyze-v2-item-name">' + itemStr + '</span>' + accuracyBadge +
    '</div><p class="analyze-v2-item-cat">' + categoryLabel + '</p></div>' +
    '<span class="material-symbols-outlined analyze-v2-item-chevron" aria-hidden="true">expand_more</span></button>' +
    '<div class="analyze-v2-item-body"><div class="analyze-v2-model-lines">' + modelLines + reasonHtml + '</div>' +
    (colorHtml || '<div></div>') + '</div></article>'
  );
}

function buildResultBlock(opts) {
  const {
    title, imageUrl, imageUrls, items, overall_style, overall_style_description,
    suggested_occasions, mix_suggestions, analysis_error, analysis_trace,
  } = opts;
  const blockId = 'az-block-' + Math.random().toString(36).slice(2, 9);
  const itemsData = items || [];
  const block = document.createElement('section');
  block.className = 'analyze-v2-result-block result-block';
  block.dataset.blockId = blockId;

  const primaryUrl = imageUrl || ((imageUrls && imageUrls[0]) ? imageUrls[0] : null);
  const extraUrls = (imageUrls || []).filter((u) => u && u !== primaryUrl);

  let visualHtml = '<div class="analyze-v2-image-frame">';
  if (primaryUrl) {
    visualHtml += '<img class="img-lightbox analyze-v2-main-img" data-src="' + escapeHtml(primaryUrl) + '" alt="' +
      escapeHtml(t('js.analyzedImage')) + '" title="' + escapeHtml(t('js.viewLarge')) + '">';
    visualHtml += buildAnalyzeV2HotspotsHtml(itemsData, blockId);
    visualHtml += '<div class="analyze-v2-image-overlay"><span class="analyze-v2-verified">' +
      '<span class="material-symbols-outlined" aria-hidden="true">verified</span>' +
      escapeHtml(t('analyze.verified')) + '</span></div>';
  } else {
    visualHtml += '<div class="analyze-v2-empty-items" style="margin:1rem;">' + escapeHtml(t('js.noItems')) + '</div>';
  }
  visualHtml += '</div>';

  if (extraUrls.length > 0) {
    visualHtml += '<div class="analyze-v2-extra-thumbs">';
    extraUrls.forEach((url) => {
      visualHtml += '<img class="img-lightbox" data-src="' + escapeHtml(url) + '" alt="' +
        escapeHtml(t('js.analyzedImage')) + '" title="' + escapeHtml(t('js.viewLarge')) + '">';
    });
    visualHtml += '</div>';
  }

  const styleName = overallStyleDisplayEn(overall_style) || '—';
  const styleDesc = (overall_style_description && overall_style_description.trim())
    ? escapeHtml(overall_style_description.trim())
    : '';

  let itemsHtml = '';
  if (itemsData.length === 0) {
    let text = t('js.noItems');
    if (analysis_error) text += ' ' + t('common.error') + ': ' + analysis_error;
    else text += t('js.tryClearer');
    itemsHtml = '<div class="analyze-v2-empty-items">' + escapeHtml(text) + '</div>';
  } else {
    itemsHtml = '<div class="analyze-v2-items-list">' +
      itemsData.map((it, idx) => buildAnalyzeV2ItemCard(it, idx, blockId)).join('') + '</div>';
  }

  const occasions = suggested_occasions || [];
  const occasionsHtml = occasions.length > 0
    ? occasions.map((occ) =>
      '<div class="analyze-v2-occasion-row"><span>' + escapeHtml(occ) +
      '</span><span class="material-symbols-outlined" aria-hidden="true">check</span></div>',
    ).join('')
    : '<p class="analyze-v2-empty-items" style="border:none;padding:0;">—</p>';

  let mixInner = '';
  if (mix_suggestions && mix_suggestions.length > 0) {
    mixInner = '<ul class="analyze-v2-mix-list">' +
      mix_suggestions.map((s) => '<li>' + escapeHtml(s) + '</li>').join('') + '</ul>';
  } else {
    mixInner = '<div class="analyze-v2-mix-empty"><p>' + escapeHtml(t('analyze.mixLead')) +
      '</p><button type="button" class="analyze-v2-btn-mix" data-mix-btn="1">' +
      '<span class="material-symbols-outlined" aria-hidden="true">auto_fix_high</span>' +
      '<span class="btn-mix-text">' + escapeHtml(t('js.mixSuggest')) + '</span></button></div>';
  }

  let traceHtml = '';
  if (analysis_trace && isAdmin()) {
    traceHtml = '<div class="analyze-v2-trace-row"><button type="button" class="analyze-v2-trace-btn" data-trace-btn="1">' +
      '<span class="material-symbols-outlined" aria-hidden="true">analytics</span>' +
      escapeHtml(t('js.viewModels')) + '</button></div>';
  }

  block.innerHTML =
    (title ? '<h3 class="analyze-v2-block-title">' + escapeHtml(title) + '</h3>' : '') +
    '<div class="analyze-v2-results-grid">' +
    '<div class="analyze-v2-visual">' + visualHtml + '</div>' +
    '<div class="analyze-v2-details">' +
    traceHtml +
    '<div class="analyze-v2-style-card">' +
    '<div class="analyze-v2-style-head">' +
    '<div class="analyze-v2-style-icon"><span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span></div>' +
    '<div><p class="analyze-v2-style-kicker">' + escapeHtml(t('analyze.overallStyleLabel')) + '</p>' +
    '<p class="analyze-v2-style-name">' + escapeHtml(styleName) + '</p></div></div>' +
    (styleDesc ? '<p class="analyze-v2-style-desc">' + styleDesc + '</p>' : '') +
    '</div>' +
    '<h4 class="analyze-v2-items-head"><span class="material-symbols-outlined" aria-hidden="true">list_alt</span> ' +
    escapeHtml(t('js.itemDetails')) + '</h4>' +
    itemsHtml +
    '<div class="analyze-v2-meta-grid">' +
    '<div class="analyze-v2-meta-card">' +
    '<h5 class="analyze-v2-meta-title"><span class="material-symbols-outlined" aria-hidden="true">calendar_today</span> ' +
    escapeHtml(t('js.occasionsTitle')) + '</h5>' +
    '<div class="analyze-v2-occasions">' + occasionsHtml + '</div></div>' +
    '<div class="analyze-v2-meta-card analyze-v2-meta-card--mix">' +
    '<h5 class="analyze-v2-meta-title"><span class="material-symbols-outlined" aria-hidden="true">auto_fix_high</span> ' +
    escapeHtml(t('js.mixTitle')) + '</h5>' + mixInner + '</div></div>' +
    '<div class="analyze-v2-actions">' +
    '<button type="button" class="analyze-v2-btn-shop" data-shop-btn="1">' +
    '<span class="material-symbols-outlined" aria-hidden="true">shopping_bag</span>' +
    escapeHtml(t('analyze.shopSimilar')) + '</button>' +
    (isAdmin()
      ? '<button type="button" class="analyze-v2-btn-save" data-save-btn="1">' +
        '<span class="material-symbols-outlined" aria-hidden="true">download</span>' +
        escapeHtml(t('analyze.saveReport')) + '</button>'
      : '') +
    '</div>' +
    '</div></div>';

  block.querySelectorAll('img[data-src]').forEach((img) => {
    bindUploadImage(img, img.getAttribute('data-src'));
    img.removeAttribute('data-src');
  });

  const traceBtn = block.querySelector('[data-trace-btn]');
  if (traceBtn) {
    traceBtn.addEventListener('click', () => openTraceModal(analysis_trace, itemsData, overall_style));
  }

  const mixBtn = block.querySelector('[data-mix-btn]');
  const mixList = block.querySelector('.analyze-v2-mix-list');
  if (mixBtn) {
    const btnMixText = mixBtn.querySelector('.btn-mix-text');
    const mixEmpty = block.querySelector('.analyze-v2-mix-empty');
    mixBtn.addEventListener('click', async () => {
      mixBtn.disabled = true;
      if (btnMixText) btnMixText.textContent = t('js.mixLoading');
      try {
        const res = await fetch(API_BASE + '/api/mix-suggestions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            overall_style: overallStyleDisplayEn(overall_style) || '',
            items: itemsData,
          }),
        });
        const data = await res.json();
        if (res.ok && Array.isArray(data.suggestions) && data.suggestions.length > 0) {
          let suggestions = data.suggestions;
          if (getUiLang() === 'en' && window.I18n && window.I18n.translateBatch) {
            const map = await window.I18n.translateBatch(suggestions, 'en');
            suggestions = suggestions.map((s) => map.get(String(s).trim()) || s);
          }
          const ul = document.createElement('ul');
          ul.className = 'analyze-v2-mix-list';
          suggestions.forEach((s) => {
            const li = document.createElement('li');
            li.textContent = s;
            ul.appendChild(li);
          });
          if (mixEmpty) mixEmpty.replaceWith(ul);
        } else if (btnMixText) {
          btnMixText.textContent = t('js.mixSuggest');
          mixBtn.disabled = false;
        }
      } catch (_) {
        if (btnMixText) btnMixText.textContent = t('js.mixSuggest');
        mixBtn.disabled = false;
      }
    });
  }

  block.querySelector('[data-shop-btn]')?.addEventListener('click', () => {
    openSimilarProductsSearch(overall_style, itemsData);
  });

  block.querySelector('[data-save-btn]')?.addEventListener('click', () => {
    downloadAnalysisReport({
      overall_style,
      overall_style_description,
      items: itemsData,
      suggested_occasions: occasions,
      mix_suggestions: mix_suggestions || [],
      image_url: primaryUrl,
      image_urls: imageUrls || (primaryUrl ? [primaryUrl] : []),
    });
  });

  if (typeof window.refreshAnalyzeV2UI === 'function') {
    window.refreshAnalyzeV2UI(block);
  }

  return block;
}

async function displayResult(data) {
  const resultContent = el('resultContent');
  if (!resultContent) return;
  lastDisplayedResult = data;
  resultContent.innerHTML = '';

  const view = getUiLang() === 'en'
    ? await localizeAnalysisData(data)
    : await localizeAnalysisReasonsToVi(data);

  if (view.results && Array.isArray(view.results) && view.results.length > 0) {
    const imageUrls = view.image_urls || [];
    view.results.forEach((r, i) => {
      const block = buildResultBlock({
        title: t('js.imageN', { n: i + 1 }),
        imageUrl: imageUrls[i] || null,
        items: r.items,
        overall_style: r.overall_style,
        overall_style_description: r.overall_style_description,
        suggested_occasions: r.suggested_occasions,
        mix_suggestions: r.mix_suggestions,
        analysis_error: view.analysis_error,
        analysis_trace: r.analysis_trace,
      });
      resultContent.appendChild(block);
    });
  } else {
    const block = buildResultBlock({
      imageUrls: view.image_urls || [],
      items: view.items,
      overall_style: view.overall_style,
      overall_style_description: view.overall_style_description,
      suggested_occasions: view.suggested_occasions,
      mix_suggestions: view.mix_suggestions,
      analysis_error: view.analysis_error,
      analysis_trace: view.analysis_trace,
    });
    if (!view.image_urls && view.image_count) {
      const p = document.createElement('p');
      p.className = 'meta';
      p.textContent = t('js.imagesAnalyzed', { n: view.image_count });
      const visual = block.querySelector('.analyze-v2-visual');
      if (visual) visual.appendChild(p);
    }
    resultContent.appendChild(block);
  }
  if (typeof window.refreshAnalyzeV2UI === 'function') {
    window.refreshAnalyzeV2UI(resultContent);
  }
}

function _traceItemLabel(it) {
  const name = (it && (it.item_type || it.item || it.item_en || it.description) ? String(it.item_type || it.item || it.item_en || it.description) : '').trim() || '—';
  const cat = (it && it.category ? String(it.category) : '').trim();
  return cat ? `${name} (${cat})` : name;
}

function _renderTraceItemsList(items) {
  const ul = document.createElement('ul');
  ul.className = 'trace-list';
  (Array.isArray(items) ? items : []).forEach((it, idx) => {
    const li = document.createElement('li');
    const label = document.createElement('div');
    label.innerHTML = `<span class="trace-pill">#${idx + 1}</span>${escapeHtml(_traceItemLabel(it))}`;
    li.appendChild(label);
    const styles = (it && it.detected_styles) || (it && it.styles) || [];
    const finalStyle = (it && (it.final_style || it.style)) ? String(it.final_style || it.style) : '';
    if ((Array.isArray(styles) && styles.length) || finalStyle) {
      const meta = document.createElement('div');
      meta.className = 'trace-reason';
      const st = Array.isArray(styles)
        ? styles.slice(0, 3).map(s => {
            const nm = (s.style || s.name || '').toString();
            const sc = s.score != null ? Math.round((Number(s.score) || 0) * 100) + '%' : (s.confidence != null ? String(s.confidence) + '%' : '');
            return sc ? `${nm} (${sc})` : nm;
          }).filter(Boolean).join(', ')
        : '';
      const fs = finalStyle ? `${t('js.styleMain')} ${finalStyle}` : '';
      meta.textContent = [fs, st ? `${t('js.styleDetected')} ${st}` : ''].filter(Boolean).join(' · ');
      li.appendChild(meta);
    }
    const reasons = it && Array.isArray(it.reason) ? it.reason : [];
    if (reasons.length) {
      const r = document.createElement('div');
      r.className = 'trace-reason';
      r.textContent = t('js.reason') + ' ' + reasons.slice(0, 3).join(' · ');
      li.appendChild(r);
    }
    ul.appendChild(li);
  });
  return ul;
}

function openTraceModal(trace, finalItems, overallStyle) {
  const modal = el('modalAnalysisTrace');
  const body = el('traceBody');
  const meta = el('traceMeta');
  if (!modal || !body) return;
  body.innerHTML = '';
  const visionModels = Array.isArray(trace.vision_models) ? trace.vision_models : [];
  const weightsByModel = (trace.vision_model_weights_by_model && typeof trace.vision_model_weights_by_model === 'object')
    ? trace.vision_model_weights_by_model
    : {};
  const mergerModel = (trace.merger_model || '').toString();
  if (meta) {
    const wtxt = visionModels.length
      ? (' · Weights: ' + visionModels.map(m => `${m}=${weightsByModel[m] != null ? String(weightsByModel[m]) : '—'}`).join(', '))
      : '';
    meta.textContent = `Vision: ${visionModels.join(', ') || '—'} · Merger: ${mergerModel || '—'}${wtxt}` + (overallStyle ? ` · Overall: ${overallStyleDisplayEn(overallStyle)}` : '');
  }

  function renderTab(key) {
    body.innerHTML = '';
    if (key === 'detect') {
      const outs = Array.isArray(trace.step1_detect_outputs) ? trace.step1_detect_outputs : [];
      const errs = (trace.step1_detect_errors_by_model && typeof trace.step1_detect_errors_by_model === 'object')
        ? trace.step1_detect_errors_by_model
        : {};
      const byModel = new Map(outs.map(o => [o.model, o]));
      (visionModels.length ? visionModels : Array.from(byModel.keys())).forEach((mid) => {
        const o = byModel.get(mid) || { model: mid, data: null };
        const blk = document.createElement('div');
        blk.className = 'trace-block';
        const h = document.createElement('h3');
        h.textContent = t('js.traceModelSingle', { model: o.model || '—' });
        blk.appendChild(h);
        const items = o && o.data ? o.data.items : [];
        if (items && items.length) {
          blk.appendChild(_renderTraceItemsList(items));
        } else {
          const p = document.createElement('p');
          p.className = 'hint';
          p.textContent = errs[mid] ? t('js.traceNoOutputErr', { err: errs[mid] }) : t('js.traceNoOutput');
          blk.appendChild(p);
        }
        body.appendChild(blk);
      });
      if (!outs.length && !visionModels.length) {
        const blk = document.createElement('div');
        blk.className = 'trace-block';
        blk.innerHTML = '<h3>' + escapeHtml(t('js.traceNoData')) + '</h3><p class="hint">' + escapeHtml(t('js.traceNoBackend')) + '</p>';
        body.appendChild(blk);
      }
      return;
    }

    if (key === 'merged') {
      const blk = document.createElement('div');
      blk.className = 'trace-block';
      blk.innerHTML = '<h3>' + escapeHtml(t('js.traceMergedList')) + '</h3>';
      blk.appendChild(_renderTraceItemsList(trace.step2_merged_items || []));
      body.appendChild(blk);
      return;
    }

    if (key === 'final') {
      const blk = document.createElement('div');
      blk.className = 'trace-block';
      blk.innerHTML = '<h3>' + escapeHtml(t('js.traceFinalResult')) + '</h3>';
      const items = finalItems || trace.final_items || [];
      const table = document.createElement('table');
      table.className = 'stats-table';
      const thead = document.createElement('thead');
      thead.innerHTML = '<tr><th>' + escapeHtml(t('js.traceColItem')) + '</th><th>' + escapeHtml(t('js.traceColFinalStyle')) + '</th></tr>';
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      if (!Array.isArray(items) || items.length === 0) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="2" class="hint">' + escapeHtml(t('admin.noData')) + '</td>';
        tbody.appendChild(tr);
      } else {
        items.forEach((it) => {
          const tr = document.createElement('tr');
          const name = escapeHtml(_traceItemLabel(it));
          const fs = (it && (it.final_style || it.style) ? String(it.final_style || it.style) : '').trim() || '—';
          tr.innerHTML = `<td>${name}</td><td>${escapeHtml(fs)}</td>`;
          tbody.appendChild(tr);
        });
      }
      table.appendChild(tbody);
      blk.appendChild(table);
      body.appendChild(blk);
      return;
    }
  }

  // Tabs
  const tabs = qsAll('.trace-tab', modal);
  tabs.forEach((t) => {
    t.classList.toggle('active', t.dataset.traceTab === 'detect');
    t.onclick = () => {
      tabs.forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      renderTab(t.dataset.traceTab);
    };
  });
  renderTab('detect');

  modal.classList.remove('hidden');
}
