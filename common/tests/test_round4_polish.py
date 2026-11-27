"""Round 4 regression tests: brand dedup, Tideline component guard, Bench Drop badges, exact-offer grouping."""
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from bs4 import BeautifulSoup
from camp_deals.catalog import CATALOG as CAMP
from deal_common.catalog.definition import (
    brand_id,
    clean_brand,
    compile_brand_aliases,
    display_brand,
)
from deal_common.catalog.query import query
from deal_common.catalog.refresh import normalize
from deal_common.catalog.storage import read_offer_history, save_source
from deal_common.catalog.web import create_app, deduplicate_badge_labels
from deal_common.models import Product
from deal_common.offers import VariantOffer
from deal_common.portal.definition import read_shared_catalog_snapshot
from fastapi.testclient import TestClient
from tool_deals.catalog import CATALOG as TOOLS
from water_deals.catalog import CATALOG as WATER
from water_deals.catalog import classify as classify_water

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
BASE_FILTERS = {"min_reference": 0, "min_discount": 0, "max_price": 0, "page": 1}


def make_product(
    brand: str,
    name: str,
    category: str,
    *,
    size: str = "",
    options: str = "",
    fmt: str = "",
    platform: str = "",
    price: float = 100.0,
    reference: float = 250.0,
    domain: str = "shop.example",
    offers: list[dict] | None = None,
) -> dict:
    url = f"https://{domain}/products/" + quote(name, safe="")
    return {
        "brand": brand,
        "name": name,
        "category": category,
        "url": url,
        "format": fmt,
        "platform": platform,
        "offers": offers or [{
            "url": url + "?variant=1",
            "variant_key": size or options or "default",
            "size": size,
            "options": options,
            "availability": "in_stock",
            "current_price": price,
            "original_price": reference,
            "image_url": None,
            "reference_basis": "retailer_compare_at",
        }],
    }


def make_source(name: str, products: list[dict], now: datetime = NOW) -> dict:
    return {"name": name, "products": products, "successful_at": now.isoformat()}


def test_brand_helpers_deduplicate_casing_spacing_and_reject_conflicts():
    assert clean_brand("  Outdoor\u00a0\u00a0Vitals\t ") == "Outdoor Vitals"
    assert brand_id("LIFT") == brand_id(" Lift ") == brand_id("lift") == "lift"
    assert display_brand("lift", {"LIFT", " Lift ", "lift"}) == "Lift"
    assert display_brand("dewalt", {"DEWALT", "DeWalt", "dewalt"}) == "DeWalt"
    assert display_brand("nemo", {"NEMO"}) == "NEMO"
    assert brand_id("AB C") != brand_id("A BC")

    with pytest.raises(ValueError, match="Conflicting brand alias"):
        compile_brand_aliases((("acme", "Acme"), ("acme", "Other")))
    with pytest.raises(ValueError, match="Conflicting preferred brand spelling"):
        compile_brand_aliases((("lift", "Lift"), ("lift foils", "LIFT")))


