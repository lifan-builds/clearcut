from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deal_common.catalog.query import FRESHNESS_HOURS, query
from deal_common.catalog.storage import read_sources
from deal_common.offers import discount, plausible_offer


@dataclass(frozen=True)
class PortalHighlight:
    name: str
    store: str
    current_price: float
    original_price: float
    discount_pct: float
    currency: str
    detail: str
    url: str
    in_stock: bool
    image_url: str | None = None
    world_slug: str = ""
    world_name: str = ""


@dataclass(frozen=True)
class PortalSummary:
    live_deals: int | None
    highlights: tuple[PortalHighlight, ...]


@dataclass(frozen=True)
class PortalCatalog:
    slug: str
    chapter: str
    name: str
    domain_label: str
    headline: str
    description: str
    image_name: str
    url: str
    status_path: str
    source_count: int
    floor_note: str
    search_placeholder: str
    size_label: str
    size_placeholder: str
    size_param: str
    anchor: str
    categories: tuple[tuple[str, str], ...]
    summary_loader: Callable[[], PortalSummary | None] | None = None


def read_snow_snapshot(db_path: Path | None, *, limit: int = 4, now: datetime | None = None) -> PortalSummary | None:
    if not db_path or not db_path.is_file() or db_path.stat().st_size == 0:
        return None
    cutoff = ((now or datetime.now(UTC)).replace(tzinfo=None) - timedelta(hours=FRESHNESS_HOURS)).isoformat()
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            cols = {r[1] for r in conn.execute("PRAGMA table_info(deals)").fetchall()}
            img_expr = "image_url" if "image_url" in cols else "NULL AS image_url"
            excl_where = "AND exclusion_reason IS NULL" if "exclusion_reason" in cols else ""
            rows = conn.execute(
                f"""
                SELECT store, name, url, current_price, original_price, category, sizes, currency, {img_expr}
                FROM deals
                WHERE scraped_at >= ?
                  {excl_where}
                  AND original_price IS NOT NULL
                  AND current_price >= 40
                  AND original_price > current_price
                  AND category IN ('skis', 'snowboards', 'ski boots', 'snowboard boots', 'bindings', 'helmets', 'goggles')
                """,
                (cutoff,),
            ).fetchall()
    except sqlite3.Error:
        return None

    qualified: list[PortalHighlight] = []
    for row in rows:
        sale = float(row["current_price"])
        ref = float(row["original_price"])
        if not plausible_offer(sale, ref):
            continue
        pct = discount(sale, ref)
        detail = row["sizes"] or (row["category"] or "Gear").capitalize()
        img = row["image_url"] if row["image_url"] and str(row["image_url"]).startswith("https://") else None
        qualified.append(
            PortalHighlight(
                name=str(row["name"]),
                store=str(row["store"]),
                current_price=sale,
                original_price=ref,
                discount_pct=pct,
                currency=str(row["currency"] or "USD"),
                detail=str(detail),
                url=str(row["url"]),
                in_stock=bool(row["sizes"]),
                image_url=img,
                world_slug="snow",
                world_name="FreshPowder",
            )
        )
    if not qualified:
        return None
    qualified.sort(key=lambda h: (h.image_url is None, -h.discount_pct, -(h.original_price - h.current_price), h.name))
    return PortalSummary(live_deals=len(qualified), highlights=tuple(qualified[:limit]))


