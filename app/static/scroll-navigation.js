// Quick navigation for long card lists, with keyboard labels and reduced motion.
(() => {
  const controls = document.getElementById('page-scroll');
  if (!controls) return;
  const up = document.getElementById('page-scroll-up');
  const down = document.getElementById('page-scroll-down');
  let pending = false;
  function refresh() {
    pending = false;
    const max = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    controls.hidden = max < 300;
    up.disabled = window.scrollY <= 8;
    down.disabled = window.scrollY >= max - 8;
  }
  function schedule() {
    if (!pending) { pending = true; requestAnimationFrame(refresh); }
  }
  function jump(bottom) {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({top: bottom ? document.documentElement.scrollHeight : 0,
      behavior: reduced ? 'instant' : 'smooth'});
  }
  up.addEventListener('click', () => jump(false));
  down.addEventListener('click', () => jump(true));
  window.addEventListener('scroll', schedule, {passive: true});
  window.addEventListener('resize', schedule);
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(schedule).observe(document.body);
  refresh();
})();
