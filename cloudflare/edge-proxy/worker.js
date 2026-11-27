const ORIGIN = 'https://clearcut-cjt5.onrender.com';
const CACHE_VERSION = 'v4';
const CACHEABLE_HOSTS = new Set([
  'clearcut.clifan.com',
  'freshpowder.clifan.com',
  'bigring.clifan.com',
  'benchdrop.clifan.com',
  'tideline.clifan.com',
  'highline.clifan.com',
]);

function isExcludedPath(pathname) {
  return (
    pathname === '/healthz' ||
    pathname === '/go' ||
    pathname.startsWith('/admin') ||
    pathname.startsWith('/invite') ||
    pathname.startsWith('/api/') ||
    pathname.startsWith('/alerts/remove') ||
    pathname.startsWith('/alerts/unsubscribe')
  );
}

function hasSessionCookie(request) {
  const cookie = request.headers.get('cookie') || '';
  return cookie.includes('snow_deals_session=') || cookie.includes('admin_session=');
}

function makeCacheKey(url, request) {
  const keyUrl = new URL(url.toString());
  keyUrl.searchParams.set('__cc_edge_v', CACHE_VERSION);
  if (request.headers.get('hx-request') === 'true') {
    keyUrl.searchParams.set('__cc_hx', '1');
  }
  return new Request(keyUrl.toString(), { method: 'GET' });
}

export default {
  async fetch(request, _env, ctx) {
    const url = new URL(request.url);
    const host = url.hostname.toLowerCase();
    if (!CACHEABLE_HOSTS.has(host)) {
      return new Response('Not Found', { status: 404 });
    }

    const isCacheableMethod = request.method === 'GET' || request.method === 'HEAD';
    const isExcluded = isExcludedPath(url.pathname) || hasSessionCookie(request);
    const cache = caches.default;
    const cacheKey = makeCacheKey(url, request);

    if (isCacheableMethod && !isExcluded) {
      const cached = await cache.match(cacheKey);
      if (cached) {
        const hit = new Response(request.method === 'HEAD' ? null : cached.body, cached);
        hit.headers.set('X-Clearcut-Edge-Cache', 'HIT');
        return hit;
      }
    }

    const upstreamUrl = `${ORIGIN}${url.pathname}${url.search}`;
    const upstreamHeaders = new Headers(request.headers);
    upstreamHeaders.set('X-Forwarded-Host', host);
    upstreamHeaders.set('X-Forwarded-Proto', 'https');
    upstreamHeaders.delete('host');

    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers: upstreamHeaders,
      body: isCacheableMethod ? undefined : request.body,
      redirect: 'manual',
    });

    const respHeaders = new Headers(upstream.headers);
    const cdnCC = respHeaders.get('cdn-cache-control') || '';
    const cc = (respHeaders.get('cache-control') || '').toLowerCase();
    const shouldCache =
      request.method === 'GET' &&
      !isExcluded &&
      upstream.status === 200 &&
      !respHeaders.has('set-cookie') &&
      !cc.includes('no-store') &&
      !cc.includes('private') &&
      (cdnCC.includes('s-maxage') ||
        url.pathname.startsWith('/static/') ||
        url.pathname.startsWith('/portal-static/') ||
        url.pathname.startsWith('/brand/'));

    if (shouldCache && !respHeaders.has('cache-control')) {
      respHeaders.set('Cache-Control', 'public, max-age=300, s-maxage=86400, stale-while-revalidate=3600');
    }
    respHeaders.set('X-Clearcut-Edge-Cache', shouldCache ? 'MISS' : 'BYPASS');

    const response = new Response(request.method === 'HEAD' ? null : upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: respHeaders,
    });

    if (shouldCache) {
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }

    return response;
  },
};