def read_shared_catalog_snapshot(
    db_path: Path | None,
    catalog_slug: str,
    *,
    world_name: str = "",
    min_reference: float,
    limit: int = 3,
    now: datetime | None = None,
) -> PortalSummary | None:
    if not db_path or not db_path.is_file() or db_path.stat().st_size == 0:
        return None
    try:
        rows = read_sources(db_path, catalog_slug)
    except (sqlite3.Error, ValueError):
        return None
    result = query(rows, {"min_reference": min_reference, "max_price": 0, "min_discount": 0, "page": 1}, now=now)
    if not result["total"]:
        return None
    highlights: list[PortalHighlight] = []
    for card in result["cards"][:limit]:
        offer = card["offer"]
        detail = offer.get("size") or offer.get("options") or card.get("platform") or card.get("category") or ""
        img = offer.get("image_url") if offer.get("image_url") and str(offer["image_url"]).startswith("https://") else None
        highlights.append(
            PortalHighlight(
                name=str(card["name"]),
                store=str(card["store"]),
                current_price=float(offer["current_price"]),
                original_price=float(offer["original_price"]),
                discount_pct=float(offer["discount"]),
                currency="USD",
                detail=str(detail),
                url=str(offer["url"]),
                in_stock=offer.get("availability") == "in_stock",
                image_url=img,
                world_slug=catalog_slug,
                world_name=world_name or catalog_slug.title(),
            )
        )
    return PortalSummary(live_deals=int(result["total"]), highlights=tuple(highlights))


