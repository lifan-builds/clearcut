"""Deterministic regression tests over gpt-5.4-pro synthesized adversarial retailer fixtures."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from aggregator.db import init_db, query_deals, upsert_deals
from aggregator.parsers.bluezone import BlueZoneParser
from aggregator.parsers.coloradodiscount import ColoradoDiscountParser
from aggregator.parsers.sacredride import SacredRideParser
from aggregator.scraper import _products_to_deals
from cycling_deals.catalog import CATALOG as CYCLING
from deal_common.catalog.query import query as catalog_query
from deal_common.catalog.refresh import normalize
from deal_common.parsers.beaver import detail_data as beaver_detail
from deal_common.parsers.bikecloset import validate_detail as bikecloset_detail
from deal_common.parsers.common import parse_price
from deal_common.parsers.shopify import ShopifyParser
from deal_common.parsers.taylor import detail_data as taylor_detail

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "adversarial_retailer_cases.json"
PAYLOAD = json.loads(FIXTURE_PATH.read_text())
CASES = PAYLOAD["cases"]


def test_adversarial_fixture_manifest_covers_all_four_categories():
    assert PAYLOAD["model"] == "gpt-5.4-pro"
    assert len(CASES) >= 25
    categories = {c["category"] for c in CASES}
    assert categories == {
        "strikethrough_msrp_trap",
        "multi_currency",
        "sold_out_matrix",
        "bundle_pricing",
    }
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_adversarial_retailer_case(case: dict):
    target = case["target"]
    expected = case["expected"]

    if target == "parse_price":
        actual = [parse_price(raw) for raw in case["input"]]
        assert actual == expected["results"]
        return

    if target == "beaver_detail":
        if expected.get("raises") == "ValueError":
            with pytest.raises(ValueError):
                beaver_detail(case["input"], case["page_url"])
        else:
            detail = beaver_detail(case["input"], case["page_url"])
            assert detail["sold_out"] is expected["sold_out"]
            assert detail["in_stock"] is (not expected["sold_out"])
        return

    if target == "bikecloset_detail":
        with pytest.raises(ValueError):
            bikecloset_detail(case["input"], case["parent_meta"])
        return

    if target == "taylor_detail":
        with pytest.raises(ValueError):
            taylor_detail(case["input"], case["page_url"])
        return

    parser_map = {
        "shopify": ShopifyParser(),
        "bluezone": BlueZoneParser(),
        "coloradodiscount": ColoradoDiscountParser(),
        "sacredride": SacredRideParser(),
    }
    parser = parser_map[target]
    products = parser.parse_listing_page(case["input"], case["page_url"])
    assert len(products) == expected["product_count"], case["trap_summary"]

    for actual_prod, exp_prod in zip(products, expected["products"], strict=True):
        assert actual_prod.name == exp_prod["name"]
        if "url" in exp_prod:
            assert actual_prod.url == exp_prod["url"]
        assert actual_prod.current_price == pytest.approx(exp_prod["current_price"])
        if exp_prod.get("original_price") is None:
            assert actual_prod.original_price is None
        else:
            assert actual_prod.original_price == pytest.approx(exp_prod["original_price"])
        if "sizes" in exp_prod:
            assert actual_prod.sizes == exp_prod["sizes"]
        if "image_url" in exp_prod:
            assert actual_prod.image_url == exp_prod["image_url"]
        if "product_type" in exp_prod:
            assert actual_prod.product_type == exp_prod["product_type"]
        if "offers" in exp_prod:
            assert len(actual_prod.offers) == len(exp_prod["offers"])
            for actual_offer, exp_offer in zip(actual_prod.offers, exp_prod["offers"], strict=True):
                assert actual_offer.variant_key == exp_offer["variant_key"]
                assert actual_offer.current_price == pytest.approx(exp_offer["current_price"])
                assert actual_offer.original_price == pytest.approx(exp_offer["original_price"])
                if "size" in exp_offer:
                    assert actual_offer.size == exp_offer["size"]
                if "options" in exp_offer:
                    assert actual_offer.options == exp_offer["options"]
                if "availability" in exp_offer:
                    assert actual_offer.availability == exp_offer["availability"]
                if "url" in exp_offer:
                    assert actual_offer.url == exp_offer["url"]


def test_sold_out_and_unknown_matrix_end_to_end_exact_size_filtering(tmp_path):
    """Unknown-stock and sold-out variants in the adversarial ski matrix never satisfy exact-size queries."""
    matrix_case = next(c for c in CASES if c["id"] == "shopify-ski-matrix-unknown-vs-instock")
    products = ShopifyParser().parse_listing_page(matrix_case["input"], matrix_case["page_url"])
    snow_db = tmp_path / "matrix.db"
    asyncio.run(init_db(snow_db))
    deals = _products_to_deals(products, "Powder Shop")
    asyncio.run(upsert_deals(deals, snow_db))

    # Sold-out 164cm (75% off) and omitted-availability 172cm (60% off) must NEVER match exact size filters
    assert asyncio.run(query_deals(db_path=snow_db, size="164cm")) == []
    assert asyncio.run(query_deals(db_path=snow_db, size="172cm")) == []

    # Confirmed in-stock 180cm (40% off) matches with its own exact $480/$800 price and variant URL
    matched_180 = asyncio.run(query_deals(db_path=snow_db, size="180cm"))
    assert len(matched_180) == 1
    assert matched_180[0].current_price == 480.0
    assert matched_180[0].original_price == 800.0
    assert matched_180[0].purchase_url.endswith("variant=18030")


def test_bundle_addon_siblings_never_survive_catalog_normalization_or_query():
    """Cheap replacement/brake-only siblings never turn full-price framesets into deals or hijack ski pricing."""
    hanger_case = next(c for c in CASES if c["id"] == "shopify-carbon-frameset-hanger-collapse")
    parsed_frameset = ShopifyParser(
        size_option_names=CYCLING.size_option_names,
        size_value_pattern=CYCLING.size_value_pattern,
    ).parse_listing_page(hanger_case["input"], hanger_case["page_url"])
    assert parsed_frameset == []
    assert normalize(parsed_frameset, CYCLING, "aethercycles.com") == []

    brake_case = next(c for c in CASES if c["id"] == "shopify-ski-binding-brake-only-sibling")
    parsed_skis = ShopifyParser().parse_listing_page(brake_case["input"], brake_case["page_url"])
    assert len(parsed_skis) == 1
    assert [o.variant_key for o in parsed_skis[0].offers] == ["2101", "2102"]
    now = datetime.now(UTC)
    sources = [{
        "name": "Summit Shop",
        "successful_at": now.isoformat(),
        "products": [{
            "name": parsed_skis[0].name,
            "url": parsed_skis[0].url,
            "brand": "North Ridge",
            "category": "Skis",
            "format": "",
            "platform": "",
            "offers": [
                {
                    "variant_key": o.variant_key,
                    "url": o.url,
                    "current_price": o.current_price,
                    "original_price": o.original_price,
                    "size": o.size,
                    "options": o.options,
                    "availability": o.availability,
                }
                for o in parsed_skis[0].offers
            ],
        }],
    }]
    res = catalog_query(sources, {"min_discount": 0, "min_reference": 0, "max_price": 0, "page": 1}, now=now)
    assert res["total"] == 1
    assert res["cards"][0]["offer"]["current_price"] == 950.0
