#!/usr/bin/env node
/** Offline checks for the Nucleus embed HMAC gate. */
const assert = require('assert');
const crypto = require('crypto');

process.env.DEMO_MAKER_ACCESS_SECRET = 'test-secret-for-gate';
const gate = require('../server/lib/access-gate');

const minted = gate.mintToken('Justin.Fowler@clearspeed.com', 600);
assert.ok(minted, 'mintToken returns a session');
assert.equal(minted.email, 'justin.fowler@clearspeed.com');

const parsed = gate.parseToken(minted.token);
assert.ok(parsed, 'parseToken accepts a fresh token');
assert.equal(parsed.email, minted.email);

const bad = gate.parseToken(minted.token.replace(/\.[^.]+$/, '.deadbeef'));
assert.equal(bad, null, 'tampered sig rejected');

const expired = (() => {
  const exp = Math.floor(Date.now() / 1000) - 10;
  const email = 'justin.fowler@clearspeed.com';
  const sig = crypto
    .createHmac('sha256', process.env.DEMO_MAKER_ACCESS_SECRET)
    .update(`v1|${exp}|${email}`)
    .digest('base64')
    .replace(/=+$/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  const emailB64 = Buffer.from(email)
    .toString('base64')
    .replace(/=+$/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return gate.parseToken(`${exp}.${emailB64}.${sig}`);
})();
assert.equal(expired, null, 'expired token rejected');

delete process.env.DEMO_MAKER_ACCESS_SECRET;
assert.equal(gate.gatingEnabled(), false, 'unset secret disables gate');

console.log('access-gate: ok');
