from datetime import UTC, datetime

import pytest
from deal_common.catalog.query import query
from deal_common.catalog.refresh import normalize
from deal_common.models import Product
from deal_common.offers import VariantOffer
from tool_deals.catalog import CATALOG, classify


@pytest.mark.parametrize(("name", "kind", "category", "form", "platform"), [
    ("DeWalt 20V MAX Drill Bare Tool", "Drills", "Power tools", "Bare tool", "DeWalt 20V MAX"),
    ("Milwaukee M18 5-Tool Combo Kit", "Combo Kits", "Combo kits", "Kit", "Milwaukee M18"),
    ("Makita LXT Circular Saw Kit", "Saws", "Power tools", "Kit", "Makita LXT"),
    ("10 Inch Table Saw", "Saws", "Workshop equipment", "Not specified", ""),
    ("DeWalt 20V MAX Battery Starter Kit", "Batteries and Chargers", None, "", ""),
    ("Table Saw Replacement Blade", "Parts", None, "", ""),
    ("Reconditioned Drill", "Drills", None, "", ""),
    ("20V MAX Drill Kit", "Drills", "Power tools", "Kit", ""),
    ("Router Corner Radius Jigs/Templates - Small", "Router Jigs", None, "", ""),
    ("Nova Modular Tool Rest", "Lathe Accessories", None, "", ""),
    ("Nova Spindle Adaptor", "Lathe", None, "", ""),
    ("Drilling Guide & Stop Collar", "Drills", None, "", ""),
    ("Belt Sander", "Sanders", "Power tools", "Not specified", ""),
    ("DeWALT 20V MAX Deep Cut Band Saw, Tool Only", "Band Saw", "Power tools", "Bare tool", "DeWalt 20V MAX"),
    ("JET 18-in. Scroll Saw", "Saws", "Workshop equipment", "Not specified", ""),
    ("JET Industrial Disc Finishing Machine", "Sander", "Workshop equipment", "Not specified", ""),
    ("3-PC. Professional 10 Inch Table Saw Package", "Saw Blades > Dado Sets", None, "", ""),
    ("3-Pc. Cabinet Door Shape-Up Shaper Cutter Sets", "Shaper Cutters", None, "", ""),
    ("Carbide Tipped Planer & Jointer Knives", "Power Tools > Jointers & Planers", None, "", ""),
    ("4-Pc. Auto Dust Collection Pack with VAC Switch", "Dust Collection", None, "", ""),
    ("Adjustable Drill Depth Stops", "", None, "", ""),
    ("Universal Saw Indicator", "Measuring & Layout > Gauges", None, "", ""),
    ("Drill Press Sharpening Station", "Power Tools > Drill Presses", None, "", ""),
    ("10 Inch Cabinet Saw with Big Eye Rip Fence", "Table Saw", "Workshop equipment", "Not specified", ""),
    ("M18 Recon BL Hex Impact Driver", "Power Tools", None, "", ""),
    ("Portacube Miter Saw Work Station", "Miter Saws", None, "", ""),
    ("Bandsaw Outfeed Table", "", None, "", ""),
    ("5 Inch Gray Industrial Fixed Caster", "Casters", None, "", ""),
    ("How to Run a Lathe", "Books", None, "", ""),
    ("1 Inch Staples for Flooring Stapler", "Staples", None, "", ""),
    ("Driver Kit for G3811", "", None, "", ""),
    ("Bi-Metal Hole Saw Sets", "", None, "", ""),
    ("Benchtop Machine Cabinet", "", None, "", ""),
    ("24 Inch Wide-Belt Sander", "Sanders", "Workshop equipment", "Not specified", ""),
    ('15" Planer - 3 HP - Straight Knives', "Planers", "Workshop equipment", "Not specified", ""),
    ("JET Industrial Buffer", "Buffers", "Workshop equipment", "Not specified", ""),
    ("Milwaukee M18 FUEL Jig Saw, Tool Only", "Jig Saws", "Power tools", "Bare tool", "Milwaukee M18"),
    ("DeWalt 20V MAX XR Brushless Cordless Jigsaw", "Jig Saws", "Power tools", "Not specified", "DeWalt 20V MAX"),
    ("DeWalt 20V MAX XR Oscillating Tool Kit", "Power Tools", "Power tools", "Kit", "DeWalt 20V MAX"),
    ("Milwaukee M12 Cordless Ratchet, Tool Only", "Ratchets", "Power tools", "Bare tool", "Milwaukee M12"),
    ("Pneumatic Ratchet", "Ratchets", "Power tools", "Not specified", ""),
    ("1/2 Inch Ratchet", "Ratchets", None, "", ""),
    ("Cordless Ratchet Replacement Head", "Ratchets", None, "", ""),
    ("Jigsaw Blade Set", "Power Tools", None, "", ""),
    ("DEWALT DCS334B Cordless Jig Saw 20V Brushless 1 in Blade Stroke Length 4-Position Orbital Action",
     "Jig Saws", "Power tools", "Not specified", ""),
    ("Replacement Jigsaw Blade for 1 in Blade Stroke Length", "Jig Saws", None, "", ""),
    ("Jigsaw with 1 in Blade Stroke", "Saw Blades", None, "", ""),
    ("DEWALT DCP580B Planer Brushless Motor 20 V 3.25 in Cutting Width 15000 RPM Cordless",
     "Planers", "Power tools", "Not specified", ""),
    ("Cordless Planer Replacement Knives", "Planers", None, "", ""),
    ("Oscillating Multi-Tool Blade Kit", "Power Tools", None, "", ""),
    ("Kreg Precision Router Table System", "", None, "", ""),
    ("SCM Formula Band Saw (Showroom Model)", "Machinery", None, "", ""),
    ("Display Model Table Saw", "Table Saws", None, "", ""),
    ("Demo CNC Router", "CNC Router", None, "", ""),
    ("CNC Router with Digital Display", "CNC Router", "Workshop equipment", "Not specified", ""),
    ("20V Brushless Handheld Planer - Tool Only", "Planers", "Power tools", "Bare tool", ""),
    ("Chef'n Plastic Pepper Ball Grinder", "Pepper Ball Grinder", None, "", ""),
    ("Electric Coffee Grinder", "Grinders", None, "", ""),
    ("Brushless Angle Grinder", "Grinders", "Power tools", "Not specified", ""),
    ("Milwaukee M18 FUEL 1/2-in. High Torque Impact Wrench with Friction Ring 5.0Ah Kit (2967-21B)",
     "Impact Wrench", "Power tools", "Kit", "Milwaukee M18"),
    ("DEWALT, DCV501HB, Hand Vacuum, 20V, Cordless, Wet or Dry, HEPA Filtration, 21 min Runtime",
     "Filters", "Power tools", "Not specified", ""),
])
def test_scope_package_and_platform(name, kind, category, form, platform):
    actual = classify(Product(name, "https://example.com", 200, 500, product_type=kind))
    if category is None:
        assert actual is None
    else:
        assert (actual.category, actual.format, actual.platform) == (category, form, platform)


