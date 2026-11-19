"""Independent product-endpoint checks against selected listing offers."""
import hashlib
import random
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

from deal_common.catalog.refresh import safe_url
from deal_common.http import DEFAULT_HEADERS, fetch_listing
from deal_common.parsers.beaver import detail_data as beaver_data
from deal_common.parsers.bikecloset import (
    API as BIKECLOSET_API,
)
from deal_common.parsers.bikecloset import (
    decode_offer as bikecloset_offer,
)
from deal_common.parsers.bikecloset import (
    manufacturer as bikecloset_manufacturer,
)
from deal_common.parsers.bikecloset import (
    money as bikecloset_money,
)
from deal_common.parsers.bikecloset import (
    new_condition as bikecloset_new,
)
from deal_common.parsers.bikecloset import (
    offer_destination as bikecloset_destination,
)
from deal_common.parsers.racycles import detail_data as racycles_data
from deal_common.parsers.shopify import (
    is_contender_generated_variant,
    pricing_notice,
    shipping_notice,
)
from deal_common.parsers.taylor import detail_data as taylor_data
from deal_common.parsers.taylor import offer_destination as taylor_destination


def _decimal_equal(value, expected, *, scale: int = 1) -> bool:
    try:
        return not isinstance(value, bool) and Decimal(str(value)) / scale == Decimal(str(expected))
    except (InvalidOperation, TypeError, ValueError):
        return False


def select_sample(cards: list[dict], seed: int = 20261102) -> list[dict]:
    top = cards[:10]
    rest = sorted(cards[10:], key=lambda c: (c["store"], c["url"]))
    return top + random.Random(seed).sample(rest, min(20, len(rest)))


def validate_product(card: dict, raw: dict, currency: str | None) -> dict:
    offer = card["offer"]
    variants = raw.get("variants")
    if not isinstance(variants, list):
        return {"status": "inconclusive", "reason": "Malformed product response"}
    matches = [v for v in variants if isinstance(v, dict) and str(v.get("id")) == offer["variant_key"]]
    if len(matches) != 1:
        return {"status": "inconclusive", "reason": "Variant absent or ambiguous; endpoint may truncate variants"}
    actual = matches[0]
    option_definitions = raw.get("options") or []
    price_positions = {i for i, definition in enumerate(option_definitions)
                       if isinstance(definition, dict) and re.search(
                           r"\b(?:msrp|retail|price)\b", str(definition.get("name", "")), re.IGNORECASE)}
    raw_options = actual.get("options", [])
    actual_options = " / ".join(str(v) for i, v in enumerate(raw_options)
                                if i not in price_positions and v != "Default Title")
    notice = shipping_notice(raw, actual, offer["url"])
    price_note = pricing_notice(raw, offer["url"])
    checks = {
        "identity": str(raw.get("title", "")).strip() == card["name"].strip(),
        "currency": currency == card.get("currency", "USD"),
        "price": _decimal_equal(actual.get("price"), offer["current_price"], scale=100),
        "reference": _decimal_equal(actual.get("compare_at_price"), offer["original_price"], scale=100),
        "options": actual_options == offer.get("options", ""),
        "stock": actual.get("available") is True and not notice,
    }
    if is_contender_generated_variant(actual, offer["url"]):
        checks["ordinary_variant"] = False
    if price_note or offer.get("price_note", ""):
        checks["price_note"] = price_note == offer.get("price_note", "")
    return {"status": "match" if all(checks.values()) else "mismatch", "checks": checks,
            "merchant": {"title": raw.get("title"), "variant": str(actual["id"]), "sku": actual.get("sku"),
                         "price_minor": actual.get("price"), "reference_minor": actual.get("compare_at_price"),
                         "currency": currency, "options": actual_options, "raw_options": raw_options,
                         "option_definitions": option_definitions, "available": actual.get("available"),
                         "shipping_notice": notice,
                         **({"price_note": price_note} if price_note or offer.get("price_note", "") else {})}}