def test_brand_case_and_spacing_dedup_unifies_facets_filter_urls_and_history(tmp_path):
    p_upper = make_product("LIFT", "Lift4 eFoil", "Foils & wings", price=5000, reference=9000, domain="a.example")
    p_mixed = make_product(" Lift ", "Lift3 F eFoil", "Foils & wings", price=4500, reference=8500, domain="b.example")
    p_lower = make_product("lift", "Lift Pro Wing", "Foils & wings", price=600, reference=1000, domain="c.example")
    p_dewalt = make_product("DeWalt", "DeWalt Drill", "Foils & wings", price=120, reference=240, domain="a.example")
    p_nemo = make_product("NEMO", "NEMO Dry Bag", "Foils & wings", price=120, reference=240, domain="a.example")
    p_abc1 = make_product("AB C", "Board One", "Foils & wings", price=300, reference=600, domain="a.example")
    p_abc2 = make_product("A BC", "Board Two", "Foils & wings", price=300, reference=600, domain="b.example")

    rows = [
        make_source("REAL Watersports", [p_upper, p_dewalt, p_nemo, p_abc1]),
        make_source("Houston Kiteboarding", [p_mixed, p_abc2]),
        make_source("Force Kite & Wake", [p_lower]),
    ]

    # Without any configured aliases: mixed-case Lift wins deterministically, DeWalt & NEMO preserved, AB C != A BC.
    res = query(rows, BASE_FILTERS, now=NOW)
    res_rev = query(list(reversed(rows)), BASE_FILTERS, now=NOW)
    assert res["facets"]["brand"] == ["A BC", "AB C", "DeWalt", "Lift", "NEMO"]
    assert res_rev["facets"]["brand"] == res["facets"]["brand"]
    for requested in ("LIFT", "lift", " Lift "):
        filtered = query(rows, {**BASE_FILTERS, "brand": requested}, now=NOW)
        assert filtered["total"] == 3
        assert {c["store"] for c in filtered["cards"]} == {
            "REAL Watersports", "Houston Kiteboarding", "Force Kite & Wake",
        }
        assert {c["brand"] for c in filtered["cards"]} == {"Lift"}

    # Web app resolves ?brand=LIFT and ?brand=lift to canonical Lift option and returns all offers.
    db = tmp_path / "water.db"
    now = datetime.now(UTC)
    for row in rows:
        save_source(db, "watersports", row["name"], now.isoformat(), row["products"], "complete", {})
    client = TestClient(create_app(WATER, db))
    for raw_q in ("LIFT", "lift", " Lift "):
        soup = BeautifulSoup(client.get("/", params={"brand": raw_q, "min_reference": 0}).text, "html.parser")
        assert [o.get_text() for o in soup.select('select[name="brand"] option[selected]')] == ["Lift"]
        assert soup.select('select[name="brand"] option').__len__() == 6  # All brands + 5 distinct brands
        assert len(soup.select("article.deal-card")) == 3
        assert {b.get_text(strip=True) for b in soup.select(".product-brand")} == {"Lift"}

    # Highline OutdoorVitals / Outdoor  Vitals merge via explicit alias while preserving NEMO.
    camp_rows = [
        make_source("Outdoor Vitals", [make_product("OutdoorVitals", "StormLoft Quilt", "Sleeping bags & quilts", price=200, reference=350)]),
        make_source("Kaviso", [make_product("Outdoor\u00a0\u00a0Vitals", "CS40 Backpack", "Backpacks", price=180, reference=300)]),
        make_source("NEMO Equipment", [make_product("NEMO", "Kayu 30 Down Sleeping Bag", "Sleeping bags & quilts", price=220, reference=400)]),
    ]
    camp_res = query(camp_rows, BASE_FILTERS, now=NOW, brand_aliases=CAMP.brand_aliases)
    assert camp_res["facets"]["brand"] == ["NEMO", "Outdoor Vitals"]
    ov_res = query(camp_rows, {**BASE_FILTERS, "brand": "OutdoorVitals"}, now=NOW, brand_aliases=CAMP.brand_aliases)
    assert ov_res["total"] == 2
    assert {c["brand"] for c in ov_res["cards"]} == {"Outdoor Vitals"}

    # History survives brand casing/spacing changes without rewriting stored rows.
    hist_db = tmp_path / "hist.db"
    day1 = make_product("LIFT", "Lift4 eFoil", "Foils & wings", price=5500, reference=9000)
    day2 = make_product(" Lift ", "Lift4 eFoil", "Foils & wings", price=5000, reference=9000)
    save_source(hist_db, "watersports", "REAL Watersports", (NOW - timedelta(days=1)).isoformat(), [day1], "complete", {})
    save_source(hist_db, "watersports", "REAL Watersports", NOW.isoformat(), [day2], "complete", {})
    hist = read_offer_history(hist_db, "watersports", "REAL Watersports", day2, day2["offers"][0], now=NOW)
    assert [entry["price"] for entry in hist] == [5000, 5500]


