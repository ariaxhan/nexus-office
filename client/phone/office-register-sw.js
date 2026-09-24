if ('serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('controllerchange', () => location.reload());
  navigator.serviceWorker.register('/office-sw.js').catch(() => {});
}