@pytest.mark.parametrize(("title", "kind"), [
    ("Fletcher-Terry FrameMaster Flat 3/8 in. W Point Driver 1 pk", "Point Driver"),
    ('Saw Minihack8-3/4"Stanly (Pack Of 6)', "Hacksaw"),
    ("Irwin Impact Performance Series 5/16 in. Metric Lobular Power Nut Driver 1-7/8 in. L 1 pc", "Nut Driver"),
    ("Irwin Impact Performance 1/4 in. SAE Lobular Power Nut Driver 1-7/8 in. L 1 pc", "Nut Driver"),
    ("Rockwell Metal Sanding Pad 1 in. 20000 rpm 2 pc", "Oscillating Tool Access"),
    ("FLEX FAM10301-4 2-9/16 in 4-Piece Impact Magnetic Nut Driver Set", "Nut Setters"),
    ("Impact Nutsetter for Cordless Impact Driver", "Power Tools"),
    ("Power Nut Setter Compatible with Electric Drill", "Drills"),
    ("Lobular Power Nut Driver 1-7/8 in.", "Nut Driver"),
    ("Nut Driver for Cordless Drill", "Nut Driver"),
    ("FrameMaster Point Driver for Electric Drill", "Point Driver"),
    ("Point Driver Compatible with Pneumatic Tools", "Point Driver"),
    ("Cordless Drill Compatible Point Driver", "Point Driver"),
    ("Cordless Drill Compatible Power Nut Driver", "Nut Driver"),
    ("Electric Drill Compatible Hacksaw", "Hacksaw"),
    ("Hand Hacksaw for Cordless Saw", "Hacksaw"),
    ("Hacksaw Including Electric Drill", "Hacksaw"),
    ("Oscillating Tool Sanding Pad Compatible with Cordless Sander", "Power Tools"),
    ("Sanding Pads for 18V Oscillating Tool", "Power Tools"),
    ("Sanding Pad with Free Cordless Sander", "Power Tools"),
    ("Cordless Oscillating Tool", "Oscillating Tool Access"),
    ("Non-powered Hacksaw", "Hacksaw"),
    ("Hand-Powered Point Driver", "Point Driver"),
    ("Hand-Operated Cordless Point Driver", "Point Driver"),
    ("Manual Pneumatic Nut Driver", "Nut Driver"),
])
def test_manual_tools_bits_and_compatibility_accessories_excluded(title, kind):
    assert classify(Product(title, "https://example.com/products/tool", 100, 200, product_type=kind)) is None