async def audit_sample(cards: list[dict], allowed_domains: dict[str, str], *, delay: float = 5.0,
                       transport=None) -> list[dict]:
    results = []
    sampled_at = datetime.now(UTC)
    expired = False

    def within_window(now):
        nonlocal expired
        expired = expired or not 0 <= (now - sampled_at).total_seconds() <= 15 * 60
        return not expired

    def record(row):
        finished_at = datetime.now(UTC)
        timely = within_window(finished_at)
        row.update(sampled_at=sampled_at.isoformat(), checked_at=finished_at.isoformat(),
                   within_audit_window=timely)
        if not timely:
            row.update(status="inconclusive", reason="Outside the 15-minute audit observation window")
        results.append(row)

    currencies = {}
    blocked = set()
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=20, transport=transport,
                                 follow_redirects=False) as client:
        async def fetch(url):
            if not within_window(datetime.now(UTC)):
                raise TimeoutError("Audit observation window expired")
            return await fetch_listing(client, url, delay)

        for card in cards:
            offer = card["offer"]
            parts = urlsplit(offer["url"])
            domain = allowed_domains.get(card["store"])
            row = {"name": card["name"], "store": card["store"], "offer": offer,
                   "check_started_at": datetime.now(UTC).isoformat()}
            if not within_window(datetime.now(UTC)):
                record({**row, "status": "inconclusive", "reason": "Audit observation window expired"})
                continue
            taylor_product = taylor_destination(offer["url"], offer["variant_key"])
            bikecloset_product = bikecloset_destination(offer["url"], offer["variant_key"])
            shopify_product = ("/products/" in parts.path
                               and parse_qs(parts.query).get("variant") == [offer["variant_key"]])
            if not domain or not safe_url(offer["url"], domain) or not (
                    taylor_product or bikecloset_product or shopify_product):
                record({**row, "status": "inconclusive", "reason": "Unsupported or unsafe product destination"})
                continue
            origin = f"{parts.scheme}://{parts.netloc}"
            prefix = parts.path.split("/products/", 1)[0]
            currency_url = origin + prefix + "/cart.js?" + urlencode({"currency": card.get("currency", "USD")})
            endpoint = urlunsplit((parts.scheme, parts.netloc, parts.path + ".js",
                                   urlencode({"currency": card.get("currency", "USD")}), ""))
            row["evidence_url"] = offer["url"] if taylor_product else endpoint
            if origin in blocked:
                record({**row, "status": "inconclusive", "reason": "Host blocked or rate-limited earlier in this audit"})
                continue
            try:
                if bikecloset_product:
                    parent_id, variant_id = offer["variant_key"].split("|")[0].split(":")
                    row["evidence_url"] = BIKECLOSET_API + "/" + variant_id
                    response = await fetch(row["evidence_url"])
                    parent_url = BIKECLOSET_API + "/" + parent_id
                    parent = await fetch(parent_url)
                    row.update(response_sha256=hashlib.sha256(response.content).hexdigest(),
                               parent_evidence_url=parent_url,
                               parent_response_sha256=hashlib.sha256(parent.content).hexdigest())
                    row.update(validate_bikecloset_product(card, parent.json(), response.json()))
                elif taylor_product:
                    response = await fetch(offer["url"])
                    row["response_sha256"] = hashlib.sha256(response.content).hexdigest()
                    row.update(validate_taylor_product(card, taylor_data(response.text, offer["url"])))
                else:
                    if origin not in currencies:
                        response = await fetch(currency_url)
                        currencies[origin] = response.json().get("currency")
                    if currencies[origin] != card.get("currency", "USD"):
                        record({**row, "status": "inconclusive", "reason": "Presentment currency not confirmed"})
                        continue
                    response = await fetch(endpoint)
                    raw = response.json()
                    if not isinstance(raw, dict):
                        raise TypeError("Malformed product")
                    row["response_sha256"] = hashlib.sha256(response.content).hexdigest()
                    row.update(validate_product(card, raw, currencies[origin]))
                    if parts.hostname in {"beavertools.com", "racycles.com"}:
                        page = await fetch(offer["url"])
                        detail = (validate_beaver_product(card, beaver_data(page.text, offer["url"]))
                                  if parts.hostname == "beavertools.com"
                                  else validate_racycles_product(card, racycles_data(page.text, offer["url"], raw)))
                        row.update(product_evidence_url=offer["url"],
                                   product_response_sha256=hashlib.sha256(page.content).hexdigest())
                        row.setdefault("checks", {"product_endpoint": False})
                        row["checks"].update({"purchase_" + key: value for key, value in detail["checks"].items()})
                        if row["status"] != "inconclusive":
                            row["status"] = "match" if all(row["checks"].values()) else "mismatch"
                        row["purchase_panel"] = detail["merchant"]
            except TimeoutError:
                row.update(status="inconclusive", reason="Audit observation window expired")
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in {403, 429}:
                    blocked.add(origin)
                row.update(status="inconclusive", reason=f"HTTP {status}")
            except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
                row.update(status="inconclusive", reason="Response could not be verified")
            record(row)
    return results


