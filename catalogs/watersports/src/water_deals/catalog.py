import re
from pathlib import Path

from deal_common.catalog.definition import Catalog, Classification
from deal_common.config import RetailerConfig
from deal_common.models import Product

WATER_SIZE_PATTERN = (
    r"(?:"
    r"(?:[2-4]?X{0,2}[SML]T?|MS|LS|XLS|SM|MD|LG)"
    r"|(?:\d(?:\.\d)?\s*m(?:2|²)?\b)"
    r"|(?:\d{1,3}(?:\.\d)?\s*(?:L|cm|cm²)\b)"
    r"|(?:\d'\d{1,2}\"?)"
    r"|(?:\d{1,2}\s*wt\b)"
    r"|(?:\d/\d\s*mm\b)"
    r")(?:[ /(#-].*)?"
)

_EXCLUDED_TAGS = frozenset({
    "used", "pre-owned", "demo", "ex-demo", "rental", "consignment",
    "factory second", "blem", "blemished", "open box", "gift card", "pickup only",
})

_EXCLUDED_TEXT_RE = re.compile(
    r"\b("
    r"kids?|youths?|junior|groms?|boys|girls|toddlers?|child(?:ren)?|"
    r"used|pre-owned|demo|ex-demo|rental|consignment|refurbished|blem(?:ished)?|open.box|repaired|"
    r"gift ?cards?|gift ?certificates?|e-?gift|store credit|stickers?|decals?|"
    r"(?:shipping|package|order|damage) (?:protection|insurance)|navidium|route protection|"
    r"(?:extended|accident) warrant(?:y|ies)|protection plans?|(?:reservation|pre[- ]?order) deposits?|"
    r"surf wax|leash(?:es)?|traction pads?|deck pads?|tail pads?|board ?bags?|board ?socks?|"
    r"rash ?guards?|boardshorts?|walkshorts?|t-?shirts?|tees?|hoodies?|hats?|caps?|beanies?|towels?|ponchos?|"
    r"flip[- ]?flops?|sandals?|sunglasses|sunscreen|ear ?plugs?|wetsuit shampoo|wetsuit hanger|changing mat|"
    r"screws?|hardware kits?|t-?nuts?|shims?|fuselage covers?|wing covers?|mast covers?|foil covers?|"
    r"pumps?(?!\s+foil)|repair kits?|patch kits?|valve adapters?|straps?|footstraps?|harnesses?(?!\s+pack)|booms?|"
    r"fly line|backing|leaders?|tippets?|flies\b|nymphs?|streamers?|dry flies|fly box(?:es)?|"
    r"nippers?|forceps|hemostats?|floatants?|indicators?|split shot|zingers?|rod tubes?|reel cases?|rod racks?"
    r")\b"
)

_FOIL_WING_RE = re.compile(
    r"\b("
    r"hydrofoils?|foil (?:kits?|packages?|sets?|boards?|masts?|front wings?)|"
    r"efoils?|wing[- ]?foil(?:ing)?|hand[- ]?wings?|foil wings?|"
    r"(?:duotone|armstrong|axis|f-one|cabrinha|slingshot|north|naish|reedin|ozone|sic|gofoil)\b.*\b(?:wing|foil)\b"
    r")\b"
)
_KITE_RE = re.compile(
    r"\b("
    r"kiteboarding kites?|kitesurf(?:ing)? kites?|twin[- ]?tips?|kiteboards?|"
    r"(?:duotone|cabrinha|north|naish|core|slingshot|reedin|airush|ozone)\b.*\bkites?\b"
    r")\b"
)
_BOARD_SUP_RE = re.compile(
    r"\b("
    r"surfboards?|shortboards?|longboards?|funboards?|fish surfboards?|"
    r"paddle ?boards?|stand[- ]?up paddle|inflatable sups?|touring sups?|downwind boards?|kayaks?|packrafts?"
    r")\b"
)
_WETSUIT_RE = re.compile(
    r"\b("
    r"wetsuits?|fullsuits?|springsuits?|shorty wetsuits?|steamer wetsuits?|drysuits?|"
    r"[3456]/[2345]\s*(?:mm\b)?\s*(?:chest zip|back zip|hooded|full)"
    r")\b"
)
_FLY_RE = re.compile(
    r"\b("
    r"fly rods?|spey rods?|switch rods?|euro[- ]?nymph(?:ing)? rods?|"
    r"fly reels?|fly[- ]?fishing reels?|fly rod (?:outfits?|combos?|kits?)"
    r")\b"
)


