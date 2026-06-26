(function () {
  const HOTSPOT_OFFSETS = [
    { top: 15, left: 45 },
    { top: 45, left: 50 },
    { top: 75, left: 40 },
    { top: 90, left: 48 },
    { top: 25, left: 48 },
    { top: 60, left: 35 },
    { top: 35, left: 62 },
    { top: 82, left: 55 },
  ];

  function runScanLine() {
    const line = document.getElementById('analyzeScanLine');
    const page = document.getElementById('pageAnalyze');
    if (!line || !page || !page.classList.contains('active')) return;

    line.style.opacity = '1';
    line.style.top = '0%';

    let pos = 0;
    const interval = setInterval(() => {
      if (!page.classList.contains('active')) {
        clearInterval(interval);
        line.style.opacity = '0';
        return;
      }
      if (pos >= 100) {
        clearInterval(interval);
        line.style.opacity = '0';
        setTimeout(runScanLine, 15000);
      } else {
        pos += 0.5;
        line.style.top = pos + '%';
      }
    }, 10);
  }

  function bindHotspots(root) {
    (root || document).querySelectorAll('.analyze-v2-hotspot').forEach((spot) => {
      if (spot.dataset.azBound) return;
      spot.dataset.azBound = '1';
      spot.addEventListener('mouseenter', () => {
        spot.style.animation = 'none';
        spot.style.transform = 'scale(1.35)';
      });
      spot.addEventListener('mouseleave', () => {
        spot.style.animation = '';
        spot.style.transform = '';
      });
      spot.addEventListener('click', (e) => {
        e.stopPropagation();
        const itemId = spot.dataset.itemId;
        if (!itemId) return;
        const card = document.getElementById(itemId);
        if (!card) return;
        card.classList.add('is-open');
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
    });
  }

  function bindItemAccordions(root) {
    (root || document).querySelectorAll('.analyze-v2-item-head').forEach((btn) => {
      if (btn.dataset.azBound) return;
      btn.dataset.azBound = '1';
      btn.addEventListener('click', () => {
        const card = btn.closest('.analyze-v2-item-card');
        if (card) card.classList.toggle('is-open');
      });
    });
  }

  function refreshAnalyzeV2Interactions(root) {
    bindHotspots(root);
    bindItemAccordions(root);
  }

  window.refreshAnalyzeV2UI = refreshAnalyzeV2Interactions;
  window.analyzeV2HotspotOffsets = HOTSPOT_OFFSETS;

  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(runScanLine, 2000);
  });

  document.addEventListener('pagechange', (e) => {
    if (e.detail && e.detail.page === 'analyze') {
      setTimeout(runScanLine, 800);
    }
  });
})();
