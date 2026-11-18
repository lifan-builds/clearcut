import re
from pathlib import Path
from urllib.parse import urlsplit

from deal_common.catalog.definition import Catalog, Classification
from deal_common.config import RetailerConfig
from deal_common.models import Product

FRAME_SIZE_PATTERN = (
    r"(?!.*\b(?:foot|feet|ft|cable)\b)(?!\d+(?:\.\d+)?\s*[x×])"
    r"(?!.*\bwheel\b$)(?:"
    r'(?:\d+(?:\.\d+)?\s*(?:cm|in|tt|s|")?|S[1-6]|Size\s+\d+)'
    r"|(?:[2-6]?X{0,3}[SML]|SM|MD|LG|ML|XM|XMD|R|Regular|All)"
    r"|(?:(?:X{1,3}-)?(?:Small|Medium|Large))|One\s+Size(?:\s+Fits\s+Most)?"
    r")(?:[ /(#-].*)?"
)

_EXCLUDED_TAGS = frozenset({
    "used", "pre-owned", "demo", "consignment",
    "pickup only", "in store only", "in-store-only", "no-ship", "ebike block ship",
    "badge:ship to store only",
    "condition:lightly-ridden", "condition:new-cosmetic-blem", "condition:open-box",
})
_EXCLUDED_TEXT_RE = re.compile(
    r"\b(kids?|junior|youth|boys|girls|used|pre-owned|demo|display|consignment|"
    r"refurbished|reconditioned|returned|unridden|blem(?:ish(?:ed)?)?|open.box|scratch|dent)\b"
)
_PRIMARY_APPAREL_RE = re.compile(
    r"\b(socks?|pants?|bottles?|seat ?posts?|clamps?|goggles?|jackets?|shirts?|"
    r"gloves?|jerseys?|shorts|shoes?|helmets?)\b"
)
_ACCESSORY_RE = re.compile(
    r"\b(helmets?|jerseys?|shorts|gloves?|shoes?|racks?|bags?|wheels?|tires?|tyres?|tubes?|"
    r"pedals?|saddles?|hangers?|forks?|batter(?:y|ies)|chargers?|covers?|stands?|trainers?|"
    r"parts?|accessor(?:y|ies)|maintenance|locks?|pumps?|goggles?|computers?|heart rate|"
    r"lights?|cables?|brakes?|adapt(?:e|o)rs?)\b"
)
_FRAME_TYPE_RE = re.compile(
    r"(?:(?:bike|bicycle|mtb|mountain|road|gravel|cyclocross|touring|all-road|fat bike|bmx|"
    r"e-bike|mountain e-bike|gravel & cyclocross|am/enduro|trail/am) )?frames?"
)
_EXPLICIT_BIKE_TITLE_RE = re.compile(
    r"(?:mountain|road|gravel|hybrid|commuter|electric|trail|xc|e-mountain|e-road) "
    r"(?:bikes?|bicycles?)(?:\s*\[new\])?$"
)
_DISCIPLINE_RULES = (
    (re.compile(r"\b(e-bikes?|ebikes?|electric|e-mountain|e-road|e-gravel)\b"), "Electric bikes"),
    (re.compile(r"\b(mountain|mtb|trail|enduro|downhill|xc)\b"), "Mountain bikes"),
    (re.compile(r"gravel"), "Gravel bikes"),
    (re.compile(r"road"), "Road bikes"),
)


def _shopify(name: str, domain: str, *collections: str) -> RetailerConfig:
    urls = [f"https://{domain}/collections/{slug}?currency=USD" for slug in collections]
    return RetailerConfig(name=name, domain=domain, scrape_urls=urls, verify_shopify_currency=True)


def classify(product: Product) -> Classification | None:
    title = product.name.casefold()
    kind = (product.product_type or "").casefold()
    text = f"{title} {kind}"
    tags = {tag.casefold().strip().replace("_", " ") for tag in product.tags}
    if tags & _EXCLUDED_TAGS or _EXCLUDED_TEXT_RE.search(text):
        return None

    primary_title = re.split(r"\s+(?:with\s+|w/\s*|including\s+)", title, maxsplit=1)[0]
    if _PRIMARY_APPAREL_RE.search(f"{primary_title} {kind}"):
        return None
    unbranded_title = title.replace(product.brand.casefold(), "") if product.brand else title
    if kind == "bicycles" and re.search(r"\b(tires?|tyres?)\b", title) and not re.search(r"\b(bikes?|bicycles?|framesets?)\b", unbranded_title):
        return None
    accessory_kind = re.sub(r"^(bikes and frames/bikes - electric/)electric mountain light weight(?=/|$)", r"\1electric mountain", kind)
    if _ACCESSORY_RE.search(accessory_kind):
        return None

    if "frameset" in tags or re.search(r"\b(framesets?|frame sets?|frame[- ]only)\b", text) or _FRAME_TYPE_RE.fullmatch(kind):
        return Classification("Framesets")
    if kind == "sporting goods":
        source = urlsplit(product.url)
        if source.scheme == "https" and source.netloc == "www.incycle.com" and source.path.startswith("/products/"):
            if _ACCESSORY_RE.search(primary_title):
                return None
            if "turbo & electric bike" in tags:
                return Classification("Electric bikes")
            if "bicycles" in tags:
                for discipline, category in (("mountain", "Mountain bikes"), ("gravel", "Gravel bikes"),
                                             ("road", "Road bikes"), ("active", "Other complete bikes")):
                    if discipline in tags:
                        return Classification(category)
            return None
    if kind == "e-commuter":
        return Classification("Electric bikes")
    if kind in {"hardtail", "full suspension"}:
        return Classification("Mountain bikes")
    if kind == "6061 all road" and "bicycle" in tags and (product.brand or "").casefold() == "state bicycle co.":
        return Classification("Gravel bikes")
    if not (kind in {"mountain completes", "e-mountain completes", "gravel completes"}
            or re.search(r"\b(bikes?|bicycles?)\b", kind)
            or _EXPLICIT_BIKE_TITLE_RE.search(title)):
        return None

    category_text = f"{unbranded_title} {kind}"
    for pattern, category in _DISCIPLINE_RULES:
        if pattern.search(category_text):
            return Classification(category)
    return Classification("Other complete bikes")


