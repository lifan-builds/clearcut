import re
from pathlib import Path

from deal_common.catalog.definition import Catalog, Classification
from deal_common.config import RetailerConfig
from deal_common.models import Product

CAMP_SIZE_PATTERN = (
    r"(?:"
    r"(?:Regular|Long|Short|Wide|Regular\s*Wide|Long\s*Wide|Double|1P|2P|3P|4P)"
    r"|(?:[2-4]?X{0,2}[SML]|SM|MD|LG|S/M|M/L|L/XL|One\s+Size)"
    r"|(?:\d{2,3}\s*(?:L|liter|litre|m|mm)\b)"
    r"|(?:(?:-?\d{1,2})\s*°?\s*[FC]\b)"
    r")(?:[ /(#-].*)?"
)

_EXCLUDED_TAGS = frozenset({
    "used", "pre-owned", "demo", "ex-demo", "rental", "factory second",
    "blem", "blemished", "open box", "gift card", "pickup only", "in store only",
})

_EXCLUDED_TEXT_RE = re.compile(
    r"\b("
    r"kids?|youths?|junior|toddlers?|infants?|boys|girls|child(?:ren)?|"
    r"used|pre-owned|demo|ex-demo|rental|refurbished|reconditioned|factory seconds?|blem(?:ished)?|open.box|"
    r"gift ?cards?|gift ?certificates?|e-?gift|store credit|stickers?|decals?|patches?|"
    r"(?:shipping|package|order|damage) (?:protection|insurance)|navidium|route protection|"
    r"(?:extended|accident) warrant(?:y|ies)|protection plans?|(?:reservation|pre[- ]?order) deposits?|"
    r"t-?shirts?|tees?|hoodies?|sweatshirts?|jackets?|vests?|parkas?|pants?|shorts?|leggings?|tights?|"
    r"socks?|underwear|boxers?|bras?|hats?|caps?|beanies?|bandanas?|gloves?|mittens?|shoes?|sandals?|boots?(?!\s*crampon)|"
    r"freeze[- ]?dried|meals?|snacks?|bars?|gels?|electrolytes?|coffee|tea|food|seasoning|"
    r"footprints?|groundsheets?|tent stakes?|pegs?|guylines?|tent poles?|repair (?:kits?|tape)|"
    r"stuff sacks?|compression sacks?|dry bags?|packing cubes?|rain ?covers?|pack covers?|"
    r"pillows?|sheets?(?!\s+bend)|blankets?(?!\s+quilt)|towels?|trekking pole (?:baskets?|tips?)|"
    r"chalk|chalk bags?|climbing tape|belay glasses|keychains?|dog (?:leashes?|collars?|harnesses?|beds?)|"
    r"water bottles?|flasks?|tumblers?|mugs?|utensils?|sporks?|fuel canisters?|isobutane"
    r")\b"
)

_CLIMB_RE = re.compile(
    r"\b("
    r"climbing ropes?|dynamic ropes?|static ropes?|half ropes?|twin ropes?|"
    r"camming|cams?\b|camalots?|totem cams?|friends?\b|stoppers?|nuts?\b|hexes|tricams?|"
    r"quickdraws?|alpine draws?|climbing harnesses?|mountaineering harnesses?|"
    r"belay devices?|grigri|atc\b|jul\b|megajul|gigajul|neox\b|ascenders?|descenders?|pulleys?|"
    r"ice ax(?:e|es)|ice tools?|crampons?|avalung|avalanche (?:beacons?|transceivers?|probes?|shovels?|airbags?)|"
    r"climbing helmets?|mountaineering helmets?|portaledges?|crash ?pads?|bouldering pads?"
    r")\b"
)
_SHELTER_RE = re.compile(
    r"\b("
    r"tents?|ultralight shelters?|pyramid shelters?|tarptents?|biv(?:y|ies|ouacs?)|"
    r"hammock (?:tents?|systems?)|camping tarps?|ultralight tarps?|rooftop tents?"
    r")\b"
)
_SLEEP_BAG_RE = re.compile(
    r"\b("
    r"sleeping bags?|down quilts?|top ?quilts?|under ?quilts?|backpacking quilts?|sleep systems?"
    r")\b"
)
_SLEEP_PAD_RE = re.compile(
    r"\b("
    r"sleeping pads?|sleeping mats?|insulated pads?|air pads?|air mats?|backpacking pads?"
    r")\b"
)
_PACK_RE = re.compile(
    r"\b("
    r"backpacks?|backpacking packs?|ultralight packs?|framed packs?|frameless packs?|"
    r"mountaineering packs?|climbing packs?|expedition packs?|haul bags?|duffels?"
    r")\b"
)
_TRAVEL_LUGGAGE_RE = re.compile(
    r"\b(?:global travel bag roller|travel roller bags?|wheeled luggage|roller bags?)\b"
)
_TRAVEL_BAG_RE = re.compile(
    r"\b(?:global travel bags?|global (?:pro 3-way )?briefcases?)\b"
)


