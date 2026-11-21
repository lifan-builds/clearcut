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
