"""Contender generated-option exclusion and merchant price-note contracts."""
import json
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from cycling_deals.catalog import CATALOG
from deal_common.catalog.audit import validate_product
from deal_common.catalog.query import query
from deal_common.catalog.refresh import normalize
from deal_common.catalog.storage import read_offer_history, read_sources, save_source
from deal_common.catalog.web import create_app
from deal_common.config import RetailerConfig
from deal_common.parsers.shopify import (
    ShopifyParser,
    is_contender_generated_variant,
    pricing_notice,
)
from fastapi.testclient import TestClient

ORIGIN = "https://contenderbicycles.com"
NOTE = "Retailer asks you to contact them for current pricing."
TAG = "contact-for-current-price"
MARKED = {"67738217840885", "67967680479477", "67967684641013", "48504718950645",
          "48790444245237", "48896550568181", "67593172910325"}


def payload():
    return json.loads((Path(__file__).parent / "fixtures/contender_generated_options.json").read_text())


def rapha_item():
    raw = next(p for p in payload()["products"] if p["handle"] == "open-up-rapha-frameset")
    raw["variants"] = [v for v in raw["variants"] if v["id"] == 43266977104117]
    return raw


def parse(raw, origin=ORIGIN):
    return ShopifyParser(size_option_names=CATALOG.size_option_names,
                         size_value_pattern=CATALOG.size_value_pattern).parse_listing_page(
                             json.dumps(raw), origin + "/collections/sale?currency=USD")


def endpoint(item):
    result = deepcopy(item)
    for variant in result["variants"]:
        for field in ("price", "compare_at_price"):
            variant[field] = int(Decimal(str(variant[field])) * 100)
        variant["options"] = [variant[key] for key in ("option1", "option2", "option3") if variant.get(key)]
    return result


def old_card(item, variant, origin=ORIGIN):
    url = origin + "/products/" + item["handle"]
    return {"name": item["title"], "url": url, "currency": "USD", "offer": {
        "variant_key": str(variant["id"]), "url": url + "?currency=USD&variant=" + str(variant["id"]),
        "current_price": float(variant["price"]), "original_price": float(variant["compare_at_price"]),
        "options": " / ".join(variant[key] for key in ("option1", "option2", "option3") if variant.get(key)),
        "price_note": pricing_notice(item, url),
    }}


@pytest.mark.parametrize("origin", [ORIGIN, "https://www.contenderbicycles.com"])
def test_captured_marked_variants_disappear_and_surviving_ordinary_fields_are_exact(origin):
    raw = payload()
    products = parse(raw, origin)
    assert len(products) == 3
    facts = {str(v["id"]): v for item in raw["products"] for v in item["variants"]}
    kept = {o.variant_key: o for p in products for o in p.offers}
    assert set(kept) == {"46988618367221", "47652505780469", "43266977104117",
                         "37271992697000", "37271992533160"}
    for product in products:
        source = next(p for p in raw["products"] if p["title"] == product.name)
        ordinary_only = {**source, "variants": [v for v in source["variants"] if str(v["id"]) not in MARKED]}
        assert asdict(product) == asdict(parse({"products": [ordinary_only]}, origin)[0])
        for offer in product.offers:
            variant = facts[offer.variant_key]
            assert (offer.current_price, offer.original_price, offer.availability) == (
                float(variant["price"]), float(variant["compare_at_price"]), "in_stock")


def test_generated_variants_never_revive_full_price_or_sold_out_base_or_affect_ranking():
    open_up = next(p for p in payload()["products"] if p["handle"] == "open-up-2-0-frameset")
    assert parse({"products": [open_up]}) == []
    upper = next(p for p in payload()["products"] if p["handle"] == "open-upper-frameset")
    pair = [v for v in upper["variants"] if str(v["id"]) in {"67593172910325", "37271992664232"}]
    assert parse({"products": [{**upper, "variants": pair}]}) == []

    rapha = next(p for p in payload()["products"] if p["handle"] == "open-up-rapha-frameset")
    next(v for v in rapha["variants"] if str(v["id"]) in MARKED)["compare_at_price"] = "10000.00"
    normalized = normalize(parse({"products": [rapha]}), CATALOG, "contenderbicycles.com")
    sources = [{"name": "Contender", "successful_at": datetime.now(UTC).isoformat(), "products": normalized}]
    result = query(sources, {"min_reference": 0, "min_discount": 0, "max_price": 0, "stock": True, "page": 1})
    assert result["total"] == 1 and result["cards"][0]["offer"]["variant_key"] == "43266977104117"
    assert all("#MWS Options" not in value for value in result["facets"]["size"])


