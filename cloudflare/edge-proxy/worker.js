const ORIGIN = 'https://clearcut-cjt5.onrender.com';
const CACHEABLE_HOSTS = new Set([
  'bigring.clifan.com',
  'benchdrop.clifan.com',
  'tideline.clifan.com',
  'highline.clifan.com',
]);

export default {
  async fetch(request, _env, ctx) {
    const url = new URL(request.url);
    const host = url.hostname.toLowerCase();
    if (!CACHEABLE_HOSTS.has(host)) {
      return new Response('Not Found', { status: 404 });
    }

    const isCacheableMethod = request.method === 'GET' || request.method === 'HEAD';
    const isExcludedPath = url.pathname === '/healthz';
    const cache = caches.default;
    const cacheKey = new Request(url.toString(), { method: 'GET' });

    if (isCacheableMethod && !isExcludedPath) {
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
      redirect: 'manual',
    });

    const respHeaders = new Headers(upstream.headers);
    const cdnCC = respHeaders.get('cdn-cache-control') || '';
    const cc = (respHeaders.get('cache-control') || '').toLowerCase();
    const shouldCache =
      isCacheableMethod &&
      !isExcludedPath &&
      upstream.status === 200 &&
      !cc.includes('no-store') &&
      !cc.includes('private') &&
      (cdnCC.includes('s-maxage') || url.pathname.startsWith('/static/') || url.pathname.startsWith('/brand/'));

    if (shouldCache && !respHeaders.has('cache-control')) {
      respHeaders.set('Cache-Control', 'public, max-age=300, s-maxage=86400, stale-while-revalidate=3600');
    }
    respHeaders.set('X-Clearcut-Edge-Cache', shouldCache ? 'MISS' : 'BYPASS');

    const response = new Response(upstream.body, {
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
