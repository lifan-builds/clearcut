import re
from pathlib import Path

from deal_common.catalog.definition import Catalog, Classification
from deal_common.config import RetailerConfig
from deal_common.models import Product

_EXCLUDED_TAGS = frozenset({
    "used", "pre-owned", "refurbished", "reconditioned", "open box",
    "factory second", "blem", "blemished", "gift card",
    "pickup only", "in store only", "demo", "showroom", "display model",
})
_EXCLUDED_TEXT_RE = re.compile(
    r"\b(?:(?:pepper|salt|coffee|spice|meat|food) (?:ball )?grinders?|"
    r"used|pre-owned|refurbished|reconditioned|recon|renewed|open.box|"
    r"factory seconds?|blem(?:ished)?|scratch|dent|demo|showroom|display model|"
    r"gift ?cards?|gift ?certificates?|e-?gift|store credit|"
    r"(?:shipping|package|order|damage) (?:protection|insurance)|navidium|route protection|"
    r"(?:extended|accident) warrant(?:y|ies)|warrant(?:y|ies) plans?|protection plans?|service plans?|"
    r"(?:reservation|pre[- ]?order|layaway) deposits?)\b"
)
_EXCLUDED_KIND_RE = re.compile(
    r"^oscillating tool access$|\b("
    r"accessories|parts|rests?|jigs?(?!\s+saws?\b)|templates?|blades|bits|cutters|"
    r"knives|clamps|vises|guides|gauges|books|casters|storage|staples|sawhorses|"
    r"abrasives|sanding paper|batteries and chargers|apparel|fasteners|"
    r"hand tools|turning tools|consumables?|chisels|screwdrivers|files|rasps|"
    r"gift cards|warranties"
    r")\b"
)
_MANUAL_RE = re.compile(
    r"\b(?:"
    r"point drivers?|hack ?saws?|nut drivers?|screwdrivers?|"
    r"coping saws?|flush[- ]?cut saws?|dovetail saws?|(?:japanese )?pull saws?|"
    r"ryoba|dozuki|kataba|fret saws?|jeweler'?s? saws?|tenon saws?|carcass saws?|"
    r"back ?saws?|gent'?s? saws?|veneer saws?|bow saws?|pruning saws?|folding saws?|"
    r"jab saws?|keyhole saws?|drywall saws?|hand ?saws?|miter box(?:es)?|"
    r"(?:t[- ]handle|multi[- ]bit|cushion[- ]grip|precision|insulated|ratcheting) (?:[\w-]+ )?drivers?|"
    r"(?:hex|torx|slotted|phillips|pozidriv|socket|nut) (?:key )?drivers?|"
    r"(?:manual|hand) impact drivers?|"
    r"hand sanders?|sanding blocks?|manual sanders?|drywall pole sanders?|"
    r"hand drills?|push drills?|breast drills?|brace drills?|pin vises?"
    r")\b"
)
_WELDING_COMPONENT = r"(?:spool\s*guns?|mig guns?|wire feed(?:ers?| units?)|tig torch(?:es)?|plasma torch(?:es)?)"
_WELDING_MACHINE = r"(?:welders?|welding machines?|plasma cutters?)"
_WELDING_COMPONENT_RE = re.compile(rf"\b{_WELDING_COMPONENT}\b")
_WELDING_MACHINE_RE = re.compile(rf"\b{_WELDING_MACHINE}\b")
_WELDING_EXCLUSION_RE = re.compile(
    rf"\b{_WELDING_COMPONENT}\s+for\b"
    rf"|\b{_WELDING_MACHINE}\s+(?:foot controls?|carts?|guns?|torch(?:es)?|wire feed(?:ers?| units?))\b"
    r"|^(?:wireless\s+)?foot controls?\b"
)
_WELDING_KIND_RE = re.compile(
    r"(?:(?:mig|tig|stick|multiprocess) welding machines?|welding machines?|"
    r"plasma cutting machines?|welder\s*/\s*generator|battery powered welder)"
)
_WELDING_TAGS = frozenset({"multiprocess welder", "mig welder", "tig welder", "stick welder", "plasma cutter"})
_PRIMARY_ACCESSORY_RE = re.compile(
    r"\b(?:"
    r"nut[- ]?setters?|sanding pads?|sandpaper|staples|casters?|systainer|sawhorse|"
    r"(?:abrasives|sanding (?:discs|belts|sheets))\s+for|"
    r"(?:drill|driver|router) bits?|bit set|blades?(?!\s+(?:stroke|right\s+circular\s+saw)\b)|"
    r"(?:planer|jointer)\s*(?:&\s*(?:planer|jointer))?\s+knives|"
    r"cutter sets?|shaper cutters?|hole saw|"
    r"replacement|adapt(?:e|o)r|attachment|insert|filters?|hose|cover|chuck|"
    r"stand (?:only|kit)|clamp kit|tool bag|rests?|jigs?(?!\s+saw\b)|templates?|"
    r"stop collar|depth stops?|drill(?:ing)? guides?|sharpening station|saw indicator|"
    r"vac switch|tool cabinet|machine cabinet|outfeed table|work station|"
    r"router table system|pole saw|how to|driver kit for|"
    r"(?:block|bench|smoothing|jack|jointer|fore|trying|scrub|shoulder|rabbet|rebate|"
    r"router|plow|plough|hand|apron|chisel|chamfer|bullnose|dovetail) planes?|"
    r"spokeshaves?|(?:cabinet|card) scrapers?|chisels?(?!\s+mortis)|gouges?|rasps?|hand files?|"
    r"arbors?|collets?|fences?|miter gauges?|featherboards?|push (?:blocks?|sticks?)|"
    r"zero[- ]?clearance(?: inserts?| plates?)?|throat plates?|dado inserts?|table inserts?|"
    r"riving kni(?:fe|ves)|(?:dust|filter|vacuum|vac|collection|chip|fleece|disposable) bags?|"
    r"dust (?:hoods?|ports?)|blast gates?|"
    r"guide rails?|track saw (?:rails?|tracks?|guides?)|parallel guides?|rail connectors?|splinter ?guards?|"
    r"sub[- ]?bases?|base ?plates?|edge guides?|template guides?|guide bushings?|bushing sets?|"
    r"router (?:lifts?|tables?)|extension (?:wings?|tables?)|bed extensions?|"
    r"faceplates?|(?:live|drive) centers?|(?:scroll|lathe) chucks?|jaw sets?|banjos?|"
    r"mobile bases?|(?:miter saw|table saw|planer|roller|universal) stands?|"
    r"carbon brush(?:es)?|power cords?|(?:drive|v[- ]?)belts?|bandsaw tires?|urethane tires?"
    r")\b"
)
_WORKSHOP_EQUIPMENT_RE = re.compile(
    r"\b(table saw|band saw|bandsaw|drill press|lathes?|planer|jointer|"
    r"dust (?:collector|extractor|processor)|air compressor|benchtop|stationary|cabinet saw|"
    r"cnc (?:machine|router|mill)|milling machine|mortis(?:er|ing machine)|wide[- ]belt sander|"
    r"industrial.*(?:sander|buffer|finishing machine)|scroll saw|shaper)\b"
)
_POWER_TOOL_RE = re.compile(
    r"\b(drill|driver|impact wrench|saw|jigsaws?|oscillating (?:multi[- ]?)?tools?|"
    r"grinder|sander|router|nailer|stapler|polisher|(?:rotary|demolition) hammer|vacuum|vac)\b"
)
_PLATFORMS = (
    (re.compile(r"\bm18\b"), "milwaukee", "Milwaukee M18"),
    (re.compile(r"\bm12\b"), "milwaukee", "Milwaukee M12"),
    (re.compile(r"\bflexvolt\b"), "dewalt", "DeWalt FLEXVOLT"),
    (re.compile(r"\b20v\s*max\b"), "dewalt", "DeWalt 20V MAX"),
    (re.compile(r"\b12v\s*max\b"), "dewalt", "DeWalt 12V MAX"),
    (re.compile(r"\bxgt\b"), "makita", "Makita XGT"),
    (re.compile(r"\blxt\b"), "makita", "Makita LXT"),
    (re.compile(r"\b18v\b"), "festool", "Festool 18V"),
    (re.compile(r"\b18v\b"), "bosch", "Bosch 18V"),
    (re.compile(r"\b(?:36v|multivolt)\b"), "metabo hpt", "Metabo HPT 36V MultiVolt"),
    (re.compile(r"\b18v\b"), "metabo hpt", "Metabo HPT 18V"),
    (re.compile(r"\b24v\b"), "flex", "FLEX 24V"),
    (re.compile(r"\b18v\b"), "ridgid", "Ridgid 18V"),
)


