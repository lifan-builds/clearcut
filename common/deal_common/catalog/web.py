"""Server-rendered catalog app with read-only snapshots and validated filters."""
import os
import re
import unicodedata
from collections.abc import Iterable
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
from deal_common.catalog.definition import (
    Catalog,
    brand_id,
    clean_brand,
    compile_brand_aliases,
    display_brand,
    manufacturer_name,
)
from deal_common.catalog.health import snapshot_health
from deal_common.catalog.query import FRESHNESS_HOURS, SORTS, is_fresh, query
from deal_common.catalog.storage import read_offer_history, read_sources
from deal_common.offers import size_key

ASSETS = Path(__file__).parent
_BARE_TOOL_LABELS = frozenset({"bare tool", "bare-tool", "tool only", "tool-only"})
_OPTION_SEP_RE = re.compile(r"\s*([/|])\s*")


def _norm_badge_label(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def deduplicate_badge_labels(labels: Iterable[str | None]) -> list[str]:
    """Render each distinct package/platform badge once, normalizing Bare tool / Tool only synonyms."""
    result: list[str] = []
    seen: set[str] = set()
    shown_bare = False
    for raw in labels:
        label = clean_brand(raw)
        if not label:
            continue
        norm = _norm_badge_label(label)
        if norm in _BARE_TOOL_LABELS:
            if shown_bare:
                continue
            label = "Bare tool"
            norm = "bare tool"
            shown_bare = True
        if norm in seen:
            continue
        seen.add(norm)
        result.append(label)
    return result


def card_variant_and_badges(card: dict) -> tuple[str, list[str]]:
    """Compute deduplicated variant line and tag badges without mutating stored offer options."""
    raw_options = clean_brand(card["offer"].get("options"))
    fmt = clean_brand(card.get("format"))
    platform = clean_brand(card.get("platform"))
    fallback = clean_brand(card.get("variant_fallback"))

    raw_badges: list[str] = []
    if fmt and (fmt != "Not specified" or raw_options):
        raw_badges.append(fallback or "Package not specified" if fmt == "Not specified" else fmt)
    if platform:
        raw_badges.append(platform)
    badges = deduplicate_badge_labels(raw_badges)
    badge_norms = {_norm_badge_label(label) for label in badges}
    has_bare_badge = "bare tool" in badge_norms

    if raw_options:
        option_norm = _norm_badge_label(raw_options)
        if option_norm in _BARE_TOOL_LABELS:
            if has_bare_badge:
                variant_text = ""
            elif badges:
                badges = deduplicate_badge_labels(["Bare tool", *badges])
                variant_text = ""
            else:
                variant_text = "Bare tool"
        elif option_norm in badge_norms:
            variant_text = ""
        elif has_bare_badge and _OPTION_SEP_RE.search(raw_options):
            parts = [part.strip() for part in _OPTION_SEP_RE.split(raw_options)]
            kept: list[str] = []
            for idx in range(0, len(parts), 2):
                seg = parts[idx]
                if seg and _norm_badge_label(seg) not in _BARE_TOOL_LABELS:
                    kept.append(seg)
            variant_text = " / ".join(kept)
        else:
            variant_text = raw_options
    else:
        candidate = fallback or "Standard configuration"
        candidate_norm = _norm_badge_label(candidate)
        if (has_bare_badge and candidate_norm in _BARE_TOOL_LABELS) or candidate_norm in badge_norms:
            variant_text = ""
        else:
            variant_text = candidate

    return variant_text, badges


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
    compiled_aliases, preferred_brands = compile_brand_aliases(catalog.brand_aliases)
    _sources_cache: dict[str, object] = {"stamp": None, "rows": []}

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

    def _snapshot_brand_label(raw_value: str | None, all_rows: list[dict]) -> str:
        raw_label = (
            manufacturer_name(raw_value, brand_aliases=catalog.brand_aliases)
            if catalog.brand_aliases else clean_brand(raw_value)
        )
        target_id = brand_id(raw_label, compiled_aliases=compiled_aliases)
        if target_id in preferred_brands:
            return preferred_brands[target_id]
        observed: set[str] = {raw_label} if raw_label else set()
        for row in all_rows:
            for item in row.get("products") or ():
                candidate = (
                    manufacturer_name(item.get("brand"), brand_aliases=catalog.brand_aliases)
                    if catalog.brand_aliases else clean_brand(item.get("brand"))
                )
                if brand_id(candidate, compiled_aliases=compiled_aliases) == target_id and candidate:
                    observed.add(candidate)
        return display_brand(target_id, observed, preferred_brands)

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
              compare: bool = False,
              group: str = Query("", max_length=64),
              sort: Literal["discount", "dropped"] = "discount",
              page: int = Query(1, ge=1, le=100000)):
        if brand and catalog.brand_aliases:
            brand = manufacturer_name(brand, brand_aliases=catalog.brand_aliases)
        filters = {"q": q, "category": category, "brand": brand, "store": store, "size": size, "format": format,
                   "platform": platform, "min_reference": min_reference, "max_price": max_price,
                   "min_discount": min_discount, "stock": stock, "sort": sort, "page": page}
        query_filters = {**filters}
        if compare:
            query_filters["compare"] = True
        if group:
            query_filters["group"] = group
        all_sources = sources()
        result = query(
            all_sources,
            query_filters,
            brand_aliases=catalog.brand_aliases,
            opt_in_categories=catalog.opt_in_categories,
            size_value_pattern=catalog.size_value_pattern,
            collapse_duplicates=catalog.collapse_duplicates,
            reconcile_product=catalog.reconcile_product,
            exact_offer_key=catalog.exact_offer_key,
        )
        if brand:
            requested_brand_id = brand_id(brand, compiled_aliases=compiled_aliases)
            filters["brand"] = next(
                (label for label in result["facets"]["brand"]
                 if brand_id(label, compiled_aliases=compiled_aliases) == requested_brand_id),
                brand,
            )
        if size:
            requested_size_key = size_key(size)
            filters["size"] = next((label for label in result["facets"]["size"]
                                    if size_key(label) == requested_size_key), size)

        for card in result["cards"]:
            card["history_url"] = "/history?" + urlencode({"source": card["store"], "product": card["url"],
                                                           "variant": card["offer"]["variant_key"]})
            card["variant_fallback"] = catalog.variant_fallback(card) if catalog.variant_fallback else ""
            card["variant_text"], card["badges"] = card_variant_and_badges(card)
            other_stores = tuple(card.get("other_store_names") or ())
            other_cnt = len(other_stores)
            card["other_shops"] = other_cnt
            if other_cnt and card.get("exact_group_id"):
                compare_params: dict[str, object] = {"group": card["exact_group_id"], "compare": "true"}
                if category:
                    compare_params["category"] = category
                if min_reference != catalog.default_reference:
                    compare_params["min_reference"] = min_reference
                if max_price:
                    compare_params["max_price"] = max_price
                if min_discount:
                    compare_params["min_discount"] = min_discount
                if stock:
                    compare_params["stock"] = "true"
                card["compare_url"] = "/?" + urlencode(compare_params) + "#deals"
            else:
                card["compare_url"] = ""

        def page_url(number):
            params = {**filters, "page": number, "stock": str(stock).lower()}
            if compare:
                params["compare"] = "true"
            if group:
                params["group"] = group
            return "/?" + urlencode(params) + "#deals"

        labels = {"q": "Search", "category": "Category", "brand": "Brand", "store": "Retailer",
                  "size": catalog.size_label, "format": "Package", "platform": "Platform",
                  "min_reference": "Reference ≥ $", "max_price": "Budget ≤ $",
                  "min_discount": "Discount ≥", "stock": "Reported in stock"}
        active_filters = []
        if group:
            cleared_group = {**filters, "stock": str(stock).lower(), "page": 1}
            active_filters.append({"label": "Comparing exact offer", "url": "/?" + urlencode(cleared_group) + "#deals"})
        for key, label in labels.items():
            value = filters[key]
            if not value:
                continue
            cleared = {**filters, key: "false" if key == "stock" else (0 if key in {"min_reference", "max_price", "min_discount"} else ""),
                       "stock": "false" if key == "stock" else str(stock).lower(), "page": 1}
            if group:
                cleared["group"] = group
            if compare:
                cleared["compare"] = "true"
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
            "sorts": {key: (label, "/?" + urlencode({**filters, "sort": key, "page": 1, "stock": str(stock).lower(),
                                                     **({"compare": "true"} if compare else {}),
                                                     **({"group": group} if group else {})}) + "#deals")
                      for key, label in SORTS.items()},
        })

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, source: str = Query(..., max_length=100),
                product: str = Query(..., max_length=2000), variant: str = Query(..., max_length=100)):
        all_rows = sources()
        for row in all_rows:
            if row["name"] != source:
                continue
            for item in row["products"]:
                if item["url"] != product:
                    continue
                for offer in item["offers"]:
                    if offer["variant_key"] == variant:
                        observations = read_offer_history(database, catalog.slug, source, item, offer,
                                                          now=datetime.now(UTC), brand_aliases=catalog.brand_aliases)
                        display_item = {**item, "brand": _snapshot_brand_label(item.get("brand"), all_rows)}
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
