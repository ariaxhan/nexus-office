function startOffice() {
  const app = document.createElement('script');
  app.type = 'module';
  app.src = '/office-bundle.js';
  app.addEventListener('error', () => {
    document.querySelector('#connection').textContent = 'Could not open Office';
    const content = document.querySelector('#content');
    const failure = document.createElement('section');
    failure.className = 'startup-failure';
    const title = document.createElement('h1');
    title.textContent = 'Office could not open';
    const message = document.createElement('p');
    message.textContent = 'Check your connection and try again.';
    const retry = document.createElement('button');
    retry.textContent = 'Try again';
    retry.addEventListener('click', () => location.reload());
    failure.append(title, message, retry);
    content.replaceChildren(failure);
  });
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
