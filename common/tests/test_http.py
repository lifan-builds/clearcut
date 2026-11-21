"""Exercise shared HTTP ingestion and parser helpers without live network calls."""

import asyncio

import httpx
import pytest
from deal_common.config import RetailerConfig
from deal_common.http import fetch_products
from deal_common.parsers.common import clean_product_name, parse_price
from deal_common.parsers.shopify import ShopifyParser


@pytest.mark.parametrize(("raw", "expected"), [
    ("$269.99", 269.99),
    ("$1,250.00", 1250.00),
    ("C$485.99", 485.99),
    ("579.99", 579.99),
    ("Was: $299.99", 299.99),
    ("  $89.50  ", 89.50),
    ("", None),
    ("Call for price", None),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def test_clean_product_name_recovers_placeholder_labels():
    assert clean_product_name("  Atomic   Bent 100 ") == "Atomic Bent 100"
    assert clean_product_name("Product Image", "https://shop.test/products/atomic-bent-100.html") == "Atomic Bent 100"
    assert clean_product_name("Image", "") == ""


def test_shopify_pagination_retains_available_products_and_metadata():
    requests = []

    def handle(request):
        requests.append(str(request.url))
        page = request.url.params["page"]
        if page == "1":
            return httpx.Response(200, json={"products": [{
                "title": "Carbon Gravel Frameset", "handle": "carbon-gravel-frame",
                "product_type": "Framesets", "images": [{"src": "https://example.com/frame.jpg"}],
                "variants": [
                    {"price": "999", "compare_at_price": "2999", "available": False, "option1": "S"},
                    {"price": "1200", "compare_at_price": "3000", "available": True, "option1": "M"},
                ],
            }]})
        return httpx.Response(200, json={"products": []})

    async def run():
        store = RetailerConfig("Example", "example.com", ["https://example.com/collections/sale"],
                               shopify_products_limit=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await fetch_products(store, ShopifyParser(), client, semaphore=asyncio.Semaphore(2), delay=0)

    products = asyncio.run(run())
    assert len(requests) == 2
    assert "/collections/sale/products.json?limit=1&page=1" in requests[0]
    assert products[0].current_price == 1200
    assert products[0].discount_pct == 60
    assert products[0].sizes == ["M"]
    assert products[0].image_url == "https://example.com/frame.jpg"
    assert products[0].product_type == "Framesets"


def test_page_cap_and_failed_collection_do_not_discard_other_results():
    requests = []

    def handle(request):
        requests.append(request.url.path)
        if "broken" in request.url.path:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"products": [{
            "title": "Example", "handle": "example",
            "variants": [{"price": "500", "compare_at_price": "1000", "available": True}],
        }]})

    async def run():
        store = RetailerConfig("Example", "example.com", [
            "https://example.com/collections/broken", "https://example.com/collections/sale",
        ], shopify_products_limit=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await fetch_products(store, ShopifyParser(), client,
                                        semaphore=asyncio.Semaphore(1), delay=0, max_pages=1)

    assert len(asyncio.run(run())) == 1
    assert len(requests) == 5


def test_persistent_429_aborts_remaining_collections_on_same_store():
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "20"})

    async def run():
        outcome = {"errors": 0, "pages": 0}
        store = RetailerConfig(
            "Throttled",
            "example.com",
            [
                "https://example.com/collections/skis",
                "https://example.com/collections/snowboards",
                "https://example.com/collections/boots",
            ],
            shopify_products_limit=1,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            products = await fetch_products(
                store, ShopifyParser(), client, semaphore=asyncio.Semaphore(1), delay=0, outcome=outcome
            )
        return products, outcome

    async def fast_sleep(_):
        pass

    import deal_common.http as http_mod

    orig_sleep = http_mod.asyncio.sleep
    http_mod.asyncio.sleep = fast_sleep
    try:
        products, outcome = asyncio.run(run())
    finally:
        http_mod.asyncio.sleep = orig_sleep

    assert products == []
    assert len(requests) == 2
    assert [c["status"] for c in outcome["collections"]] == ["http_error", "rate_limited", "rate_limited"]
    assert outcome["errors"] == 3
