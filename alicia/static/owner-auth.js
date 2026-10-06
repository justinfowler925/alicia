/* Owner session pairing for consequential Alicia APIs.
 *
 * Serve-proven identity (studio_owner) authorizes without a pasted token.
 * Loopback needs cookie + CSRF (or X-Alicia-Owner-Token). open-operator can
 * pass ?pair=<ticket> to redeem once.
 */
(() => {
  'use strict';
  let csrf = null;
  let ensurePromise = null;
  const SKIP = /^\/api\/auth\/|^\/webhooks\/|^\/v1\/|^\/api\/convai\/llm\//;

  function needsOwner(url, method) {
    const m = (method || 'GET').toUpperCase();
    if (m === 'GET' || m === 'HEAD' || m === 'OPTIONS') return false;
    try {
      const path = typeof url === 'string' ? url : (url && url.url) || '';
      const u = path.startsWith('http') ? new URL(path).pathname : path.split('?')[0];
      if (!u.startsWith('/api/')) return false;
      if (SKIP.test(u)) return false;
      return true;
    } catch {
      return false;
    }
  }

  async function redeemPair(ticket) {
    const r = await window.fetch('/api/auth/redeem', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ticket }),
    });
    if (!r.ok) throw new Error('pair ticket rejected');
    const data = await r.json();
    csrf = data.csrf;
    try {
      const u = new URL(location.href);
      u.searchParams.delete('pair');
      history.replaceState({}, '', u.pathname + u.search + u.hash);
    } catch { /* ignore */ }
    return csrf;
  }

  async function promptToken() {
    const token = window.prompt(
      'Paste your Alicia owner token (run: alicia owner-token). Required once for local loopback.',
    );
    if (!token) throw new Error('owner token required');
    const r = await window.fetch('/api/auth/session', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ token: token.trim() }),
    });
    if (!r.ok) throw new Error('owner token rejected');
    const data = await r.json();
    csrf = data.csrf;
    return csrf;
  }

  async function ensureOwnerSession() {
    if (csrf) return csrf;
    if (ensurePromise) return ensurePromise;
    ensurePromise = (async () => {
      const params = new URLSearchParams(location.search);
      const pair = params.get('pair');
      if (pair) return redeemPair(pair);

      const status = await window.fetch('/api/auth/status', { credentials: 'same-origin' })
        .then((r) => r.json())
        .catch(() => ({ authenticated: false }));
      if (status.csrf) {
        csrf = status.csrf;
        return csrf;
      }
      if (status.studio_owner || status.via === 'studio_owner') {
        const r = await window.fetch('/api/auth/studio-session', {
          method: 'POST',
          credentials: 'same-origin',
        });
        if (r.ok) {
          const data = await r.json();
          csrf = data.csrf;
          return csrf;
        }
      }
      // Serve-proven requests may already authorize without CSRF.
      if (status.authenticated && status.via === 'studio_owner') return null;
      return promptToken();
    })().finally(() => { ensurePromise = null; });
    return ensurePromise;
  }

  const orig = window.fetch.bind(window);
  window.fetch = async function aliciaOwnerFetch(input, init) {
    init = init ? { ...init } : {};
    const method = init.method || (typeof input !== 'string' && input && input.method) || 'GET';
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (needsOwner(url, method)) {
      const token = await ensureOwnerSession();
      const headers = new Headers(init.headers || (typeof input !== 'string' && input.headers) || undefined);
      if (token) headers.set('X-Alicia-CSRF', token);
      init.headers = headers;
      init.credentials = init.credentials || 'same-origin';
    }
    return orig(input, init);
  };

  window.aliciaEnsureOwner = ensureOwnerSession;
})();