def _shopify(name: str, domain: str, *collections: str) -> RetailerConfig:
    urls = [f"https://{domain}/collections/{slug}?currency=USD" for slug in collections]
    return RetailerConfig(name=name, domain=domain, scrape_urls=urls, verify_shopify_currency=True)


def classify(product: Product) -> Classification | None:
    title = product.name.casefold()
    kind = (product.product_type or "").casefold()
    text = f"{title} {kind}"
    tags = {tag.casefold().strip().replace("_", " ") for tag in product.tags}
    if tags & _EXCLUDED_TAGS or _EXCLUDED_TEXT_RE.search(text) or _EXCLUDED_KIND_RE.search(kind):
        return None

    primary_title = re.split(r"\s+(?:(?:with|including)\s+|w/\s*)", title, maxsplit=1)[0]
    primary_text = f"{primary_title} {kind}"
    identity_title = re.split(
        r"(?<!\bbuilt )(?<!\bmade )(?<!\bdesigned )(?<!\bengineered )(?<!\bideal )\b(?:for|compatible|fits?)\b",
        primary_title,
        maxsplit=1,
    )[0]
    powered = bool(
        re.search(r"\b(?:electric|corded|cordless|pneumatic|powered)\b", identity_title)
        and not re.search(r"\b(?:manual(?:ly)?|non[- ]?powered|unpowered|hand[- ](?:powered|operated))\b", identity_title)
    )
    manual_in_title = bool(_MANUAL_RE.search(primary_title))
    manual_identity = manual_in_title or bool(_MANUAL_RE.fullmatch(kind))
    if manual_identity and (not powered or (manual_in_title and not _MANUAL_RE.search(identity_title))):
        return None

    if (
        "spool guns & wire feeders" in tags
        or (tags & {"wire feeders", "wire feeder"} and not re.search(r"\bwire feeder welder\b", primary_title))
        or _WELDING_EXCLUSION_RE.search(primary_title)
        or (_WELDING_COMPONENT_RE.search(primary_title) and not _WELDING_MACHINE_RE.search(primary_title))
    ):
        return None

    if primary_title != title:
        accessory_head = re.sub(r"\bfixed[- ]base(?=\s+router\b)", "", primary_title)
        if re.fullmatch(r"bundle|package|tool set|tool kit", primary_title) or re.search(
            r"\b(stands?|bags?|cases?|guards?|fences?|bases?|guides?)\b", accessory_head
        ):
            return None

    if _PRIMARY_ACCESSORY_RE.search(primary_title):
        return None

    manufacturers = {tag.removeprefix("brand ") for tag in tags if tag.startswith("brand ")}
    is_bundle = bool(re.search(r"\bbundle\b", primary_title))
    bundle_machine = is_bundle and any(
        any(brand == match[1] or brand.startswith(match[1] + " ") for brand in manufacturers)
        for tag in tags
        if (match := re.fullmatch(r"(.+) (?:welding|stick) machine", tag))
    )
    if (
        identity_title != primary_title
        and not bundle_machine
        and not _WORKSHOP_EQUIPMENT_RE.search(identity_title)
        and not _POWER_TOOL_RE.search(identity_title)
        and not _WELDING_MACHINE_RE.search(identity_title)
        and not _MANUAL_RE.search(identity_title)
        and not re.search(r"\bratchets?\b", identity_title)
    ):
        return None
    if _WELDING_KIND_RE.fullmatch(kind) or tags & _WELDING_TAGS or bundle_machine:
        category = "Workshop equipment"
    elif (
        (re.search(r"\b(band saw|bandsaw)\b", primary_text)
         and re.search(r"\b(cordless|portable|20v|18v|24v|36v|40v|m18|m12|lxt|xgt)\b", primary_text))
        or re.search(r"\b(hand|handheld|cordless) planer\b", primary_text)
        or (re.search(r"\bplaner\b", primary_text) and re.search(r"\bcordless\b", primary_title))
    ):
        category = "Power tools"
    elif _WORKSHOP_EQUIPMENT_RE.search(primary_text):
        category = "Workshop equipment"
    elif re.search(r"\b(combo|combination)\b.*\bkit\b|\b\d+[- ]tool\b.*\bkit\b", primary_title):
        category = "Combo kits"
    elif (
        (manual_identity and powered)
        or _POWER_TOOL_RE.search(primary_text)
        or (re.search(r"\bratchets?\b", primary_text) and re.search(r"\b(cordless|pneumatic|electric)\b", primary_text))
    ):
        category = "Power tools"
    else:
        return None

    if is_bundle:
        form = "Bundle"
    elif re.search(r"\b(bare tool|tool.only)\b", title):
        form = "Bare tool"
    elif re.search(r"\bkit\b", primary_title) or re.search(
        r"\b(?:batter(?:y|ies)\s+(?:and|&|\+)\s+chargers?|\d+(?:\.\d+)?\s*ah)\s+kit\b", title
    ):
        form = "Kit"
    else:
        form = "Not specified"

    brand_and_title = f"{(product.brand or '').casefold()} {title}"
    platform = next(
        (label for pattern, brand, label in _PLATFORMS if brand in brand_and_title and pattern.search(title)),
        "",
    )
    return Classification(category, form, platform)


