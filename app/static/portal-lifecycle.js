(() => {
  const token = document.currentScript?.dataset.token;
  if (!token) return;

  const key = "sc-panel-portal-client-id";
  let clientId = sessionStorage.getItem(key);
  if (!clientId) {
    clientId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    sessionStorage.setItem(key, clientId);
  }

  const heartbeat = () => fetch("/api/portal/heartbeat", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Portal-Token": token },
    body: JSON.stringify({ client_id: clientId }),
    keepalive: true,
  }).catch(() => {});

  heartbeat();
  window.setInterval(heartbeat, 5000);
})();