CATALOG = Catalog(
    slug="cycling",
    name="Big Ring",
    eyebrow="THE CYCLING DEAL INDEX",
    headline="More bike.\nLess money.",
    description="Big discounts on complete bikes and framesets. The deepest percentage cuts come first, with the exact size and price attached.",
    categories=("Mountain bikes", "Road bikes", "Gravel bikes", "Electric bikes", "Framesets", "Other complete bikes"),
    sources=(
        _shopify("Incycle Bicycles", "www.incycle.com", "sale-bicycles"),
        _shopify("RA Cycles", "racycles.com", "bikes"),
        _shopify("evo", "www.evo.com", "bikes"),
        RetailerConfig(
            name="Bike Closet",
            domain="bikecloset.com",
            parser_type="bikecloset",
            scrape_urls=["https://bikecloset.com/wp-json/wc/store/v1/products?category=3411&type=variable&per_page=100&page=1&orderby=id&order=asc"],
        ),
        _shopify("Gravity Coalition", "gravitycoalition.com", "all-bikes-frames", "bike-sale"),
        _shopify("Bicycle Warehouse", "bicyclewarehouse.com", "bikes-on-sale"),
        _shopify("BikesOnline", "bikesonline.com", "sale"),
        _shopify("Mike's Bikes", "mikesbikes.com", "clearance-and-sale-bikes"),
        _shopify("ERIK’S Bike Shop", "www.eriksbikeshop.com", "cycling-bicycles"),
        _shopify("Pro Bike Supply", "www.probikesupply.com", "on-sale-bikes-frames"),
        _shopify("Contender Bicycles", "contenderbicycles.com", "sale"),
        _shopify("Cambria Bike", "cambriabike.com", "mtb-bikes", "mtb-frames"),
        _shopify("INTENSE", "intensecycles.com", "sale"),
        _shopify("Bike Mart", "www.bikemart.com", "bikes"),
        _shopify("First Stop Board Barn", "www.firststopboardbarn.com", "blazing-summer-bike-sale"),
        _shopify("Colorado Cyclist", "coloradocyclist.com", "all-bikes"),
        _shopify("Planet Cyclery", "planetcyclery.com", "mtb-bikes", "road-bikes"),
        _shopify("Worldwide Cyclery", "worldwidecyclery.com", "bikes-and-frames"),
        _shopify("State Bicycle Co.", "www.statebicycle.com", "sale"),
        _shopify("The Path Bike Shop", "www.thepathbikeshop.com", "bike-eligible-to-ship"),
        _shopify("Fanatik Bike Co.", "www.fanatikbike.com", "on-sale-complete-bikes", "on-sale-frames", "on-sale-e-bikes"),
    ),
    classify=classify,
    default_reference=1000,
    size_label="Frame size",
    size_option_names=("size", "frame size", "framesize", "bike size"),
    size_value_pattern=FRAME_SIZE_PATTERN,
    brand_aliases=(
        ("liv", "Liv"), ("time", "TIME"),
        ("bmc", "BMC"), ("bmc bikes", "BMC"),
        ("cervelo", "Cervelo"), ("cervelo bikes", "Cervelo"),
        ("colnago", "Colnago"), ("colnago bikes", "Colnago"),
        ("enve", "ENVE"), ("enve bikes", "ENVE"), ("enve composites", "ENVE"),
        ("ibis", "Ibis"), ("ibis cycles", "Ibis"),
        ("look", "LOOK"), ("look cycle", "LOOK"),
        ("masi", "Masi"), ("masi bicycles", "Masi"),
        ("mondraker", "Mondraker"), ("mondraker bikes", "Mondraker"),
        ("open", "OPEN"), ("open bikes", "OPEN"),
        ("orbea", "Orbea"), ("orbea bikes", "Orbea"),
        ("pinarello", "Pinarello"), ("pinarello bikes", "Pinarello"),
        ("pivot", "Pivot"), ("pivot cycles", "Pivot"),
        ("santa cruz", "Santa Cruz"), ("santa cruz bicycles", "Santa Cruz"), ("santa cruz bikes", "Santa Cruz"),
        ("scott", "Scott"), ("scott bikes", "Scott"),
        ("specialized", "Specialized"), ("specialized bikes", "Specialized"),
        ("transition", "Transition"), ("transition bikes", "Transition"),
    ),
    assets=Path(__file__).parent / "static",
    art_label="01 / THE NEXT RIDE",
    theme_color="#163d32",
    buying_note=(
        "Framesets are listed separately from complete bikes. Confirm geometry, components, assembly, "
        "shipping, and return terms with the retailer. Sizes are retailer labels, not a fit recommendation. "
        "Mike's Bikes advertises a $79 assembly charge added at checkout; its closeout sales are final. "
        "Incycle lists $75 bike and $30 frameset shipping within the contiguous US, with product-specific "
        "exceptions. Check manufacturer shipping restrictions before buying."
    ),
)