def test_tideline_component_pack_guard_routes_wing_packs_and_preserves_complete_kits_and_boards(tmp_path):
    pack = make_product(
        "Cabrinha",
        "Mkii Foil Kit Wing Pack",
        "Foils & wings",
        options="X2100 | V285",
        price=399,
        reference=1199,
        domain="www.adventuresportsusa.com",
    )
    front_set = make_product(
        "Armstrong",
        "Hydrofoil Front Wing Set",
        "Foils & wings",
        options="HA925",
        price=499,
        reference=999,
        domain="www.adventuresportsusa.com",
    )
    complete_option_set = make_product(
        "Armstrong",
        "Hydrofoil Front Wing Set",
        "Foils & wings",
        options="Complete foil",
        price=899,
        reference=1599,
        domain="www.adventuresportsusa.com",
    )
    complete_option_set["url"] += "-complete"
    complete_kit = make_product("Cabrinha", "Mkii Foil Kit", "Foils & wings", price=899, reference=1599)
    plus_pack = make_product("Cabrinha", "Mkii Foil Kit + Wing Pack", "Foils & wings", price=999, reference=1899)
    board = make_product("Slingshot", "Slingshot Wing Craft V2", "Foils & wings", options="80 L | 4′9″ × 25.5″", price=399, reference=1649.99)
    wing = make_product("Ozone", "Ozone Fusion Ram Air Foil Wing", "Foils & wings", options="5.0m", price=699, reference=1299)

    rows = [make_source(
        "Adventure Sports USA",
        [pack, front_set, complete_option_set, complete_kit, plus_pack, board, wing],
    )]
    default_urls = {c["url"] for c in query(
        rows, BASE_FILTERS, now=NOW,
        brand_aliases=WATER.brand_aliases,
        opt_in_categories=WATER.opt_in_categories,
        reconcile_product=WATER.reconcile_product,
    )["cards"]}
    assert pack["url"] not in default_urls
    assert front_set["url"] not in default_urls
    assert {complete_option_set["url"], complete_kit["url"], plus_pack["url"], board["url"], wing["url"]} <= default_urls

    comp_cards = query(
        rows, {**BASE_FILTERS, "category": "Foil components"}, now=NOW,
        brand_aliases=WATER.brand_aliases,
        opt_in_categories=WATER.opt_in_categories,
        reconcile_product=WATER.reconcile_product,
    )["cards"]
    assert {c["url"] for c in comp_cards} == {pack["url"], front_set["url"]}
    pack_card = next(c for c in comp_cards if c["url"] == pack["url"])
    assert pack_card["offer"]["options"] == "X2100 | V285"

    # Ingestion classify() agrees with reconcile_product().
    ingested_pack = Product(
        "Mkii Foil Kit Wing Pack",
        "https://www.adventuresportsusa.com/products/mkii-wing-pack",
        399,
        1199,
        brand="Cabrinha",
        product_type="Hydrofoils",
        offers=[VariantOffer("1", "https://www.adventuresportsusa.com/products/mkii-wing-pack?variant=1", 399, 1199, options="X2100 | V285", availability="in_stock")],
    )
    assert classify_water(ingested_pack).category == "Foil components"

    # Web route + history link + portal hub exclusion work end to end.
    db = tmp_path / "water-packs.db"
    now = datetime.now(UTC)
    save_source(db, "watersports", "Adventure Sports USA", now.isoformat(), [pack, board], "complete", {})
    client = TestClient(create_app(WATER, db))
    default_soup = BeautifulSoup(client.get("/").text, "html.parser")
    assert [h.get_text(strip=True) for h in default_soup.select("article.deal-card h3")] == ["Slingshot Wing Craft V2"]
    comp_soup = BeautifulSoup(client.get("/?category=Foil+components").text, "html.parser")
    comp_card = comp_soup.select_one("article.deal-card")
    assert comp_card is not None
    assert "Mkii Foil Kit Wing Pack" in comp_card.select_one("h3").get_text()
    assert "X2100 | V285" in comp_card.get_text()
    assert client.get(comp_card.select_one(".history-link")["href"]).status_code == 200
    hub = read_shared_catalog_snapshot(db, "watersports", world_name="Tideline", min_reference=180, now=now, catalog=WATER)
    assert hub is not None and [h.name for h in hub.highlights] == ["Slingshot Wing Craft V2"]


