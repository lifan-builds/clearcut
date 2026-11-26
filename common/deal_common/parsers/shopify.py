from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit

from bs4 import BeautifulSoup

from deal_common.models import Product
from deal_common.offers import (
    MAX_REFERENCE_PRICE,
    VariantOffer,
    is_addon_variant,
    is_price_collapse_addon,
    plausible_offer,
)
from deal_common.parsers.base import BaseParser

log = logging.getLogger(__name__)


def _tags(item: dict) -> list[str]:
    raw = item.get("tags") or []
    items = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return [tag.strip() for tag in items if isinstance(tag, str)]


def is_contender_generated_variant(variant: dict, url: str) -> bool:
    """Exclude observed generated choices without aliasing their prices or stock."""
    if urlparse(url).hostname not in {"contenderbicycles.com", "www.contenderbicycles.com"}:
        return False
    values = [variant.get(key) for key in ("title", "option1", "option2", "option3")]
    if isinstance(variant.get("options"), list):
        values.extend(variant["options"])
    return any(isinstance(value, str) and re.search(r"(?<!\w)#MWS Options [0-9]+(?!\w)", value)
               for value in values)


def pricing_notice(item: dict, url: str) -> str:
    """Retain the observed merchant caveat without changing price or stock."""
    if (urlparse(url).hostname in {"contenderbicycles.com", "www.contenderbicycles.com"}
            and "contact-for-current-price" in _tags(item)):
        return "Retailer asks you to contact them for current pricing."
    return ""


def _harvey_shipping_notice(item: dict, variant: dict) -> str:
    vendor = unicodedata.normalize("NFKC", str(item.get("vendor") or "")).strip()
    if not re.match(r"ESD\s*:", vendor, re.IGNORECASE):
        return ""
    estimate = vendor.split(":", 1)[1].strip()
    if not estimate or len(estimate) > 160:
        return "Retailer lists an estimated shipping date; timing needs confirmation."
    note = f"Retailer shipping estimate: {estimate}"
    scoped = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", estimate)
    color_positions = [i for i, opt in enumerate(item.get("options") or [])
                       if isinstance(opt, dict) and str(opt.get("name", "")).strip().casefold() in {"color", "colour"}]
    if not scoped or len(color_positions) != 1:
        return note
    position = color_positions[0]

    def color(candidate: dict) -> str:
        value = candidate.get(f"option{position + 1}")
        if value is None:
            values = candidate.get("options") or []
            value = values[position] if isinstance(values, list) and len(values) > position else None
        return unicodedata.normalize("NFKC", str(value or "")).strip()

    scope = scoped[2].strip()
    pattern = re.compile(r"(?<!\w)" + re.escape(scope) + r"(?!\w)", re.IGNORECASE)
    variants = item.get("variants") or []
    if not scope or not any(isinstance(v, dict) and pattern.search(color(v)) for v in variants):
        return note
    selected = color(variant)
    return note if not selected or pattern.search(selected) else ""


def shipping_notice(item: dict, variant: dict, url: str) -> str:
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    if host in {"cyberweld.com", "store.cyberweld.com"}:
        if any(tag.casefold() == "auto_outofstock" for tag in _tags(item)):
            return "Retailer marks this product out of stock."
    elif host == "bakersgas.com":
        for field in ("body_html", "description"):
            body = BeautifulSoup(str(item.get(field) or ""), "html.parser")
            for element in body(["script", "style"]):
                element.decompose()
            notice = re.search(
                r"\b(?:machine|welder|product|item)\s+is\s+(?:currently\s+)?on\s+back[- ]?order\b[^.!?]{0,120}[.!?]?",
                " ".join(body.stripped_strings),
                re.IGNORECASE,
            )
            if notice:
                return "Retailer shipping notice: " + notice[0].strip()
    elif host == "harveywoodworking.com":
        notice = _harvey_shipping_notice(item, variant)
        if notice:
            return notice
    if any(re.fullmatch(r"pre[- ]?order(?:ed|s)?|back[- ]?order(?:ed|s)?", tag, re.IGNORECASE)
           for tag in _tags(item)):
        return "Retailer marks this offer as pre-order or backorder."
    values = [item.get("title"), variant.get("title"), variant.get("option1"), variant.get("option2"), variant.get("option3")]
    if isinstance(variant.get("options"), list):
        values.extend(variant["options"])
    if any(isinstance(value, str) and re.search(r"\b(?:pre[- ]?order(?:ed|s)?|back[- ]?order(?:ed|s)?)\b", value, re.IGNORECASE)
           for value in values):
        return "Retailer marks this offer as pre-order or backorder."
    return ""


