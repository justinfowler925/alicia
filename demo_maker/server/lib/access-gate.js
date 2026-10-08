/**
 * Access gate for Demo Maker when DEMO_MAKER_ACCESS_SECRET is set.
 *
 * Nucleus mints a short-lived HMAC embed URL. The query string carries an
 * opaque subject (HMAC of the email), never the email itself. Unset secret
 * keeps Studio/tailnet behaviour (network was the auth).
 */
const crypto = require('crypto');

const COOKIE = 'dm_embed';
const MAX_TTL_SEC = 60 * 60; // 1h hard cap
const DEFAULT_TTL_SEC = 15 * 60; // 15m

function accessSecret() {
  return (process.env.DEMO_MAKER_ACCESS_SECRET || '').trim();
}

function gatingEnabled() {
  return Boolean(accessSecret());
}

function b64url(buf) {
  return Buffer.from(buf)
    .toString('base64')
    .replace(/=+$/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
}

function fromB64url(s) {
  const pad = s.length % 4 === 0 ? '' : '='.repeat(4 - (s.length % 4));
  return Buffer.from(String(s).replace(/-/g, '+').replace(/_/g, '/') + pad, 'base64').toString(
    'utf8',
  );
}

/** Opaque subject derived from email — never put email in URLs or cookies. */
function subjectForEmail(email) {
  const secret = accessSecret();
  if (!secret) return '';
  const clean = String(email || '')
    .trim()
    .toLowerCase();
  if (!clean || !clean.includes('@')) return '';
  return b64url(crypto.createHmac('sha256', secret).update(`sub|${clean}`).digest());
}

function sign(exp, sub) {
  const secret = accessSecret();
  if (!secret) return '';
  return b64url(crypto.createHmac('sha256', secret).update(`v2|${exp}|${sub}`).digest());
}

function mintToken(email, ttlSec = DEFAULT_TTL_SEC) {
  const sub = subjectForEmail(email);
  if (!sub) return null;
  const ttl = Math.min(Math.max(Number(ttlSec) || DEFAULT_TTL_SEC, 60), MAX_TTL_SEC);
  const exp = Math.floor(Date.now() / 1000) + ttl;
  const sig = sign(exp, sub);
  if (!sig) return null;
  return { exp, sub, sig, token: `${exp}.${sub}.${sig}` };
}

function parseToken(raw) {
  const parts = String(raw || '').split('.');
  if (parts.length !== 3) return null;
  const exp = Number(parts[0]);
  if (!Number.isFinite(exp)) return null;
  const sub = parts[1];
  if (!sub || sub.length < 16) return null;
  const sig = parts[2];
  const expected = sign(exp, sub);
  if (!expected || sig.length !== expected.length) return null;
  if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
  if (exp < Math.floor(Date.now() / 1000)) return null;
  return { exp, sub, sig, token: `${exp}.${sub}.${sig}` };
}

function verifyParts(exp, sub, sig) {
  return parseToken(`${exp}.${String(sub || '').trim()}.${sig}`);
}

function readCookie(req) {
  const header = req.headers.cookie || '';
  for (const part of header.split(';')) {
    const idx = part.indexOf('=');
    if (idx < 0) continue;
    const name = part.slice(0, idx).trim();
    if (name !== COOKIE) continue;
    return decodeURIComponent(part.slice(idx + 1).trim());
  }
  return '';
}

function setSessionCookie(res, token, exp) {
  const maxAge = Math.max(0, exp - Math.floor(Date.now() / 1000));
  const secure = process.env.NODE_ENV === 'production' || process.env.DEMO_MAKER_COOKIE_SECURE === '1';
  const parts = [
    `${COOKIE}=${encodeURIComponent(token)}`,
    'Path=/',
    'HttpOnly',
    `Max-Age=${maxAge}`,
    secure ? 'Secure' : '',
    // Nucleus frames this origin; SameSite=None is required for the cookie to
    // stick inside the iframe. Secure is mandatory with None.
    secure ? 'SameSite=None' : 'SameSite=Lax',
  ].filter(Boolean);
  res.setHeader('Set-Cookie', parts.join('; '));
}

function deniedHtml() {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Demo Maker</title>
<style>body{font:15px/1.45 system-ui,sans-serif;max-width:36rem;margin:4rem auto;padding:0 1.25rem;color:#1a1a1a}
h1{font-size:1.25rem;margin:0 0 .5rem}p{margin:.5rem 0;color:#444}</style></head>
<body><h1>Open Demo Maker from Nucleus</h1>
<p>This tool is gated. Sign in to Nucleus, open <strong>RevOps → Demo Maker</strong>, and use that panel (or Open in new tab).</p>
<p>Direct public access is not available.</p></body></html>`;
}

function install(app) {
  // Liveness stays above the gate so the orchestrator can probe without a session.
  app.get('/healthz', (_req, res) => {
    res.set('cache-control', 'no-store').json({ ok: true });
  });

  app.get('/auth/embed', (req, res) => {
    if (!gatingEnabled()) {
      return res.redirect(302, '/');
    }
    const exp = req.query.exp;
    const sub = req.query.sub;
    const sig = req.query.sig;
    const session = verifyParts(exp, sub, sig);
    if (!session) {
      res.status(401).type('html').send(deniedHtml());
      return;
    }
    setSessionCookie(res, session.token, session.exp);
    const next = typeof req.query.next === 'string' && req.query.next.startsWith('/') ? req.query.next : '/';
    res.redirect(302, next);
  });

  app.use((req, res, next) => {
    if (!gatingEnabled()) return next();
    if (req.path === '/healthz' || req.path === '/auth/embed') return next();
    const session = parseToken(readCookie(req));
    if (session) {
      // Opaque only — never attach email to the request for logs/handlers.
      req.demoMakerSession = { exp: session.exp, sub: session.sub };
      return next();
    }
    if (req.path.startsWith('/api/')) {
      res.status(401).json({ error: 'open Demo Maker from Nucleus RevOps' });
      return;
    }
    res.status(401).type('html').send(deniedHtml());
  });
}

module.exports = {
  COOKIE,
  accessSecret,
  gatingEnabled,
  subjectForEmail,
  mintToken,
  parseToken,
  verifyParts,
  sign,
  install,
  DEFAULT_TTL_SEC,
};
