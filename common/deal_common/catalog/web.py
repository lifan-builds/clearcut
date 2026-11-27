"""Server-rendered catalog app with read-only snapshots and validated filters."""
import os
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from deal_common.cache_headers import apply_cdn_cache_headers
from deal_common.catalog.definition import Catalog, manufacturer_name
from deal_common.catalog.health import snapshot_health
from deal_common.catalog.query import FRESHNESS_HOURS, SORTS, is_fresh, query
from deal_common.catalog.storage import read_offer_history, read_sources
from deal_common.offers import size_key

ASSETS = Path(__file__).parent


def create_app(catalog: Catalog, database: Path, *, public_url: str | None = None) -> FastAPI:
    app = FastAPI(title=catalog.name, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=ASSETS / "static"), name="static")
    app.mount("/brand", StaticFiles(directory=catalog.assets), name="brand")
    templates = Jinja2Templates(directory=ASSETS / "templates")
    base_url = public_url.rstrip("/") if public_url else None
    templates.env.globals["public_url"] = base_url
    templates.env.globals["network_urls"] = lambda: {
        "hub": os.environ.get("CLEARCUT_HUB_URL", "https://clearcut.clifan.com").rstrip("/"),
        "snow": os.environ.get("SNOW_SITE_URL", "https://freshpowder.clifan.com").rstrip("/"),
        "cycling": os.environ.get("CYCLING_SITE_URL", "https://bigring.clifan.com").rstrip("/"),
        "tools": os.environ.get("TOOLS_SITE_URL", "https://benchdrop.clifan.com").rstrip("/"),
        "camp": os.environ.get("CAMP_SITE_URL", "https://highline.clifan.com").rstrip("/"),
        "watersports": os.environ.get("WATER_SITE_URL", "https://tideline.clifan.com").rstrip("/"),
    }
    templates.env.filters["money"] = lambda value: f"${value:,.2f}" if value % 1 else f"${value:,.0f}"
    templates.env.filters["date"] = (
        lambda value: datetime.fromisoformat(value).strftime("%b %d, %H:%M UTC") if value else "Not yet refreshed"
    )

    configured = {source.name for source in catalog.sources}
    _sources_cache: dict[str, object] = {"stamp": None, "rows": []}
    _model_stores_cache: dict[str, object] = {"key": None, "value": {}}

    def _db_stamp() -> tuple[int, int] | None:
        try:
            st = database.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def sources():
        stamp = _db_stamp()
        if stamp is None:
            _sources_cache["stamp"] = None
            _sources_cache["rows"] = []
            return []
        if _sources_cache["stamp"] != stamp:
            _sources_cache["rows"] = [row for row in read_sources(database, catalog.slug) if row["name"] in configured]
            _sources_cache["stamp"] = stamp
        return _sources_cache["rows"]

    def _sig_for(brand_val: str, name_val: str, cat_val: str) -> tuple[str, str, str] | None:
        if not brand_val or brand_val == "Unspecified":
            return None
        lower = name_val.casefold()
        if lower.startswith(brand_val.casefold() + " "):
            lower = lower[len(brand_val) + 1:]
        lower = re.sub(r"\b20\d{2}\b", " ", lower)
        toks = [t for t in re.findall(r"[a-z0-9.]+", lower) if t not in {"bike", "bikes", "road", "gravel", "mountain", "carbon", "kit", "bare", "tool", "wetsuit", "tent", "pack"}]
        return (brand_val.casefold(), " ".join(toks[:3]), cat_val) if len(toks) >= 2 else None

    def _model_stores(all_sources: list[dict], now_utc: datetime) -> dict[tuple[str, str, str], set[str]]:
        stamp = _db_stamp()
        cache_key = (stamp, int(time.time() // 300))
        if stamp is not None and _model_stores_cache["key"] == cache_key:
            return _model_stores_cache["value"]
        computed: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for src in all_sources:
            if not is_fresh(src["successful_at"], now_utc):
                continue
            for prod in src["products"]:
                b_name = manufacturer_name(prod["brand"], brand_aliases=catalog.brand_aliases) if catalog.brand_aliases else prod["brand"]
                s_key = _sig_for(b_name, prod["name"], prod["category"])
                if s_key:
                    computed[s_key].add(src["name"])
        if stamp is not None:
            _model_stores_cache["key"] = cache_key
            _model_stores_cache["value"] = computed
        return computed

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' https:; style-src 'self'; "
            "script-src 'self' https://static.cloudflareinsights.com; "
            "connect-src 'self' https://cloudflareinsights.com; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        if not base_url or request.url.query or request.url.path == "/healthz":
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        apply_cdn_cache_headers(request, response)
        return response

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = Query("", max_length=150),
              category: str = Query("", max_length=80), brand: str = Query("", max_length=100),
              store: str = Query("", max_length=100), size: str = Query("", max_length=80),
              format: str = Query("", max_length=80), platform: str = Query("", max_length=80),
              min_reference: float = Query(catalog.default_reference, ge=0, le=1000000),
              max_price: float = Query(0, ge=0, le=1000000),
              min_discount: float = Query(0, ge=0, le=100), stock: bool = False,
              sort: Literal["discount", "dropped"] = "discount",
              page: int = Query(1, ge=1, le=100000)):
        if brand and catalog.brand_aliases:
            brand = manufacturer_name(brand, brand_aliases=catalog.brand_aliases)
        filters = {"q": q, "category": category, "brand": brand, "store": store, "size": size, "format": format,
                   "platform": platform, "min_reference": min_reference, "max_price": max_price,
                   "min_discount": min_discount, "stock": stock, "sort": sort, "page": page}
        all_sources = sources()
        result = query(all_sources, filters, brand_aliases=catalog.brand_aliases)
        if size:
            requested_size_key = size_key(size)
            filters["size"] = next((label for label in result["facets"]["size"]
                                    if size_key(label) == requested_size_key), size)
        now_utc = datetime.now(UTC)
        model_stores = _model_stores(all_sources, now_utc)

        for card in result["cards"]:
            card["history_url"] = "/history?" + urlencode({"source": card["store"], "product": card["url"],
                                                           "variant": card["offer"]["variant_key"]})
            s_key = _sig_for(card["brand"], card["name"], card["category"])
            other_cnt = max(0, len(model_stores[s_key] - {card["store"]})) if s_key else 0
            card["other_shops"] = other_cnt
            card["compare_url"] = ("/?" + urlencode({"q": f"{card['brand']} {s_key[1]}"}) + "#deals") if other_cnt and s_key else ""

        def page_url(number):
            return "/?" + urlencode({**filters, "page": number, "stock": str(stock).lower()}) + "#deals"

        labels = {"q": "Search", "category": "Category", "brand": "Brand", "store": "Retailer",
                  "size": catalog.size_label, "format": "Package", "platform": "Platform",
                  "min_reference": "Reference ≥ $", "max_price": "Budget ≤ $",
                  "min_discount": "Discount ≥", "stock": "Reported in stock"}
        active_filters = []
        for key, label in labels.items():
            value = filters[key]
            if not value:
                continue
            cleared = {**filters, key: "false" if key == "stock" else (0 if key in {"min_reference", "max_price", "min_discount"} else ""),
                       "stock": "false" if key == "stock" else str(stock).lower(), "page": 1}
            if key == "stock":
                display = label
            elif isinstance(value, (int, float)):
                display = f"{label} {value:g}" + ("%" if key == "min_discount" else "")
            else:
                display = f"{label}: {value}"
            active_filters.append({"label": display, "url": "/?" + urlencode(cleared) + "#deals"})
        return templates.TemplateResponse(request=request, name="index.html", context={
            "catalog": catalog, "filters": filters, "result": result, "active_filters": active_filters,
            "previous": page_url(result["page"] - 1), "next": page_url(result["page"] + 1),
            "source_count": len(catalog.sources), "freshness_hours": FRESHNESS_HOURS,
            "sorts": {key: (label, "/?" + urlencode({**filters, "sort": key, "page": 1, "stock": str(stock).lower()}) + "#deals")
                      for key, label in SORTS.items()},
        })

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, source: str = Query(..., max_length=100),
                product: str = Query(..., max_length=2000), variant: str = Query(..., max_length=100)):
        for row in sources():
            if row["name"] != source:
                continue
            for item in row["products"]:
                if item["url"] != product:
                    continue
                for offer in item["offers"]:
                    if offer["variant_key"] == variant:
                        observations = read_offer_history(database, catalog.slug, source, item, offer,
                                                          now=datetime.now(UTC), brand_aliases=catalog.brand_aliases)
                        display_item = {**item, "brand": manufacturer_name(
                            item["brand"], brand_aliases=catalog.brand_aliases)} if catalog.brand_aliases else item
                        return templates.TemplateResponse(request=request, name="history.html", context={
                            "catalog": catalog, "product": display_item, "offer": offer, "store": source,
                            "observations": observations})
        raise HTTPException(status_code=404, detail="This variant is not in the current snapshot")

    @app.get("/sources", response_class=HTMLResponse)
    def source_health(request: Request):
        rows = {row["name"]: row for row in sources()}
        now = datetime.now(UTC)
        health = [{
            "name": source.name, "url": source.scrape_urls[0],
            "checked_at": (row := rows.get(source.name, {})).get("checked_at"),
            "successful_at": row.get("successful_at"),
            "status": row.get("status", "Not refreshed"),
            "fresh": is_fresh(row.get("successful_at"), now),
            "count": len(row.get("products", [])),
            "collections": row.get("detail", {}).get("collections", []),
        } for source in catalog.sources]
        return templates.TemplateResponse(request=request, name="sources.html", context={
            "catalog": catalog, "sources": health, "freshness_hours": FRESHNESS_HOURS})

    @app.get("/healthz")
    def health(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return snapshot_health(catalog, sources())

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        if base_url:
            return (
                "User-agent: *\nAllow: /\nDisallow: /*?\nDisallow: /history\nDisallow: /healthz\n"
                f"Sitemap: {base_url}/sitemap.xml\n"
            )
        return "User-agent: *\nDisallow: /\n"

    @app.get("/sitemap.xml")
    def sitemap():
        base = escape(base_url) if base_url else ""
        entries = "".join(f"<url><loc>{base}{path}</loc></url>" for path in ("/", "/sources")) if base else ""
        return Response('<?xml version="1.0" encoding="UTF-8"?>'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        + entries + '</urlset>', media_type="application/xml")

    return app