def _products_json(body: str) -> list[dict]:
    data = json.loads(body)
    products = data.get("products") if isinstance(data, dict) else None
    if type(products) is not list or any(type(item) is not dict for item in products):
        raise ValueError("Shopify feed is missing or contains malformed products")
    return products


def product_type_from_item(item: dict) -> str:
    """Retain the retailer's readable taxonomy when its type is an internal code."""
    tags = _tags(item)
    taxonomy = {}
    for tag in tags:
        key, separator, value = tag.partition(":")
        if separator and key in {"BreadcrumbClass", "BreadcrumbSubBMC"}:
            taxonomy[key] = value.strip()
    if taxonomy.get("BreadcrumbClass"):
        result = " | ".join(taxonomy[key] for key in ("BreadcrumbClass", "BreadcrumbSubBMC") if taxonomy.get(key))
    else:
        explicit_tags = {tag for tag in tags if tag.startswith("Gear: ")}
        if len(explicit_tags) == 1:
            result = explicit_tags.pop().removeprefix("Gear: ")
        elif "Skis:" in tags or "Skis" in tags:
            result = "Skis"
        else:
            result = str(item.get("product_type") or "")
    if any(tag.lower() in {"kids", "junior", "youth", "boys", "girls", "gender/age:kids"} for tag in tags):
        result += " | Kids"
    return result


