(function initLandingV2() {
  const root = document.getElementById('pageIntro');
  if (!root || !root.classList.contains('landing-v2')) return;

  root.querySelectorAll('.landing-v2-glass-card').forEach((card) => {
    card.addEventListener('mousemove', (e) => {
      const rect = card.getBoundingClientRect();
      card.style.setProperty('--mouse-x', `${e.clientX - rect.left}px`);
      card.style.setProperty('--mouse-y', `${e.clientY - rect.top}px`);
    });
  });

  const revealEls = root.querySelectorAll('.lv2-reveal');
  if (revealEls.length && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('lv2-visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.1, rootMargin: '0px 0px -40px 0px' }
    );
    revealEls.forEach((el) => observer.observe(el));
  } else {
    revealEls.forEach((el) => el.classList.add('lv2-visible'));
  }

  const scrollRoot = root.querySelector('.landing-scroll');
  const navLinks = root.querySelectorAll('.landing-v2-link[href^="#"]');
  const sections = [];
  navLinks.forEach((link) => {
    const id = link.getAttribute('href').slice(1);
    const sec = document.getElementById(id);
    if (sec) sections.push({ id, el: sec, link });
    link.addEventListener('click', (e) => {
      e.preventDefault();
      sec?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      navLinks.forEach((l) => l.classList.toggle('is-active', l === link));
    });
  });

  if (scrollRoot && sections.length && 'IntersectionObserver' in window) {
    const spy = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (!visible) return;
        const id = visible.target.id;
        navLinks.forEach((link) => {
          link.classList.toggle('is-active', link.getAttribute('href') === `#${id}`);
        });
      },
      { root: scrollRoot, threshold: [0.2, 0.45, 0.65], rootMargin: '-20% 0px -55% 0px' }
    );
    sections.forEach(({ el }) => spy.observe(el));
  }
})();