@pytest.mark.parametrize(("title", "kind", "form"), [
    ("Electric Point Driver", "Point Driver", "Not specified"),
    ("Cordless Point Driver Kit", "Point Driver", "Kit"),
    ("Pneumatic Point Driver with Points", "Point Driver", "Not specified"),
    ("Pneumatic Point Driver Compatible with Mounting Hardware", "Point Driver", "Not specified"),
    ("Battery-Powered Point Driver", "Point Driver", "Not specified"),
    ("Corded Hacksaw", "Hacksaw", "Not specified"),
    ("Electric Hack Saw", "Hacksaw", "Not specified"),
    ("Cordless Hacksaw with Blade Kit", "Hacksaw", "Not specified"),
    ("Pneumatic Nut Driver", "Nut Driver", "Not specified"),
    ("Cordless Impact Driver Kit with Nutsetter Bits", "Power Tools", "Kit"),
    ("Orbital Sander Kit with Sanding Pads", "Sanders", "Kit"),
    ("Cordless Oscillating Tool Kit Including Sanding Pad", "Power Tools", "Kit"),
    ("Pneumatic Point Driver Kit w/ Nut Driver Bits", "Point Driver", "Kit"),
    ("Electric Point Driver with Owner Manual", "Point Driver", "Not specified"),
    ("Cordless Point Driver Including Manual", "Point Driver", "Not specified"),
    ("Pneumatic Point Driver Compatible with Hand-Operated Hardware", "Point Driver", "Not specified"),
    ("Handheld Cordless Point Driver", "Point Driver", "Not specified"),
    ("Non-Electric Pneumatic Nut Driver", "Nut Driver", "Not specified"),
])
def test_explicit_powered_machines_and_kits_with_included_accessories_survive(title, kind, form):
    actual = classify(Product(title, "https://example.com/products/tool", 100, 200, product_type=kind))
    assert actual is not None
    assert (actual.category, actual.format, actual.platform) == ("Power tools", form, "")


