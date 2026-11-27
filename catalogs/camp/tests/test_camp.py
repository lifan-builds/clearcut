import pytest
from camp_deals.catalog import CATALOG, classify
from deal_common.models import Product


@pytest.mark.parametrize(
    ("title", "product_type", "expected"),
    [
        ("NEMO Dagger OSMO 2P Backpacking Tent", "Tents", "Tents & shelters"),
        ("Durston X-Mid Pro 2 Ultralight Shelter", "Shelters", "Tents & shelters"),
        ("Sea to Summit Spark 15F Down Sleeping Bag", "Sleeping Bags", "Sleeping bags & quilts"),
        ("Outdoor Vitals StormLoft 20F TopQuilt", "Quilts", "Sleeping bags & quilts"),
        ("Therm-a-Rest NeoAir XLite NXT Insulated Sleeping Pad", "Sleeping Pads", "Sleeping pads"),
        ("Gossamer Gear Mariposa 60L Backpack", "Backpacks", "Backpacks"),
        ("Sterling Evolution Velocity 9.8mm Dynamic Climbing Rope", "Ropes", "Climbing & mountaineering"),
        ("Black Diamond Camalot C4 Cam", "Climbing", "Climbing & mountaineering"),
        ("Petzl Neox Assisted-Braking Belay Device", "Hardware", "Climbing & mountaineering"),
    ],
)
def test_camp_classifies_core_hardware(title, product_type, expected):
    product = Product(name=title, url="https://example.com/products/item", current_price=199, original_price=349, product_type=product_type)
    result = classify(product)
    assert result is not None and result.category == expected


@pytest.mark.parametrize(
    ("title", "product_type", "tags"),
    [
        ("NEMO Dagger 2P Tent Footprint", "Tent Accessories", ()),
        ("DAC V-Stakes 6-Pack Tent Stakes", "Accessories", ()),
        ("Peak Refuel Freeze-Dried Chicken Alfredo Meal", "Food", ()),
        ("Black Diamond Loose Chalk 300g", "Climbing", ()),
        ("Gossamer Gear Logo Hoodie", "Apparel", ()),
        ("Ultralight Dry Sack 10L Stuff Sack", "Accessories", ()),
        ("Kids Sprout 20F Youth Sleeping Bag", "Sleeping Bags", ()),
        ("Blemished 2P Backpacking Tent", "Tents", ("blemished",)),
        ("Everyday Crossbody Sling Bag", "Bags", ()),
    ],
)
def test_camp_excludes_accessories_apparel_food_and_used(title, product_type, tags):
    product = Product(name=title, url="https://example.com/products/junk", current_price=29, original_price=59, product_type=product_type, tags=tags)
    assert classify(product) is None


def test_camp_catalog_sources_are_https_usd():
    assert len(CATALOG.sources) >= 10
    for src in CATALOG.sources:
        assert src.currency == "USD"
        assert all(u.startswith("https://") for u in src.scrape_urls)


@pytest.mark.parametrize(
    ("title", "product_type", "expected"),
    [
        ("Topo Designs Global Travel Bag Roller", "Backpacks and Bags", "Travel bags & luggage"),
        ("Topo Designs Global Travel Bag 30L", "Backpacks and Bags", "Travel bags & luggage"),
        ("Topo Designs Global Travel Bag 30L (S24)", "Backpacks and Bags", "Travel bags & luggage"),
        ("Topo Designs Apex Global Travel Bag 30L", "Backpacks and Bags", "Travel bags & luggage"),
        ("Ultralight Air Sleeping Pad", "Sleeping Pads", "Sleeping pads"),
    ],
)
def test_camp_routes_travel_bags_out_of_backpacks_and_sleeping_pads_out_of_bags(title, product_type, expected):
    product = Product(
        name=title,
        url="https://example.com/products/item",
        current_price=101.4,
        original_price=169,
        product_type=product_type,
        tags=("sleep system",) if "Sleeping Pad" in title else ("backpacks",),
    )
    result = classify(product)
    assert result is not None and result.category == expected


def test_camp_default_browse_excludes_travel_bags_and_reconciles_legacy_sleeping_pad_rows(tmp_path):
    from datetime import UTC, datetime

    from bs4 import BeautifulSoup
    from deal_common.catalog.storage import save_source
    from deal_common.catalog.web import create_app
    from fastapi.testclient import TestClient

    db = tmp_path / "camp.db"
    now = datetime.now(UTC)
    legacy_products = [
        {
            "name": "Topo Designs Global Travel Bag Roller",
            "url": "https://kaviso.com/products/roller",
            "brand": "Topo Designs",
            "category": "Backpacks",
            "source_category": "Backpacks and Bags",
            "format": "",
            "platform": "",
            "offers": [{
                "variant_key": "1",
                "url": "https://kaviso.com/products/roller?variant=1",
                "current_price": 169.0,
                "original_price": 279.0,
                "size": None,
                "options": "Black",
                "availability": "in_stock",
                "image_url": None,
                "reference_basis": "retailer_compare_at",
            }],
        },
        {
            "name": "Ultralight Air Sleeping Pad",
            "url": "https://seatosummit.com/products/ultralight-pad",
            "brand": "Sea to Summit",
            "category": "Sleeping bags & quilts",
            "source_category": "Sleeping Pads",
            "format": "",
            "platform": "",
            "offers": [{
                "variant_key": "2",
                "url": "https://seatosummit.com/products/ultralight-pad?variant=2",
                "current_price": 101.4,
                "original_price": 169.0,
                "size": "Large",
                "options": "Large",
                "availability": "in_stock",
                "image_url": None,
                "reference_basis": "retailer_compare_at",
            }],
        },
    ]
    save_source(db, "camp", "Kaviso", now.isoformat(), legacy_products, "complete", {})

    client = TestClient(create_app(CATALOG, db))
    default_titles = [h.get_text(strip=True) for h in BeautifulSoup(client.get("/").text, "html.parser").select("article.deal-card h3")]
    assert default_titles == ["Ultralight Air Sleeping Pad"]
    assert BeautifulSoup(client.get("/?category=Backpacks").text, "html.parser").select("article.deal-card") == []
    assert BeautifulSoup(client.get("/?category=Sleeping+bags+%26+quilts").text, "html.parser").select("article.deal-card") == []
    assert [h.get_text(strip=True) for h in BeautifulSoup(client.get("/?category=Sleeping+pads").text, "html.parser").select("article.deal-card h3")] == [
        "Ultralight Air Sleeping Pad"
    ]
    assert [h.get_text(strip=True) for h in BeautifulSoup(client.get("/?category=Travel+bags+%26+luggage").text, "html.parser").select("article.deal-card h3")] == [
        "Topo Designs Global Travel Bag Roller"
    ]
