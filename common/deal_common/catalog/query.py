"""Choose one eligible offer per listing before counts, order, and pagination."""
import hashlib
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import ceil

from deal_common.catalog.definition import (
    brand_id,
    clean_brand,
    compile_brand_aliases,
    display_brand,
    manufacturer_name,
)
from deal_common.offers import (
    discount,
    is_addon_variant,
    is_price_collapse_addon,
    plausible_offer,
    price_signal,
    size_key,
)

PAGE_SIZE = 24
FRESHNESS_HOURS = 36
SORTS = {"discount": "deepest % off", "dropped": "dropped in 24h"}
SIGNAL_RANK = {"new_low": 0, "dropped": 1, None: 2}


def is_fresh(timestamp: str | None, now: datetime) -> bool:
    if not timestamp:
        return False
    try:
        age = now - datetime.fromisoformat(timestamp)
        return timedelta(0) <= age <= timedelta(hours=FRESHNESS_HOURS)
    except (ValueError, TypeError):
        return False


def _norm_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def default_exact_offer_key(
    product: dict,
    offer: dict,
    brand_key: str,
    valid_size_value: str | None,
) -> tuple[str, ...]:
    return (
        "default",
        brand_key,
        _norm_key(product.get("name")),
        _norm_key(product.get("category")),
        size_key(valid_size_value),
        _norm_key(offer.get("options")),
        _norm_key(product.get("format")),
    )


def group_token(key: tuple[str, ...] | None) -> str | None:
    if key is None:
        return None
    return hashlib.sha256("\x1f".join(key).encode("utf-8")).hexdigest()[:16]