def test_bench_drop_deduplicates_bare_tool_and_tool_only_badges(tmp_path):
    assert deduplicate_badge_labels(["Bare tool", "Tool only", "Bare-Tool", "Makita LXT"]) == ["Bare tool", "Makita LXT"]

    makita_empty_opts = Product(
        "Makita XVJ04Z 18V LXT Brushless Cordless Jig Saw, Tool Only",
        "https://fastenersinc.myshopify.com/products/xvj04z",
        99,
        219,
        product_type="Jig Saws",
        brand="Makita",
        offers=[VariantOffer("1", "https://fastenersinc.myshopify.com/products/xvj04z?variant=1", 99, 219, options="", availability="in_stock")],
    )
    makita_dup_opts = Product(
        "Makita XVJ04Z-OPT 18V LXT Brushless Jig Saw, Tool Only",
        "https://fastenersinc.myshopify.com/products/xvj04z-opt",
        105,
        219,
        product_type="Jig Saws",
        brand="Makita",
        offers=[VariantOffer("2", "https://fastenersinc.myshopify.com/products/xvj04z-opt?variant=2", 105, 219, options="Bare tool", availability="in_stock")],
    )
    dewalt_tool_only = Product(
        "DeWALT 20V MAX 6-1/2-in. Brushless Cordless Circular Saw, Tool Only (PROMO DCS565B)",
        "https://fastenersinc.myshopify.com/products/dcs565b",
        134.99,
        238,
        product_type="Circular Saws",
        brand="DeWalt",
        offers=[VariantOffer("3", "https://fastenersinc.myshopify.com/products/dcs565b?variant=3", 134.99, 238, options="18V / Tool Only", availability="in_stock")],
    )
    dewalt_unknown = Product(
        "DEWALT, DCW600B, Router, 20 V, Brushless Motor, Cordless",
        "https://fastenersinc.myshopify.com/products/dcw600b",
        99,
        249,
        product_type="Routers",
        brand="DeWalt",
        offers=[VariantOffer("4", "https://fastenersinc.myshopify.com/products/dcw600b?variant=4", 99, 249, options="", availability="in_stock")],
    )

    db = tmp_path / "tools-badges.db"
    save_source(
        db,
        "tools",
        "Fasteners Inc",
        datetime.now(UTC).isoformat(),
        normalize([makita_empty_opts, makita_dup_opts, dewalt_tool_only, dewalt_unknown], TOOLS, "fastenersinc.myshopify.com"),
        "complete",
        {},
    )
    client = TestClient(create_app(TOOLS, db))
    soup = BeautifulSoup(client.get("/").text, "html.parser")
    cards = {c.select_one("h3").get_text(strip=True): c for c in soup.select("article.deal-card")}

    for title, expected_platform in [
        (makita_empty_opts.name, "Makita LXT"),
        (makita_dup_opts.name, "Makita LXT"),
        (dewalt_tool_only.name, "DeWalt 20V MAX"),
    ]:
        card = cards[title]
        card_text = card.get_text(" ", strip=True)
        assert card_text.count("Bare tool") == 1, title
        assert "Tool only" not in card_text
        assert expected_platform in [s.get_text(strip=True) for s in card.select(".tags span")]
    assert "18V" in cards[dewalt_tool_only.name].select_one(".variant").get_text(strip=True)

    unknown_text = cards[dewalt_unknown.name].get_text(" ", strip=True)
    assert "Bare tool" not in unknown_text
    assert "Standard configuration" not in unknown_text
    assert unknown_text.count("Battery/charger inclusion not specified") == 1