def _shopify(name: str, domain: str, *collections: str, limit: int = 250) -> RetailerConfig:
    return RetailerConfig(
        name=name,
        domain=domain,
        scrape_urls=[f"https://{domain}/collections/{c}" for c in collections],
        shopify_products_limit=limit,
    )


def classify(product: Product) -> Classification | None:
    tags = {t.strip().casefold() for t in product.tags}
    if tags & _EXCLUDED_TAGS:
        return None
    title = product.name.casefold()
    kind = (product.product_type or "").casefold()
    combined = f"{title} {kind} {' '.join(tags)}"
    if _EXCLUDED_TEXT_RE.search(title) or _EXCLUDED_TEXT_RE.search(kind):
        return None
    if any(k in kind for k in ("apparel", "clothing", "footwear", "fins", "leashes", "flies", "terminal tackle", "line")):
        return None
    if re.search(r"\b(?:surfboard fins?|thruster fins?|quad fins?|single fins?|future fins?|fcs ii)\b", combined):
        return None
    if _FOIL_WING_RE.search(combined):
        return Classification("Foils & wings")
    if _KITE_RE.search(combined):
        return Classification("Kites & kiteboards")
    if _WETSUIT_RE.search(combined) and not re.search(r"\b(boots?|booties|gloves?|hoods?|caps?|vests?|tops?|jackets?)\b", title):
        return Classification("Wetsuits")
    if _BOARD_SUP_RE.search(combined):
        return Classification("Surfboards & SUPs")
    if _FLY_RE.search(combined) and not re.search(r"\b(spare spools?|replacement tips?|rod cases?|reel pouches?)\b", title):
        return Classification("Fly rods & reels")
    return None


