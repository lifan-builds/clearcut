"""Bounded source refreshes keep previous complete data on a partial response."""
import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from deal_common.catalog.definition import Catalog, manufacturer_name
from deal_common.catalog.storage import save_source
from deal_common.http import DEFAULT_HEADERS, fetch_products
from deal_common.offers import discount
from deal_common.parsers.beaver import BeaverShopifyParser
from deal_common.parsers.bikecloset import BikeClosetParser
from deal_common.parsers.bikecloset import offer_destination as bikecloset_destination
from deal_common.parsers.racycles import RacyclesShopifyParser
from deal_common.parsers.shopify import ShopifyParser
from deal_common.parsers.taylor import TaylorParser
from deal_common.parsers.taylor import offer_destination as taylor_destination

DESTINATION_VALIDATORS = {
    "bikecloset": bikecloset_destination,
    "taylor": taylor_destination,
}


def safe_url(value: str | None, domain: str | None = None) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443)
                and (domain is None or parsed.hostname.removeprefix("www.") == domain.removeprefix("www.")))
    except ValueError:
        return False


def _shopify_destination(url: str, key: str) -> bool:
    return parse_qs(urlsplit(url).query).get("variant") == [key]


def normalize(products, catalog: Catalog, domain: str, *, parser_type: str = "shopify") -> list[dict]:
    validate_destination = DESTINATION_VALIDATORS.get(parser_type, _shopify_destination)
    result = {}
    for product in products:
        classification = catalog.classify(product)
        if not classification or classification.category not in catalog.categories or not safe_url(product.url, domain):
            continue
        offers = []
        for offer in product.offers:
            if (offer.availability not in {"in_stock", "unknown"}
                    or discount(offer.current_price, offer.original_price) <= 0
                    or not safe_url(offer.url, domain)
                    or urlsplit(offer.url).path != urlsplit(product.url).path
                    or not validate_destination(offer.url, offer.variant_key)):
                continue
            data = asdict(offer)
            data["image_url"] = offer.image_url if safe_url(offer.image_url) else None
            offers.append(data)
        if not offers:
            continue
        result[product.url] = {
            "name": product.name, "url": product.url,
            "brand": manufacturer_name(product.brand, brand_aliases=catalog.brand_aliases),
            "source_category": product.product_type,
            **asdict(classification), "offers": offers,
        }
    return list(result.values())


def _build_parser(source, catalog: Catalog):
    if source.parser_type == "bikecloset":
        return BikeClosetParser()
    if source.parser_type == "taylor":
        return TaylorParser()
    if source.parser_type != "shopify":
        raise ValueError("Unsupported catalog parser")
    if source.domain == "racycles.com":
        return RacyclesShopifyParser(size_option_names=catalog.size_option_names,
                                     size_value_pattern=catalog.size_value_pattern)
    if source.domain == "beavertools.com":
        return BeaverShopifyParser()
    return ShopifyParser(size_option_names=catalog.size_option_names,
                         size_value_pattern=catalog.size_value_pattern)


async def refresh(catalog: Catalog, path: Path, *, max_pages: int = 30,
                  delay: float = 1.5, transport=None) -> list[dict]:
    reports = []
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=30, follow_redirects=True,
                                 transport=transport) as client:
        for source in catalog.sources:
            outcome = {"errors": 0, "pages": 0}
            observed_at = datetime.now(UTC).isoformat()
            if source.currency != "USD":
                raise ValueError("These catalogs accept USD sources only")
            parser = _build_parser(source, catalog)
            products = await fetch_products(source, parser, client,
                                            semaphore=asyncio.Semaphore(1), delay=delay,
                                            max_pages=max_pages, outcome=outcome,
                                            include_product=lambda product: catalog.classify(product) is not None)
            normalized = normalize(products, catalog, source.domain, parser_type=source.parser_type)
            status = "complete" if outcome["errors"] == 0 and outcome.get("collections") else "incomplete"
            outcome.update(parsed_products=len(products), accepted_products=len(normalized))
            save_source(path, catalog.slug, source.name, observed_at, normalized, status, outcome)
            reports.append({"source": source.name, "status": status, **outcome})
    return reports