def query(
    sources: list[dict],
    filters: dict,
    *,
    now: datetime | None = None,
    brand_aliases: tuple[tuple[str, str], ...] = (),
    opt_in_categories: tuple[str, ...] = (),
    size_value_pattern: str = "",
    collapse_duplicates: bool = False,
    reconcile_product: Callable[[dict], dict | None] | None = None,
    exact_offer_key: Callable[[dict, dict, str, str | None], tuple[str, ...] | None] | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    compiled_aliases, preferred_brands = compile_brand_aliases(brand_aliases)

    def _raw_brand(value: str | None) -> str:
        if brand_aliases:
            return manufacturer_name(value, brand_aliases=brand_aliases)
        return clean_brand(value)

    requested_brand = filters.get("brand")
    requested_brand_id = (
        brand_id(_raw_brand(requested_brand), compiled_aliases=compiled_aliases)
        if requested_brand else ""
    )
    opt_in = set(opt_in_categories)
    requested_category = filters.get("category") or ""
    requested_group = (filters.get("group") or "").strip()
    size_re = re.compile(size_value_pattern, re.IGNORECASE) if size_value_pattern else None
    key_fn = exact_offer_key or default_exact_offer_key

    def valid_size(raw: str | None) -> str | None:
        if not raw:
            return None
        stripped = raw.strip()
        if not stripped:
            return None
        if size_re and not size_re.fullmatch(stripped):
            return None
        return raw

    cards: list[dict] = []
    card_brand_ids: list[str] = []
    card_group_ids: list[str | None] = []
    facets = {key: set() for key in ("brand", "store", "size", "platform")}
    observed_brands: dict[str, set[str]] = defaultdict(set)
    group_stores: dict[str, set[str]] = defaultdict(set)
    fresh_sources = 0
    for source in sources:
        if not is_fresh(source["successful_at"], now):
            continue
        fresh_sources += 1
        for raw_product in source["products"]:
            product = reconcile_product(raw_product) if reconcile_product else raw_product
            if product is None:
                continue
            if opt_in and not requested_category and product["category"] in opt_in:
                continue
            plausible_offers = [
                offer for offer in product["offers"]
                if offer["availability"] in {"in_stock", "unknown"}
                and plausible_offer(offer["current_price"], offer["original_price"])
                and not is_addon_variant(offer.get("options"))
            ]
            max_reference = max((o["original_price"] for o in plausible_offers), default=0.0)
            valid_offers = [o for o in plausible_offers
                            if not is_price_collapse_addon(o["original_price"], max_reference)]
            if not valid_offers:
                continue
            raw_brand = _raw_brand(product["brand"])
            b_id = brand_id(raw_brand, compiled_aliases=compiled_aliases)
            observed_brands[b_id].add(raw_brand)
            facets["brand"].add(b_id)
            facets["store"].add(source["name"])
            if product["platform"]:
                facets["platform"].add(product["platform"])
            for offer in valid_offers:
                sz = valid_size(offer["size"])
                if sz and offer["availability"] == "in_stock":
                    facets["size"].add(sz)
            if requested_brand_id and requested_brand_id != b_id:
                continue
            if any(filters.get(key) and filters[key] != product[key]
                   for key in ("category", "format", "platform")):
                continue
            if filters.get("store") and filters["store"] != source["name"]:
                continue
            if filters.get("q"):
                needle = filters["q"].casefold()
                if not any(needle in (product["name"] + " " + b).casefold() for b in (product["brand"], raw_brand)):
                    continue
            eligible: list[tuple[dict, str | None]] = []
            for offer in valid_offers:
                sz = valid_size(offer["size"])
                if filters.get("size") and (
                    offer["availability"] != "in_stock" or not sz or size_key(sz) != size_key(filters["size"])
                ):
                    continue
                if filters.get("stock") and offer["availability"] != "in_stock":
                    continue
                pct = discount(offer["current_price"], offer["original_price"])
                if (pct < filters["min_discount"]
                        or offer["original_price"] < filters["min_reference"]
                        or (filters["max_price"] and offer["current_price"] > filters["max_price"])):
                    continue
                gid = group_token(key_fn(product, offer, b_id, sz))
                if gid:
                    group_stores[gid].add(source["name"])
                if requested_group and gid != requested_group:
                    continue
                eligible.append((
                    {**offer, "discount": pct, "savings": round(offer["original_price"] - offer["current_price"], 2)},
                    gid,
                ))
            if not eligible:
                continue
            best, best_gid = min(
                eligible,
                key=lambda item: (-item[0]["discount"], -item[0]["savings"], item[0]["current_price"], item[0]["variant_key"]),
            )
            history = best.get("history") or {}
            signal = price_signal(best["current_price"], history.get("previous_price"), history.get("previous_at"),
                                  history.get("low"), history.get("tracked_since"), source["successful_at"])
            cards.append({**product, "brand": raw_brand, "offer": best, "store": source["name"],
                          "observed_at": source["successful_at"], "matching_variants": len(eligible),
                          "price_signal": signal,
                          "price_drop": round(history["previous_price"] - best["current_price"], 2) if signal else 0})
            card_brand_ids.append(b_id)
            card_group_ids.append(best_gid)

    brand_labels = {
        bid: display_brand(bid, labels, preferred_brands)
        for bid, labels in observed_brands.items()
    }
    group_by_card_id: dict[int, str | None] = {}
    for card, bid, gid in zip(cards, card_brand_ids, card_group_ids, strict=True):
        card["brand"] = brand_labels[bid]
        group_by_card_id[id(card)] = gid
    facets["brand"] = {brand_labels[bid] for bid in facets["brand"]}

    size_labels = {}
    for label in sorted(facets["size"], key=lambda value: (len(value), value.casefold(), value)):
        size_labels.setdefault(size_key(label), label)
    facets["size"] = set(size_labels.values())
    cards.sort(key=lambda p: (SIGNAL_RANK[p["price_signal"]] if filters.get("sort") == "dropped" else 0,
                              -p["offer"]["discount"], -p["offer"]["savings"], p["offer"]["current_price"],
                              p["name"], p["store"], p["url"]))

    for card in cards:
        gid = group_by_card_id.get(id(card))
        if gid:
            others = sorted(s for s in group_stores[gid] if s != card["store"])
            if others:
                card["other_store_names"] = tuple(others)
                card["exact_group_id"] = gid

    if collapse_duplicates and not filters.get("compare") and not filters.get("store") and not requested_group:
        collapsed: list[dict] = []
        seen_groups: set[str] = set()
        for card in cards:
            gid = group_by_card_id.get(id(card))
            if gid is None:
                collapsed.append(card)
            elif gid not in seen_groups:
                seen_groups.add(gid)
                collapsed.append(card)
        cards = collapsed
    total = len(cards)
    pages = max(1, ceil(total / PAGE_SIZE))
    page = min(filters["page"], pages)
    return {"cards": cards[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], "total": total,
            "page": page, "pages": pages, "fresh_sources": fresh_sources,
            "facets": {key: sorted(values) for key, values in facets.items()},
            "dropped": sum(card["price_signal"] is not None for card in cards),
            "top_discount": cards[0]["offer"]["discount"] if cards else 0}
