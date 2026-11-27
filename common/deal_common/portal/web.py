from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from deal_common.cache_headers import apply_cdn_cache_headers
from deal_common.portal.definition import PortalCatalog, default_portal_catalogs

PORTAL_DIR = Path(__file__).parent
CATALOG_STATIC = PORTAL_DIR.parent / "catalog" / "static"


def _format_money(value: float, currency: str = "USD") -> str:
    prefix = "C$" if currency == "CAD" else "$"
    return f"{prefix}{value:,.2f}" if value % 1 else f"{prefix}{value:,.0f}"


def create_portal_app(
    catalogs: Sequence[PortalCatalog] | None = None,
    *,
    public_url: str | None = None,
) -> FastAPI:
    entries = tuple(catalogs) if catalogs is not None else default_portal_catalogs()
    by_slug = {entry.slug: entry for entry in entries}
    app = FastAPI(title="Clearcut", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=CATALOG_STATIC), name="static")
    app.mount("/portal-static", StaticFiles(directory=PORTAL_DIR / "static"), name="portal_static")
    templates = Jinja2Templates(directory=PORTAL_DIR / "templates")
    base_url = public_url.rstrip("/") if public_url else None
    templates.env.globals["public_url"] = base_url
    templates.env.filters["portal_money"] = _format_money

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' https:; style-src 'self'; "
            "script-src 'self' https://static.cloudflareinsights.com; "
            "connect-src 'self' https://cloudflareinsights.com; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        if not base_url or request.url.query or request.url.path in {"/healthz", "/go"}:
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        apply_cdn_cache_headers(request, response)
        return response

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, catalog: str = Query("snow", max_length=24)):
        selected_slug = catalog if catalog in by_slug else entries[0].slug
        desks = []
        featured_cuts = []
        total_sources = 0
        total_live_deals = 0
        for entry in entries:
            total_sources += entry.source_count
            summary = entry.summary_loader() if entry.summary_loader else None
            if summary and summary.live_deals:
                total_live_deals += summary.live_deals
                with_img = [h for h in summary.highlights if h.image_url]
                featured_cuts.extend((with_img or list(summary.highlights))[:2])
            category_links = [
                {
                    "label": label,
                    "url": f"{entry.url}/?{urlencode({'category': value})}{entry.anchor}",
                }
                for label, value in entry.categories
            ]
            desks.append({"catalog": entry, "summary": summary, "category_links": category_links})

        featured_cuts.sort(key=lambda h: (h.image_url is None, -h.discount_pct, -(h.original_price - h.current_price)))

        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "desks": desks,
                "featured_cuts": featured_cuts[:8],
                "selected_slug": selected_slug,
                "selected_catalog": by_slug[selected_slug],
                "total_sources": total_sources,
                "total_live_deals": total_live_deals,
            },
        )

    @app.get("/go")
    def handoff(
        catalog: str = Query("snow", max_length=24),
        q: str = Query("", max_length=150),
        size: str = Query("", max_length=80),
        category: str = Query("", max_length=80),
        min_discount: float = Query(0, ge=0, le=100),
    ):
        target = by_slug.get(catalog)
        if target is None:
            raise HTTPException(status_code=422, detail="Unknown catalog")
        params: dict[str, str] = {}
        if q.strip():
            params["q"] = q.strip()
        if category.strip():
            params["category"] = category.strip()
        if size.strip():
            params[target.size_param] = size.strip()
        if min_discount > 0:
            params["min_discount"] = f"{int(min_discount)}" if min_discount.is_integer() else f"{min_discount:g}"
        query_str = ("?" + urlencode(params)) if params else ""
        destination = f"{target.url}/{query_str}{target.anchor if params else ''}"
        return RedirectResponse(url=destination, status_code=302, headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    def health():
        return Response(
            content='{"status":"ready","service":"clearcut-portal","catalogs":' + str(len(entries)) + "}",
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        if base_url:
            return f"User-agent: *\nAllow: /\nDisallow: /go\nDisallow: /healthz\nSitemap: {base_url}/sitemap.xml\n"
        return "User-agent: *\nDisallow: /\n"

    @app.get("/sitemap.xml")
    def sitemap():
        loc = f"<url><loc>{escape(base_url)}/</loc></url>" if base_url else ""
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + loc
            + "</urlset>",
            media_type="application/xml",
        )

    return app
