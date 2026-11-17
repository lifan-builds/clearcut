#!/usr/bin/env python3
"""Black Friday Full-Catalog Audit & Enrichment via Quotaswitch OpenAI Gateway.

Routes all OpenAI API traffic exclusively through the local quotaswitch gateway
(`http://127.0.0.1:18455/v1`) with `Authorization: Bearer quotaswitch-local`.
Never reads `.env`, raw OpenAI API keys, or auth databases, and opens local
SQLite deal snapshots strictly read-only (`?mode=ro`).

Subcommands:
  inspect       Summarize local catalog snapshot coverage and anomaly strata.
  live          Run 16-worker concurrent live JSON audits (`gpt-6.1-sol`).
  batch-submit  Export JSONL slice, upload `/v1/files`, and submit 24h `/v1/batches`.
  batch-status  Poll `/v1/batches/<batch_id>` using saved receipt account header.
  batch-collect Download `/v1/files/<output_file_id>/content` and summarize findings.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import sqlite3
import time
import urllib.error
from urllib.parse import quote, urlparse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GATEWAY_BASE = "http://127.0.0.1:18455/v1"
GATEWAY_BEARER_TOKEN = "quotaswitch-local"
DEFAULT_BATCH_ACCOUNT = "api-clfgpt3-exp1129"
DEFAULT_LIVE_MODEL = "gpt-6.1-sol"
DEFAULT_BATCH_MODEL = "gpt-6.1-sol"
DEFAULT_BATCH_ENDPOINT = "/v1/responses"
DEFAULT_RUNTIME_DIR = ROOT / "build" / "openai-catalog-audit"

DEFAULT_SNAPSHOT_CANDIDATES = (
    ROOT / ".local" / "release-rollback" / "2026-11-02" / "deals-released-703257183.db",
    ROOT / ".local" / "release-rollback" / "2026-11-02" / "deals-before-702002844.db",
    ROOT / "catalogs" / "fresh-powder" / "data" / "deals.db",
    ROOT / "aggregator" / "deals.db",
)

SUPPLEMENTAL_SHOPIFY_FEEDS = (
    (
        "https://www.aspenskiandboard.com/products/",
        "",
        ROOT / "docs" / "research" / "2026-11-02-deal-site-landscape" / "execution" / "aspen-skis-page-1.json",
    ),
    (
        "https://www.aspenskiandboard.com/products/",
        "",
        ROOT / "catalogs" / "fresh-powder" / "tests" / "fixtures" / "aspen_variant_sample.json",
    ),
)

NEW_SOURCE_FEEDS_PATH = ROOT / "catalogs" / "fresh-powder" / "tests" / "fixtures" / "new_source_feeds.json"
NEW_SOURCE_ORIGINS = {
    "utahskis": ("https://utahskis.com/products/", ""),
    "startingate": ("https://shopstartingate.com/products/", ""),
    "freeride": ("https://www.freerideboardshop.com/en-us/products/", ""),
    "boardom": ("https://boardom.net/products/", "?currency=CAD"),
}

VALID_CATEGORIES = (
    "skis",
    "snowboards",
    "ski boots",
    "snowboard boots",
    "winter boots",
    "bindings",
    "poles",
    "helmets",
    "goggles",
    "jackets",
    "pants",
    "gloves",
    "layers",
    "accessories",
    "exclude",
)

ISSUE_TAXONOMY = {
    # (a) Pricing & discount claims
    "inflated_reference_price": "pricing_integrity",
    "legacy_vintage_msrp": "pricing_integrity",
    "blem_or_demo_vs_new_msrp": "pricing_integrity",
    "currency_suspect": "pricing_integrity",
    # (b) Variant / size / stock / price consistency
    "variant_price_spread": "variant_stock_consistency",
    "oos_headline_risk": "variant_stock_consistency",
    "unverified_stock_or_missing_variants": "variant_stock_consistency",
    "missing_sizes_in_record": "variant_stock_consistency",
    # (c) Category, gender, discipline, or youth/kids classification
    "wrong_category": "classification_accuracy",
    "non_snow_item": "classification_accuracy",
    "nordic_vs_alpine_mismatch": "classification_accuracy",
    "youth_unmarked_in_adult": "classification_accuracy",
    "gender_mismatch": "classification_accuracy",
    # (d) Fulfillment, condition, package, or final-sale caveats
    "blem_demo_used_condition": "fulfillment_caveats",
    "final_sale_or_clearance_caveat": "fulfillment_caveats",
    "call_to_order_or_pickup_only": "fulfillment_caveats",
    "package_binding_ambiguity": "fulfillment_caveats",
    "made_to_order_or_delayed_shipping": "fulfillment_caveats",
}

SYSTEM_PROMPT = (
    "You are a senior e-commerce catalog quality and pricing auditor for Fresh Powder, "
    "a snow-sports deal aggregator whose core contract is accurate deep percentage discounts "
    "with honest reference prices, verified purchasable variant stock/size/price agreement, "
    "accurate snow-gear taxonomy, and surfaced condition/fulfillment caveats.\n"
    "Evaluate each catalog offer strictly on four axes:\n"
    "1) pricing_integrity: implausible or inflated reference/MSRP prices (e.g., single strap/sock/layer/glove "
    "compared to multipack or 4x-10x bogus MSRP), 20+ year vintage models compared to $1,000 MSRP, "
    "factory blem/demo/used gear compared directly against first-quality new MSRP, or suspect USD/CAD currency.\n"
    "2) variant_stock_consistency: wide variant price/discount spreads where one outlier size or color drives "
    "the headline discount while mainstream sizes cost substantially more, out-of-stock variants in raw feed metadata, "
    "unverified stock (`variant_offer_count == 0` / `availability == 'unknown'`), or sizes/widths written in title "
    "while `sizes` is null.\n"
    "3) classification_accuracy: non-snow items (roof racks, overland mounts, wake/street items) not excluded, "
    "null or generic `boots` category, wrong category among Fresh Powder categories, cross-country/Nordic gear mixed "
    "into alpine categories without distinction, gender mismatch, or youth/kids/junior/grom gear unmarked in adult feeds.\n"
    "4) fulfillment_caveats: factory blem/cosmetic second/demo/rental-track/used/sample condition, phone-number / "
    "call-to-order / in-store-only restrictions in title, flat (no bindings) vs system-binding package ambiguity, "
    "made-to-order / preorder / delayed shipping, or final-sale / non-returnable outlet clearance caveats.\n"
    "Always return a single valid json object and do not invent facts absent from the offer record and standard "
    "snow-industry product/MSRP knowledge."
)

USER_PROMPT_PREFIX = (
    "Audit the following catalog offer and respond in json with exact keys:\n"
    "- \"verdict\": one of \"pass\", \"warn\", \"fail\"\n"
    "- \"severity\": integer 0 (clean), 1 (minor caveat), 2 (material shopper/ranking issue), 3 (severe misleading price/non-snow/broken offer)\n"
    "- \"flags\": array of zero or more issue codes from "
    + json.dumps(list(ISSUE_TAXONOMY.keys()))
    + "\n"
    "- \"suggested_category\": one of "
    + json.dumps(list(VALID_CATEGORIES))
    + "\n"
    "- \"audience\": one of \"adult_men\", \"adult_women\", \"adult_unisex\", \"youth_kids\", \"non_snow\"\n"
    "- \"estimated_fair_msrp_range\": concise string (e.g. \"$75-$95 USD\" or \"$700-$850 CAD\")\n"
    "- \"price_plausible\": boolean\n"
    "- \"summary\": 1-2 concise, concrete sentences citing exact prices/sizes/terms from the offer.\n\n"
    "OFFER JSON:\n"
)

YOUTH_RE = re.compile(
    r"\b(?:kids?|youth|junior|jr\.?|boys?|girls?|toddler|mini|grom|cadet|lil\b|xs-kids)\b",
    re.IGNORECASE,
)
CAVEAT_RE = re.compile(
    r"(?:\b(?:blem(?:ish(?:ed)?)?|demo|used|second|sample|cosmetic|final[\s-]*sale|non[\s-]*returnable|"
    r"pre[\s-]*order|made[\s-]*to[\s-]*order|custom|call|in[\s-]*store|pickup|no\s+bindings|flat|"
    r"with\s+.*bindings|system\s+bindings)\b|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b)",
    re.IGNORECASE,
)
TITLE_SIZE_RE = re.compile(r"\b(?:\d{2,3}\s*(?:cm|mm)|width:\s*\d+)\b", re.IGNORECASE)


def assert_local_gateway(gateway_base: str) -> str:
    """Enforce strict loopback quotaswitch routing."""
    parsed = urlparse(gateway_base)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError(
            f"Refusing non-loopback gateway URL {gateway_base!r}; must route via local quotaswitch."
        )
    return gateway_base.rstrip("/")


def resolve_snapshot_db(explicit_path: Path | None = None) -> Path:
    """Locate the freshest local SQLite deal snapshot without modifying it."""
    if explicit_path is not None:
        resolved = explicit_path.expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Snapshot DB not found: {resolved}")
        return resolved
    for candidate in DEFAULT_SNAPSHOT_CANDIDATES:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate.resolve()
    raise FileNotFoundError(
        "No local deal snapshot DB found in expected paths; pass --db-path explicitly."
    )


def sqlite_ro_uri(db_path: Path) -> str:
    """Build a read-only SQLite URI supporting spaces and special path chars."""
    return f"file:{quote(db_path.resolve().as_posix(), safe='/')}?mode=ro"


def load_supplemental_raw_feeds() -> dict[str, dict]:
    """Load optional checked-in raw Shopify feed metadata (tags + OOS variants)."""
    by_url: dict[str, dict] = {}

    def ingest_items(items: list[dict], prefix: str, suffix: str) -> None:
        for item in items:
            if not isinstance(item, dict) or not item.get("handle"):
                continue
            url = f"{prefix}{item['handle']}{suffix}"
            variants = [v for v in (item.get("variants") or []) if isinstance(v, dict)]
            oos = [v for v in variants if v.get("available") is False]
            instock = [v for v in variants if v.get("available") is True]
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            body_html = str(item.get("body_html") or "").strip()
            body_text = re.sub(r"<[^>]+>", " ", body_html)
            body_text = re.sub(r"\s+", " ", body_text).strip()[:400]
            by_url[url] = {
                "raw_total_variants": len(variants),
                "raw_in_stock_variants": len(instock),
                "raw_oos_variants": len(oos),
                "raw_oos_examples": [
                    {
                        "title": v.get("title"),
                        "price": v.get("price"),
                        "compare_at_price": v.get("compare_at_price"),
                    }
                    for v in oos[:4]
                ],
                "raw_tags": tags[:15],
                "raw_body_excerpt": body_text or None,
            }

    for prefix, suffix, path in SUPPLEMENTAL_SHOPIFY_FEEDS:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict) and isinstance(data.get("products"), list):
                ingest_items(data["products"], prefix, suffix)

    if NEW_SOURCE_FEEDS_PATH.exists():
        feeds = json.loads(NEW_SOURCE_FEEDS_PATH.read_text())
        if isinstance(feeds, dict):
            for key, (prefix, suffix) in NEW_SOURCE_ORIGINS.items():
                feed = feeds.get(key)
                if isinstance(feed, dict) and isinstance(feed.get("products"), list):
                    ingest_items(feed["products"], prefix, suffix)

    return by_url


def classify_stratum(offer: dict) -> str:
    """Assign an offer to its highest-priority audit risk stratum."""
    cat = offer.get("category")
    text = f"{offer.get('name', '')} {offer.get('url', '')} {offer.get('product_type') or ''}"
    if cat in (None, "", "boots"):
        return "unresolved_or_generic_category"
    if offer.get("discount_pct", 0) >= 70.0 or re.search(r"\b00\s*[-–]\s*01\b", offer.get("name", "")):
        return "extreme_discount_or_legacy_msrp"
    if CAVEAT_RE.search(text):
        return "condition_or_fulfillment_caveat"
    if offer.get("variant_price_spread", 0.0) >= 25.0 or offer.get("variant_discount_spread", 0.0) >= 15.0:
        return "variant_price_spread"
    if (offer.get("supplemental_raw_feed") or {}).get("raw_oos_variants", 0) > 0:
        return "raw_feed_oos_variants"
    if YOUTH_RE.search(text):
        return "youth_or_audience_check"
    if offer.get("variant_offer_count", 0) == 0 and (
        offer.get("sizes") is None and TITLE_SIZE_RE.search(offer.get("name", ""))
    ):
        return "missing_structured_sizes"
    if offer.get("variant_offer_count", 0) == 0:
        return "unverified_non_shopify_stock"
    if (offer.get("original_price") or 0) >= 600 and offer.get("discount_pct", 0) >= 40:
        return "high_ticket_deep_discount"
    return "baseline_active_catalog"


def load_active_offers(db_path: Path) -> list[dict]:
    """Load latest-per-store active offers with variant & price-history facts (read-only)."""
    supplemental = load_supplemental_raw_feeds()
    with sqlite3.connect(sqlite_ro_uri(db_path), uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        deal_cols = {row["name"] for row in db.execute("PRAGMA table_info(deals)").fetchall()}

        has_exclusion = "exclusion_reason" in deal_cols
        has_currency = "currency" in deal_cols
        has_product_type = "product_type" in deal_cols
        has_brand = "brand" in deal_cols

        where_exclusion = "WHERE d.exclusion_reason IS NULL" if has_exclusion else ""
        select_cols = [
            "d.id",
            "d.store",
            "d.name",
            "d.url",
            "d.current_price",
            "d.original_price",
            "d.discount_pct",
            "d.category",
            "d.sizes",
            "d.length_min",
            "d.length_max",
            "d.scraped_at",
            "d.brand" if has_brand else "NULL AS brand",
            "d.currency" if has_currency else "'USD' AS currency",
            "d.product_type" if has_product_type else "NULL AS product_type",
        ]
        rows = db.execute(
            f"""
            WITH latest AS (
                SELECT store, MAX(scraped_at) AS max_scraped
                FROM deals
                GROUP BY store
            )
            SELECT {', '.join(select_cols)}
            FROM deals d
            JOIN latest l ON l.store = d.store AND d.scraped_at = l.max_scraped
            {where_exclusion}
            ORDER BY d.discount_pct DESC, d.id ASC
            """
        ).fetchall()

        variants_by_url: dict[str, list[dict]] = defaultdict(list)
        if "deal_offers" in tables:
            for v in db.execute(
                """
                SELECT product_url, variant_key, size, options, current_price,
                       original_price, discount_pct, availability, reference_basis
                FROM deal_offers
                ORDER BY discount_pct DESC, current_price ASC, variant_key ASC
                """
            ):
                variants_by_url[v["product_url"]].append(
                    {
                        "size": v["size"],
                        "options": v["options"],
                        "price": v["current_price"],
                        "original": v["original_price"],
                        "discount_pct": v["discount_pct"],
                        "availability": v["availability"],
                    }
                )

        history_by_url: dict[str, dict] = {}
        if "price_history" in tables:
            for h in db.execute(
                """
                SELECT url, COUNT(*) AS obs_count, MIN(price) AS min_price, MAX(price) AS max_price
                FROM price_history
                GROUP BY url
                """
            ):
                history_by_url[h["url"]] = {
                    "observations": h["obs_count"],
                    "min_observed_price": h["min_price"],
                    "max_observed_price": h["max_price"],
                }

    offers: list[dict] = []
    for row in rows:
        url = row["url"]
        vlist = variants_by_url.get(url, [])
        prices = [v["price"] for v in vlist]
        discs = [v["discount_pct"] for v in vlist]
        in_stock_cnt = sum(1 for v in vlist if v["availability"] == "in_stock")
        oos_cnt = sum(1 for v in vlist if v["availability"] == "out_of_stock")
        unk_cnt = sum(1 for v in vlist if v["availability"] not in {"in_stock", "out_of_stock"})

        # Keep prompt compact while showing cheapest, most expensive, and middle variants.
        if len(vlist) <= 8:
            sampled_variants = vlist
        else:
            sampled_variants = vlist[:4] + vlist[-4:]

        offer = {
            "id": row["id"],
            "store": row["store"],
            "currency": row["currency"] or "USD",
            "name": row["name"],
            "brand": row["brand"],
            "url": url,
            "current_price": row["current_price"],
            "original_price": row["original_price"],
            "discount_pct": row["discount_pct"],
            "category": row["category"],
            "product_type": row["product_type"],
            "sizes": row["sizes"],
            "length_min": row["length_min"],
            "length_max": row["length_max"],
            "scraped_at": row["scraped_at"],
            "variant_offer_count": len(vlist),
            "variant_in_stock_count": in_stock_cnt,
            "variant_oos_count": oos_cnt,
            "variant_unknown_stock_count": unk_cnt,
            "variant_price_min": min(prices) if prices else None,
            "variant_price_max": max(prices) if prices else None,
            "variant_price_spread": round(max(prices) - min(prices), 2) if prices else 0.0,
            "variant_discount_min": min(discs) if discs else None,
            "variant_discount_max": max(discs) if discs else None,
            "variant_discount_spread": round(max(discs) - min(discs), 1) if discs else 0.0,
            "variants_sample": sampled_variants,
            "price_history": history_by_url.get(url),
            "supplemental_raw_feed": supplemental.get(url),
        }
        offer["stratum"] = classify_stratum(offer)
        offers.append(offer)
    return offers


def select_stratified_sample(offers: list[dict], limit: int, seed: int = 42) -> list[dict]:
    """Select a deterministic merchant-balanced + risk-stratified offer slice."""
    if limit <= 0 or limit >= len(offers):
        return list(offers)

    rng = random.Random(seed)
    selected_ids: set[int] = set()
    selected: list[dict] = []

    def add_offer(item: dict) -> bool:
        if len(selected) >= limit or item["id"] in selected_ids:
            return False
        selected_ids.add(item["id"])
        selected.append(item)
        return True

    # Pass 1: Guarantee representation across EVERY active merchant (at least min_per_store).
    by_store: dict[str, list[dict]] = defaultdict(list)
    for offer in offers:
        by_store[offer["store"]].append(offer)

    stores = sorted(by_store.keys())
    min_per_store = max(1, min(3, limit // max(1, len(stores) * 2)))
    priority_strata = {
        "unresolved_or_generic_category": 0,
        "extreme_discount_or_legacy_msrp": 1,
        "condition_or_fulfillment_caveat": 2,
        "variant_price_spread": 3,
        "raw_feed_oos_variants": 4,
        "missing_structured_sizes": 5,
        "youth_or_audience_check": 6,
        "high_ticket_deep_discount": 7,
        "unverified_non_shopify_stock": 8,
        "baseline_active_catalog": 9,
    }
    for store in stores:
        ranked = sorted(
            by_store[store],
            key=lambda o: (priority_strata.get(o["stratum"], 99), -o.get("discount_pct", 0.0), o["id"]),
        )
        for item in ranked[:min_per_store]:
            add_offer(item)

    # Pass 2: Allocate remaining slots proportionally/round-robin across risk strata.
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for offer in offers:
        if offer["id"] not in selected_ids:
            by_stratum[offer["stratum"]].append(offer)

    stratum_order = [
        "unresolved_or_generic_category",
        "extreme_discount_or_legacy_msrp",
        "condition_or_fulfillment_caveat",
        "raw_feed_oos_variants",
        "missing_structured_sizes",
        "variant_price_spread",
        "youth_or_audience_check",
        "high_ticket_deep_discount",
        "unverified_non_shopify_stock",
        "baseline_active_catalog",
    ]
    for s_name in stratum_order:
        bucket = by_stratum.get(s_name, [])
        # Keep top half sorted by discount and shuffle tail for merchant diversity
        head = bucket[: max(10, len(bucket) // 3)]
        tail = bucket[max(10, len(bucket) // 3) :]
        rng.shuffle(tail)
        by_stratum[s_name] = head + tail

    cursors = {s: 0 for s in stratum_order}
    while len(selected) < limit:
        progressed = False
        for s_name in stratum_order:
            bucket = by_stratum.get(s_name, [])
            idx = cursors[s_name]
            while idx < len(bucket) and bucket[idx]["id"] in selected_ids:
                idx += 1
            if idx < len(bucket):
                add_offer(bucket[idx])
                cursors[s_name] = idx + 1
                progressed = True
                if len(selected) >= limit:
                    break
            else:
                cursors[s_name] = idx
        if not progressed:
            break

    return selected


def compact_offer_for_prompt(offer: dict) -> dict:
    """Strip internal-only keys and null noise so prompt tokens stay lean and exact."""
    payload = {
        "id": offer["id"],
        "store": offer["store"],
        "currency": offer["currency"],
        "name": offer["name"],
        "brand": offer["brand"],
        "url": offer["url"],
        "current_price": offer["current_price"],
        "original_price": offer["original_price"],
        "discount_pct": offer["discount_pct"],
        "category": offer["category"],
        "product_type": offer["product_type"],
        "sizes": offer["sizes"],
        "length_range_cm": [offer["length_min"], offer["length_max"]]
        if (offer["length_min"] is not None or offer["length_max"] is not None)
        else None,
        "variant_offer_count": offer["variant_offer_count"],
        "variant_in_stock_count": offer["variant_in_stock_count"],
        "variant_price_range": [offer["variant_price_min"], offer["variant_price_max"]]
        if offer["variant_offer_count"] > 0
        else None,
        "variant_discount_range_pct": [offer["variant_discount_min"], offer["variant_discount_max"]]
        if offer["variant_offer_count"] > 0
        else None,
        "variants_sample": offer["variants_sample"],
    }
    if offer.get("price_history"):
        payload["price_history"] = offer["price_history"]
    if offer.get("supplemental_raw_feed"):
        payload["supplemental_raw_feed"] = offer["supplemental_raw_feed"]
    return payload


def build_user_prompt(offer: dict) -> str:
    return USER_PROMPT_PREFIX + json.dumps(compact_offer_for_prompt(offer), ensure_ascii=False)


def build_batch_line(offer: dict, *, endpoint: str, model: str) -> dict:
    """Build a single OpenAI Batch API JSONL request object."""
    custom_id = f"offer-{offer['id']}"
    user_prompt = build_user_prompt(offer)
    if endpoint == "/v1/responses":
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": model,
                "input": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 900,
            },
        }
    if endpoint == "/v1/chat/completions":
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "max_completion_tokens": 900,
            },
        }
    raise ValueError(f"Unsupported batch endpoint: {endpoint!r}")


def gateway_request(
    gateway_base: str,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    raw_body: bytes | None = None,
    content_type: str | None = None,
    account: str | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict[str, str], bytes]:
    """Send an HTTP request through the local quotaswitch gateway."""
    base = assert_local_gateway(gateway_base)
    headers = {"Authorization": f"Bearer {GATEWAY_BEARER_TOKEN}"}
    if account:
        headers["X-Quotaswitch-Account"] = account

    data = raw_body
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type

    req = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hdrs = {k: v for k, v in resp.headers.items()}
            return resp.status, hdrs, resp.read()
    except urllib.error.HTTPError as exc:
        hdrs = {k: v for k, v in exc.headers.items()} if exc.headers else {}
        return exc.code, hdrs, exc.read()


def normalize_audit_payload(parsed: dict) -> dict:
    """Validate and normalize model JSON output."""
    verdict = str(parsed.get("verdict") or "warn").lower().strip()
    if verdict not in {"pass", "warn", "fail"}:
        verdict = "warn"
    try:
        severity = max(0, min(3, int(parsed.get("severity", 0))))
    except (TypeError, ValueError):
        severity = 1 if verdict != "pass" else 0
    raw_flags = parsed.get("flags") or []
    if not isinstance(raw_flags, list):
        raw_flags = []
    flags = [str(f).strip() for f in raw_flags if str(f).strip() in ISSUE_TAXONOMY]
    axes = sorted({ISSUE_TAXONOMY[f] for f in flags})
    return {
        "verdict": verdict,
        "severity": severity,
        "flags": flags,
        "axes": axes,
        "suggested_category": parsed.get("suggested_category"),
        "audience": parsed.get("audience"),
        "estimated_fair_msrp_range": parsed.get("estimated_fair_msrp_range"),
        "price_plausible": bool(parsed.get("price_plausible", True)),
        "summary": str(parsed.get("summary") or "").strip(),
    }


def extract_assistant_text_from_response_body(body: dict) -> str:
    """Extract JSON string from either `/v1/chat/completions` or `/v1/responses` body."""
    if "choices" in body and isinstance(body["choices"], list) and body["choices"]:
        msg = body["choices"][0].get("message") or {}
        return str(msg.get("content") or "")
    if "output_text" in body and isinstance(body["output_text"], str):
        return body["output_text"]
    for item in body.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("text"):
                    return str(part["text"])
    return ""


def summarize_offer_record(offer: dict) -> dict:
    return {
        "id": offer["id"],
        "store": offer["store"],
        "currency": offer["currency"],
        "name": offer["name"],
        "brand": offer["brand"],
        "url": offer["url"],
        "current_price": offer["current_price"],
        "original_price": offer["original_price"],
        "discount_pct": offer["discount_pct"],
        "category": offer["category"],
        "sizes": offer["sizes"],
        "variant_offer_count": offer["variant_offer_count"],
        "variant_price_spread": offer["variant_price_spread"],
        "stratum": offer["stratum"],
    }


def audit_single_offer_live(
    offer: dict,
    *,
    gateway_base: str,
    model: str,
    account: str | None = None,
    max_retries: int = 3,
) -> dict:
    """Audit one offer via `/v1/chat/completions` with bounded backoff."""
    payload = {
        "model": model,
        "reasoning_effort": "low",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(offer)},
        ],
        "max_completion_tokens": 600,
    }
    last_err = None
    for attempt in range(max_retries):
        t0 = time.monotonic()
        try:
            status, hdrs, raw = gateway_request(
                gateway_base,
                "POST",
                "/chat/completions",
                json_body=payload,
                account=account,
                timeout=75.0,
            )
            latency_ms = round((time.monotonic() - t0) * 1000.0, 1)
            hdr_lower = {k.lower(): v for k, v in hdrs.items()}
            acct_id = hdr_lower.get("x-quotaswitch-account-id")
            acct_label = hdr_lower.get("x-quotaswitch-account")
            provider = hdr_lower.get("x-quotaswitch-provider")

            if status == 200:
                body = json.loads(raw.decode("utf-8"))
                content = extract_assistant_text_from_response_body(body)
                parsed = json.loads(content)
                audit = normalize_audit_payload(parsed)
                return {
                    "ok": True,
                    "custom_id": f"offer-{offer['id']}",
                    "offer": summarize_offer_record(offer),
                    "audit": audit,
                    "gateway": {
                        "account_id": acct_id,
                        "account": acct_label,
                        "provider": provider,
                        "latency_ms": latency_ms,
                        "usage": body.get("usage"),
                    },
                }
            last_err = f"HTTP {status}: {raw.decode('utf-8', errors='replace')[:300]}"
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < max_retries:
            time.sleep(1.2 * (attempt + 1))

    return {
        "ok": False,
        "custom_id": f"offer-{offer['id']}",
        "offer": {
            "id": offer["id"],
            "store": offer["store"],
            "name": offer["name"],
            "url": offer["url"],
            "stratum": offer["stratum"],
        },
        "error": last_err,
    }


def summarize_results(results: list[dict], *, mode: str, extra_meta: dict | None = None) -> dict:
    """Aggregate verdicts, flags, axes, merchant breakdowns, and top findings."""
    ok_rows = [r for r in results if r.get("ok")]
    err_rows = [r for r in results if not r.get("ok")]
    verdict_counts = Counter(r["audit"]["verdict"] for r in ok_rows)
    severity_counts = Counter(str(r["audit"]["severity"]) for r in ok_rows)
    flag_counts = Counter(f for r in ok_rows for f in r["audit"]["flags"])
    axis_counts = Counter(a for r in ok_rows for a in r["audit"]["axes"])
    store_totals = Counter(r["offer"]["store"] for r in ok_rows)
    store_flagged = Counter(
        r["offer"]["store"] for r in ok_rows if r["audit"]["verdict"] in {"warn", "fail"}
    )
    account_ids = Counter(
        (r.get("gateway") or {}).get("account_id")
        for r in ok_rows
        if (r.get("gateway") or {}).get("account_id")
    )
    providers = Counter(
        (r.get("gateway") or {}).get("provider")
        for r in ok_rows
        if (r.get("gateway") or {}).get("provider")
    )
    latencies = sorted(
        (r.get("gateway") or {}).get("latency_ms")
        for r in ok_rows
        if (r.get("gateway") or {}).get("latency_ms") is not None
    )
    total_prompt_tokens = sum(
        ((r.get("gateway") or {}).get("usage") or {}).get("prompt_tokens", 0)
        or ((r.get("gateway") or {}).get("usage") or {}).get("input_tokens", 0)
        for r in ok_rows
    )
    total_completion_tokens = sum(
        ((r.get("gateway") or {}).get("usage") or {}).get("completion_tokens", 0)
        or ((r.get("gateway") or {}).get("usage") or {}).get("output_tokens", 0)
        for r in ok_rows
    )

    severe_examples = sorted(
        [r for r in ok_rows if r["audit"]["verdict"] in {"fail", "warn"} and r["audit"]["flags"]],
        key=lambda r: (
            -r["audit"]["severity"],
            0 if r["audit"]["verdict"] == "fail" else 1,
            -(r["offer"].get("discount_pct") or 0.0),
        ),
    )

    summary = {
        "mode": mode,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "offers_requested": len(results),
        "offers_succeeded": len(ok_rows),
        "offers_errored": len(err_rows),
        "merchants_covered": len(store_totals),
        "verdict_counts": dict(verdict_counts),
        "severity_counts": dict(severity_counts),
        "axis_counts": dict(axis_counts.most_common()),
        "flag_counts": dict(flag_counts.most_common()),
        "quotaswitch_account_ids": dict(account_ids),
        "quotaswitch_providers": dict(providers),
        "tokens": {
            "input_tokens": total_prompt_tokens,
            "output_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
        },
        "latency_ms": {
            "median": latencies[len(latencies) // 2] if latencies else None,
            "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None,
        },
        "merchant_breakdown": {
            store: {"audited": store_totals[store], "flagged": store_flagged[store]}
            for store in sorted(store_totals)
        },
        "top_findings": severe_examples[:40],
        "errors": err_rows[:10],
    }
    if extra_meta:
        summary.update(extra_meta)
    return summary


def upload_batch_jsonl(
    gateway_base: str,
    jsonl_path: Path,
    *,
    account: str,
) -> tuple[dict, dict[str, str]]:
    """Upload a `.jsonl` file via `POST /v1/files` (`purpose='batch'`) with account header."""
    boundary = f"----FreshPowderBatch{uuid.uuid4().hex}"
    file_bytes = jsonl_path.read_bytes()
    body = b"".join(
        [
            f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nbatch\r\n'.encode(
                "utf-8"
            ),
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{jsonl_path.name}"\r\nContent-Type: application/jsonl\r\n\r\n'
            ).encode("utf-8"),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    status, hdrs, raw = gateway_request(
        gateway_base,
        "POST",
        "/files",
        raw_body=body,
        content_type=f"multipart/form-data; boundary={boundary}",
        account=account,
        timeout=120.0,
    )
    if status != 200:
        raise RuntimeError(
            f"Batch file upload failed (HTTP {status}): {raw.decode('utf-8', errors='replace')[:400]}"
        )
    return json.loads(raw.decode("utf-8")), hdrs


def create_batch_job(
    gateway_base: str,
    *,
    input_file_id: str,
    endpoint: str,
    account: str,
    metadata: dict[str, str],
) -> tuple[dict, dict[str, str]]:
    """Submit a 24h Batch job via `POST /v1/batches` with the matching account header."""
    payload = {
        "input_file_id": input_file_id,
        "endpoint": endpoint,
        "completion_window": "24h",
        "metadata": metadata,
    }
    status, hdrs, raw = gateway_request(
        gateway_base,
        "POST",
        "/batches",
        json_body=payload,
        account=account,
        timeout=60.0,
    )
    if status != 200:
        raise RuntimeError(
            f"Batch job submission failed (HTTP {status}): {raw.decode('utf-8', errors='replace')[:400]}"
        )
    return json.loads(raw.decode("utf-8")), hdrs


def cmd_inspect(args: argparse.Namespace) -> int:
    db_path = resolve_snapshot_db(args.db_path)
    offers = load_active_offers(db_path)
    strata = Counter(o["stratum"] for o in offers)
    stores = Counter(o["store"] for o in offers)
    categories = Counter(o["category"] or "<null>" for o in offers)
    info = {
        "snapshot_db": str(db_path),
        "active_offers": len(offers),
        "active_merchants": len(stores),
        "with_variant_offers": sum(1 for o in offers if o["variant_offer_count"] > 0),
        "with_price_history": sum(1 for o in offers if o.get("price_history")),
        "with_supplemental_raw_feed": sum(1 for o in offers if o.get("supplemental_raw_feed")),
        "strata_counts": dict(strata.most_common()),
        "category_counts": dict(categories.most_common()),
        "merchant_counts": dict(stores.most_common()),
    }
    print(json.dumps(info, indent=2))
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    db_path = resolve_snapshot_db(args.db_path)
    runtime_dir: Path = args.runtime_dir.expanduser().resolve()
    live_dir = runtime_dir / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    offers = load_active_offers(db_path)
    sample = select_stratified_sample(offers, args.limit, seed=args.seed)

    started = time.monotonic()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_map = {
            pool.submit(
                audit_single_offer_live,
                offer,
                gateway_base=args.gateway_base,
                model=args.model,
                account=args.account,
            ): offer
            for offer in sample
        }
        for fut in as_completed(future_map):
            results.append(fut.result())

    results.sort(key=lambda r: r["offer"]["id"])
    wall_seconds = round(time.monotonic() - started, 2)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jsonl_path = live_dir / f"live-results-{stamp}.jsonl"
    summary_path = live_dir / f"live-summary-{stamp}.json"
    latest_path = live_dir / "latest-live-summary.json"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = summarize_results(
        results,
        mode="live",
        extra_meta={
            "snapshot_db": str(db_path),
            "model": args.model,
            "workers": args.workers,
            "wall_clock_seconds": wall_seconds,
            "results_jsonl": str(jsonl_path),
            "summary_json": str(summary_path),
            "sample_strata": dict(Counter(o["stratum"] for o in sample).most_common()),
        },
    )
    summary_text = json.dumps(summary, indent=2, ensure_ascii=False)
    summary_path.write_text(summary_text, encoding="utf-8")
    latest_path.write_text(summary_text, encoding="utf-8")

    # Print concise CLI summary without dumping 40 full findings to stdout unless small
    stdout_view = {k: v for k, v in summary.items() if k != "top_findings"}
    stdout_view["top_findings_preview_count"] = len(summary["top_findings"])
    print(json.dumps(stdout_view, indent=2, ensure_ascii=False))
    return 0 if summary["offers_succeeded"] > 0 else 1


def resolve_receipt_path(runtime_dir: Path, batch_id: str | None, receipt: Path | None) -> Path:
    if receipt is not None:
        return receipt.expanduser().resolve()
    batches_dir = runtime_dir.expanduser().resolve() / "batches"
    if batch_id:
        candidate = batches_dir / f"{batch_id}.json"
        if candidate.exists():
            return candidate
    latest = batches_dir / "latest-batch.json"
    if latest.exists():
        return latest
    raise FileNotFoundError(
        f"No saved batch receipt found in {batches_dir}; pass --batch-id and --account or --receipt."
    )


def cmd_batch_submit(args: argparse.Namespace) -> int:
    db_path = resolve_snapshot_db(args.db_path)
    runtime_dir: Path = args.runtime_dir.expanduser().resolve()
    batches_dir = runtime_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)

    offers = load_active_offers(db_path)
    slice_offers = select_stratified_sample(offers, args.limit, seed=args.seed)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jsonl_path = batches_dir / f"batch-input-{stamp}.jsonl"
    manifest_path = batches_dir / f"batch-manifest-{stamp}.json"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for offer in slice_offers:
            line = build_batch_line(offer, endpoint=args.endpoint, model=args.model)
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    manifest_by_custom_id = {
        f"offer-{o['id']}": summarize_offer_record(o)
        for o in slice_offers
    }
    manifest_path.write_text(json.dumps(manifest_by_custom_id, indent=2, ensure_ascii=False), encoding="utf-8")

    file_obj, file_hdrs = upload_batch_jsonl(args.gateway_base, jsonl_path, account=args.account)
    batch_obj, batch_hdrs = create_batch_job(
        args.gateway_base,
        input_file_id=file_obj["id"],
        endpoint=args.endpoint,
        account=args.account,
        metadata={
            "project": "fresh-powder",
            "purpose": "black-friday-catalog-audit",
            "offers": str(len(slice_offers)),
            "model": args.model,
        },
    )

    batch_id = batch_obj["id"]
    fh_lower = {k.lower(): v for k, v in file_hdrs.items()}
    bh_lower = {k.lower(): v for k, v in batch_hdrs.items()}
    receipt = {
        "batch_id": batch_id,
        "input_file_id": file_obj["id"],
        "account": args.account,
        "file_quotaswitch_account_id": fh_lower.get("x-quotaswitch-account-id"),
        "batch_quotaswitch_account_id": bh_lower.get("x-quotaswitch-account-id"),
        "quotaswitch_provider": bh_lower.get("x-quotaswitch-provider")
        or fh_lower.get("x-quotaswitch-provider"),
        "gateway_base": args.gateway_base,
        "endpoint": args.endpoint,
        "model": args.model,
        "completion_window": "24h",
        "item_count": len(slice_offers),
        "merchants_covered": len({o["store"] for o in slice_offers}),
        "strata_counts": dict(Counter(o["stratum"] for o in slice_offers).most_common()),
        "snapshot_db": str(db_path),
        "input_jsonl": str(jsonl_path),
        "manifest_json": str(manifest_path),
        "submitted_at_utc": datetime.now(timezone.utc).isoformat(),
        "batch": batch_obj,
    }
    receipt_path = batches_dir / f"{batch_id}.json"
    latest_path = batches_dir / "latest-batch.json"
    receipt_text = json.dumps(receipt, indent=2, ensure_ascii=False)
    receipt_path.write_text(receipt_text, encoding="utf-8")
    latest_path.write_text(receipt_text, encoding="utf-8")

    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "input_file_id": file_obj["id"],
                "account": args.account,
                "quotaswitch_account_id": receipt["batch_quotaswitch_account_id"],
                "quotaswitch_provider": receipt["quotaswitch_provider"],
                "endpoint": args.endpoint,
                "model": args.model,
                "item_count": len(slice_offers),
                "merchants_covered": receipt["merchants_covered"],
                "status": batch_obj.get("status"),
                "receipt_path": str(receipt_path),
            },
            indent=2,
        )
    )
    return 0


def poll_batch(gateway_base: str, batch_id: str, account: str) -> tuple[dict, dict[str, str]]:
    status, hdrs, raw = gateway_request(
        gateway_base, "GET", f"/batches/{batch_id}", account=account, timeout=30.0
    )
    if status != 200:
        raise RuntimeError(
            f"Polling batch {batch_id} failed (HTTP {status}): {raw.decode('utf-8', errors='replace')[:400]}"
        )
    return json.loads(raw.decode("utf-8")), hdrs


def cmd_batch_status(args: argparse.Namespace) -> int:
    receipt_data: dict = {}
    receipt_path: Path | None = None
    try:
        receipt_path = resolve_receipt_path(args.runtime_dir, args.batch_id, args.receipt)
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not args.batch_id:
            raise

    batch_id = args.batch_id or receipt_data["batch_id"]
    account = args.account or receipt_data.get("account") or DEFAULT_BATCH_ACCOUNT
    gateway_base = args.gateway_base or receipt_data.get("gateway_base") or DEFAULT_GATEWAY_BASE

    batch_obj, hdrs = poll_batch(gateway_base, batch_id, account)
    hdr_lower = {k.lower(): v for k, v in hdrs.items()}
    if receipt_path and receipt_data:
        receipt_data["batch"] = batch_obj
        receipt_data["last_polled_at_utc"] = datetime.now(timezone.utc).isoformat()
        receipt_path.write_text(json.dumps(receipt_data, indent=2, ensure_ascii=False), encoding="utf-8")
        latest = receipt_path.parent / "latest-batch.json"
        if latest.exists():
            latest_data = json.loads(latest.read_text(encoding="utf-8"))
            if latest_data.get("batch_id") == batch_id:
                latest.write_text(
                    json.dumps(receipt_data, indent=2, ensure_ascii=False), encoding="utf-8"
                )

    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "account": account,
                "quotaswitch_account_id": hdr_lower.get("x-quotaswitch-account-id"),
                "status": batch_obj.get("status"),
                "endpoint": batch_obj.get("endpoint"),
                "model": batch_obj.get("model") or receipt_data.get("model"),
                "request_counts": batch_obj.get("request_counts"),
                "errors": batch_obj.get("errors"),
                "output_file_id": batch_obj.get("output_file_id"),
                "error_file_id": batch_obj.get("error_file_id"),
                "receipt_path": str(receipt_path) if receipt_path else None,
            },
            indent=2,
        )
    )
    return 0


def download_gateway_file(gateway_base: str, file_id: str, account: str) -> bytes:
    status, _, raw = gateway_request(
        gateway_base, "GET", f"/files/{file_id}/content", account=account, timeout=120.0
    )
    if status != 200:
        raise RuntimeError(
            f"Downloading file {file_id} failed (HTTP {status}): {raw.decode('utf-8', errors='replace')[:400]}"
        )
    return raw


def parse_batch_output_jsonl(raw_jsonl: str, manifest: dict[str, dict], account_id: str | None) -> list[dict]:
    """Parse OpenAI Batch output `.jsonl` lines and join back to manifest offers."""
    parsed_rows: list[dict] = []
    for line in raw_jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        custom_id = item.get("custom_id", "")
        offer_meta = manifest.get(custom_id, {"id": custom_id, "store": "unknown", "name": custom_id})
        resp = item.get("response") or {}
        status_code = resp.get("status_code")
        body = resp.get("body") or {}
        if status_code == 200 and isinstance(body, dict):
            try:
                text = extract_assistant_text_from_response_body(body)
                audit = normalize_audit_payload(json.loads(text))
                parsed_rows.append(
                    {
                        "ok": True,
                        "custom_id": custom_id,
                        "offer": offer_meta,
                        "audit": audit,
                        "gateway": {
                            "account_id": account_id,
                            "provider": "openai-api-key",
                            "latency_ms": None,
                            "usage": body.get("usage"),
                        },
                    }
                )
                continue
            except Exception as exc:
                err_msg = f"Output parse error: {exc}"
        else:
            err_msg = json.dumps(item.get("error") or body.get("error") or {"status_code": status_code})
        parsed_rows.append(
            {
                "ok": False,
                "custom_id": custom_id,
                "offer": offer_meta,
                "error": err_msg,
            }
        )
    return parsed_rows


def cmd_batch_collect(args: argparse.Namespace) -> int:
    receipt_path = resolve_receipt_path(args.runtime_dir, args.batch_id, args.receipt)
    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    batch_id = args.batch_id or receipt_data["batch_id"]
    account = args.account or receipt_data.get("account") or DEFAULT_BATCH_ACCOUNT
    gateway_base = args.gateway_base or receipt_data.get("gateway_base") or DEFAULT_GATEWAY_BASE
    batches_dir = receipt_path.parent

    deadline = time.monotonic() + args.timeout_seconds
    while True:
        batch_obj, hdrs = poll_batch(gateway_base, batch_id, account)
        status = batch_obj.get("status")
        if status in {"completed", "failed", "expired", "cancelled"} or not args.wait:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(args.poll_seconds)

    hdr_lower = {k.lower(): v for k, v in hdrs.items()}
    acct_id = hdr_lower.get("x-quotaswitch-account-id") or receipt_data.get("batch_quotaswitch_account_id")
    receipt_data["batch"] = batch_obj
    receipt_data["last_polled_at_utc"] = datetime.now(timezone.utc).isoformat()
    receipt_path.write_text(json.dumps(receipt_data, indent=2, ensure_ascii=False), encoding="utf-8")

    output_file_id = batch_obj.get("output_file_id")
    error_file_id = batch_obj.get("error_file_id")
    out_info: dict = {
        "batch_id": batch_id,
        "account": account,
        "status": batch_obj.get("status"),
        "request_counts": batch_obj.get("request_counts"),
        "output_file_id": output_file_id,
        "error_file_id": error_file_id,
    }

    if error_file_id:
        err_bytes = download_gateway_file(gateway_base, error_file_id, account)
        err_path = batches_dir / f"errors-{batch_id}.jsonl"
        err_path.write_bytes(err_bytes)
        out_info["downloaded_error_jsonl"] = str(err_path)

    if not output_file_id:
        print(json.dumps(out_info, indent=2))
        return 0

    out_bytes = download_gateway_file(gateway_base, output_file_id, account)
    out_jsonl_path = batches_dir / f"output-{batch_id}.jsonl"
    out_jsonl_path.write_bytes(out_bytes)

    manifest_path = Path(receipt_data["manifest_json"]) if receipt_data.get("manifest_json") else None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if (manifest_path and manifest_path.exists()) else {}
    results = parse_batch_output_jsonl(out_bytes.decode("utf-8"), manifest, acct_id)
    collected_summary = summarize_results(
        results,
        mode="batch",
        extra_meta={
            "batch_id": batch_id,
            "account": account,
            "endpoint": receipt_data.get("endpoint"),
            "model": receipt_data.get("model"),
            "output_file_id": output_file_id,
            "output_jsonl": str(out_jsonl_path),
        },
    )
    collected_path = batches_dir / f"collected-{batch_id}.json"
    collected_path.write_text(json.dumps(collected_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    stdout_view = {k: v for k, v in collected_summary.items() if k != "top_findings"}
    stdout_view["collected_summary_json"] = str(collected_path)
    stdout_view["top_findings_preview_count"] = len(collected_summary["top_findings"])
    print(json.dumps(stdout_view, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-base", default=DEFAULT_GATEWAY_BASE, help="Local quotaswitch base URL")
    parser.add_argument("--db-path", type=Path, default=None, help="Read-only SQLite snapshot path")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=DEFAULT_RUNTIME_DIR,
        help="Gitignored directory for batch/live artifacts (default: build/openai-catalog-audit)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("inspect", help="Inspect local catalog snapshot and risk strata")

    p_live = sub.add_parser("live", help="Run 16-worker concurrent live catalog QA")
    p_live.add_argument("--limit", type=int, default=150, help="Number of stratified offers to audit")
    p_live.add_argument("--workers", type=int, default=16, help="Concurrent HTTP workers")
    p_live.add_argument("--model", default=DEFAULT_LIVE_MODEL, help="Model for live chat completions")
    p_live.add_argument("--account", default=None, help="Optional X-Quotaswitch-Account header")
    p_live.add_argument("--seed", type=int, default=42, help="Deterministic sampling seed")

    p_submit = sub.add_parser("batch-submit", help="Export JSONL, upload file, and submit 24h Batch job")
    p_submit.add_argument("--limit", type=int, default=750, help="Number of catalog offers to export")
    p_submit.add_argument("--endpoint", choices=["/v1/responses", "/v1/chat/completions"], default=DEFAULT_BATCH_ENDPOINT)
    p_submit.add_argument("--model", default=DEFAULT_BATCH_MODEL, help="Model inside batch JSONL")
    p_submit.add_argument("--account", default=DEFAULT_BATCH_ACCOUNT, help="X-Quotaswitch-Account header")
    p_submit.add_argument("--seed", type=int, default=101, help="Deterministic sampling seed")

    p_status = sub.add_parser("batch-status", help="Check status of submitted OpenAI Batch job")
    p_status.add_argument("--batch-id", default=None, help="Batch ID (defaults to latest receipt)")
    p_status.add_argument("--receipt", type=Path, default=None, help="Explicit receipt JSON path")
    p_status.add_argument("--account", default=None, help="Override X-Quotaswitch-Account header")

    p_collect = sub.add_parser("batch-collect", help="Download and summarize completed Batch output")
    p_collect.add_argument("--batch-id", default=None, help="Batch ID (defaults to latest receipt)")
    p_collect.add_argument("--receipt", type=Path, default=None, help="Explicit receipt JSON path")
    p_collect.add_argument("--account", default=None, help="Override X-Quotaswitch-Account header")
    p_collect.add_argument("--wait", action="store_true", help="Poll until terminal batch state")
    p_collect.add_argument("--poll-seconds", type=float, default=10.0)
    p_collect.add_argument("--timeout-seconds", type=float, default=600.0)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "inspect": cmd_inspect,
        "live": cmd_live,
        "batch-submit": cmd_batch_submit,
        "batch-status": cmd_batch_status,
        "batch-collect": cmd_batch_collect,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
