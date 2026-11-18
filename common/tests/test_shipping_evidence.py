"""Source-scoped Shopify shipping notices for Harvey, Baker's Gas, and Cyberweld."""
import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from deal_common.catalog.audit import validate_product
from deal_common.catalog.query import query
from deal_common.catalog.refresh import normalize, refresh
from deal_common.catalog.storage import read_offer_history, read_sources, save_source
from deal_common.catalog.web import create_app
from deal_common.config import RetailerConfig
from deal_common.parsers.shopify import ShopifyParser, shipping_notice
from fastapi.testclient import TestClient
from tool_deals.catalog import CATALOG

HARVEY = "https://www.harveywoodworking.com"
BAKERS = "https://bakersgas.com"
CYBER = "https://store.cyberweld.com"


def harvey_item(vendor="ESD：Nov.30（Black）"):
    return {"title": "Industrial Dust Processor", "handle": "dust-processor",
            "vendor": vendor, "product_type": "Dust Collector",
            "options": [{"name": "Color"}], "variants": [
                {"id": 1, "option1": "Lunar White", "price": "3500", "compare_at_price": "4000", "available": True},
                {"id": 2, "option1": "Nebula Black", "price": "3000", "compare_at_price": "4000", "available": True},
            ]}


def bakers_item():
    return json.loads((Path(__file__).parent / "fixtures/bakers_backorder.json").read_text())


def parse(raw, origin=HARVEY):
    return ShopifyParser().parse_listing_page(json.dumps({"products": [raw]}),
                                              origin + "/collections/all?currency=USD")[0]


def endpoint(raw):
    actual = deepcopy(raw)
    if "body_html" in actual:
        actual["description"] = actual.pop("body_html")
    for variant in actual["variants"]:
        variant["options"] = [variant["option1"]]
        variant["price"] = int(float(variant["price"]) * 100)
        variant["compare_at_price"] = int(float(variant["compare_at_price"]) * 100)
    return actual


def test_harvey_shipping_evidence_applies_to_exact_color_and_preserves_offer_facts():
    p = parse(harvey_item())
    white, black = p.offers
    assert (white.availability, white.availability_note) == ("in_stock", "")
    assert black.availability == "unknown"
    assert black.availability_note == "Retailer shipping estimate: Nov.30(Black)"
    assert (black.variant_key, black.current_price, black.original_price, black.options) == ("2", 3000, 4000, "Nebula Black")
    assert shipping_notice(harvey_item(), harvey_item()["variants"][1], "https://other.example/products/dust-processor") == ""


@pytest.mark.parametrize("vendor", ["ESD: Nov. 30", "ESD: Nov. 30 (Red)", "ESD:",
                                    "ESD: Pending (Black/White)", "ESD: Jan. 1 2020 (Black)"])
def test_harvey_unresolved_or_past_looking_notice_cannot_confirm_stock(vendor):
    p = parse(harvey_item(vendor))
    assert p.offers[1].availability == "unknown" and p.offers[1].availability_note
    if "(Black)" not in vendor:
        assert p.offers[0].availability == "unknown"


def test_harvey_notice_survives_audit_refresh_storage_filtering_and_rendering(tmp_path):
    source_item = harvey_item()
    product = parse(source_item)
    white, black = product.offers
    raw = endpoint(source_item)
    card = {"name": product.name, "offer": asdict(black)}
    rejected = validate_product(card, raw, "USD")
    assert rejected["status"] == "mismatch" and rejected["checks"]["stock"] is False
    assert validate_product({**card, "offer": asdict(white)}, raw, "USD")["status"] == "match"

    source = RetailerConfig("Harvey Woodworking", "www.harveywoodworking.com", [HARVEY + "/collections/all"])
    catalog = replace(CATALOG, sources=(source,))
    path = tmp_path / "catalog.db"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"products": [source_item]}))
    assert asyncio.run(refresh(catalog, path, delay=0, transport=transport))[0]["status"] == "complete"
    rows = read_sources(path, "tools")
    filters = {"min_reference": 200, "min_discount": 0, "max_price": 0, "page": 1}
    assert query(rows, filters)["cards"][0]["offer"]["variant_key"] == "2"
    assert query(rows, {**filters, "stock": True})["cards"][0]["offer"]["variant_key"] == "1"
    with TestClient(create_app(catalog, path)) as client:
        assert "Retailer shipping estimate" in client.get("/").text
        assert "Lunar White" in client.get("/", params={"stock": "true"}).text


def test_bakers_and_cyberweld_notices_override_stock_and_survive_history(tmp_path):
    raw = bakers_item()
    product = parse(raw, BAKERS)
    assert product.offers[0].availability == "unknown"
    assert product.offers[0].availability_note == "Retailer shipping notice: Machine is on backorder."
    assert parse(raw, "https://other.example").offers[0].availability == "in_stock"
    assert validate_product({"name": product.name, "offer": asdict(product.offers[0])}, endpoint(raw), "USD")["checks"]["stock"] is False

    service_parts = {**bakers_item(), "body_html": "<p>backordered service parts may qualify for expedited shipping.</p>"}
    assert parse(service_parts, BAKERS).offers[0].availability == "in_stock"

    cyber_raw = {**bakers_item(), "body_html": "", "tags": "auto_instock, AUTO_OUTOFSTOCK, TIG Welder"}
    cyber_offer = parse(cyber_raw, CYBER).offers[0]
    assert cyber_offer.availability == "unknown"
    assert cyber_offer.availability_note == "Retailer marks this product out of stock."

    now = datetime.now(UTC)
    earlier = now - timedelta(days=1)
    path = tmp_path / "welding.db"
    save_source(path, "tools", "Baker's Gas", earlier.isoformat(),
                normalize([product], CATALOG, "bakersgas.com"), "complete", {})
    cleared = normalize([parse({**bakers_item(), "body_html": "<p>$200 rebate.</p>"}, BAKERS)], CATALOG, "bakersgas.com")
    save_source(path, "tools", "Baker's Gas", now.isoformat(), cleared, "complete", {})
    history = read_offer_history(path, "tools", "Baker's Gas", cleared[0], cleared[0]["offers"][0], now=now)
    assert [row["availability"] for row in history] == ["in_stock", "unknown"]
    assert history[1]["availability_note"] == product.offers[0].availability_note
