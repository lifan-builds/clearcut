import assert from 'node:assert/strict';
import test from 'node:test';
import worker from './worker.js';

function makeEnv(upstreamResponses) {
  const store = new Map();
  const pending = [];
  const calls = [];

  globalThis.caches = {
    default: {
      async match(req) {
        const hit = store.get(req.url);
        return hit ? hit.clone() : undefined;
      },
      async put(req, res) {
        store.set(req.url, res.clone());
      },
    },
  };

  globalThis.fetch = async (url, init) => {
    calls.push({ url, method: init.method, headers: Object.fromEntries(init.headers.entries()) });
    const next = upstreamResponses.shift();
    return next(url, init);
  };

  const ctx = {
    waitUntil(promise) {
      pending.push(promise);
    },
  };

  return { store, pending, calls, ctx };
}

test('HEAD miss never poisons GET cache with empty body, and HEAD hit uses cached GET', async () => {
  const { pending, calls, ctx } = makeEnv([
    () => new Response(null, {
      status: 200,
      headers: { 'content-type': 'text/html', 'cdn-cache-control': 'public, s-maxage=86400' },
    }),
    () => new Response('<html><body>Big Ring</body></html>', {
      status: 200,
      headers: { 'content-type': 'text/html', 'cdn-cache-control': 'public, s-maxage=86400' },
    }),
  ]);

  const headMiss = await worker.fetch(new Request('https://bigring.clifan.com/', { method: 'HEAD' }), {}, ctx);
  await Promise.all(pending);
  assert.equal(headMiss.status, 200);
  assert.equal(headMiss.headers.get('X-Clearcut-Edge-Cache'), 'BYPASS');
  assert.equal(await headMiss.text(), '');

  const getAfterHead = await worker.fetch(new Request('https://bigring.clifan.com/', { method: 'GET' }), {}, ctx);
  await Promise.all(pending);
  assert.equal(getAfterHead.status, 200);
  assert.equal(getAfterHead.headers.get('X-Clearcut-Edge-Cache'), 'MISS');
  assert.equal(await getAfterHead.text(), '<html><body>Big Ring</body></html>');

  const headHit = await worker.fetch(new Request('https://bigring.clifan.com/', { method: 'HEAD' }), {}, ctx);
  assert.equal(headHit.status, 200);
  assert.equal(headHit.headers.get('X-Clearcut-Edge-Cache'), 'HIT');
  assert.equal(await headHit.text(), '');

  const getHit = await worker.fetch(new Request('https://bigring.clifan.com/', { method: 'GET' }), {}, ctx);
  assert.equal(getHit.status, 200);
  assert.equal(getHit.headers.get('X-Clearcut-Edge-Cache'), 'HIT');
  assert.equal(await getHit.text(), '<html><body>Big Ring</body></html>');

  assert.equal(calls.length, 2);
  assert.equal(calls[0].method, 'HEAD');
  assert.equal(calls[1].method, 'GET');
  assert.equal(calls[1].headers['x-forwarded-host'], 'bigring.clifan.com');
});

test('portal and freshpowder hosts cache versioned GETs and bypass POST, cookies, and /go', async () => {
  const { pending, calls, ctx } = makeEnv([
    () => new Response('<html>Portal</html>', {
      status: 200,
      headers: { 'content-type': 'text/html', 'cdn-cache-control': 'public, s-maxage=86400' },
    }),
    () => new Response(null, {
      status: 302,
      headers: { location: 'https://bigring.clifan.com/' },
    }),
    () => new Response(null, { status: 204 }),
    () => new Response('<html>Admin</html>', {
      status: 200,
      headers: { 'content-type': 'text/html', 'cdn-cache-control': 'public, s-maxage=86400' },
    }),
  ]);

  const portalMiss = await worker.fetch(new Request('https://clearcut.clifan.com/'), {}, ctx);
  await Promise.all(pending);
  assert.equal(portalMiss.headers.get('X-Clearcut-Edge-Cache'), 'MISS');

  const portalHit = await worker.fetch(new Request('https://clearcut.clifan.com/'), {}, ctx);
  assert.equal(portalHit.headers.get('X-Clearcut-Edge-Cache'), 'HIT');

  const goResp = await worker.fetch(new Request('https://clearcut.clifan.com/go?catalog=cycling'), {}, ctx);
  assert.equal(goResp.status, 302);
  assert.equal(goResp.headers.get('X-Clearcut-Edge-Cache'), 'BYPASS');

  const postResp = await worker.fetch(
    new Request('https://freshpowder.clifan.com/api/event', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ event_type: 'page_view' }),
    }),
    {},
    ctx,
  );
  assert.equal(postResp.status, 204);
  assert.equal(postResp.headers.get('X-Clearcut-Edge-Cache'), 'BYPASS');

  const authed = await worker.fetch(
    new Request('https://freshpowder.clifan.com/', { headers: { cookie: 'admin_session=secret' } }),
    {},
    ctx,
  );
  assert.equal(authed.headers.get('X-Clearcut-Edge-Cache'), 'BYPASS');
  assert.equal(calls.length, 4);
});