def validate_bikecloset_product(card: dict, parent: dict, variant: dict) -> dict:
    expected = card["offer"]
    actual = bikecloset_offer(parent, variant)
    checks = {
        "identity": unescape(parent["name"]) == card["name"],
        "currency": variant["prices"]["currency_code"] == card.get("currency", "USD"),
        "price": bikecloset_money(variant["prices"], "price") == expected["current_price"],
        "reference": bikecloset_money(variant["prices"], "regular_price") == expected["original_price"],
        "condition": bikecloset_new(parent),
        "stock": actual is not None and actual.availability == "in_stock",
        "destination": actual is not None and actual.url == expected["url"] and actual.variant_key == expected["variant_key"],
        "options": actual is not None and actual.options == expected.get("options") and actual.size == expected.get("size"),
        "shipping_note": actual is not None and actual.availability_note == expected.get("availability_note", ""),
    }
    return {"status": "match" if all(checks.values()) else "mismatch", "checks": checks,
            "merchant": {"parent_id": parent["id"], "variant_id": variant["id"], "sku": variant.get("sku"),
                         "brand": bikecloset_manufacturer(parent),
                         "price": bikecloset_money(variant["prices"], "price"),
                         "reference": bikecloset_money(variant["prices"], "regular_price"),
                         "currency": variant["prices"]["currency_code"], "is_in_stock": variant["is_in_stock"],
                         "is_on_backorder": variant["is_on_backorder"]}}


def validate_racycles_product(card: dict, product: dict) -> dict:
    expected = card["offer"]
    actual = product["variants"].get(expected["variant_key"])
    if actual is None:
        return {"checks": {"identity": False}, "merchant": {"reason": "Selected variant absent"}}
    checks = {
        "identity": product["name"] == card["name"] and product["selected_key"] == expected["variant_key"],
        "destination": urlsplit(actual["url"]).path == urlsplit(expected["url"]).path,
        "currency": product["currency"] == card.get("currency", "USD"),
        "price": actual["price"] == expected["current_price"],
        "reference": actual["reference"] == expected["original_price"],
        "options": actual["options"] == expected.get("options", ""),
        "size": not expected.get("size") or actual["size"] == expected["size"],
        "stock": actual["in_stock"],
        "shipping_note": actual["shipping_note"] == expected.get("availability_note", ""),
    }
    return {"status": "match" if all(checks.values()) else "mismatch", "checks": checks,
            "merchant": {key: actual[key] for key in ("sku", "price", "reference", "options", "size",
                                                      "stock_state", "location_stock", "virtual_warehouse", "shipping_note")}}


def validate_beaver_product(card: dict, product: dict) -> dict:
    expected = card["offer"]
    checks = {
        "identity": product["name"] == card["name"] and product["variant_key"] == expected["variant_key"],
        "destination": urlsplit(product["url"]).path == urlsplit(expected["url"]).path,
        "currency": product["currency"] == card.get("currency", "USD"),
        "price": product["price"] == expected["current_price"],
        "reference": product["reference"] == expected["original_price"],
        "options": product["options"] == expected.get("options", "") and not expected.get("size"),
        "stock": product["in_stock"],
        "shipping_note": product["shipping_note"] == expected.get("availability_note", ""),
    }
    return {"status": "match" if all(checks.values()) else "mismatch", "checks": checks,
            "merchant": {key: product[key] for key in ("sku", "product_id", "variant_key", "price",
                                                       "reference", "currency", "stock_label", "shipping_note")}}


def validate_taylor_product(card: dict, product: dict) -> dict:
    expected = card["offer"]
    sku, _, query = expected["variant_key"].partition("|")
    actual = product["skus"].get(sku)
    if actual is None:
        return {"status": "inconclusive", "reason": "SKU absent from Taylor product page"}
    try:
        quantity = Decimal(str(actual.get("qty")))
        has_stock = quantity.is_finite() and quantity > 0
    except (InvalidOperation, ValueError, TypeError):
        has_stock = False
    checks = {
        "identity": product["name"] == card["name"],
        "destination": actual["query"] == query and taylor_destination(expected["url"], expected["variant_key"]),
        "currency": True,
        "price": _decimal_equal(actual.get("price"), expected["current_price"]),
        "reference": _decimal_equal(actual.get("regular_price"), expected["original_price"]),
        "options": actual["options"] == expected.get("options"),
        "stock": actual.get("available") is True and actual.get("ib") is False and has_stock,
        "condition": actual.get("level_name") == "Regular",
    }
    return {"status": "match" if all(checks.values()) else "mismatch", "checks": checks,
            "merchant": {"sku": actual["vendor_sku"], "selector_sku": sku, "selection": actual["selection"],
                         "price": actual.get("price"), "reference": actual.get("regular_price"),
                         "stock": actual.get("qty"), "options": actual["options"], "currency": "USD"}}


def audit_summary(results):
    counts = {status: sum(row["status"] == status for row in results) for status in ("match", "mismatch", "inconclusive")}
    return {"sample_size": len(results), **counts,
            "verified_match_percent": round(100 * counts["match"] / len(results), 2) if results else None,
            "scope": "Anonymous merchant product endpoint and currency; not checkout or historical-reference validation",
            "results": results}
