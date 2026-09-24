function startOffice() {
  const app = document.createElement('script');
  app.type = 'module';
  app.src = '/office.js';
  document.head.append(app);
}

(async () => {
  if (!('serviceWorker' in navigator)) return startOffice();
  try {
    await Promise.race([
      (async () => {
        await navigator.serviceWorker.register('/office-sw.js');
        await navigator.serviceWorker.ready;
      })(),
      new Promise(resolve => setTimeout(resolve, 8000))
    ]);
    if (!navigator.serviceWorker.controller) {
      await Promise.race([
        new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true })),
        new Promise(resolve => setTimeout(resolve, 1000))
      ]);
    }
  } catch {}
  startOffice();
})();
