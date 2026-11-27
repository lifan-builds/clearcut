import re
import unicodedata
from pathlib import Path

from deal_common.catalog.definition import Catalog, Classification
from deal_common.catalog.query import default_exact_offer_key
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

_PROMO_SUFFIX_RE = re.compile(
    r"(?:\s*\([^)]*(?:black\s+friday|deal|special|sale|promo|closeout|\$\d+)[^)]*\)"
    r"|\s*[-–—]\s*(?:sizes?\s+vary|t\d+)\b"
    r"|\s+\bnew\b\s*!*)+\s*$",
    re.IGNORECASE,
)
_COMPLETE_FOIL_RE = re.compile(
    r"\b("
    r"complete (?:hydro)?foils?|complete (?:wing|kite|wake|surf)?\s*foil(?:ing)? (?:sets?|packages?|kits?)|"
    r"(?:hydro)?foil (?:kits?|packages?|sets?|complete sets?)|"
    r"semi[- ]?complete|efoils?|e-?foils?|electric (?:hydro)?foils?"
    r")\b"
)
_FOIL_COMPONENT_TITLE_RE = re.compile(
    r"\b("
    r"front wings?|rear (?:wing )?stabili[sz]ers?|rear wings?|tail wings?|stabili[sz]ers?|"
    r"(?:foil|carbon|alloy|alumin(?:um|ium))\s+masts?(?:\s*/\s*strut)?|"
    r"fuselages?|mast\s+plate(?:\s+socket)?|socket\s+plate\s+mount"
    r")\b"
)
_FOIL_COMPONENT_KIND_RE = re.compile(
    r"\b("
    r"front wings?|tail wings?|rear wings?|rear stabili[sz]ers?|foil masts?|fuselages?|foil plates?"
    r")\b|^masts?$|^foil\s*[-–]\s*masts?$"
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
        scrape_urls=[f"https://{domain}/collections/{c}?currency=USD" for c in collections],
        shopify_products_limit=limit,
    )


_CABRINHA_PACK_RE = re.compile(
    r"mk\s*(?:ii|2) foil kit (?:wing|front wing|rear wing) (?:pack|set)"
)
_COMPONENT_PACK_RE = re.compile(
    r"(?:foil|hydrofoil) (?:front wing|rear wing|front and rear wing|stabilizer) (?:pack|set)"
)
_WHOLE_PACKAGE_HINT_RE = re.compile(
    r"\b(?:boards?|foilboards?|complete|assembled|full system|masts?|fuselages?)\b"
)
_WATER_BOARD_HINT_RE = re.compile(
    r"\b(?:wing\s+craft|wing\s+foil\s+boards?|foil\s*boards?|surfboards?|paddle\s*boards?|sups?|downwind\s+boards?)\b",
    re.IGNORECASE,
)
_WATER_BOARD_SUFFIX_RE = re.compile(
    r"(?:\s+(?:wing\s+foil\s+boards?|foil\s*boards?|wing\s+boards?|boards?))+$",
    re.IGNORECASE,
)
_VOLUME_L_RE = re.compile(r"\b(\d{2,3}(?:\.\d+)?)\s*l(?:iters?|itres?)?\b", re.IGNORECASE)
_BOARD_LENGTH_FT_IN_RE = re.compile(r"\b(\d)\s*'\s*(\d{1,2}(?:\.\d+)?)\s*\"?")
_BOARD_WIDTH_RE = re.compile(r"(?:\bx\b|(?<=\d)\s*x\s*|\bwidth\s*:?\s*)(\d{1,2}(?:\.\d+)?)\s*\"?", re.IGNORECASE)