@pytest.mark.parametrize(("name", "kind", "category", "form"), [
    ("Festool RTS 400 Orbital Sander + CT 26 EI HEPA Dust Extractor Bundle w/ Abrasives & Filter Bags RTS+CT26",
     "Power Tools", "Workshop equipment", "Bundle"),
    ("Fein MULTIMASTER AMM 700 MAX Black Edition 18V Cordless Oscillating Multi-Tool Bundle with 6-Piece E-Cut Blade Set 7129476109035222967010",
     "Power Tools", "Power tools", "Bundle"),
    ('Bosch 18V Brushless 6-1/2" Blade Right Circular Saw (Bare) GKS18V-22N',
     "Power Tools", "Power tools", "Not specified"),
    ('Fein MCCS14 14" Slugger Metal Cutting Chop Saw Bundle w/ Free MCBL14 Mild Steel Blade',
     "Bundles", "Power tools", "Bundle"),
    ("Dust Collector with Filter", "Dust Collection", "Workshop equipment", "Not specified"),
    ("Table Saw with Stand", "Table Saws", "Workshop equipment", "Not specified"),
    ("Dust Extractor with Filter Bags", "Power Tools", "Workshop equipment", "Not specified"),
    ("Cordless Drill with Blade Kit", "Power Tools", "Power tools", "Not specified"),
    ("Circular Saw with Blade Bundle", "Power Tools", "Power tools", "Not specified"),
    ("Cordless Drill Kit Including Drill Bits", "Power Tools", "Power tools", "Kit"),
    ("Bosch EVS Fixed-Base Router w/ Trigger Control, 2.3 HP MRF23EVS",
     "Power Tools", "Power tools", "Not specified"),
    ('Bosch 18V Brushless 4-1/2" Angle Grinder with Slide Switch (Bare Tool) GWS18V-8-2N',
     "Power Tools", "Power tools", "Bare tool"),
    ('Ionic Drive 1/4" Trim Router with 2AH Battery and Charger Kit',
     "Routers", "Power tools", "Kit"),
])
def test_complete_tools_keep_explicitly_included_accessories(name, kind, category, form):
    actual = classify(Product(name, "https://example.com/products/tool", 300, 500, product_type=kind))
    assert actual is not None
    assert (actual.category, actual.format, actual.platform) == (category, form, "")


@pytest.mark.parametrize(("name", "kind", "tags"), [
    ("Festool Filter Bags with Free Orbital Sander", "Power Tools", ()),
    ("Circular Saw Blade with Drill Bundle", "Power Tools", ()),
    ("Circular Saw Blades with Drill Bundle", "Power Tools", ()),
    ("Replacement Circular Saw with Free Blade", "Power Tools", ()),
    ("6-Piece Blade Set with Oscillating Multi-Tool", "Power Tools", ()),
    ("Work Station with Circular Saw", "Miter Saws", ()),
    ("Table Saw Stand with Blade Set", "Power Tools", ()),
    ("Dust Extractor Bag with Filter", "Power Tools", ()),
    ("Table Saw Fence with Blade", "Power Tools", ()),
    ("Router Base with Drill Bits", "Power Tools", ()),
    ("Track Saw Guide with Blade Set", "Power Tools", ()),
    ("Circular Saw Case with Replacement Blade", "Power Tools", ()),
    ("Table Saw Guard with Blade", "Power Tools", ()),
    ("Bundle with Circular Saw", "Bundles", ()),
    ("Cordless Drill with Bits", "Drill Bits", ()),
    ("Dust Collector with Filter", "Dust Collector Parts", ()),
    ("Orbital Sander Bundle with Filter Bags", "Consumables", ()),
    ("Blade Right Circular Saw Replacement Blade", "Power Tools", ()),
    ("Circular Saw with Reconditioned Blade", "Power Tools", ()),
    ("Circular Saw with Blade", "Power Tools", ("open_box",)),
    ("Fein E-Cut Long Life Saw Blade Starlock, 1-3/8 in, 50-Pack 63502160250", "Consumables", ()),
    ("Powermatic PM1250 Micro Dust Air Filtration System 1791331", "Air Filtration", ()),
    ("Fixed-Base Router Stand with Blade Set", "Power Tools", ()),
    ("Replacement Dust Collector Filter with Saw", "Filters", ()),
    ("Circular Saw with Blade", "Consumable", ()),
    ("Bundle with Circular Saw Blade", "Table Saw", ()),
    ("Tool Set with Blade", "Table Saw", ()),
])
def test_included_wording_does_not_admit_accessory_primary_or_bad_condition(name, kind, tags):
    assert classify(Product(name, "https://example.com/products/tool", 300, 500, product_type=kind, tags=tags)) is None


