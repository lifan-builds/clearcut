"""Choose one eligible offer per listing before counts, order, and pagination."""
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import ceil

from deal_common.catalog.definition import manufacturer_name
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


def _dedupe_key(card: dict, valid_size: Callable[[str | None], str | None]) -> tuple[str, str, str, str, str]:
    offer = card["offer"]
    return (
        _norm_key(card.get("brand")),
        _norm_key(card.get("name")),
        _norm_key(card.get("category")),
        size_key(valid_size(offer.get("size"))),
        _norm_key(offer.get("options")),
    )


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
) -> dict:
    now = now or datetime.now(UTC)
    requested_brand = filters.get("brand")
    if requested_brand and brand_aliases:
        requested_brand = manufacturer_name(requested_brand, brand_aliases=brand_aliases)
    opt_in = set(opt_in_categories)
    requested_category = filters.get("category") or ""
    size_re = re.compile(size_value_pattern, re.IGNORECASE) if size_value_pattern else None

    def valid_size(raw: str | None) -> str | None:
        if not raw:
            return None
        stripped = raw.strip()
        if not stripped:
            return None
        if size_re and not size_re.fullmatch(stripped):
            return None
        return raw

    cards = []
    facets = {key: set() for key in ("brand", "store", "size", "platform")}
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
            brand = (manufacturer_name(product["brand"], brand_aliases=brand_aliases)
                     if brand_aliases else product["brand"])
            facets["brand"].add(brand)
            facets["store"].add(source["name"])
            if product["platform"]:
                facets["platform"].add(product["platform"])
            for offer in valid_offers:
                sz = valid_size(offer["size"])
                if sz and offer["availability"] == "in_stock":
                    facets["size"].add(sz)
            if requested_brand and requested_brand != brand:
                continue
            if any(filters.get(key) and filters[key] != product[key]
                   for key in ("category", "format", "platform")):
                continue
            if filters.get("store") and filters["store"] != source["name"]:
                continue
            if filters.get("q"):
                needle = filters["q"].casefold()
                if not any(needle in (product["name"] + " " + b).casefold() for b in (product["brand"], brand)):
                    continue
            eligible = []
            for offer in valid_offers:
                if filters.get("size"):
                    sz = valid_size(offer["size"])
                    if offer["availability"] != "in_stock" or not sz or size_key(sz) != size_key(filters["size"]):
                        continue
                if filters.get("stock") and offer["availability"] != "in_stock":
                    continue
                pct = discount(offer["current_price"], offer["original_price"])
                if (pct < filters["min_discount"]
                        or offer["original_price"] < filters["min_reference"]
                        or (filters["max_price"] and offer["current_price"] > filters["max_price"])):
                    continue
                eligible.append({**offer, "discount": pct,
                                 "savings": round(offer["original_price"] - offer["current_price"], 2)})
            if not eligible:
                continue
            best = min(eligible, key=lambda o: (-o["discount"], -o["savings"], o["current_price"], o["variant_key"]))
            history = best.get("history") or {}
            signal = price_signal(best["current_price"], history.get("previous_price"), history.get("previous_at"),
                                  history.get("low"), history.get("tracked_since"), source["successful_at"])
            cards.append({**product, "brand": brand, "offer": best, "store": source["name"],
                          "observed_at": source["successful_at"], "matching_variants": len(eligible),
                          "price_signal": signal,
                          "price_drop": round(history["previous_price"] - best["current_price"], 2) if signal else 0})
    size_labels = {}
    for label in sorted(facets["size"], key=lambda value: (len(value), value.casefold(), value)):
        size_labels.setdefault(size_key(label), label)
    facets["size"] = set(size_labels.values())
    cards.sort(key=lambda p: (SIGNAL_RANK[p["price_signal"]] if filters.get("sort") == "dropped" else 0,
                              -p["offer"]["discount"], -p["offer"]["savings"], p["offer"]["current_price"],
                              p["name"], p["store"], p["url"]))
    if collapse_duplicates and not filters.get("compare") and not filters.get("store"):
        collapsed: list[dict] = []
        seen: dict[tuple[str, str, str, str, str], tuple[dict, set[str]]] = {}
        for card in cards:
            key = _dedupe_key(card, valid_size)
            if key not in seen:
                others: set[str] = set()
                seen[key] = (card, others)
                collapsed.append(card)
            else:
                winner, others = seen[key]
                if card["store"] != winner["store"]:
                    others.add(card["store"])
        for winner, others in seen.values():
            if others:
                winner["other_store_names"] = tuple(sorted(others))
        cards = collapsed
    total = len(cards)
    pages = max(1, ceil(total / PAGE_SIZE))
    page = min(filters["page"], pages)
    return {"cards": cards[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], "total": total,
            "page": page, "pages": pages, "fresh_sources": fresh_sources,
            "facets": {key: sorted(values) for key, values in facets.items()},
            "dropped": sum(card["price_signal"] is not None for card in cards),
            "top_discount": cards[0]["offer"]["discount"] if cards else 0}
