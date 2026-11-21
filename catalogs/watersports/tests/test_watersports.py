import pytest
from deal_common.models import Product
from water_deals.catalog import CATALOG, classify


@pytest.mark.parametrize(
    ("title", "product_type", "expected"),
    [
        ("Armstrong HA925 Carbon Hydrofoil Kit", "Hydrofoils", "Foils & wings"),
        ("Duotone Unit D/LAB 5.0m Wing-Foil Wing", "Wings", "Foils & wings"),
        ("North Reach 12m Kiteboarding Kite", "Kites", "Kites & kiteboards"),
        ("Firewire Seaside 5'7\" Helium Surfboard", "Surfboards", "Surfboards & SUPs"),
        ("BOTE Rackham Aero 12'4\" Inflatable SUP Paddle Board", "SUP", "Surfboards & SUPs"),
        ("O'Neill Hyperfreak 4/3+ Chest Zip Full Wetsuit", "Wetsuits", "Wetsuits"),
        ("Sage R8 Core 9' 5wt Fly Rod", "Fly Rods", "Fly rods & reels"),
        ("Abel SDS 9/10 Saltwater Fly Reel", "Fly Reels", "Fly rods & reels"),
    ],
)
def test_watersports_classifies_core_equipment(title, product_type, expected):
    product = Product(name=title, url="https://example.com/products/item", current_price=499, original_price=899, product_type=product_type)
    result = classify(product)
    assert result is not None and result.category == expected


@pytest.mark.parametrize(
    ("title", "product_type", "tags"),
    [
        ("FCS II Performer PC Thruster Surfboard Fins", "Fins", ()),
        ("Dakine Kainui 6ft Surf Leash", "Leashes", ()),
        ("O'Neill Psycho Tech 3mm Split-Toe Wetsuit Booties", "Wetsuit Boots", ()),
        ("Rio Gold Elite Floating Fly Line WF5F", "Fly Line", ()),
        ("Simms Nippers & Forceps Tool Kit", "Accessories", ()),
        ("Parachute Adams Dry Flies Dozen", "Flies", ()),
        ("Used Demo Duotone Slick 5.5m Wing", "Wings", ("demo",)),
        ("Kids Grom 3/2mm Full Wetsuit", "Wetsuits", ()),
        ("Titanium Foil Mast Hardware Screw Kit", "Accessories", ()),
    ],
)
def test_watersports_excludes_accessories_flies_lines_booties_and_demo(title, product_type, tags):
    product = Product(name=title, url="https://example.com/products/junk", current_price=49, original_price=99, product_type=product_type, tags=tags)
    assert classify(product) is None


def test_watersports_catalog_sources_are_https_usd():
    assert len(CATALOG.sources) >= 10
    for src in CATALOG.sources:
        assert src.currency == "USD"
        assert all(u.startswith("https://") for u in src.scrape_urls)