@pytest.mark.parametrize(("name", "kind", "tags", "form"), [
    ("Hobart IronMan 240 MIG Welder - 500574", "MIG Welding Machine", (), "Not specified"),
    ("Fronius Ignis 150 Battery Stick Welder", "Battery Powered Welder", (), "Not specified"),
    ("Engine Driven Welder / Generator", "Welder / Generator", (), "Not specified"),
    ("Plasma Cutting Machine", "Plasma Cutting Machine", (), "Not specified"),
    ("Miller Dynasty 300 Wireless Foot Control Complete 951937", "Welders", ("TIG Welder",), "Not specified"),
    ("Miller Deltaweld 350 w/ArcConnect", "Welders", ("MIG Welder",), "Not specified"),
    ("Miller XMT 400 MIGRunner w/Intellx Wire Feeder", "Welders", ("MIG Welder",), "Not specified"),
    ("TIG Welding Machine with Cold Wire Feed Unit", "TIG Welding Machine", (), "Not specified"),
    ("Lincoln SP140-T Wire Feeder Welder K5261-1", "Welders", ("MIG Welder",), "Not specified"),
    ("Lincoln SP-140T Wire Feeder Welder - K5261-1", "MIG Welding Machine", ("Wire Feeder",), "Not specified"),
    ("ESAB Renegade VOLT EMP 200i Spool Gun Package Battery-Powered Welder", "Welders", ("Multiprocess Welder",), "Not specified"),
    ("Everlast Cyclone 200ES with SM3035N Spoolgun Bundle", "Welders", ("MIG Welder",), "Not specified"),
    ("Everlast Typhoon 230 AC/DC TIG Cooler, Cart, Wireless Bundle", "Welders", ("TIG Welder",), "Bundle"),
    ("Hypertherm Powermax45 SYNC Plasma Cutter (No Torch)", "Welders", ("Plasma Cutter",), "Not specified"),
    ("Baker's Thunderbolt 160/210 Essentials Bundle", "Baker's Bundle",
     ("Brand_Miller Electric", "Miller Stick Machine", "Miller Welding Machine"), "Bundle"),
])
def test_complete_welding_equipment_and_fixed_packages(name, kind, tags, form):
    actual = classify(Product(name, "https://example.com/products/machine", 1500, 2500, product_type=kind, tags=tags))
    assert actual is not None
    assert (actual.category, actual.format, actual.platform) == ("Workshop equipment", form, "")


@pytest.mark.parametrize(("name", "kind", "tags"), [
    ("Miller XR-Pistol-Pro Gun", "Welders", ("Spool Guns & Wire Feeders",)),
    ("Miller XR-AlumaPro W 25 ft", "Welders", ("Spool Guns & Wire Feeders",)),
    ("Miller ArcReach Smart Feeder", "Welders", ("Wire Feeders",)),
    ("Miller ArcReach 16 Wire Feeder w/Shop Cart", "Welders", ("Wire Feeder",)),
    ("Spool Gun with MIG Welder", "Welders", ("MIG Welder",)),
    ("Wire Feeder for MIG Welder", "Welders", ("MIG Welder",)),
    ("CK Worldwide Cold Wire Feed Unit Model #5 - WF5-023", "TIG Welding Machine", ()),
    ("TIG Welder Wire Feed Unit", "Welders", ("TIG Welder",)),
    ("TIG Welder Foot Control", "Welders", ("TIG Welder",)),
    ("Wireless Foot Control for TIG Welder", "Welders", ("TIG Welder",)),
    ("Plasma Cutter Torch", "Welders", ("Plasma Cutter",)),
    ("MIG Welder Cart", "Welders", ("MIG Welder",)),
    ("MIG Gun Liner", "MIG Gun Liner", ()),
    ("Dynasty 300", "Welders", ()),
    ("TIG Welding Machine", "Machine Accessories", ("TIG Welder",)),
    ("Refurbished TIG Welding Machine", "TIG Welding Machine", ()),
    ("TIG Welding Machine with used Foot Control", "TIG Welding Machine", ()),
    ("TIG Welding Machine", "TIG Welding Machine", ("open_box",)),
    ("TIG Welding Machine", "TIG Welding Machine", ("Wire Feeder", "TIG Welder")),
    ("Machine Essentials Bundle", "Baker's Bundle", ("Brand_Miller Electric", "For Miller Welding Machine")),
    ("Machine Essentials Bundle", "Baker's Bundle", ("Other Welding Machine",)),
    ("Machine Essentials Bundle", "Baker's Bundle", ("Miller Welding Machine",)),
])
def test_welding_components_generic_taxonomy_and_condition_excluded(name, kind, tags):
    assert classify(Product(name, "https://example.com/products/component", 1500, 2500, product_type=kind, tags=tags)) is None