CATALOG = Catalog(
    slug="tools",
    name="Bench Drop",
    eyebrow="THE WORKSHOP DEAL INDEX",
    headline="Build more.\nSpend less.",
    description="Deep cuts on power tools, serious kits, and workshop equipment. Ranked by percentage off, with the package details kept in view.",
    categories=("Power tools", "Combo kits", "Workshop equipment"),
    sources=(
        _shopify(
            "Baker's Gas", "bakersgas.com",
            "welding-machines-accessories", "mig-welders", "tig-welders", "multiprocess-welders",
            "best-selling-stick-welders", "plasma-cutters", "angle-grinders", "bench-grinders",
            "die-grinders", "cordless-angle-grinders",
        ),
        _shopify("Cyberweld", "store.cyberweld.com", "welders-plasma-cutters"),
        _shopify("Beaver Industrial Supply", "beavertools.com", "sales-and-closeouts"),
        _shopify("Tool Nut", "www.toolnut.com", "saws", "power-tools", "stationary-tools"),
        _shopify("Woodcraft", "www.woodcraft.com", "clearance", "power-tools", "woodworking-machines", "power-tool-deals"),
        _shopify("Craft Supplies USA", "woodturnerscatalog.com", "wood-lathes"),
        _shopify("Harvey Woodworking", "www.harveywoodworking.com", "all"),
        _shopify("WEN", "wenproducts.com", "power-tools"),
        _shopify("Factory Direct Supply", "factorydirectsupply.com", "power-tools"),
        _shopify("Max Warehouse", "www.maxwarehouse.com", "power-tools"),
        _shopify(
            "OakTree Supply", "www.oaktreesupply.com",
            "clearance", "jpw-overstock-items-2026", "jet-and-powermatic-price-drops", "scm-sale", "sawstop",
        ),
        RetailerConfig(
            name="Taylor Toolworks",
            domain="taytools.com",
            parser_type="taylor",
            scrape_urls=["https://taytools.com/power-tools?ppg=65535&pn=1"],
        ),
        _shopify(
            "Fasteners Inc", "fastenersinc.myshopify.com",
            "power-tools", "power-tools-1", "machine-tools", "power-tool-closeouts", "all-closeouts",
        ),
    ),
    classify=classify,
    default_reference=200,
    assets=Path(__file__).parent / "static",
    art_label="02 / THE NEXT PROJECT",
    theme_color="#282922",
    mark="↘",
    formats=("Bare tool", "Kit", "Bundle", "Not specified"),
    brand_aliases=(
        ("axiom", "Axiom"), ("axiom precision", "Axiom"),
        ("bessey", "Bessey"), ("bessey tools", "Bessey"),
        ("bosch", "Bosch"), ("bosch power tools", "Bosch"), ("bosch tools", "Bosch"),
        ("dewalt", "DeWalt"), ("dewalt power tools", "DeWalt"), ("dewalt tools", "DeWalt"),
        ("esab", "ESAB"), ("esab welding", "ESAB"),
        ("fein", "Fein"), ("fein power tools", "Fein"),
        ("festool", "Festool"), ("festool usa", "Festool"),
        ("flex", "FLEX"), ("flex power tools", "FLEX"),
        ("grizzly", "Grizzly"), ("grizzly industrial", "Grizzly"),
        ("harvey", "Harvey"), ("harvey industries", "Harvey"), ("harvey woodworking", "Harvey"),
        ("hilti", "Hilti"),
        ("hobart", "Hobart"), ("hobart welding products", "Hobart"),
        ("hypertherm", "Hypertherm"),
        ("jet", "JET"), ("jet tools", "JET"),
        ("kreg", "Kreg"), ("kreg tool", "Kreg"),
        ("laguna", "Laguna"), ("laguna tools", "Laguna"),
        ("lincoln", "Lincoln"), ("lincoln electric", "Lincoln"),
        ("makita", "Makita"), ("makita power tools", "Makita"), ("makita usa", "Makita"),
        ("metabo", "Metabo"), ("metabo corporation", "Metabo"),
        ("metabo hpt", "Metabo HPT"), ("metabo-hpt", "Metabo HPT"), ("hitachi/metabo hpt", "Metabo HPT"),
        ("miller", "Miller"), ("miller electric", "Miller"),
        ("milwaukee", "Milwaukee"), ("milwaukee electric tool", "Milwaukee"), ("milwaukee tool", "Milwaukee"),
        ("mirka", "Mirka"), ("mirka abrasives", "Mirka"),
        ("oliver", "Oliver"), ("oliver machinery", "Oliver"),
        ("powermatic", "Powermatic"), ("powermatic tools", "Powermatic"),
        ("ridgid", "Ridgid"), ("ridgid power tools", "Ridgid"), ("ridgid tools", "Ridgid"),
        ("rikon", "Rikon"), ("rikon power tools", "Rikon"),
        ("sawstop", "SawStop"),
        ("scm", "SCM"), ("scm group", "SCM"),
        ("shaper", "Shaper"), ("shaper tools", "Shaper"),
        ("wen", "WEN"), ("wen products", "WEN"),
    ),
    buying_note=(
        "A bare tool does not include a battery or charger. A kit label does not guarantee particular contents. "
        "Confirm the package, battery compatibility, shipping, and return policy on the retailer page. "
        "Check required voltage and phase for workshop machines. Clearance may be final sale."
    ),
)
