"""Cross-shop model price comparisons over the read-only deal snapshot."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from aggregator import db
from aggregator.models import model_year

CATALOG = json.loads(Path(__file__).with_name("comparison_catalog.json").read_text())
PRODUCTS = {p["slug"]: p for p in CATALOG}
REFERENCES = {r["url"]: (p, r) for p in CATALOG for r in p["offers"]}
BRANDS = {re.sub(r"[^a-z0-9]+", "-", p["brand"].lower()): p["brand"] for p in CATALOG}
INDEXABLE_BRANDS = {s: b for s, b in BRANDS.items() if sum(p['brand'] == b for p in CATALOG) >= 2}

_STRIP_NOISE_RE = re.compile(
    r"\b(?:mens|men's|men|unisex|adult|skis|snowboard|snowboards|ski boots|snowboard boots|"
    r"alpine|freeride|all[- ]mountain|powder|carving|pair|flat)\b",
    re.IGNORECASE,
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _auto_model_identity(row: dict) -> tuple[str, str, str, int | str] | None:
    brand = (row.get("brand") or "").strip()
    category = (row.get("category") or "").strip()
    name = (row.get("name") or "").strip()
    if not brand or category not in {"skis", "snowboards", "ski boots", "snowboard boots", "bindings", "goggles", "helmets"}:
        return None
    year = model_year(name) or "current"
    lowered = unicodedata.normalize("NFKC", name).casefold()
    gender = "womens" if re.search(r"\b(?:women'?s?|womens|wmns|ladies)\b", lowered) else ""
    wide = "wide" if re.search(r"\bwide\b|\b1\d{2}w\b", lowered) else ""
    with_bindings = "system" if re.search(r"\b(?:with|w/)\s+[a-z0-9 ]*bindings?\b|\bsystem skis?\b", lowered) else ""
    cleaned = re.sub(r"\b20\d{2}(?:[-/]\d{2,4})?\b|\b\d{2}[-/]\d{2}\b", " ", lowered)
    cleaned = re.sub(r"\b(?:women'?s?|womens|wmns|ladies)\b", " ", cleaned)
    cleaned = _STRIP_NOISE_RE.sub(" ", cleaned)
    if cleaned.startswith(brand.casefold() + " "):
        cleaned = cleaned[len(brand) + 1:]
    tokens = [t for t in re.findall(r"[a-z0-9.]+", cleaned) if not re.fullmatch(r"1\d{2}cm", t)]
    if len(tokens) < 1 or len(tokens) > 6:
        return None
    core = "-".join(tokens[:4])
    parts = [ _slugify(brand), core ]
    if gender:
        parts.append(gender)
    if wide:
        parts.append(wide)
    if with_bindings:
        parts.append(with_bindings)
    if year != "current":
        parts.append(str(year))
    slug = "auto-" + "-".join(p for p in parts if p)
    display_tokens = " ".join(w.upper() if len(w) <= 3 and any(c.isdigit() for c in w) else w.title() for w in tokens[:4])
    suffix = " ".join(x for x in [("Women's" if gender else ""), ("Wide" if wide else ""), (str(year) if year != "current" else "")] if x)
    display_name = f"{brand.title()} {display_tokens}" + (f" ({suffix})" if suffix else "")
    return slug, display_name, brand.title(), year


def normalized_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", value))


def matched_product(name: str, url: str, category: str | None) -> dict | None:
    pair = REFERENCES.get(url)
    if not pair:
        return None
    product, reference = pair
    if category == product["category"] and normalized_title(name) == normalized_title(reference["title"]):
        return product
    return None


def comparison_path(deal) -> str | None:
    product = matched_product(deal.name, deal.url, deal.category)
    return f'/products/{product["slug"]}' if product else None


def reported_sizes(sizes: str | None) -> list[str]:
    """Keep wide snowboard variants distinct and never fill gaps in a range."""
    found = set()
    for token in (sizes or "").split(","):
        match = re.fullmatch(r"\s*(\d{3})\s*(w)?\s*(?:cm)?\s*(wide)?\s*", token, re.IGNORECASE)
        if match and 100 <= int(match[1]) <= 220:
            found.add(match[1] + ("W" if match[2] or match[3] else ""))
    return sorted(found, key=lambda value: (int(value.rstrip("W")), value.endswith("W")))


async def catalog_rows(db_path: Path | None = None) -> list[dict]:
    placeholders = ",".join("?" for _ in REFERENCES)
    async with db._connect(db_path or db.DB_PATH, read_only=True) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (await conn.execute(f"""
            SELECT d.*, latest.ts AS store_latest
            FROM deals d JOIN (SELECT store, MAX(scraped_at) ts FROM deals GROUP BY store) latest
              ON latest.store=d.store
            WHERE d.exclusion_reason IS NULL AND d.url IN ({placeholders})
        """, tuple(REFERENCES))).fetchall()
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=72)
    result = []
    for row in rows:
        offer = dict(row)
        product = matched_product(offer["name"], offer["url"], offer["category"])
        if not product or offer["currency"] not in {"USD", "CAD"}:
            continue
        price = offer["current_price"]
        if not isinstance(price, (int, float)) or not math.isfinite(price) or price < 1:
            continue
        offer["slug"] = product["slug"]
        offer["size_keys"] = reported_sizes(offer["sizes"])
        offer["current"] = offer["scraped_at"] == offer["store_latest"] and datetime.fromisoformat(offer["scraped_at"]) >= cutoff
        result.append(offer)
    return result


async def _auto_grouped_models(db_path: Path | None = None) -> dict[str, dict]:
    target = db_path or db.DB_PATH
    if not Path(target).exists():
        return {}
    cutoff = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=36)).isoformat()
    async with db._connect(target, read_only=True) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (await conn.execute("""
            SELECT d.*
            FROM deals d
            JOIN (SELECT store, MAX(scraped_at) ts FROM deals GROUP BY store) latest
              ON latest.store = d.store AND d.scraped_at = latest.ts
            WHERE d.exclusion_reason IS NULL
              AND d.scraped_at >= ?
              AND d.current_price >= 1
        """, (cutoff,))).fetchall()
    buckets: dict[str, dict] = {}
    for raw in rows:
        row = dict(raw)
        if row["url"] in REFERENCES or row.get("currency") not in {"USD", "CAD"}:
            continue
        ident = _auto_model_identity(row)
        if not ident:
            continue
        slug, display_name, brand_title, year = ident
        if slug in PRODUCTS:
            continue
        entry = buckets.setdefault(slug, {
            "slug": slug,
            "name": display_name,
            "brand": brand_title,
            "category": row["category"],
            "year": year,
            "rows": [],
        })
        row["slug"] = slug
        row["size_keys"] = reported_sizes(row.get("sizes"))
        row["current"] = True
        entry["rows"].append(row)
    return {slug: data for slug, data in buckets.items() if len({r["store"] for r in data["rows"]}) >= 2}


async def catalog_summary(db_path: Path | None = None) -> list[dict]:
    grouped = defaultdict(list)
    for row in await catalog_rows(db_path):
        if row["current"]:
            grouped[row["slug"]].append(row)
    result = []
    for product in CATALOG:
        offers = grouped[product["slug"]]
        prices = {currency: min(o["current_price"] for o in offers if o["currency"] == currency)
                  for currency in ("USD", "CAD") if any(o["currency"] == currency for o in offers)}
        max_disc = max((o.get("discount_pct") or 0 for o in offers), default=0)
        result.append({**product, "prices": prices, "store_count": len({o["store"] for o in offers}),
                       "max_discount": max_disc,
                       "image": next((o["image_url"] for o in offers if o["image_url"]), None)})
    auto_models = await _auto_grouped_models(db_path)
    for slug, item in auto_models.items():
        offers = item["rows"]
        prices = {currency: min(o["current_price"] for o in offers if o["currency"] == currency)
                  for currency in ("USD", "CAD") if any(o["currency"] == currency for o in offers)}
        max_disc = max((o.get("discount_pct") or 0 for o in offers), default=0)
        result.append({
            "slug": slug,
            "name": item["name"],
            "brand": item["brand"],
            "category": item["category"],
            "year": item["year"],
            "prices": prices,
            "store_count": len({o["store"] for o in offers}),
            "max_discount": max_disc,
            "image": next((o["image_url"] for o in offers if o.get("image_url")), None),
        })
    result.sort(key=lambda m: (m["store_count"] == 0, -m["max_discount"], -m["store_count"], m["name"]))
    return result


async def product_detail(slug: str, currency: str = "USD", size: str | None = None,
                         db_path: Path | None = None) -> dict | None:
    product = PRODUCTS.get(slug)
    auto_entry = None
    if not product:
        auto_entry = (await _auto_grouped_models(db_path)).get(slug)
        if not auto_entry:
            return None
        product = {k: auto_entry[k] for k in ("slug", "name", "brand", "category", "year")}
        rows = auto_entry["rows"]
    else:
        rows = [r for r in await catalog_rows(db_path) if r["slug"] == slug]
    current = [r for r in rows if r["current"]]
    offers = sorted((r for r in current if r["currency"] == currency and
                     (size is None or size in r["size_keys"])), key=lambda r: (r["current_price"], r["store"]))
    # History starts at each reviewed observation, not at an old reused URL's
    # first price. The selected currency and length filter also scope its URLs.
    history_rows = [r for r in rows if r["currency"] == currency and (size is None or size in r["size_keys"])]
    history = []
    if history_rows:
        urls = [r["url"] for r in history_rows]
        async with db._connect(db_path or db.DB_PATH, read_only=True) as conn:
            history = await (await conn.execute(
                f"SELECT url,observed_at,price FROM price_history WHERE url IN ({','.join('?' for _ in urls)}) ORDER BY observed_at",
                urls)).fetchall()
    daily = {}
    for url, observed, price in history:
        min_date = REFERENCES[url][1]["history_since"] if url in REFERENCES else ""
        if (min_date and observed < min_date) or not math.isfinite(price) or price < 1:
            continue
        day = observed[:10]
        daily[day] = min(daily.get(day, price), price)
    points = [{"date": day, "price": price} for day, price in sorted(daily.items())]
    low = min(daily.values()) if daily else None
    # Render an accessible, dependency-free chart; the exact data is also a table.
    chart = []
    if points:
        first = datetime.fromisoformat(points[0]["date"])
        span = max(1, (datetime.fromisoformat(points[-1]["date"]) - first).days)
        floor, ceiling = min(daily.values()), max(daily.values())
        for point in points:
            x = 30 + 540 * (datetime.fromisoformat(point["date"]) - first).days / span
            y = 160 - 120 * (point["price"] - floor) / (ceiling - floor) if ceiling > floor else 100
            chart.append({**point, "x": round(x, 2), "y": round(y, 2)})
    return {**product, "offers": offers, "currency": currency, "size": size,
            "currencies": sorted({o["currency"] for o in current}),
            "sizes": sorted({n for o in current if o["currency"] == currency for n in o["size_keys"]}, key=lambda n: (int(n.rstrip("W")), n.endswith("W"))),
            "store_count": len({o["store"] for o in offers}),
            "unknown_size_count": sum(not o["size_keys"] for o in current if o["currency"] == currency),
            "image": next((r["image_url"] for r in rows if r["image_url"]), None),
            "history": points, "observed_low": low, "chart": chart,
            "chart_line": " ".join(f'{p["x"]},{p["y"]}' for p in chart)}