def _label(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _is_tideline_component_pack(name: str | None, brand: str | None, package_fields: list[str | None]) -> bool:
    norm_brand = _label(brand)
    title = _PROMO_SUFFIX_RE.sub("", _label(name)).strip()
    is_cabrinha = norm_brand == "cabrinha"
    if norm_brand:
        title = title.removeprefix(norm_brand + " ")
    if not is_cabrinha and title.startswith("cabrinha "):
        is_cabrinha = True
        title = title.removeprefix("cabrinha ")

    if not (
        (is_cabrinha and _CABRINHA_PACK_RE.fullmatch(title))
        or _COMPONENT_PACK_RE.fullmatch(title)
    ):
        return False

    return not any(_WHOLE_PACKAGE_HINT_RE.search(_label(v)) for v in package_fields)


def route_tideline_pack(product: dict | None) -> dict | None:
    if product is None or product.get("category") != "Foils & wings":
        return product
    package_fields: list[str | None] = [product.get("format")]
    package_fields.extend(o.get("options") for o in product.get("offers") or ())
    if _is_tideline_component_pack(product.get("name"), product.get("brand"), package_fields):
        return {**product, "category": "Foil components"}
    return product


def _norm_water_dims(raw: str | None) -> str:
    text = unicodedata.normalize("NFKC", raw or "")
    return text.replace("′", "'").replace("’", "'").replace("″", '"').replace("”", '"').replace("×", "x")


def exact_offer_key(
    product: dict,
    offer: dict,
    brand_key: str,
    valid_size_value: str | None,
) -> tuple[str, ...] | None:
    category = _label(product.get("category"))
    raw_title = _PROMO_SUFFIX_RE.sub("", _label(product.get("name"))).strip()
    variant_text = _norm_water_dims(f"{offer.get('size') or ''} {offer.get('options') or ''}").strip()
    vol_match = _VOLUME_L_RE.search(variant_text)
    len_match = _BOARD_LENGTH_FT_IN_RE.search(variant_text)
    is_board = (
        category in {"foils & wings", "surfboards & sups"}
        and (bool(_WATER_BOARD_HINT_RE.search(raw_title)) or bool(vol_match) or bool(len_match))
    )
    if is_board:
        if not brand_key or brand_key == "unspecified":
            return None
        if not vol_match and not len_match:
            return None
        model = raw_title
        if brand_key and model.startswith(brand_key + " "):
            model = model[len(brand_key) + 1:].strip()
        model = _WATER_BOARD_SUFFIX_RE.sub("", model).strip()
        if not model:
            return None
        vol = f"{float(vol_match[1]):g}l" if vol_match else ""
        length = f"{len_match[1]}'{float(len_match[2]):g}" if len_match else ""
        width_match = _BOARD_WIDTH_RE.search(variant_text)
        width = f"{float(width_match[1]):g}" if width_match else ""
        return ("water_board", brand_key, category, model, vol, length, width)
    return default_exact_offer_key(product, offer, brand_key, valid_size_value)


def is_foil_component(name: str, product_type: str = "", package_fields: tuple[str | None, ...] | list[str | None] = ()) -> bool:
    if any(_WHOLE_PACKAGE_HINT_RE.search(_label(v)) for v in package_fields):
        return False
    norm_title = re.sub(r"\s+", " ", (name or "").strip()).casefold()
    stripped_title = _PROMO_SUFFIX_RE.sub("", norm_title).strip()
    kind = re.sub(r"\s+", " ", (product_type or "").strip()).casefold()
    if _COMPLETE_FOIL_RE.search(stripped_title) or _COMPLETE_FOIL_RE.search(kind):
        return False
    if _FOIL_COMPONENT_TITLE_RE.search(stripped_title):
        return True
    return bool(kind and _FOIL_COMPONENT_KIND_RE.search(kind) and not re.search(r"\b(?:boards?|complete|kits?|packages?|sets?)\b", kind))


def reconcile_product(row: dict) -> dict | None:
    package_fields: list[str | None] = [row.get("format")]
    package_fields.extend(o.get("options") for o in row.get("offers") or ())
    if row.get("category") == "Foils & wings" and is_foil_component(
        row.get("name") or "", row.get("source_category") or "", package_fields
    ):
        row = {**row, "category": "Foil components"}
    return route_tideline_pack(row)


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
    offer_options = [o.options for o in product.offers]
    if is_foil_component(product.name, product.product_type or "", offer_options) or _is_tideline_component_pack(
        product.name, product.brand, offer_options
    ):
        return Classification("Foil components")
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
        "Foil components",
    ),
    opt_in_categories=("Foil components",),
    reconcile_product=reconcile_product,
    exact_offer_key=exact_offer_key,
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
        ("lift", "Lift"), ("lift foils", "Lift"),
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