def test_bench_drop_groups_exact_jet_jat127_equivalents_and_keeps_drive_or_package_distinct(tmp_path):
    tool_nut = make_product(
        "JET",
        "JET JAT-127, 3/4-in. Composite Impact Wrench (505127)",
        "Power tools",
        fmt="Not specified",
        price=299.99,
        reference=580.99,
        domain="www.toolnut.com",
    )
    oaktree = make_product(
        "JET",
        'JET JAT-127 3/4" Composite Pneumatic Impact Wrench',
        "Power tools",
        fmt="Not specified",
        price=299.0,
        reference=560.99,
        domain="www.oaktreesupply.com",
    )
    half_inch = make_product(
        "JET",
        'JET JAT-127 1/2" Composite Pneumatic Impact Wrench',
        "Power tools",
        fmt="Not specified",
        price=249.0,
        reference=500.0,
        domain="beavertools.com",
    )
    kit_version = make_product(
        "JET",
        'JET JAT-127 3/4" Composite Impact Wrench Kit',
        "Power tools",
        fmt="Kit",
        price=349.0,
        reference=650.0,
        domain="www.woodcraft.com",
    )

    db = tmp_path / "tools-jet.db"
    now = datetime.now(UTC)
    save_source(db, "tools", "Tool Nut", now.isoformat(), [tool_nut], "complete", {})
    save_source(db, "tools", "OakTree Supply", now.isoformat(), [oaktree], "complete", {})
    save_source(db, "tools", "Beaver Industrial Supply", now.isoformat(), [half_inch], "complete", {})
    save_source(db, "tools", "Woodcraft", now.isoformat(), [kit_version], "complete", {})

    client = TestClient(create_app(TOOLS, db))
    soup = BeautifulSoup(client.get("/").text, "html.parser")
    cards = soup.select("article.deal-card")
    # Half-inch and Kit stay distinct; Tool Nut + OakTree 3/4" collapse to 1 card.
    assert len(cards) == 3
    jet_cards = [c for c in cards if c.select_one(".product-store").get_text(strip=True) == "Tool Nut"]
    assert len(jet_cards) == 1
    assert jet_cards[0].select_one(".other-shops-link").get_text(strip=True) == "+1 shop"
    # Distinct drive/package cards have no +1 shop link.
    for other in cards:
        if other is not jet_cards[0]:
            assert other.select_one(".other-shops-link") is None

    compare_url = jet_cards[0].select_one(".other-shops-link")["href"]
    assert "group=" in compare_url and "compare=true" in compare_url
    compare_cards = BeautifulSoup(client.get(compare_url).text, "html.parser").select("article.deal-card")
    assert len(compare_cards) == 2
    price_pairs = {
        c.select_one(".product-store").get_text(strip=True): (
            c.select_one(".prices strong").get_text(strip=True),
            c.select_one(".reference s").get_text(strip=True),
        )
        for c in compare_cards
    }
    assert price_pairs == {
        "Tool Nut": ("$299.99", "$580.99"),
        "OakTree Supply": ("$299", "$560.99"),
    }


