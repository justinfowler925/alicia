#!/usr/bin/env node
/** Offline checks for the Nucleus embed HMAC gate. */
const assert = require('assert');
const crypto = require('crypto');

process.env.DEMO_MAKER_ACCESS_SECRET = 'test-secret-for-gate';
const gate = require('../server/lib/access-gate');

const minted = gate.mintToken('Justin.Fowler@clearspeed.com', 600);
assert.ok(minted, 'mintToken returns a session');
assert.ok(minted.sub, 'opaque sub present');
assert.equal(minted.email, undefined, 'email must not appear on the token object');
assert.doesNotMatch(minted.token, /@|clearspeed/i, 'token must not contain email');

const parsed = gate.parseToken(minted.token);
assert.ok(parsed, 'parseToken accepts a fresh token');
assert.equal(parsed.sub, minted.sub);
assert.equal(parsed.email, undefined);

const bad = gate.parseToken(minted.token.replace(/\.[^.]+$/, '.deadbeef'));
assert.equal(bad, null, 'tampered sig rejected');

const expired = (() => {
  const exp = Math.floor(Date.now() / 1000) - 10;
  const sub = gate.subjectForEmail('justin.fowler@clearspeed.com');
  const sig = crypto
    .createHmac('sha256', process.env.DEMO_MAKER_ACCESS_SECRET)
    .update(`v2|${exp}|${sub}`)
    .digest('base64')
    .replace(/=+$/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return gate.parseToken(`${exp}.${sub}.${sig}`);
})();
assert.equal(expired, null, 'expired token rejected');

// Legacy v1 email query shape must not verify.
assert.equal(gate.verifyParts(Math.floor(Date.now() / 1000) + 60, 'justin.fowler@clearspeed.com', 'x'), null);

delete process.env.DEMO_MAKER_ACCESS_SECRET;
assert.equal(gate.gatingEnabled(), false, 'unset secret disables gate');

console.log('access-gate: ok');