@pytest.mark.parametrize("field", ["title", "option1", "option2", "option3", "options"])
def test_marker_in_each_variant_field_is_bounded_and_source_scoped(field):
    value = "M #MWS Options 12"
    variant = {field: [value] if field == "options" else value}
    assert is_contender_generated_variant(variant, ORIGIN + "/products/model")
    assert not is_contender_generated_variant(variant, "https://other.example/products/model")


def test_independent_audit_rejects_marked_variant_and_preserves_other_hosts():
    item = next(p for p in payload()["products"] if p["handle"] == "santa-cruz-stigmata-4-cc-frameset")
    ordinary = next(v for v in item["variants"] if str(v["id"]) not in MARKED)
    marked = next(v for v in item["variants"] if str(v["id"]) in MARKED)
    rejected = validate_product(old_card(item, marked), endpoint(item), "USD")
    assert rejected["status"] == "mismatch" and rejected["checks"]["ordinary_variant"] is False
    assert validate_product(old_card(item, ordinary), endpoint(item), "USD")["status"] == "match"
    assert validate_product(old_card(item, marked, "https://other.example"), endpoint(item), "USD")["status"] == "match"


@pytest.mark.parametrize("tags", [[TAG], ["Frameset", TAG], "Frameset, contact-for-current-price"])
def test_price_note_attaches_only_for_exact_contender_tag_and_audits_cleanly(tags):
    raw = {**rapha_item(), "tags": tags}
    product = parse({"products": [raw]})[0]
    offer = product.offers[0]
    assert offer.price_note == NOTE and offer.availability == "in_stock"
    assert pricing_notice(raw, "https://other.example") == ""
    assert pricing_notice({**raw, "tags": ["contact-for-price", 1, None]}, ORIGIN) == ""

    actual = endpoint(raw)
    card = {"name": product.name, "offer": asdict(offer)}
    result = validate_product(card, actual, "USD")
    assert result["status"] == "match" and result["checks"]["price_note"] is True
    actual["tags"] = []
    assert validate_product(card, actual, "USD")["status"] == "mismatch"


def test_price_note_renders_escaped_in_cards_and_history(tmp_path):
    raw = rapha_item()
    product = normalize(parse({"products": [raw]}), CATALOG, "contenderbicycles.com")[0]
    selected = product["offers"][0]
    selected["price_note"] = "<script>selected XL</script> & current"
    catalog = replace(CATALOG, sources=(RetailerConfig("Contender", "contenderbicycles.com", [ORIGIN + "/collections/sale"]),))
    path = tmp_path / "catalog.db"
    now = datetime.now(UTC)
    legacy = deepcopy(product)
    legacy["offers"][0].pop("price_note")
    save_source(path, "cycling", "Contender", (now - timedelta(days=1)).isoformat(), [legacy], "complete", {})
    save_source(path, "cycling", "Contender", now.isoformat(), [product], "complete", {})
    history = read_offer_history(path, "cycling", "Contender", product, selected, now=now)
    assert [row["price_note"] for row in history] == [selected["price_note"], ""]
    assert read_sources(path, "cycling")[0]["successful_at"] == now.isoformat()
    with TestClient(create_app(catalog, path)) as client:
        page = client.get("/", params={"size": "XL"})
        soup = BeautifulSoup(page.text, "html.parser")
        notes = soup.select("article .price-note")
        assert len(notes) == 2 and {p.get_text() for p in notes} == {selected["price_note"]}
        assert selected["price_note"] not in page.text
        history_page = client.get(soup.select_one("a.history-link")["href"])
        assert selected["price_note"] not in history_page.text
