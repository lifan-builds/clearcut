"""Shared CDN cache header policy for read-only Clearcut storefronts."""

from __future__ import annotations

from fastapi import Request, Response

CDN_CACHE_CONTROL_VALUE = "public, s-maxage=86400, stale-while-revalidate=3600"
_CACHEABLE_MEDIA_TYPES = ("text/html", "text/plain", "application/xml")
_DEFAULT_EXCLUDED_PREFIXES = ("/healthz", "/go", "/admin", "/invite", "/api/", "/alerts/remove", "/alerts/unsubscribe", "/static/", "/portal-static/", "/brand/")


def apply_cdn_cache_headers(
    request: Request,
    response: Response,
    *,
    is_public: bool = True,
    excluded_prefixes: tuple[str, ...] = _DEFAULT_EXCLUDED_PREFIXES,
) -> None:
    """Set deployment-scoped CDN-Cache-Control on safe public GET/HEAD responses."""
    if not is_public or request.method not in {"GET", "HEAD"} or response.status_code != 200:
        return
    if "cdn-cache-control" in response.headers or "set-cookie" in response.headers:
        return
    cache_control = response.headers.get("cache-control", "").lower()
    if "no-store" in cache_control or "private" in cache_control:
        return
    path = request.url.path
    if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in excluded_prefixes):
        return
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in _CACHEABLE_MEDIA_TYPES:
        return
    response.headers["CDN-Cache-Control"] = CDN_CACHE_CONTROL_VALUE