CATALOG = Catalog(
    slug="watersports",
    name="Tideline",
    eyebrow="SURF, FOIL, WING & FLY",
    headline="Clean swell.\nDeep closeouts.",
    description="Hydrofoils, wings, kites, surfboards, SUPs, full wetsuits, and fly rods & reels across specialist coastal and river shops—ranked strictly by percentage off.",
    categories=(
        "Foils & wings",
        "Kites & kiteboards",
        "Surfboards & SUPs",
        "Wetsuits",
        "Fly rods & reels",
    ),
    sources=(
        _shopify("REAL Watersports", "www.realwatersports.com", "kiteboarding-sale", "surfing-sale", "wetsuit-sale", "air-box-hand-picked-all-wings"),
        _shopify("Houston Kiteboarding", "houstonkiteboarding.com", "foil-boards", "all-kite-foil", "armstrong-foils", "aluula-wing-kite-products"),
        _shopify("Elite Watersports", "elitewatersports.com", "closeouts", "black-friday-hydrofoils", "black-friday-wind-wing-sale", "kiteboarding"),
        _shopify("Force Kite & Wake", "forcekiteandwake.com", "sale", "26-sale-foils", "26-sale-wings", "26-sale-kites", "wetsuits"),
        _shopify("Windance Boardshop", "www.windance.com", "hydrofoils", "2025-north-wing-foil-collection", "beginner-wing-foils"),
        _shopify("Urban Surf", "urbansurf.com", "sale", "all-wetsuits", "armstrong-foil", "duotone-foil", "all-surfboards"),
        _shopify("evo", "www.evo.com", "sale-wetsuits", "sale-surf", "sale-wake", limit=50),
        _shopify("Trident Fly Fishing", "www.tridentflyfishing.com", "sale", "atlantic-salmon-fly-rods", "bass-fly-rods", "bass-fly-reels"),
        _shopify("Fishwest", "www.fishwest.com", "sale-collection", "fly-rods", "fly-fishing-reels", "fly-rod-outfits"),
        _shopify("Tailwater Outfitters", "tailwateroutfitters.com", "clearance-and-sale"),
        _shopify("Big Y Fly Co", "www.bigyflyco.com", "all-fly-rods", "fly-reels-copy"),
        _shopify("Adventure Sports USA", "www.adventuresportsusa.com", "sale", "foils", "2026-cabrinha-foil-boards"),
        _shopify("Kite Paddle Surf", "www.kitepaddlesurf.com", "armstrong-foils", "downwind-armstrong-foils", "all-around-stand-up-paddleboards"),
        _shopify("Red's Fly Shop", "redsflyfishing.com", "abel-fly-reels", "steelhead-spey-fly-fishing-gear-list"),
    ),
    classify=classify,
    default_reference=180,
    size_label="Size / spec",
    size_option_names=("Size", "Length", "Volume", "Area", "Thickness", "Weight", "Line Weight", "Option 1", "Option 2"),
    size_value_pattern=WATER_SIZE_PATTERN,
    brand_aliases=(
        ("abel", "Abel"), ("abel reels", "Abel"),
        ("airush", "Airush"),
        ("armstrong", "Armstrong"), ("armstrong foils", "Armstrong"),
        ("axis", "AXIS Foils"), ("axis foils", "AXIS Foils"),
        ("billabong", "Billabong"),
        ("bote", "BOTE"),
        ("cabrinha", "Cabrinha"),
        ("channel islands", "Channel Islands"),
        ("duotone", "Duotone"), ("duotone kiteboarding", "Duotone"), ("duotone wing", "Duotone"),
        ("echo", "Echo"),
        ("f-one", "F-One"),
        ("firewire", "Firewire"),
        ("g. loomis", "G. Loomis"), ("g loomis", "G. Loomis"),
        ("galvan", "Galvan"),
        ("gofoil", "GoFoil"),
        ("hardy", "Hardy"),
        ("hatch", "Hatch Outdoors"), ("hatch outdoors", "Hatch Outdoors"),
        ("lamson", "Waterworks-Lamson"), ("waterworks-lamson", "Waterworks-Lamson"),
        ("liquid force", "Liquid Force"),
        ("mystic", "Mystic"),
        ("naish", "Naish"),
        ("nautilus", "Nautilus Reels"),
        ("north", "North"), ("north kiteboarding", "North"), ("north actionsports", "North"),
        ("o'neill", "O'Neill"), ("oneill", "O'Neill"),
        ("orvis", "Orvis"),
        ("ozone", "Ozone"),
        ("redington", "Redington"),
        ("reedin", "Reedin"),
        ("rip curl", "Rip Curl"),
        ("ross", "Ross Reels"), ("ross reels", "Ross Reels"),
        ("sage", "Sage"),
        ("scott", "Scott Fly Rods"),
        ("simms", "Simms"),
        ("slingshot", "Slingshot"), ("slingshot sports", "Slingshot"),
        ("tfo", "Temple Fork Outfitters"), ("temple fork outfitters", "Temple Fork Outfitters"),
        ("thomas & thomas", "Thomas & Thomas"),
        ("winston", "R.L. Winston"), ("r.l. winston", "R.L. Winston"),
        ("xcel", "Xcel"),
    ),
    assets=Path(__file__).parent / "static",
    art_label="05 / ON THE WATER",
    theme_color="#0a202b",
    buying_note="Hydrofoils, wings, kites, surfboards, SUPs, full wetsuits, and fly rods & reels ($180+ floor), ranked by biggest percentage off.",
)