def test_brand_aliases_bundle_format_and_query_preserve_exact_offers():
    for brand, expected in [
        ("Sawstop", "SawStop"), ("Jet Tools", "JET"), ("Kreg Tool", "Kreg"),
        ("Miller Electric", "Miller"), ("Lincoln Electric", "Lincoln"), ("Hobart Welding Products", "Hobart"),
        ("Different Tools", "Different Tools"), ("In Stock", "Unspecified"), ("ESD：Nov.30（Black）", "Unspecified"),
    ]:
        product = Product(
            "Table Saw", "https://example.com/products/saw", 200, 500, brand=brand,
            offers=[VariantOffer("1", "https://example.com/products/saw?variant=1", 200, 500, availability="in_stock")],
        )
        assert normalize([product], CATALOG, "example.com")[0]["brand"] == expected

    bundle_url = "https://beavertools.com/products/festool-rts-400-bundle"
    bundle = Product(
        "Festool RTS 400 Orbital Sander + CT 26 EI HEPA Dust Extractor Bundle w/ Abrasives & Filter Bags RTS+CT26",
        bundle_url, 1248, 1385, brand="Festool", product_type="Power Tools",
        offers=[VariantOffer("67863854284893", f"{bundle_url}?variant=67863854284893", 1248, 1385, availability="in_stock")],
    )
    normalized_bundle = normalize([bundle], CATALOG, "beavertools.com")
    bundle_source = {"name": "Beaver Industrial Supply", "successful_at": datetime.now(UTC).isoformat(), "products": normalized_bundle}
    bundle_filters = {"min_reference": 200, "max_price": 0, "min_discount": 0, "stock": True, "page": 1, "format": "Bundle"}
    bundle_result = query([bundle_source], bundle_filters)
    assert bundle_result["total"] == 1
    card = bundle_result["cards"][0]
    assert (card["category"], card["offer"]["variant_key"], card["offer"]["current_price"]) == (
        "Workshop equipment", "67863854284893", 1248,
    )
    assert query([bundle_source], {**bundle_filters, "format": "Kit"})["total"] == 0

    sources = []
    for store, domain, brand, key, price in [
        ("Bakers", "bakersgas.com", "Miller Electric", "123", 1800),
        ("Cyberweld", "store.cyberweld.com", "Miller", "456", 1700),
    ]:
        url = f"https://{domain}/products/welder"
        welder = Product(
            "Miller TIG Welding Machine", url, price, 3000, brand=brand, product_type="TIG Welding Machine",
            offers=[VariantOffer(key, f"{url}?variant={key}", price, 3000, availability="in_stock")],
        )
        sources.append({"name": store, "successful_at": datetime.now(UTC).isoformat(), "products": normalize([welder], CATALOG, domain)})
    result = query(sources, {"min_reference": 200, "min_discount": 0, "max_price": 0, "brand": "Miller", "stock": True, "page": 1})
    assert result["facets"]["brand"] == ["Miller"]
    assert [(c["store"], c["offer"]["variant_key"], c["offer"]["current_price"]) for c in result["cards"]] == [
        ("Cyberweld", "456", 1700), ("Bakers", "123", 1800),
    ]