def test_tideline_wing_craft_v2_prevents_cross_size_exact_shop_badge_and_isolates_compare(tmp_path):
    board_80_a = make_product(
        "Slingshot",
        "Slingshot Wing Craft V2 Wing Foil Board",
        "Foils & wings",
        options="80 L | 4′9″ × 25.5″",
        price=399,
        reference=1649.99,
        domain="houstonkiteboarding.com",
    )
    board_70_b = make_product(
        "Slingshot",
        "Slingshot Wing Craft V2",
        "Foils & wings",
        options="70 L | 4′5″ × 25.5″ × 4″",
        price=449,
        reference=1811,
        domain="forcekiteandwake.com",
    )
    board_unknown_d = make_product(
        "Slingshot",
        "Slingshot Wing Craft V2",
        "Foils & wings",
        options="",
        price=499,
        reference=1649.99,
        domain="urbansurf.com",
    )
    board_conflict_dim = make_product(
        "Slingshot",
        "Slingshot Wing Craft V2",
        "Foils & wings",
        options="80 L | 4′11″ × 27″",
        price=489,
        reference=1649.99,
        domain="elitewatersports.com",
    )

    db = tmp_path / "water-exact.db"
    now = datetime.now(UTC)
    save_source(db, "watersports", "Houston Kiteboarding", now.isoformat(), [board_80_a], "complete", {})
    save_source(db, "watersports", "Force Kite & Wake", now.isoformat(), [board_70_b], "complete", {})
    save_source(db, "watersports", "Urban Surf", now.isoformat(), [board_unknown_d], "complete", {})
    save_source(db, "watersports", "Elite Watersports", now.isoformat(), [board_conflict_dim], "complete", {})

    client = TestClient(create_app(WATER, db))
    soup = BeautifulSoup(client.get("/").text, "html.parser")
    cards = soup.select("article.deal-card")
    assert len(cards) == 4
    assert all(c.select_one(".other-shops-link") is None for c in cards)

    # Add Shop C with BOTH a deeper-discount 70 L variant and an eligible 80 L variant.
    board_c = make_product(
        "Slingshot",
        "Slingshot Wing Craft V2",
        "Foils & wings",
        domain="www.windance.com",
        offers=[
            {
                "url": "https://www.windance.com/products/wing-craft?variant=70l",
                "variant_key": "70l",
                "size": "70L",
                "options": "70L (4'5\" x 25.5\" x 4\")",
                "availability": "in_stock",
                "current_price": 329.0,
                "original_price": 1811.0,
                "image_url": None,
                "reference_basis": "retailer_compare_at",
            },
            {
                "url": "https://www.windance.com/products/wing-craft?variant=80l",
                "variant_key": "80l",
                "size": "80L",
                "options": "80L - 4'9\" x 25.5\"",
                "availability": "in_stock",
                "current_price": 420.0,
                "original_price": 1649.99,
                "image_url": None,
                "reference_basis": "retailer_compare_at",
            },
        ],
    )
    save_source(db, "watersports", "Windance Boardshop", now.isoformat(), [board_c], "complete", {})

    client2 = TestClient(create_app(WATER, db))
    soup2 = BeautifulSoup(client2.get("/").text, "html.parser")
    by_store = {c.select_one(".product-store").get_text(strip=True): c for c in soup2.select("article.deal-card")}

    # Houston (80 L) discovers Windance's eligible 80 L even though Windance's card ranked 70 L first.
    houston_link = by_store["Houston Kiteboarding"].select_one(".other-shops-link")
    assert houston_link is not None and houston_link.get_text(strip=True) == "+1 shop"
    # Urban Surf (missing size) and Elite Watersports (conflicting dims) remain unbadged.
    assert by_store["Urban Surf"].select_one(".other-shops-link") is None
    assert by_store["Elite Watersports"].select_one(".other-shops-link") is None

    # Following Houston's 80 L compare link returns ONLY Houston (80 L) and Windance (80 L), never Force (70 L).
    compare_80 = BeautifulSoup(client2.get(houston_link["href"]).text, "html.parser").select("article.deal-card")
    assert {c.select_one(".product-store").get_text(strip=True) for c in compare_80} == {
        "Houston Kiteboarding",
        "Windance Boardshop",
    }
    windance_80 = next(c for c in compare_80 if c.select_one(".product-store").get_text(strip=True) == "Windance Boardshop")
    assert windance_80.select_one(".prices strong").get_text(strip=True) == "$420"
    assert "80L" in windance_80.get_text()
    assert not any("Force Kite & Wake" in c.get_text() for c in compare_80)