def default_portal_catalogs(
    *,
    snow_url: str | None = None,
    cycling_url: str | None = None,
    tools_url: str | None = None,
    camp_url: str | None = None,
    watersports_url: str | None = None,
    snow_db: Path | None = None,
    cycling_db: Path | None = None,
    tools_db: Path | None = None,
    camp_db: Path | None = None,
    watersports_db: Path | None = None,
) -> tuple[PortalCatalog, ...]:
    snow_url = snow_url or os.environ.get("SNOW_SITE_URL", "https://freshpowder.clifan.com")
    cycling_url = cycling_url or os.environ.get("CYCLING_SITE_URL", "https://bigring.clifan.com")
    tools_url = tools_url or os.environ.get("TOOLS_SITE_URL", "https://benchdrop.clifan.com")
    camp_url = camp_url or os.environ.get("CAMP_SITE_URL", "https://highline.clifan.com")
    watersports_url = watersports_url or os.environ.get("WATER_SITE_URL", "https://tideline.clifan.com")
    return (
        PortalCatalog(
            slug="snow",
            chapter="01",
            name="FreshPowder",
            domain_label="Alpine & Backcountry Snow",
            headline="More mountain, less markup.",
            description="Freeride skis, snowboards, touring boots, bindings, and alpine optics across 30 North American shops in USD and\u00a0CAD.",
            image_name="snow-card.jpg",
            url=snow_url.rstrip("/"),
            status_path="/status",
            source_count=32,
            floor_note="32 shops · USD & CAD",
            search_placeholder="Search skis, snowboards, boots, brand, or model",
            size_label="Exact size (cm / mondo)",
            size_placeholder="e.g. 178, 26.5, M",
            size_param="size",
            anchor="",
            categories=(
                ("Skis", "skis"),
                ("Snowboards", "snowboards"),
                ("Ski boots", "ski boots"),
                ("Bindings", "bindings"),
                ("Goggles", "goggles"),
            ),
            summary_loader=(lambda: read_snow_snapshot(snow_db)) if snow_db else None,
        ),
        PortalCatalog(
            slug="cycling",
            chapter="02",
            name="Big Ring",
            domain_label="Road, Gravel & MTB",
            headline="More bike, less money.",
            description="Complete road, gravel, mountain, and electric bikes plus carbon framesets, wheelsets, and groupsets across 23 bike\u00a0shops.",
            image_name="cycling-card.jpg",
            url=cycling_url.rstrip("/"),
            status_path="/sources",
            source_count=25,
            floor_note="25 shops · Bikes, frames & wheels",
            search_placeholder="Search Cervelo, Santa Cruz, gravel, wheelset, or Di2",
            size_label="Frame size",
            size_placeholder="e.g. 54cm, M, L, S3",
            size_param="size",
            anchor="#deals",
            categories=(
                ("Road bikes", "Road bikes"),
                ("Gravel bikes", "Gravel bikes"),
                ("Mountain bikes", "Mountain bikes"),
                ("Wheelsets", "Wheelsets"),
                ("Framesets", "Framesets"),
            ),
            summary_loader=(
                (lambda: read_shared_catalog_snapshot(cycling_db, "cycling", world_name="Big Ring", min_reference=1000))
                if cycling_db
                else None
            ),
        ),
        PortalCatalog(
            slug="tools",
            chapter="03",
            name="Bench Drop",
            domain_label="Workshop & Power Machinery",
            headline="Build more, spend less.",
            description="Cordless systems, combo kits, welders, helical planers, and stationary shop machinery across 15 authorized\u00a0dealers.",
            image_name="tools-card.jpg",
            url=tools_url.rstrip("/"),
            status_path="/sources",
            source_count=14,
            floor_note="14 dealers · $200+ floor",
            search_placeholder="Search track saw, planer, welder, Milwaukee, or Festool",
            size_label="Battery platform",
            size_placeholder="e.g. Milwaukee M18, DeWalt 20V MAX",
            size_param="platform",
            anchor="#deals",
            categories=(
                ("Power tools", "Power tools"),
                ("Combo kits", "Combo kits"),
                ("Workshop equipment", "Workshop equipment"),
            ),
            summary_loader=(
                (lambda: read_shared_catalog_snapshot(tools_db, "tools", world_name="Bench Drop", min_reference=200))
                if tools_db
                else None
            ),
        ),
        PortalCatalog(
            slug="camp",
            chapter="04",
            name="Highline",
            domain_label="Camp, Climb & Backcountry",
            headline="Lighter pack, deeper cuts.",
            description="Ultralight tents, down quilts, sleeping bags, insulated pads, alpine packs, and climbing hardware across 12\u00a0outfitters.",
            image_name="camp-card.jpg",
            url=camp_url.rstrip("/"),
            status_path="/sources",
            source_count=15,
            floor_note="15 outfitters · $120+ floor",
            search_placeholder="Search NEMO, Big Agnes, Durston, quilt, rope, or cam",
            size_label="Size / length",
            size_placeholder="e.g. Regular, Long, 2P",
            size_param="size",
            anchor="#deals",
            categories=(
                ("Tents & shelters", "Tents & shelters"),
                ("Sleeping bags & quilts", "Sleeping bags & quilts"),
                ("Backpacks", "Backpacks"),
                ("Climbing & mountaineering", "Climbing & mountaineering"),
            ),
            summary_loader=(
                (lambda: read_shared_catalog_snapshot(camp_db, "camp", world_name="Highline", min_reference=120))
                if camp_db
                else None
            ),
        ),
        PortalCatalog(
            slug="watersports",
            chapter="05",
            name="Tideline",
            domain_label="Surf, Foil, Wing & Fly",
            headline="Clean swell, deep closeouts.",
            description="Hydrofoils, wings, kites, surfboards, SUPs, full wetsuits, and fly rods & reels across 11 coastal and river\u00a0shops.",
            image_name="water-card.jpg",
            url=watersports_url.rstrip("/"),
            status_path="/sources",
            source_count=14,
            floor_note="14 shops · $180+ floor",
            search_placeholder="Search Armstrong, Duotone, wetsuit, surfboard, or Sage",
            size_label="Size / spec",
            size_placeholder="e.g. MT, L, 5.0m, 5wt",
            size_param="size",
            anchor="#deals",
            categories=(
                ("Foils & wings", "Foils & wings"),
                ("Surfboards & SUPs", "Surfboards & SUPs"),
                ("Wetsuits", "Wetsuits"),
                ("Fly rods & reels", "Fly rods & reels"),
            ),
            summary_loader=(
                (lambda: read_shared_catalog_snapshot(watersports_db, "watersports", world_name="Tideline", min_reference=180))
                if watersports_db
                else None
            ),
        ),
    )