class ShopifyParser(BaseParser):
    """Parser for Shopify stores exposing /collections/{handle}/products.json."""

    def __init__(self, *, size_option_names: tuple[str, ...] = (), size_value_pattern: str = ""):
        self.size_option_names = {name.casefold() for name in size_option_names}
        self.size_value_pattern = re.compile(size_value_pattern, re.IGNORECASE) if size_value_pattern else None

    def _get_collection_handle(self, url: str) -> str | None:
        m = re.search(r"/collections/([^/?#]+)", urlparse(url).path)
        return m.group(1) if m else None

    def parse_listing_page(self, html: str, page_url: str) -> list[Product]:
        items = _products_json(html)
        parsed_page = urlparse(page_url)
        origin = f"{parsed_page.scheme}://{parsed_page.netloc}"
        prefix = parsed_page.path.split("/collections/", 1)[0] if "/collections/" in parsed_page.path else ""
        currency = dict(parse_qsl(parsed_page.query)).get("currency")
        currency_query = "?" + urlencode({"currency": currency}) if currency else ""

        products: list[Product] = []
        for item in items:
            if not item.get("handle") or not item.get("title"):
                continue
            variants = item.get("variants") or []
            product_url = f"{origin}{prefix}/products/{item['handle']}{currency_query}"
            images = item.get("images") or []
            default_image = images[0].get("src") if images else (item.get("image") or {}).get("src")
            options = item.get("options") or []
            option_keys = [
                f"option{index + 1}" for index, option in enumerate(options)
                if isinstance(option, dict) and (
                    str(option.get("name", "")).strip().casefold() in self.size_option_names
                    if self.size_option_names
                    else any(w in str(option.get("name", "")).lower() for w in ("size", "length", "mondo"))
                )
            ]
            price_keys = {
                f"option{index + 1}" for index, option in enumerate(options)
                if isinstance(option, dict) and re.search(r"\b(msrp|retail|price)\b", str(option.get("name", "")), re.IGNORECASE)
            }
            max_item_price = 0.0
            for candidate in variants:
                if not isinstance(candidate, dict) or is_contender_generated_variant(candidate, product_url):
                    continue
                for field in ("price", "compare_at_price"):
                    raw_val = candidate.get(field)
                    if isinstance(raw_val, bool):
                        continue
                    try:
                        amount = float(raw_val or 0)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(amount) and 0 < amount < MAX_REFERENCE_PRICE and amount > max_item_price:
                        max_item_price = amount
            item_price_note = pricing_notice(item, product_url)
            offers = []
            for candidate in variants:
                if (not isinstance(candidate, dict) or candidate.get("available") is False
                        or is_contender_generated_variant(candidate, product_url)):
                    continue
                raw_price, raw_orig = candidate.get("price"), candidate.get("compare_at_price")
                if isinstance(raw_price, bool) or isinstance(raw_orig, bool):
                    continue
                try:
                    price = float(raw_price or 0)
                    original = float(raw_orig or 0)
                except (TypeError, ValueError):
                    continue
                if not plausible_offer(price, original) or is_price_collapse_addon(original, max_item_price):
                    continue
                options_text = " / ".join(
                    str(candidate[k]) for k in ("option1", "option2", "option3")
                    if k not in price_keys and candidate.get(k) and candidate[k] != "Default Title"
                )
                if is_addon_variant(options_text):
                    continue
                sizes = []
                for key in option_keys or (() if options or self.size_option_names else ("option1", "option2", "option3")):
                    value = str(candidate.get(key) or "").strip()
                    if self.size_value_pattern and not self.size_value_pattern.fullmatch(value):
                        continue
                    if not option_keys and not re.fullmatch(
                        r"(?:\d+(?:\.\d+)?\s*(?:cm|mm)?|[2-6]?X{0,3}[SML]|Small|Medium|Large)",
                        value,
                        re.IGNORECASE,
                    ):
                        continue
                    if value and value != "Default Title":
                        sizes.append(value)
                variant_id = candidate.get("id")
                query = dict(parse_qsl(urlparse(product_url).query))
                if variant_id is not None:
                    query["variant"] = str(variant_id)
                variant_url = product_url.split("?")[0] + ("?" + urlencode(query) if query else "")
                image = next((img.get("src") for img in images if variant_id is not None
                              and variant_id in (img.get("variant_ids") or [])), default_image)
                notice = shipping_notice(item, candidate, product_url)
                offers.append(VariantOffer(
                    variant_key=(str(variant_id) if variant_id is not None else "options-" + hashlib.sha256(
                        json.dumps([candidate.get(k) for k in ("sku", "option1", "option2", "option3")]).encode()
                    ).hexdigest()[:20]),
                    url=variant_url,
                    current_price=price,
                    original_price=original,
                    size=" / ".join(sizes) or None,
                    options=options_text,
                    availability="in_stock" if candidate.get("available") is True and not notice else "unknown",
                    image_url=image,
                    availability_note=notice,
                    price_note=item_price_note,
                ))
            if not offers:
                continue
            best = min(offers, key=lambda o: (-o.discount_pct, -(o.original_price - o.current_price),
                                              o.current_price, o.variant_key))
            price, original_price, image_url = best.current_price, best.original_price, best.image_url
            sizes = list(dict.fromkeys(o.size for o in offers if o.size and
                                       (o.current_price, o.original_price) == (price, original_price)))
            products.append(Product(
                name=item.get("title", "Unknown"),
                url=product_url,
                current_price=price,
                original_price=original_price,
                sizes=sizes or None,
                product_type=product_type_from_item(item) or None,
                image_url=image_url,
                brand=item.get("vendor") or None,
                offers=offers,
                tags=tuple(_tags(item)),
            ))

        log.info("Parsed %d products from %s", len(products), page_url)
        return products

    def get_next_page_url(self, html: str, current_url: str) -> str | None:
        products = _products_json(html)
        m_limit = re.search(r"[?&]limit=(\d+)", current_url)
        limit = int(m_limit.group(1)) if m_limit else 250
        if len(products) < limit:
            return None
        m = re.search(r"[?&]page=(\d+)", current_url)
        next_page = (int(m.group(1)) if m else 1) + 1
        base = current_url.split("?")[0]
        query = dict(parse_qsl(urlparse(current_url).query))
        query.update(limit=limit, page=next_page)
        return f"{base}?{urlencode(query)}"

    def get_api_url(self, page_url: str, limit: int = 250) -> str | None:
        handle = self._get_collection_handle(page_url)
        if not handle:
            return None
        parsed = urlparse(page_url)
        prefix = parsed.path.split("/collections/", 1)[0]
        query: dict[str, str | int] = {"limit": limit, "page": 1}
        currency = dict(parse_qsl(parsed.query)).get("currency")
        if currency:
            query["currency"] = currency
        return f"{parsed.scheme}://{parsed.netloc}{prefix}/collections/{handle}/products.json?{urlencode(query)}"
