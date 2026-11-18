"""Shopify cycling classification and normalization contracts (Incycle, evo, Mike's Bikes)."""
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cycling_deals.catalog import CATALOG, classify
from deal_common.catalog.query import query
from deal_common.catalog.refresh import normalize
from deal_common.models import Product
from deal_common.parsers.shopify import ShopifyParser

INCYCLE = "https://www.incycle.com"
INCYCLE_URL = INCYCLE + "/products/model"
LIGHTWEIGHT_BRANCH = "BIKES AND FRAMES/BIKES - ELECTRIC/ELECTRIC MOUNTAIN LIGHT WEIGHT"
MIKES = "https://mikesbikes.com"


def incycle_fixture():
    return json.loads((Path(__file__).parent / "fixtures/incycle_bicycles.json").read_text())


def lightweight_fixture():
    return json.loads((Path(__file__).parent / "fixtures/cycling_lightweight_bikes.json").read_text())


@pytest.mark.parametrize(("index", "category"), [
    (0, "Other complete bikes"), (1, "Electric bikes"), (2, "Mountain bikes"),
    (3, "Road bikes"), (4, "Gravel bikes"), (5, "Electric bikes"), (6, "Framesets"),
])
def test_incycle_complete_bike_tags_and_frameset_precedence(index, category):
    raw = incycle_fixture()["products"][index]
    product = Product(raw["title"], INCYCLE + "/products/" + raw["handle"], 1000, 4000,
                      product_type=raw["product_type"], brand=raw["vendor"], tags=tuple(raw["tags"]))
    assert classify(product).category == category


@pytest.mark.parametrize("url", [
    "https://other.example/products/model", "http://www.incycle.com/products/model",
    "https://incycle.com/products/model", "https://www.incycle.com/collections/model",
])
def test_incycle_tags_are_origin_and_product_path_scoped(url):
    assert classify(Product("2023 Specialized Sirrus 4.0", url, 1000, 2000,
                            product_type="Sporting Goods", tags=("BICYCLES", "ACTIVE"))) is None


def test_incycle_accessories_and_condition_gates_stay_excluded():
    for title in ("Rear Bike Rack", "Bicycle Wheel", "Bike Helmet", "Bicycle Rack with Free Mountain Bike"):
        assert classify(Product(title, INCYCLE_URL, 1000, 2000, product_type="Sporting Goods",
                                tags=("BICYCLES", "MOUNTAIN", "TURBO & ELECTRIC BIKE"))) is None
    assert classify(Product("Model with Rack and Lights", INCYCLE_URL, 1000, 2000, product_type="Sporting Goods",
                            tags=("BICYCLES", "MOUNTAIN", "TURBO & ELECTRIC BIKE"))).category == "Electric bikes"
    for tags in ((), ("MOUNTAIN",), ("BICYCLES",), ("BICYCLES", "MOUNTAIN", "condition:open-box")):
        assert classify(Product("2023 Specialized Sirrus 4.0", INCYCLE_URL, 1000, 2000,
                                product_type="Sporting Goods", tags=tags)) is None


def test_incycle_parser_normalization_keeps_raw_variants_and_sizes():
    payload = incycle_fixture()
    parser = ShopifyParser(size_option_names=CATALOG.size_option_names,
                           size_value_pattern=CATALOG.size_value_pattern)
    normalized = normalize(parser.parse_listing_page(json.dumps(payload), INCYCLE + "/collections/sale-bicycles?currency=USD"),
                           CATALOG, "www.incycle.com")
    source = {"name": "Incycle", "successful_at": datetime.now(UTC).isoformat(), "products": normalized}
    filters = {"min_reference": 1000, "min_discount": 0, "max_price": 0, "stock": True, "page": 1}
    result = query([source], filters, brand_aliases=CATALOG.brand_aliases)
    assert result["total"] == 7
    assert query([source], {**filters, "size": "S1"}, brand_aliases=CATALOG.brand_aliases)["total"] == 1


@pytest.mark.parametrize(("kind", "category"), [
    ("Mountain Completes", "Mountain bikes"),
    ("e-Mountain Completes", "Electric bikes"),
    ("Gravel Completes", "Gravel bikes"),
])
def test_evo_complete_taxonomy_and_store_pickup_exclusion(kind, category):
    payload = {"products": [{"title": "Example Complete Bike 2026", "handle": "bike",
        "product_type": kind, "vendor": "Example", "tags": ["Free Bike Ship"],
        "options": [{"name": "Color"}, {"name": "Size"}], "variants": [
            {"id": 1, "available": False, "price": "1000", "compare_at_price": "4000", "option1": "Red", "option2": "S"},
            {"id": 2, "available": True, "price": "2000", "compare_at_price": "4000", "option1": "Blue", "option2": "M"}]}]}
    parser = ShopifyParser(size_option_names=CATALOG.size_option_names,
                           size_value_pattern=CATALOG.size_value_pattern)
    rows = normalize(parser.parse_listing_page(json.dumps(payload), "https://www.evo.com/collections/bikes?currency=USD"),
                     CATALOG, "www.evo.com")
    assert len(rows) == 1 and rows[0]["category"] == category
    assert rows[0]["offers"][0]["url"] == "https://www.evo.com/products/bike?currency=USD&variant=2"
    assert classify(Product("Example Complete Mountain Bike 2026", "https://www.evo.com/products/bike",
                            2000, 4000, product_type="Mountain Completes",
                            tags=("Free Bike Ship", "badge:Ship to Store Only"))) is None


def test_mikes_lightweight_ebike_branch_keeps_bikes_and_excludes_accessories():
    payload = lightweight_fixture()
    original = deepcopy(payload)
    parser = ShopifyParser(size_option_names=CATALOG.size_option_names,
                           size_value_pattern=CATALOG.size_value_pattern)
    normalized = normalize(parser.parse_listing_page(json.dumps(payload), MIKES + "/collections/sale?currency=USD"),
                           CATALOG, "mikesbikes.com")
    assert len(normalized) == 3 and {p["category"] for p in normalized} == {"Electric bikes"}
    for title, kind in [
        ("Heckler SL CC", LIGHTWEIGHT_BRANCH + "/HECKLER SL/LIGHTS"),
        ("Habit Neo 1", LIGHTWEIGHT_BRANCH + "/HABIT NEO/ACCESSORIES"),
        ("Rear Bike Light", "BIKES AND FRAMES/BIKE LIGHTS"),
        ("Demo Heckler SL", LIGHTWEIGHT_BRANCH + "/HECKLER SL"),
    ]:
        assert classify(Product(title, MIKES + "/products/item", 5000, 10000, product_type=kind)) is None
    assert payload == original
