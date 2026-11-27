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


@pytest.mark.parametrize(
    ("title", "product_type"),
    [
        ("NP 21 GLIDE HP FRONT WING (BLACK FRIDAY DEAL $99)", "Front Wing"),
        ("X SERIES FRONT WING", "Front Wing"),
        ("Slingshot Phantasm Carbon Mast V1.1", "Foil - Foil Masts"),
        ("X SERIES REAR STABILIZER", "Tail Wing"),
        ("Freedom Foil Board FFB Halcyon Carbon Foil Mast", "FOIL - Mast"),
    ],
)
def test_watersports_routes_standalone_foil_components_to_opt_in_category(title, product_type):
    product = Product(name=title, url="https://example.com/products/part", current_price=99, original_price=614, product_type=product_type)
    result = classify(product)
    assert result is not None and result.category == "Foil components"


def test_watersports_keeps_complete_foil_boards_in_default_browse_and_excludes_components_from_hub(tmp_path):
    from datetime import UTC, datetime

    from bs4 import BeautifulSoup
    from deal_common.catalog.refresh import normalize
    from deal_common.catalog.storage import save_source
    from deal_common.catalog.web import create_app
    from deal_common.offers import VariantOffer
    from deal_common.portal.definition import read_shared_catalog_snapshot
    from fastapi.testclient import TestClient

    front_wing = Product(
        "NP 21 GLIDE HP FRONT WING (BLACK FRIDAY DEAL $99)",
        "https://www.adventuresportsusa.com/products/np-front-wing",
        99,
        614.56,
        product_type="Front Wing",
        brand="NeilPryde",
        offers=[VariantOffer("1", "https://www.adventuresportsusa.com/products/np-front-wing?variant=1", 99, 614.56, availability="in_stock")],
    )
    foil_mast = Product(
        "Freedom Foil Board FFB Halcyon Carbon Foil Mast",
        "https://www.adventuresportsusa.com/products/ffb-mast",
        449,
        1199,
        product_type="FOIL - Mast",
        brand="Freedom Foil Boards",
        offers=[VariantOffer("2", "https://www.adventuresportsusa.com/products/ffb-mast?variant=2", 449, 1199, availability="in_stock")],
    )
    board = Product(
        "Slingshot Wing Craft V2 Wing Foil Board",
        "https://www.adventuresportsusa.com/products/wing-craft",
        399,
        1649.99,
        product_type="Slingshot Surfboards",
        brand="Slingshot",
        offers=[VariantOffer("3", "https://www.adventuresportsusa.com/products/wing-craft?variant=3", 399, 1649.99, availability="in_stock")],
    )
    normalized = normalize([front_wing, foil_mast, board], CATALOG, "www.adventuresportsusa.com")
    assert [p["category"] for p in normalized] == ["Foil components", "Foil components", "Foils & wings"]

    db = tmp_path / "watersports.db"
    now = datetime.now(UTC)
    save_source(db, "watersports", "Adventure Sports USA", now.isoformat(), normalized, "complete", {})

    client = TestClient(create_app(CATALOG, db))
    default_page = BeautifulSoup(client.get("/").text, "html.parser")
    assert [h.get_text(strip=True) for h in default_page.select("article.deal-card h3")] == [
        "Slingshot Wing Craft V2 Wing Foil Board"
    ]

    opt_in_page = BeautifulSoup(client.get("/?category=Foil+components").text, "html.parser")
    assert [h.get_text(strip=True) for h in opt_in_page.select("article.deal-card h3")] == [
        "NP 21 GLIDE HP FRONT WING (BLACK FRIDAY DEAL $99)",
        "Freedom Foil Board FFB Halcyon Carbon Foil Mast",
    ]

    hub_summary = read_shared_catalog_snapshot(db, "watersports", world_name="Tideline", min_reference=180, now=now, catalog=CATALOG)
    assert hub_summary is not None
    assert hub_summary.live_deals == 1
    assert [h.name for h in hub_summary.highlights] == ["Slingshot Wing Craft V2 Wing Foil Board"]