def is_travel_bag(name: str) -> bool:
    cleaned = re.sub(r"\s+", " ", (name or "").strip()).casefold()
    return bool(_TRAVEL_LUGGAGE_RE.search(cleaned) or _TRAVEL_BAG_RE.search(cleaned))


def reconcile_product(row: dict) -> dict | None:
    title = re.sub(r"\s+", " ", (row.get("name") or "").strip()).casefold()
    kind = re.sub(r"\s+", " ", (row.get("source_category") or "").strip()).casefold()
    if is_travel_bag(title):
        if row.get("category") != "Travel bags & luggage":
            return {**row, "category": "Travel bags & luggage"}
        return row
    if (
        (_SLEEP_PAD_RE.search(title) or _SLEEP_PAD_RE.search(kind))
        and not _SLEEP_BAG_RE.search(title)
        and row.get("category") != "Sleeping pads"
    ):
        return {**row, "category": "Sleeping pads"}
    return row


def _shopify(name: str, domain: str, *collections: str, limit: int = 250) -> RetailerConfig:
    return RetailerConfig(
        name=name,
        domain=domain,
        scrape_urls=[f"https://{domain}/collections/{c}?currency=USD" for c in collections],
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
    if any(k in kind for k in ("apparel", "clothing", "footwear", "shoes", "food", "nutrition", "stickers", "books")):
        return None
    if _SHELTER_RE.search(combined):
        return Classification("Tents & shelters")
    if (_SLEEP_PAD_RE.search(title) or _SLEEP_PAD_RE.search(kind)) and not _SLEEP_BAG_RE.search(title):
        return Classification("Sleeping pads")
    if _SLEEP_BAG_RE.search(combined):
        return Classification("Sleeping bags & quilts")
    if _SLEEP_PAD_RE.search(combined):
        return Classification("Sleeping pads")
    if _CLIMB_RE.search(combined):
        return Classification("Climbing & mountaineering")
    if is_travel_bag(title):
        return Classification("Travel bags & luggage")
    if _PACK_RE.search(combined) and not re.search(r"\b(fanny|hip|waist|sling|crossbody|tote|wallet|pouch)\b", combined):
        return Classification("Backpacks")
    return None


CATALOG = Catalog(
    slug="camp",
    name="Highline",
    eyebrow="CAMP, CLIMB & BACKCOUNTRY",
    headline="Lighter pack.\nDeeper cuts.",
    description="Ultralight tents, down quilts, sleeping bags, alpine packs, and climbing hardware across specialist outfitters—ranked strictly by percentage off.",
    categories=(
        "Tents & shelters",
        "Sleeping bags & quilts",
        "Sleeping pads",
        "Backpacks",
        "Climbing & mountaineering",
        "Travel bags & luggage",
    ),
    opt_in_categories=("Travel bags & luggage",),
    reconcile_product=reconcile_product,
    sources=(
        _shopify("Gossamer Gear", "www.gossamergear.com", "holiday-sale-2026", "shelters-sleeping-pads", "backpacks"),
        _shopify("Sea to Summit", "seatosummit.com", "sale-discontinued", "sleep-system-gear", "tents"),
        _shopify("Sports Basement", "www.sportsbasement.com", "sleeping-bags", "1-person-backpacking-tents", "2-person-backpacking-tents", "climbing"),
        _shopify("HowNOT2", "hownot2.com", "ropes", "cams", "black-friday-2026-sterling-rope", "bolting-on-sale"),
        _shopify("Outdoor Vitals", "outdoorvitals.com", "sleeping-bags", "tents", "backpacks"),
        _shopify("NEMO Equipment", "www.nemoequipment.com", "tents-and-shelters", "all-mens-sleeping-bags", "all-womens-sleeping-bags", "all-mountaineering-products"),
        _shopify("Big Agnes", "www.bigagnes.com", "backpacking-tents", "backpacking-sleeping-bags", "packs", "3n1-sleep-system"),
        _shopify("Klymit", "klymit.com", "campsite-deals", "3-season-sleeping-bags", "4-season-sleeping-bags", "insulated-sleeping-pads"),
        _shopify("Kaviso", "kaviso.com", "durston-gear-x-mid-tents", "blankets-sleeping-gear", "backpack"),
        _shopify("Eberlestock", "eberlestock.com", "black-friday-sale", "3-day-packs"),
        _shopify("Skirack", "www.skirack.com", "camping-hiking", "sale"),
        _shopify("Alpine Shop VT", "alpineshopvt.com", "tents", "sleeping-bags", "packs"),
        _shopify("Zenbivy", "zenbivy.com", "closeouts", "all-clip-quilts", "all-gear"),
        _shopify("Six Moon Designs", "www.sixmoondesigns.com", "endofsummershelters", "endofsummer-packs", "ultralight-shelters", "backpacking-backpacks"),
        _shopify("Alpenglow Sports", "www.alpenglowsports.com", "memorial-day-sale-50-off", "40-off", "memorial-day-sale-30-off"),
    ),
    classify=classify,
    default_reference=120,
    size_label="Size / length",
    size_option_names=("Size", "Length", "Torso", "Hipbelt", "Temp", "Temperature", "Option 1", "Option 2"),
    size_value_pattern=CAMP_SIZE_PATTERN,
    brand_aliases=(
        ("big agnes", "Big Agnes"),
        ("black diamond", "Black Diamond"), ("bd", "Black Diamond"),
        ("bluewater", "BlueWater Ropes"), ("bluewater ropes", "BlueWater Ropes"),
        ("camp", "C.A.M.P."), ("camp usa", "C.A.M.P."),
        ("dmm", "DMM"),
        ("durston", "Durston"), ("durston gear", "Durston"),
        ("edelrid", "Edelrid"),
        ("eberlestock", "Eberlestock"),
        ("gossamer gear", "Gossamer Gear"),
        ("gregory", "Gregory"), ("gregory mountain products", "Gregory"),
        ("hyperlite", "Hyperlite Mountain Gear"), ("hyperlite mountain gear", "Hyperlite Mountain Gear"),
        ("klymit", "Klymit"),
        ("mammut", "Mammut"),
        ("metolius", "Metolius"),
        ("msr", "MSR"), ("mountain safety research", "MSR"),
        ("mystery ranch", "Mystery Ranch"),
        ("nemo", "NEMO"), ("nemo equipment", "NEMO"),
        ("osprey", "Osprey"), ("osprey packs", "Osprey"),
        ("outdoor vitals", "Outdoor Vitals"), ("outdoorvitals", "Outdoor Vitals"),
        ("petzl", "Petzl"),
        ("rab", "Rab"),
        ("sea to summit", "Sea to Summit"),
        ("six moon designs", "Six Moon Designs"),
        ("sterling", "Sterling Rope"), ("sterling rope", "Sterling Rope"),
        ("therm-a-rest", "Therm-a-Rest"), ("thermarest", "Therm-a-Rest"),
        ("western mountaineering", "Western Mountaineering"),
    ),
    assets=Path(__file__).parent / "static",
    art_label="04 / ABOVE TREELINE",
    theme_color="#141f1a",
    buying_note="Ultralight tents, down sleeping bags & quilts, insulated pads, packs, and climbing hardware ($120+ floor), ranked by biggest percentage off.",
)
