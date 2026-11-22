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
    "used", "pre-owned", "demo", "ex-demo", "consignment", "rental", "ex-rental",
    "factory second", "crash replacement", "gift card",
    "pickup only", "in store only", "in-store-only", "no-ship", "ebike block ship",
    "badge:ship to store only",
    "condition:lightly-ridden", "condition:new-cosmetic-blem", "condition:open-box",
})
_EXCLUDED_TEXT_RE = re.compile(
    r"\b("
    r"kids?|junior|youths?|boys|girls|toddlers?|infants?|child(?:ren)?|groms?|"
    r"used|pre-owned|demo|ex-demo|display|consignment|rental|ex-rental|factory seconds?|"
    r"refurbished|reconditioned|returned|unridden|blem(?:ish(?:ed)?)?|open.box|scratch|dent|"
    r"gift ?cards?|gift ?certificates?|e-?gift|store credit|"
    r"(?:shipping|package|order|damage) (?:protection|insurance)|navidium|route protection|"
    r"(?:extended|accident) warrant(?:y|ies)|warrant(?:y|ies) plans?|protection plans?|"
    r"crash replacements?|"
    r"(?:assembly|build|boxing|tuning|tune[- ]?up|fitting|service) (?:fees?|services?|charges?|labor)|"
    r"bike (?:assembly|fitting|tune[- ]?ups?)|(?:reservation|pre[- ]?order|layaway) deposits?|"
    r"(?:balance|push|run) bikes?|strider|training wheels?|unicycles?|tricycles?|adult trikes?|"
    r"tag[- ]?alongs?|(?:bike|bicycle|child) trailers?|"
    r"woom|prevelo|early rider|guardian bikes?|frog bikes?|spawn cycles?|precaliber|riprock|hotrock|"
    r"(?:20|24)\s*(?:\"|''|-?in(?:ch)?)\s*(?:wheel\s*)?(?:bikes?|bicycles?|mtb|mountain|trail|coaster)|"
    r"(?:20|24)\s*(?:\"|''|-?in(?:ch)?)?\s+wheels?"
    r")\b"
    r"|\b(?:12|14|16)\s*(?:\"|''|-?in(?:ch)?\b)(?!\s*(?:speed|spd)\b)"
)
_PRIMARY_APPAREL_RE = re.compile(
    r"\b(socks?|pants?|bottles?|seat ?posts?|clamps?|goggles?|jackets?|shirts?|"
    r"gloves?|jerseys?|shorts|shoes?|helmets?)\b"
)
_PRIMARY_PART_RE = re.compile(
    r"\b("
    r"ride\s*wrap|invisiframe|"
    r"frame (?:protection|protectors?|guards?|tapes?|films?|wraps?|skin kits?)|"
    r"(?:downtube|chainstay|seatstay|top ?tube|fork|crank) (?:protection|protectors?|guards?|wraps?|armou?r)|"
    r"tailgate pads?|(?:derailleur|rear|dropout|frame) hangers?|udh|thru[- ]?axles?|quick[- ]?releases?|skewers?|"
    r"shock (?:mounts?|hardware|pumps?|springs?)|"
    r"(?:mounting|pivot|bearing|rebuild|service|seal|bleed|tubeless|conversion|motor|mid[- ]?drive|hub motor) (?:kits?|hardware)|"
    r"(?:replacement|spare|e-?bike|external|internal) batter(?:y|ies)|range extenders?|"
    r"battery (?:packs?|chargers?|mounts?|covers?|cables?)|chargers?|power supplies|"
    r"cranksets?|cassettes?|chainrings?|bottom brackets?|headsets?|"
    r"dropper posts?|saddles?|pedals?|cleats?|bar tape|tire liners?|inner tubes?|"
    r"(?:bike|bicycle|frame|saddle|handlebar|seat|top ?tube|bikepacking|travel|wheel|gear|hydration) bags?|panniers?|"
    r"(?:bike|bicycle|hitch|roof|trunk|cargo|rear|front|pannier|wall|storage) racks?|"
    r"(?:bike|bicycle|repair|work|storage|display|truing) stands?|"
    r"(?:bike|bicycle|smart|indoor|turbo|direct[- ]drive|wheel[- ]on) trainers?|rollers?|"
    r"(?:bike|bicycle|u|chain|cable|folding) locks?|"
    r"(?:bike|bicycle|floor|hand|mini|frame|co2) (?:pumps?|inflators?)|"
    r"(?:bike|bicycle|cycling|gps) computers?|heart rate|power meters?|"
    r"bottle cages?|touch[- ]?up paints?|multi[- ]?tools?|torque wrench(?:es)?"
    r")\b"
)
_UNBRANDED_COMPONENT_RE = re.compile(
    r"\b(forks?|shocks?|wheels?|rims?|hubs?|tires?|tyres?|tubes?|chains?|derailleurs?|"
    r"shifters?|brakes?|rotors?|calipers?|handlebars?|stems?|fenders?|mudguards?|"
    r"kickstands?|grips?|lights?|headlights?|taillights?|racks?|bags?|stands?|"
    r"trainers?|locks?|pumps?|hangers?|axles?|batter(?:y|ies))\b"
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
    if _PRIMARY_APPAREL_RE.search(f"{primary_title} {kind}") or _PRIMARY_PART_RE.search(primary_title):
        return None
    if re.search(r"\bwheelsets?\b", primary_title) and not re.search(r"\b(bags?|covers?|skewers?|valves?|tapes?|bearings?|decals?|stickers?)\b", primary_title):
        return Classification("Wheelsets")
    if re.search(r"\bgroupsets?\b", primary_title) and not re.search(r"\b(cleaners?|tools?|cables?|bolts?|spacers?|covers?)\b", primary_title):
        return Classification("Groupsets")
    unbranded_title = title.replace(product.brand.casefold(), "") if product.brand else title
    unbranded_primary = primary_title.replace(product.brand.casefold(), "") if product.brand else primary_title
    if _UNBRANDED_COMPONENT_RE.search(unbranded_primary) and not re.search(
        r"\b(bikes?|bicycles?|framesets?|frame\s*(?:&|and)\s*fork)\b", unbranded_primary
    ):
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
    description="Big discounts on complete bikes, framesets, carbon wheelsets, and electronic groupsets—ranked strictly by percentage off.",
    categories=("Mountain bikes", "Road bikes", "Gravel bikes", "Electric bikes", "Framesets", "Wheelsets", "Groupsets", "Other complete bikes"),
    sources=(
        _shopify("Incycle Bicycles", "www.incycle.com", "sale-bicycles"),
        _shopify("RA Cycles", "racycles.com", "bikes", "wheels"),
        _shopify("evo", "www.evo.com", "bikes"),
        RetailerConfig(
            name="Bike Closet",
            domain="bikecloset.com",
            parser_type="bikecloset",
            scrape_urls=["https://bikecloset.com/wp-json/wc/store/v1/products?category=3411&type=variable&per_page=100&page=1&orderby=id&order=asc"],
        ),
        _shopify("Ari Bikes", "aribikes.com", "outlet"),
        _shopify("Thunder Mountain Bikes", "thundermountainbikes.com", "bikes-on-sale", "wheels"),
        _shopify("Spot Bikes", "spotbikes.com", "outlet", "outlet-bikes", "bikes"),
        _shopify("Evil Bikes", "www.evil-bikes.com", "bikes"),
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
        ("3t", "3T"), ("3t bikes", "3T"), ("3t cycling", "3T"),
        ("allied", "Allied"), ("allied cycle works", "Allied"),
        ("argon 18", "Argon 18"), ("argon18", "Argon 18"),
        ("aventon", "Aventon"), ("aventón", "Aventon"), ("aventon bikes", "Aventon"),
        ("basso", "Basso"), ("basso bikes", "Basso"),
        ("bianchi", "Bianchi"), ("bianchi bicycles", "Bianchi"), ("bianchi bikes", "Bianchi"),
        ("bmc", "BMC"), ("bmc bikes", "BMC"),
        ("cannondale", "Cannondale"), ("cannondale bicycles", "Cannondale"), ("cannondale bikes", "Cannondale"),
        ("canyon", "Canyon"), ("canyon bicycles", "Canyon"), ("canyon bikes", "Canyon"),
        ("cervelo", "Cervelo"), ("cervelo bikes", "Cervelo"), ("cervelo cycles", "Cervelo"),
        ("cervélo", "Cervelo"), ("cervélo bikes", "Cervelo"), ("cervélo cycles", "Cervelo"),
        ("colnago", "Colnago"), ("colnago bikes", "Colnago"),
        ("commencal", "Commencal"), ("commencal bikes", "Commencal"),
        ("de rosa", "De Rosa"), ("de rosa bikes", "De Rosa"),
        ("devinci", "Devinci"), ("devinci bikes", "Devinci"), ("cycles devinci", "Devinci"),
        ("enve", "ENVE"), ("enve bikes", "ENVE"), ("enve composites", "ENVE"),
        ("evil", "Evil"), ("evil bike co", "Evil"), ("evil bike co.", "Evil"), ("evil bikes", "Evil"),
        ("factor", "Factor"), ("factor bikes", "Factor"),
        ("felt", "Felt"), ("felt bicycles", "Felt"), ("felt bikes", "Felt"),
        ("forbidden", "Forbidden"), ("forbidden bike co", "Forbidden"), ("forbidden bike co.", "Forbidden"), ("forbidden bikes", "Forbidden"),
        ("gazelle", "Gazelle"), ("gazelle bikes", "Gazelle"), ("royal dutch gazelle", "Gazelle"),
        ("giant", "Giant"), ("giant bicycles", "Giant"), ("giant bikes", "Giant"),
        ("gt", "GT"), ("gt bicycles", "GT"), ("gt bikes", "GT"),
        ("ibis", "Ibis"), ("ibis cycles", "Ibis"),
        ("intense", "INTENSE"), ("intense bikes", "INTENSE"), ("intense cycles", "INTENSE"),
        ("juliana", "Juliana"), ("juliana bicycles", "Juliana"), ("juliana bikes", "Juliana"),
        ("kona", "Kona"), ("kona bicycle co", "Kona"), ("kona bicycle co.", "Kona"), ("kona bikes", "Kona"),
        ("liv", "Liv"), ("liv bikes", "Liv"), ("liv cycling", "Liv"),
        ("look", "LOOK"), ("look cycle", "LOOK"),
        ("marin", "Marin"), ("marin bicycles", "Marin"), ("marin bikes", "Marin"),
        ("masi", "Masi"), ("masi bicycles", "Masi"),
        ("mondraker", "Mondraker"), ("mondraker bikes", "Mondraker"),
        ("norco", "Norco"), ("norco bicycles", "Norco"), ("norco bikes", "Norco"),
        ("open", "OPEN"), ("open bikes", "OPEN"),
        ("orbea", "Orbea"), ("orbea bikes", "Orbea"),
        ("pinarello", "Pinarello"), ("pinarello bikes", "Pinarello"),
        ("pivot", "Pivot"), ("pivot cycles", "Pivot"),
        ("polygon", "Polygon"), ("polygon bikes", "Polygon"),
        ("revel", "Revel"), ("revel bikes", "Revel"),
        ("ridley", "Ridley"), ("ridley bikes", "Ridley"),
        ("rocky mountain", "Rocky Mountain"), ("rocky mountain bicycles", "Rocky Mountain"), ("rocky mountain bikes", "Rocky Mountain"),
        ("salsa", "Salsa"), ("salsa bikes", "Salsa"), ("salsa cycles", "Salsa"),
        ("santa cruz", "Santa Cruz"), ("santa cruz bicycles", "Santa Cruz"), ("santa cruz bikes", "Santa Cruz"),
        ("scott", "Scott"), ("scott bikes", "Scott"),
        ("specialized", "Specialized"), ("specialized bikes", "Specialized"),
        ("state bicycle", "State Bicycle Co."), ("state bicycle co", "State Bicycle Co."), ("state bicycle co.", "State Bicycle Co."),
        ("surly", "Surly"), ("surly bicycles", "Surly"), ("surly bikes", "Surly"),
        ("time", "TIME"),
        ("transition", "Transition"), ("transition bikes", "Transition"),
        ("trek", "Trek"), ("trek bicycle", "Trek"), ("trek bicycles", "Trek"), ("trek bikes", "Trek"),
        ("wilier", "Wilier"), ("wilier bikes", "Wilier"), ("wilier triestina", "Wilier"),
        ("yeti", "Yeti"), ("yeti bikes", "Yeti"), ("yeti cycles", "Yeti"),
    ),
    assets=Path(__file__).parent / "static",
    art_label="01 / THE NEXT RIDE",
    theme_color="#163d32",
    buying_note="Complete road, gravel, mountain, and electric bikes plus framesets ($1,000+ floor), ranked by biggest percentage off.",
)
