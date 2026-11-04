(() => {
  const token = document.currentScript?.dataset.token;
  if (!token) return;
  const newId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  let clientId = newId();
  let closed = false;
  let timer;
  const heartbeat = () => {
    if (closed) return;
    return fetch('/api/portal/heartbeat', {
      method:'POST', headers:{'Content-Type':'application/json','X-Portal-Token':token},
      body:JSON.stringify({client_id:clientId}), keepalive:true,
    }).catch(() => {});
  };
  window.addEventListener('pagehide', () => {
    if (closed) return;
    closed = true;
    clearInterval(timer);
    const body = JSON.stringify({client_id:clientId,token});
    if (!navigator.sendBeacon('/api/portal/close',body)) {
      fetch('/api/portal/close',{method:'POST',body,keepalive:true}).catch(() => {});
    }
  });
  window.addEventListener('pageshow', () => {
    if (closed) {
      clientId = newId(); // A restored BFCache document is a new lease.
      closed = false;
      timer = setInterval(heartbeat,5000);
    }
    heartbeat();
  });
  document.addEventListener('visibilitychange',heartbeat);
  heartbeat();
  timer = setInterval(heartbeat,5000);
})();
