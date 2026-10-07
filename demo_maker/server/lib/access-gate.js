/**
 * Access gate for a public (or Clearspeed-managed) Demo Maker host.
 *
 * On the Mac Studio the tailnet was the authentication — there was none in
 * the app. A public origin without a gate would expose the whole render and
 * publish pipeline. When DEMO_MAKER_ACCESS_SECRET is set, every route except
 * /healthz requires a short-lived embed session minted by Nucleus (HMAC over
 * exp + email). Unset keeps Studio/tailnet behaviour unchanged.
 */
const crypto = require('crypto');

const COOKIE = 'dm_embed';
const MAX_TTL_SEC = 60 * 60 * 12; // 12h hard cap
const DEFAULT_TTL_SEC = 60 * 60; // 1h

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

function sign(exp, email) {
  const secret = accessSecret();
  if (!secret) return '';
  return b64url(
    crypto.createHmac('sha256', secret).update(`v1|${exp}|${email}`).digest(),
  );
}

function mintToken(email, ttlSec = DEFAULT_TTL_SEC) {
  const clean = String(email || '')
    .trim()
    .toLowerCase();
  if (!clean || !clean.includes('@')) return null;
  const ttl = Math.min(Math.max(Number(ttlSec) || DEFAULT_TTL_SEC, 60), MAX_TTL_SEC);
  const exp = Math.floor(Date.now() / 1000) + ttl;
  const sig = sign(exp, clean);
  if (!sig) return null;
  return { exp, email: clean, sig, token: `${exp}.${b64url(clean)}.${sig}` };
}

function parseToken(raw) {
  const parts = String(raw || '').split('.');
  if (parts.length !== 3) return null;
  const exp = Number(parts[0]);
  if (!Number.isFinite(exp)) return null;
  let email;
  try {
    email = fromB64url(parts[1]).trim().toLowerCase();
  } catch {
    return null;
  }
  if (!email || !email.includes('@')) return null;
  const sig = parts[2];
  const expected = sign(exp, email);
  if (!expected || sig.length !== expected.length) return null;
  if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
  if (exp < Math.floor(Date.now() / 1000)) return null;
  return { exp, email, sig, token: `${exp}.${parts[1]}.${sig}` };
}

function verifyParts(exp, email, sig) {
  return parseToken(`${exp}.${b64url(String(email || '').trim().toLowerCase())}.${sig}`);
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
    const email = req.query.email;
    const sig = req.query.sig;
    const session = verifyParts(exp, email, sig);
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
      req.demoMakerSession = session;
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
  mintToken,
  parseToken,
  verifyParts,
  sign,
  install,
};
