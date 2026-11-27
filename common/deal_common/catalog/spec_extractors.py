"""Deterministic buyer-fit facts for one retailer offer and selected configuration.

Each extractor returns values, source evidence, and review issues. Snow evidence
indices address the original rows. Tideline also returns the classified kind.
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from itertools import chain
from typing import NamedTuple

# No thousands separators, signs, scientific notation, or partial numbers.
SNOW_NUMBER = r"(?:0|[1-9][0-9]{0,2})(?:[.,][0-9]{1,2})?"
U = r"mm|cm|m"
MP = r"(?:mp|mondo(?:point)?)"
SCALE_MM = {"mm": Decimal(1), "cm": Decimal(10), "m": Decimal(1000)}

GORE = r"(?P<g>(?:gore[ -]tex|gtx)(?: pro)?)"
LAYERS = r"(?P<l>2(?:[.,]5)?|3)(?:\s*l|\s*-\s*layers?|\s+layers?)"

SHELL_KEYS = {
    "main shell membrane": "shell",
    "main shell construction": "shell",
}
KEYS = {
    "ski": {
        "waist": "waist",
        "waist width": "waist",
        "underfoot width": "waist",
        "sidecut (tip-waist-tail)": "sidecut",
        "turn radius": "radius",
        "sidecut radius": "radius",
    },
    "ski_boot": {
        "mondopoint": "mp",
        "mondo": "mp",
        "mp": "mp",
        "size": "marked_size",  # only an explicitly MP-marked value is eligible
        "flex": "flex",
        "flex index": "flex",
        "last": "last",
        "last width": "last",
    },
    "ski_binding": {
        "din": "din",
        "din range": "din",
        "release value range": "din",
    },
    "shell_jacket": SHELL_KEYS,
    "shell_pants": SHELL_KEYS,
}
OUTPUTS = {
    "waist": ("ski_waist_mm",),
    "sidecut": ("ski_waist_mm",),
    "radius": ("turn_radius_m",),
    "mp": ("mondopoint_cm",),
    "marked_size": ("mondopoint_cm",),
    "flex": ("boot_flex_index",),
    "last": ("boot_last_mm",),
    "din": ("binding_din_range",),
    "shell": ("shell_membrane", "shell_layers"),
}


class Reject(ValueError):
    pass


def _snow_text(s):
    # Don't erase "/", parentheses, superscripts, or trailing qualifiers.
    s = unicodedata.normalize("NFC", s).casefold()
    s = s.translate(
        str.maketrans(
            {
                **{c: "-" for c in "‐‑‒–—−"},
                "®": "",
                "™": "",
            }
        )
    )
    return " ".join(s.split())


def match(pattern, text):
    m = re.fullmatch(pattern, text)
    if m is None:
        raise Reject("unsupported syntax")
    return m


def dec(s):
    return Decimal(s.replace(",", "."))


def out(x):
    return format(x.normalize(), "f")


def bounded(x, lo, hi):
    if not Decimal(lo) <= x <= Decimal(hi):
        raise Reject("outside ingestion bounds")
    return x


def measure(number, inline, heading, *, allowed, target):
    if inline and heading and inline != heading:
        raise Reject("value unit contradicts label unit")
    unit = inline or heading
    if unit not in allowed:
        raise Reject("missing or unsupported unit")
    return dec(number) * SCALE_MM[unit] / SCALE_MM[target]


def mondo(number, inline=None, heading=None):
    # Only called after an MP system label/marker has been established.
    # Retail MP notation uses both cm (23.5) and mm (235). The admitted
    # unitless bands are disjoint; do not apply this rule to generic sizes.
    if inline or heading:
        x = measure(number, inline, heading, allowed={"cm", "mm"}, target="cm")
    else:
        x = dec(number)
        if Decimal(120) <= x <= Decimal(380):
            x /= 10
    return out(bounded(x, "12", "38"))


def read(op, h, v):
    if op in {"flex", "din", "shell"} and h is not None:
        raise Reject("unexpected dimensional unit")

    if op == "waist":
        a, u = match(rf"({SNOW_NUMBER})\s*({U})?", v).groups()
        x = measure(a, u, h, allowed={"mm", "cm"}, target="mm")
        return {"ski_waist_mm": out(bounded(x, "45", "170"))}

    if op == "sidecut":
        # Same separator throughout. A comma is never a triplet separator.
        m = match(
            rf"({SNOW_NUMBER})\s*(?P<sep>[-/x×])\s*({SNOW_NUMBER})\s*(?P=sep)\s*({SNOW_NUMBER})\s*({U})?",
            v,
        )
        tip, _, waist, tail, u = m.groups()
        t, w, b = (
            measure(n, u, h, allowed={"mm", "cm"}, target="mm")
            for n in (tip, waist, tail)
        )
        # Deliberately abstain on atypical geometries; don't reorder numbers.
        if not (w <= t <= 220 and w <= b <= 220):
            raise Reject("unexpected tip-waist-tail geometry")
        return {"ski_waist_mm": out(bounded(w, "45", "170"))}

    if op == "radius":
        a, u, length = match(
            rf"({SNOW_NUMBER})\s*({U})?(?:\s*(?:@|at)\s*({SNOW_NUMBER})\s*cm)?", v
        ).groups()
        r = measure(a, u, h, allowed={"m", "cm"}, target="m")
        ref = None if length is None else out(bounded(dec(length), "60", "230"))
        return {"turn_radius_m": (out(bounded(r, "2", "100")), ref)}

    if op in {"mp", "marked_size"}:
        marker = re.match(rf"^{MP}\s*(?=[0-9])", v)
        if op == "marked_size" and not marker:
            return {}  # "23.5", "US 7.5", "EU 39" under generic "Size"
        a, u = match(
            rf"({SNOW_NUMBER})\s*(mm|cm)?", v[marker.end() :] if marker else v
        ).groups()
        return {"mondopoint_cm": mondo(a, u, h)}

    if op == "flex":
        # Dimensionless manufacturer index, not an inter-brand stiffness scale.
        a = match(r"[1-9][0-9]{0,2}", v)[0]
        return {"boot_flex_index": out(bounded(dec(a), "20", "180"))}

    if op == "last":
        a, u, ref, ref_u = match(
            rf"({SNOW_NUMBER})\s*({U})?(?:\s*(?:@|at)\s*{MP}\s*({SNOW_NUMBER})\s*(mm|cm)?)?",
            v,
        ).groups()
        x = measure(a, u, h, allowed={"mm", "cm"}, target="mm")
        return {
            "boot_last_mm": (
                out(bounded(x, "75", "120")),
                mondo(ref, ref_u) if ref else None,
            )
        }

    if op == "din":
        a, b = match(
            rf"({SNOW_NUMBER})\s*(?:-|to)\s*({SNOW_NUMBER})(?:\s+din)?", v
        ).groups()
        lo, hi = dec(a), dec(b)
        if not Decimal("0.5") <= lo < hi <= Decimal(30):
            raise Reject("invalid release-value interval")
        return {"binding_din_range": (out(lo), out(hi))}

    if op == "shell":
        # Whole main-shell spec only. "Pro" must modify GORE-TEX/GTX;
        # an isolated layer count provides no membrane-brand evidence.
        for pattern in (GORE + " " + LAYERS, LAYERS + " " + GORE, GORE, LAYERS):
            m = re.fullmatch(pattern, v)
            if m:
                g, l = m.groupdict().get("g"), m.groupdict().get("l")
                facts = {}
                if g:
                    facts["shell_membrane"] = (
                        "GORE-TEX Pro" if g.endswith(" pro") else "GORE-TEX"
                    )
                if l:
                    facts["shell_layers"] = out(dec(l))
                return facts
        raise Reject("unrecognized or mixed shell construction")

    raise AssertionError(op)


def extract_freshpowder_specs(product_dict):
    """Read ordered (label, value) rows for one classified snow product."""
    kind = product_dict["kind"]
    pairs = product_dict.get("rows", ())
    keys = KEYS.get(kind, {})
    seen = defaultdict(lambda: defaultdict(list))
    review = defaultdict(list)

    for i, (label, value) in enumerate(pairs):
        if not label.strip():
            continue
        k, h = match(r"(.+?)(?:\s*\((mm|cm|m)\))?", _snow_text(label)).groups()
        op = keys.get(k)
        if op is None:
            continue
        try:
            for field, val in read(op, h, _snow_text(value)).items():
                seen[field][val].append(i)
        except Reject as e:
            for field in OUTPUTS[op]:
                review[field].append((i, str(e)))

    facts, evidence = {}, {}
    for field, alternatives in seen.items():
        if len(alternatives) > 1:
            for indices in alternatives.values():
                review[field].extend((i, "conflicting observations") for i in indices)
        elif field not in review:
            facts[field], evidence[field] = next(iter(alternatives.items()))

    # Quarantine an unexpected Pro/layer pairing rather than "fixing" it to 3L.
    if facts.get("shell_membrane") == "GORE-TEX Pro" and facts.get("shell_layers") in {
        "2",
        "2.5",
    }:
        for field in ("shell_membrane", "shell_layers"):
            review[field].extend(
                (i, "review Pro construction") for i in evidence.pop(field)
            )
            del facts[field]

    return {"values": facts, "evidence": evidence, "issues": dict(review)}


def _bike_text(raw: str) -> str:
    # Replace double prime before NFKC can decompose it.
    s = raw.translate(
        str.maketrans(
            {
                "″": '"',
                "“": '"',
                "”": '"',
                "’": "'",
                "‑": "-",
                "–": "-",
                "−": "-",
            }
        )
    )
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def full(pattern: str, s: str):
    return re.fullmatch(pattern, s, flags=re.ASCII)


FRAME_MATERIAL = {
    "Carbon": r"(?:carbon(?: fib(?:er|re))?|cfrp)",
    "Alloy": r"(?:(?:6061|7005)(?:-t6)? )?(?:alumin(?:um|ium)(?: alloy)?|alloy)",
    "Ti": r"(?:ti|titanium)",
    "Steel": r"(?:(?:4130 )?(?:chromoly|cr-?mo)(?: steel)?|(?:4130 )?steel)",
}


def material(s: str):
    return next((v for v, pattern in FRAME_MATERIAL.items() if full(pattern, s)), None)


def single_wheel(s: str):
    if full(r'29(?:er|(?:[ -]?(?:in|inch|inches)|\s*"))?', s):
        return "29"
    if full(r'27\.5(?:[ -]?(?:in|inch|inches)|\s*")?|650\s*b', s):
        return "27.5"
    if full(r"700\s*c", s):
        return "700c"
    return None


def wheel(s: str):
    if s == "mullet":
        return "Mullet"
    # Roles are mandatory for a pair. "29 / 27.5" can mean two options.
    m = full(r"(.+?)\s+(?:front|f)\s*/\s*(.+?)\s+(?:rear|r)", s)
    if m:
        front, rear = map(single_wheel, m.groups())
        if (front, rear) == ("29", "27.5"):
            return "Mullet"
        return front if front is not None and front == rear else None
    return single_wheel(s)


SIZE_WORDS = {
    "xx-small": "XXS",
    "x-small": "XS",
    "small": "S",
    "medium": "M",
    "large": "L",
    "x-large": "XL",
    "xx-large": "XXL",
    "extra small": "XS",
    "extra large": "XL",
}


def size_code(s: str):
    if s in SIZE_WORDS:
        return SIZE_WORDS[s]
    if full(r"(?:xxs|xs|s|m|l|xl|xxl|xxxl|s[1-6])", s):
        return s.upper()
    if full(r"(?:s\s*/\s*m|m\s*/\s*l|l\s*/\s*xl)", s):
        return s.replace(" ", "").upper()  # one nominal size, not two
    if full(r"[1-9][0-9]s", s):
        return s  # e.g. De Rosa 46s, not 46 cm
    return None


def dec_text(s: str) -> str:
    return format(Decimal(s).normalize(), "f")


def frame_size(s: str):
    code = size_code(s)
    if code is not None:
        return code
    m = full(r'([1-9][0-9]?(?:\.[0-9]+)?)\s*(cm|in|inch|inches|")', s)
    if m:
        return dec_text(m[1]) + (" cm" if m[2] == "cm" else " in")
    return None  # no implicit cm or inches


def travel(s: str):
    # Decimal millimetres. No rounding, no ranges, no "up to", no conversion
    # from a shock's two dimensions. An explicit "0 mm" remains zero.
    m = full(r"(0|[1-9][0-9]{0,2})(\.[0-9]+)?\s*mm", s)
    return Decimal(m[1] + (m[2] or "")) if m else None


TIER_ORDER = ("GX", "X0", "XX", "XT", "XTR", "105", "Ultegra", "Force", "Red")
SHIMANO = {"xt", "xtr", "105", "ultegra", "slx"}
SRAM = {"gx", "x0", "xx", "force", "red"}


def drivetrain(s: str):
    seen = set()
    for part in re.split(r"\s*/\s*", s):
        m = full(
            r"(?:(shimano|sram) )?(?:(deore) )?"
            r"(xtr|xt|slx|105|ultegra|gx|x0|xx|force|red)"
            r"((?: (?:eagle|axs|etap|di2|t-type|[0-9]{1,2}-speed))*)",
            part,
        )
        if not m:
            return None
        brand, deore, label, suffix = m.groups()
        if brand == "shimano" and label not in SHIMANO:
            return None
        if brand == "sram" and label not in SRAM:
            return None
        if deore and label not in {"xt", "slx"}:
            return None
        if "di2" in suffix and label not in SHIMANO:
            return None
        if label not in SRAM and any(
            t in suffix for t in ("eagle", "axs", "etap", "t-type")
        ):
            return None
        seen.add(label)
    # SLX is a recognized partner in XT/SLX, but outside the requested taxonomy.
    result = tuple(t for t in TIER_ORDER if t.casefold() in seen)
    return result or None


# adapter field -> (output attribute, value parser)
FIELDS = {
    "frame_material": ("material", material),
    "wheel_size": ("wheel", wheel),
    "fork_travel": ("fork_mm", travel),
    "rear_travel": ("rear_mm", travel),
    "suspension_travel": ("travel_mm_unspecified", travel),
    "drivetrain": ("tiers", drivetrain),
    "frame_size": ("size", frame_size),
    "frame_size_cm": ("size", lambda s: frame_size(s + " cm")),
    "size_option": ("size", size_code),
}

TITLE_LABELS = {
    "frame material": "frame_material",
    "wheel size": "wheel_size",
    "fork travel": "fork_travel",
    "front suspension travel": "fork_travel",
    "rear travel": "rear_travel",
    "rear suspension travel": "rear_travel",
    "suspension travel": "suspension_travel",
    "drivetrain": "drivetrain",
    "groupset": "drivetrain",
    "frame size": "frame_size",
}
LABEL_RE = re.compile(
    r"(" + "|".join(map(re.escape, TITLE_LABELS)) + r")(?:\s*[:=]\s*|\s+)(.+)",
    re.ASCII,
)


@dataclass(frozen=True)
class Hit:
    field: str
    raw: str  # original spec value or original title clause
    value: object  # None = explicit observation not representable


def extract_bigring_specs(product_dict):
    """Read kind and canonical bike fields. Ordered fields pairs preserve repeated labels."""
    kind = product_dict["kind"]
    if kind not in {"complete_bike", "frameset"}:
        return {"values": {}, "evidence": {}, "issues": {}}

    fields = product_dict.get("fields", ())
    hits = defaultdict(list)

    def add(field, value_text, raw, source, *, explicit=True):
        if field == "drivetrain" and kind != "complete_bike":
            return
        attr, parser = FIELDS[field]
        value = parser(_bike_text(value_text))
        if value is not None or explicit:
            hits[attr].append(Hit(source, raw, value))

    for field, raw in chain(product_dict.items(), fields):
        if not isinstance(raw, str) or not raw.strip():
            continue
        if field in FIELDS:
            # A generic Size option like "29" is not contrary evidence for a
            # separate verified frame size. It is simply dimensionally unknown.
            add(field, raw, raw, field, explicit=(field != "size_option"))
            continue
        if field != "title":
            continue

        # Deliberately do not split on '/', 'x', comma, or hyphen.
        for part in re.split(r"[|;]|\s+—\s+", raw):
            part = part.strip()
            s = _bike_text(part)
            m = LABEL_RE.fullmatch(s)
            if m:
                add(TITLE_LABELS[m[1]], m[2], part, "title")
                continue

            # Entire spec clauses only; no free-text searches for Red, Force,
            # Ti, 105, a trailing M, or the first number before "mm".
            m = full(r"(.+?) (?:disc )?(?:frame|frameset)", s)
            if m and material(m[1]) is not None:
                add("frame_material", m[1], part, "title")
                continue
            m = full(r"(.+?) wheels?", s)
            if m and wheel(m[1]) is not None:
                add("wheel_size", m[1], part, "title")
                continue
            m = full(r"((?:shimano|sram) .+) (?:groupset|drivetrain)", s)
            if m:
                add("drivetrain", m[1], part, "title")
                continue

            # Narrow model-title templates for patterns in the Big Ring audit.
            # Do not generalize these to "<anything> <token> <anything>".
            if full(r"vaast e/1 xt\s*/\s*slx(?: e-?bike)?", s):
                add("drivetrain", "xt/slx", part, "title")
            elif full(r"time (?:adh alpe d'huez|adhx) carbon disc frameset", s):
                add("frame_material", "carbon", part, "title")

    values, issues = {}, {}
    for attr, observations in hits.items():
        distinct = {h.value for h in observations}
        if None in distinct:
            issues[attr] = "unsupported_or_ambiguous"
        elif len(distinct) != 1:
            issues[attr] = "conflict"
        else:
            values[attr] = distinct.pop()
    return {"values": values, "evidence": dict(hits), "issues": issues}


@dataclass(frozen=True)
class Pack:
    qty: int | None
    # (Ah as exact normalized decimal string, voltage if explicitly attached)
    ratings: tuple[tuple[str, int | None], ...] = ()


def _tool_text(s):
    # Do not lowercase or normalize the persisted raw evidence.
    s = s.casefold().translate(
        str.maketrans(
            {
                "×": "x",
                "″": '"',
                "“": '"',
                "”": '"',
                "⁄": "/",
                "‑": "-",
                "–": "-",
                "—": "-",
                "\xa0": " ",
                "¼": "1/4",
                "⅜": "3/8",
                "½": "1/2",
                "¾": "3/4",
            }
        )
    )
    return " ".join(s.split())


def one(values):
    values = set(values)
    return next(iter(values)) if len(values) == 1 else None


# Input is an English, single-configuration power-tool offer. Do not pass
# category navigation, a family description, a battery listing, or a shop badge.
# Discard compatibility/promo tails BEFORE looking for "with".
REL = re.compile(
    r"\b(?:compatible with|for use with|works with|accepts|requires|"
    r"battery sold separately|batteries sold separately|free with purchase|"
    r"buy one get one)\b"
)
INCLUDES = re.compile(r"(?:\b(?:with|includes|including|comes with)\s+|\bw/\s*)")
BARE = r"(?:bare[ -]tool|tool[ -]only)"
BARE_END = re.compile(r"(?:\s*\(\s*|\s*[,;|]\s*|\s+-\s+|\s+)(" + BARE + r")\s*\)?$")


def scopes(title, package_text):
    t = REL.split(_tool_text(title), maxsplit=1)[0].rstrip(" ,;|-")
    parts = INCLUDES.split(t, maxsplit=1)
    head = parts[0].rstrip(" ,;|-")
    statements = [_tool_text(package_text)] if package_text.strip() else []
    if len(parts) == 2:
        statements.append(parts[1])
    m = BARE_END.search(head)
    if m and not re.search(r"\b(?:not|except|excluding)\b", head[: m.start()]):
        statements.append(m[1])
        head = head[: m.start()].rstrip(" ,;|-")
    return head, statements


V = r"(?:v|volts?)\b"
VNUM = re.compile(r"(?<![\w.])([1-9]\d{0,2})\s*-?\s*" + V)
MAKITA_PAIR = re.compile(
    r"(?<!\w)2\s*x\s*(18|40)\s*"
    + V
    + r"|(?<!\w)(18|40)\s*"
    + V
    + r"(?:\s+max)?(?:\s+(?:lxt|xgt))?\s+x2\b"
)


def electrical(brand, s):
    vs = set()
    scan = s
    if brand == "makita":

        def pair(m):
            vs.add(2 * int(m[1] or m[2]))
            return " "

        scan = MAKITA_PAIR.sub(pair, s)
    vs.update(int(m[1]) for m in VNUM.finditer(scan))

    def has(pattern):
        return re.search(pattern, s) is not None

    families = set()

    if brand == "makita":
        for token in ("lxt", "xgt"):
            if has(rf"\b{token}\b"):
                families.add("makita." + token)
    elif brand == "milwaukee":
        for n in (12, 18):
            if has(rf"\bm{n}\b"):
                families.add(f"milwaukee.m{n}")
                vs.add(n)
    elif brand == "dewalt":
        for n in (20, 60):
            if has(rf"\b{n}\s*-?\s*{V}\s+max\b"):
                flex60 = n == 60 and has(r"\bflexvolt\b")
                families.add(
                    "dewalt.flexvolt_60v_max" if flex60 else f"dewalt.{n}v_max"
                )
    elif brand == "bosch" and vs == {18}:
        if has(r"\b(?:professional|ampshare)\b"):
            families.add("bosch.professional_18v")
        if has(r"\bpower for all\b"):
            families.add("bosch.power_for_all_18v")

    # "20/60V" is not a single 60V tool claim. Likewise, don't pick a
    # winner from two distinct families or contradictory voltage claims.
    if has(r"\b\d+\s*/\s*\d+\s*" + V) or len(vs) > 1 or len(families) > 1:
        return None, None
    return one(families), one(vs)


def motor(brand, s, platform):
    # Strip non-FUEL wording and "fuel gauge" before testing the line name.
    fuel_text = re.sub(r"\b(?:non[ -]fuel|fuel gauge)\b", "", s)
    negative = bool(re.search(r"\b(?:brushed|non[ -]brushless|not brushless)\b", s))
    positive_text = re.sub(r"\b(?:non[ -]brushless|not brushless)\b", "", s)
    positive = bool(re.search(r"\bbrushless\b", positive_text))
    positive |= bool(
        brand == "milwaukee"
        and platform in {"milwaukee.m12", "milwaukee.m18"}
        and re.search(r"\bfuel\b", fuel_text)
    )
    positive |= bool(brand == "festool" and re.search(r"\bec[ -]tec\b", s))
    positive |= bool(brand == "makita" and re.search(r"\bbl motor\b", s))
    if positive == negative:  # neither evidenced, or contradictory evidence
        return None
    return positive


# Deliberately require units. Don't start halfway through "6-1/2 in".
SIZE = (
    r"(?<![\w./-])(?P<n>\d+/\d+|\d+(?:\.\d+)?)\s*-?\s*"
    r'(?P<u>inches\b|inch\b|in\b\.?|mm\b|")'
)
ROLE = (
    r"(?P<r>(?:(?:keyless|keyed)\s+)?chuck|square[ -]drive|"
    r"anvil|impact wrench|hex[ -](?:drive|impact driver))\b"
)
FORWARD = re.compile(SIZE + r"\s*[- ]?\s*" + ROLE)
REVERSE = re.compile(ROLE + r"(?:\s+size)?\s*:\s*" + SIZE)


def interface(s):
    out = set()
    for rx in (FORWARD, REVERSE):
        for m in rx.finditer(s):
            if re.match(
                r"\s+(?:adapter|adaptor|attachment|bit|socket)\b", s[m.end() :]
            ):
                continue
            r, n = m["r"], m["n"]
            role = (
                "chuck" if "chuck" in r else "hex" if r.startswith("hex") else "square"
            )
            unit = "mm" if m["u"] == "mm" else "in"
            if "/" in n and int(n.split("/")[1]) == 0:
                continue
            value = (
                str(Fraction(n))
                if unit == "in"
                else (dec_text(n) if "/" not in n else None)
            )
            if value and Fraction(value) > 0:
                out.add((role, value, unit))
    return one(out)


# Contents grammar: full-match each group. A count must bind to packs,
# not tools, cells, chargers, volts, or a model number.
AH = r"(?:[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]\d*)"
Q = r"(?:(?:\((?P<a>[1-9]\d?)\)|(?P<b>[1-9]\d?)\s*x|(?P<c>[1-9]\d?)\s+)\s*)?"
NOUN = r"(?P<n>battery packs?|battery|batteries|packs?)"
GROUP = re.compile(Q + rf"(?:(?P<ah>{AH})\s*ah\s+)?" + NOUN)
SHORT = re.compile(rf"([1-9]\d?)\s*x\s*({AH})\s*ah")
DUAL = re.compile(
    Q + r"(?:flexvolt\s+)?" + NOUN + rf"\s*\(({AH})\s*ah\s*@\s*(\d+)\s*v\s*/\s*"
    rf"({AH})\s*ah\s*@\s*(\d+)\s*v\)"
)
NEUTRAL = {
    "",
    "package not specified",
    "battery/charger inclusion not specified",
    "kit",
    "basic",
    "plus",
}
NO_BATT = {
    "battery not included",
    "batteries not included",
    "no battery",
    "battery and charger not included",
    "batteries sold separately",
    "battery sold separately",
}


def qty(m):
    q = m["a"] or m["b"] or m["c"]
    if q:
        return int(q)
    return 1 if m["n"] in {"battery", "battery pack", "pack"} else None


def pack_group(s):
    m = DUAL.fullmatch(s)
    if m:
        ah1, v1, ah2, v2 = m.groups()[-4:]
        if int(v1) == int(v2):
            return None
        return Pack(qty(m), ((dec_text(ah1), int(v1)), (dec_text(ah2), int(v2))))
    m = GROUP.fullmatch(s)
    if m:
        return Pack(qty(m), ((dec_text(m["ah"]), None),) if m["ah"] else ())
    m = SHORT.fullmatch(s)
    if m:
        return Pack(int(m[1]), ((dec_text(m[2]), None),))
    return None


def box_statement(s):
    if re.fullmatch(BARE, s):
        return "bare", ()
    if s in NO_BATT:
        return "no_battery", ()
    if s in NEUTRAL:
        return "neutral", ()
    packs = []
    for part in re.split(r"\s*(?:\+|,|&|\band\b)\s*", s):
        if re.fullmatch(
            r"(?:(?:rapid|standard|\d+(?:\.\d+)?\s*a)\s+)?charger"
            r"|bag|case",
            part,
        ):
            continue
        p = pack_group(part)
        if p is None:
            return "unparsed", ()  # Never publish half a list as the total.
        packs.append(p)
    return ("kit", tuple(packs)) if packs else ("neutral", ())


def package(statements):
    claims = [box_statement(s) for s in statements]
    kinds = {k for k, _ in claims}
    kits = {v for k, v in claims if k == "kit"}  # don't add duplicate statements
    if (kits and kinds & {"bare", "no_battery"}) or len(kits) > 1:
        return "conflict", None, ()
    if "unparsed" in kinds:
        return "unknown", None, ()
    if kits:
        ps = next(iter(kits))
        n = None if any(p.qty is None for p in ps) else sum(p.qty for p in ps)
        return "battery_kit", n, ps
    if "bare" in kinds:
        return "bare", 0, ()
    # "No battery" alone doesn't establish the rest of a bare-tool package.
    return "unknown", 0 if "no_battery" in kinds else None, ()


def extract_benchdrop_specs(product_dict):
    """Read the selected tool title, brand, and package contents."""
    brand = product_dict.get("brand", "")
    title = product_dict["title"]
    package_text = product_dict.get("package_text", "")
    b = _tool_text(brand)
    head, statements = scopes(title, package_text)
    platform = volts = brushless = drive = count = None
    kind, packs = "unknown", ()
    if not re.search(r"\bbatter(?:y|ies)\b|\bcharger\b|\d\s*ah\b", head):
        platform, volts = electrical(b, head)
        brushless = motor(b, head, platform)
        drive = interface(head)
        kind, count, packs = package(statements)
    values = {
        "platform": platform,
        "volts": volts,
        "brushless": brushless,
        "interface": drive,
        "package": kind,
        "battery_count": count,
        "packs": packs,
    }
    return {
        "values": values,
        "evidence": (brand, title, package_text),
        "issues": ("conflicting package claims",) if kind == "conflict" else (),
    }


class Row(NamedTuple):
    section: str
    label: str
    value: str


def _camp_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s.replace("º", "°")).casefold()
    s = s.translate(str.maketrans({c: "-" for c in "‐‑‒–—−"}))
    return " ".join(s.split())


# All measurement grammars full-match the value. No float, substring
# number search, thousands separator, scientific notation, or unit guessing.
CAMP_NUMBER = r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
OZ = Decimal("28.349523125")
LB = 16 * OZ
MASS_UNITS = {
    "g": Decimal(1),
    "kg": Decimal(1000),
    "oz": OZ,
    "lb": LB,
    "lbs": LB,
}


def grams(v: str) -> Decimal | None:
    m = re.fullmatch(rf"({CAMP_NUMBER})\s*(kg|g|oz|lbs?)", v)
    if m:
        g = Decimal(m[1]) * MASS_UNITS[m[2]]
    else:
        # Compound avoirdupois, not a range or two alternative measurements.
        m = re.fullmatch(rf"([0-9]+)\s*lbs?\s+({CAMP_NUMBER})\s*oz", v)
        if not m or Decimal(m[2]) >= 16:
            return None
        g = Decimal(m[1]) * LB + Decimal(m[2]) * OZ
    if g <= 0:
        return None
    return g.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


# Accept identified standards, not a bare "EN", "ISO tested", or "ASTM".
# Part 2 of ISO 23537 is deliberately not a temperature-rating method here.
THERM = r"(?:en 13537|(?:en )?iso 23537(?:-1)?)(?::[0-9]{4})?"
PAD = r"astm f\s?3340(?:-[0-9]{2}[a-z]?)?"
STD = rf"(?:{THERM}|{PAD})"


def method_name(s: str) -> str:
    return s.upper().replace("F 3340", "F3340")


def rated_parts(row: Row):
    """Method may be the whole heading, a label qualifier, or a value suffix."""
    h, k, v = map(_camp_text, row)
    methods = set()
    if re.fullmatch(STD, h):
        methods.add(method_name(h))
    # "EN 13537 comfort", "ISO 23537-1:2022: lower limit"
    m = re.fullmatch(rf"({STD})(?:\s*:\s*|\s+)(.+)", k)
    if m:
        methods.add(method_name(m[1]))
        k = m[2]
    # "comfort (EN 13537)", "4.8 (ASTM F3340-22)"
    for is_label in (True, False):
        s = k if is_label else v
        m = re.fullmatch(rf"(.+?)\s*\(({STD})\)", s)
        if m:
            methods.add(method_name(m[2]))
            if is_label:
                k = m[1].strip()
            else:
                v = m[1].strip()
    return k, v, methods


TEMP_ROLES = {
    "comfort": "comfort_temp",
    "comfort rating": "comfort_temp",
    "comfort temperature": "comfort_temp",
    "tcomfort": "comfort_temp",
    "limit": "limit_temp",
    "lower limit": "limit_temp",
    "limit rating": "limit_temp",
    "lower limit rating": "limit_temp",
    "limit temperature": "limit_temp",
    "tlim": "limit_temp",
}
R_LABELS = {"r-value", "r value", "thermal resistance (r-value)"}
BEDS = {"sleeping_bag", "quilt"}

FP = re.compile(
    r"(?P<n>[1-9][0-9]{2,3})(?P<plus>\+)?"
    r"(?:[ -]*(?P<marker>fp|fill[ -]power|fill))?"
    r"(?:\s+(?P<down>(?:(?:duck|goose) )?down))?"
)
FILL_MATERIAL = {"fill type", "fill material", "insulation type"}
PURE_DOWN = {"down", "goose down", "duck down"}
TORSO_SIZES = {
    "xs",
    "s",
    "m",
    "l",
    "xl",
    "xs/s",
    "s/m",
    "m/l",
    "l/xl",
    "short",
    "regular",
    "long",
}


def extract_highline_specs(product_dict):
    """Read (section, label, value) rows for the selected camping product."""
    kind = product_dict["kind"]
    title = product_dict.get("title", "")
    rows = [Row(*row) for row in product_dict.get("rows", ())]
    observations = defaultdict(list)

    def put(key, value, *evidence):
        observations[key].append((value, evidence))

    # Only two title rules: explicit tent descriptors and a unit-bearing
    # pack-name suffix. No FP, temperature, ASTM, or mass mining from titles.
    t = _camp_text(title)
    title_row = Row("", "@title", title)
    single_name = not re.search(
        r"\b(?:not|non|fits|compatible|replacement|without|bundle|versus)\b", t
    )
    if kind == "tent" and single_name:
        if re.search(
            r"\bultra[ -]?light (?:"
            r"(?:backpacking|freestanding|bikepacking|[1-9][ -]person) "
            r")*tent\b",
            t,
        ):
            put("ultralight_claim", True, title_row)
        # Do not turn "1–2 person" into a 2-person exact-capacity facet.
        if not re.search(r"\d\s*(?:[-/+]|to|or)\s*\d", t):
            for m in re.finditer(r"\b([1-9][0-9]?)[ -]person\b", t):
                put("capacity_people", int(m[1]), title_row)

    if kind == "backpack" and single_name:
        m = re.search(rf"\b(?:pack|backpack) ({CAMP_NUMBER})\s*l$", t)
        if m and Decimal(m[1]) > 0:
            put("volume_l", Decimal(m[1]), title_row)

    materials = [r for r in rows if _camp_text(r.label) in FILL_MATERIAL]
    down_material = bool(materials) and all(
        _camp_text(r.value) in PURE_DOWN for r in materials
    )

    for r in rows:
        k, v = _camp_text(r.label), _camp_text(r.value)
        rk, rv, methods = rated_parts(r)
        method = next(iter(methods)) if len(methods) == 1 else None

        if kind in BEDS and rk in TEMP_ROLES:
            m = re.fullmatch(rf"([+-]?{CAMP_NUMBER})\s*°?\s*([cf])", rv)
            valid = len(methods) <= 1 and (
                method is None or not method.startswith("ASTM")
            )
            put(
                TEMP_ROLES[rk],
                (Decimal(m[1]), m[2].upper(), method) if valid and m else None,
                r,
            )
            continue

        if kind == "sleeping_pad" and rk in R_LABELS:
            valid = (
                len(methods) <= 1
                and (method is None or method.startswith("ASTM"))
                and re.fullmatch(CAMP_NUMBER, rv)
                and Decimal(rv) > 0
            )
            put("r_value", (Decimal(rv), method) if valid else None, r)
            continue

        if kind in BEDS and k in {
            "down fill power",
            "fill power",
            "insulation",
            "fill",
        }:
            m = FP.fullmatch(v)
            # Plain "fill: down" states material but makes no FP claim.
            if k in {"insulation", "fill"} and not (m and m["marker"]):
                continue
            explicit_down = m and (m["down"] or k == "down fill power")
            valid = (
                m
                and (explicit_down or down_material)
                and (not materials or down_material)
            )
            put(
                "down_fill_power",
                (int(m["n"]), "at_least" if m["plus"] else "stated") if valid else None,
                r,
                *materials,
            )
            continue

        if kind == "tent":
            if k in {"occupancy", "sleeping capacity", "capacity"}:
                m = re.fullmatch(r"([1-9][0-9]?)(?:[ -](?:person|people))?", v)
                put("capacity_people", int(m[1]) if m else None, r)
            basis = {
                "packed weight": "packed_weight_g",
                "minimum weight": "minimum_weight_g",
                "minimum trail weight": "minimum_weight_g",
                "trail weight": "trail_weight_g",
            }.get(k)
            if basis:
                put(basis, grams(v), r)

        if kind == "backpack":
            if k in {"volume", "pack volume", "capacity", "pack capacity"}:
                m = re.fullmatch(rf"({CAMP_NUMBER})\s*(?:l|liters?|litres?)", v)
                put("volume_l", Decimal(m[1]) if m and Decimal(m[1]) > 0 else None, r)
            elif k == "torso size":
                size = re.sub(r"\s*/\s*", "/", v)
                put("torso_size", size.upper() if size in TORSO_SIZES else None, r)
            elif k in {"torso length", "fits torso", "torso range"}:
                m = re.fullmatch(
                    rf'({CAMP_NUMBER})(?:\s*(?:-|to)\s*({CAMP_NUMBER}))?\s*(cm|in|inches|")',
                    v,
                )
                result = None
                if m:
                    factor = Decimal(1) if m[3] == "cm" else Decimal("2.54")
                    lo, hi = Decimal(m[1]) * factor, Decimal(m[2] or m[1]) * factor
                    if 0 < lo <= hi:
                        result = (lo, hi)
                put("torso_length_cm", result, r)

        # "Shell weight" is a literal shell-specific measurement. It is
        # never substituted for the whole bag, tent, or garment weight.
        if kind in BEDS | {"tent", "shell_garment"} and k in {
            "shell weight",
            "shell-only weight",
            "outer shell weight",
        }:
            put("shell_weight_g", grams(v), r)
        if kind == "shell_garment" and k in {"weight", "item weight", "garment weight"}:
            put("garment_weight_g", grams(v), r)

    fields, sources, blocked = {}, {}, {}
    for key, obs in observations.items():
        values = {value for value, _ in obs}
        evidence = tuple(dict.fromkeys(r for _, group in obs for r in group))
        if None in values or len(values) != 1:
            blocked[key] = evidence
        else:
            fields[key] = values.pop()
            sources[key] = evidence
    return {"values": fields, "evidence": sources, "issues": blocked}


def _water_text(s):
    s = unicodedata.normalize("NFKC", s).casefold()  # m² -> m2
    s = s.translate(
        str.maketrans(
            {
                "–": "-",
                "—": "-",
                "‑": "-",
                "−": "-",
                "′": "'",
                "’": "'",
                "″": '"',
                "”": '"',
            }
        )
    )
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)  # high-aspect, front-wing
    return " ".join(s.split())


HEAD = {
    # "foil wing" alone is ambiguous: never use it as a hand-wing alias.
    "hand_wing": r"\b(?:hand ?held wing|hand wing|wing foil wing|wing sail)\b",
    "front_wing": r"\bfront wing\b",
    "mast": r"\b(?:foil|hydrofoil) mast\b",
    "board": (
        r"\b(?:wing foil board|wingfoil board|foil board|"
        r"(?:slingshot )?wing craft v2)\b"
    ),
    "kite": r"\b(?:kite|kiteboarding kite)\b",
    "complete_foil": r"\bcomplete (?:hydrofoil|foil)(?: assembly| system| kit)?\b",
}

COMPONENT_PACK = r"\b(?:wing|component) (?:pack|set)\b"
ACCESSORY = (
    r"\b(?:bag|cover|case|sleeve|adapter|adaptor|shim|screws?|bolts?|"
    r"hardware|base plate|mounting plate|mast plate|repair kit)\b"
)
RELATION = r"\b(?:compatible with|designed for|for use with|for|fits|with)\b"
INCOMPLETE = (
    r"\b(?:without|no|not|only|missing|incomplete|excluded|"
    r"optional|requires|sold separately)\b"
)


def classify(title):
    """Return (sold kind, title head before relational copy, matched noun)."""
    full = _water_text(title)
    head = re.split(RELATION, full, maxsplit=1)[0].strip()

    if re.search(ACCESSORY, head):
        return "accessory", head, None
    if re.search(COMPONENT_PACK, head):
        return "component_pack", head, None

    hits = {k: list(re.finditer(pattern, head)) for k, pattern in HEAD.items()}
    hits = {k: ms for k, ms in hits.items() if ms}

    # Do not choose the last noun in "Foil Board + Foil Mast".
    if len(hits) != 1 or re.search(r"[+&]|\b(?:and|or)\b", head):
        return "unknown", head, None
    kind, matches = next(iter(hits.items()))

    if kind == "complete_foil":
        # Check the FULL title: "Complete foil kit with mast sold separately"
        # is not positive complete-assembly evidence.
        if re.search(INCOMPLETE, full):
            return "unknown", head, None
    elif re.search(r"\b(?:kit|package|bundle|set|pack)\b", head):
        return "unknown", head, None

    return kind, head, matches[-1]


# Strict scalar grammar. Locale-specific numbers such as "1,200" or "4,5"
# must be resolved by a locale-aware source adapter, not by deleting commas.
WATER_NUMBER = r"(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,3})?"
WATER_CAPTURE = rf"(?P<n>{WATER_NUMBER})"
M2 = r"(?:m\s*\^?\s*2|sq\.?\s*m|square met(?:er|re)s?)"
CM2 = r"(?:cm\s*\^?\s*2|sq\.?\s*cm|square centimet(?:er|re)s?)"
SEP = re.compile(r"[\s;|·]+")
ASPECT = {
    "ha": "HA",
    "high aspect": "HA",
    "ma": "MA",
    "mid aspect": "MA",
    "medium aspect": "MA",
    "la": "LA",
    "low aspect": "LA",
}


def number(m):
    return Decimal(m["n"])


def length_cm(m):
    return (
        number(m) * {"mm": Decimal("0.1"), "cm": Decimal(1), "m": Decimal(100)}[m["u"]]
    )


def token(field, pattern, decode):
    # Require a token boundary after the entire value + unit.
    return field, re.compile(rf"(?:{pattern})(?=$|[\s;|·])"), decode


# A recognized board dimension pair can coexist with volume. It emits
# nothing. No length x width x thickness approximation of volume is allowed.
DIM = rf"""(?:{WATER_NUMBER}'\s*{WATER_NUMBER}"|{WATER_NUMBER}"|{WATER_NUMBER}\s*cm)"""
BOARD_DIMS = rf"{DIM}\s*[x×]\s*{DIM}(?:\s*[x×]\s*{DIM})?"

GRAMMAR = {
    "hand_wing": (token("wing_area_m2", rf"{WATER_CAPTURE}\s*{M2}", number),),
    "front_wing": (
        token("front_wing_area_cm2", rf"{WATER_CAPTURE}\s*{CM2}", number),
        token(
            "aspect_ratio", rf"(?:ar|aspect ratio)\s*[:=]?\s*{WATER_CAPTURE}", number
        ),
        token(
            "aspect_class",
            r"ha|ma|la|(?:high|medium|mid|low) aspect",
            lambda m: ASPECT[m[0]],
        ),
    ),
    "mast": (
        token("mast_length_cm", rf"{WATER_CAPTURE}\s*(?P<u>mm|cm|m)", length_cm),
        token(
            "mast_material",
            r"(?:hm |high modulus )?carbon(?: fib(?:er|re))?",
            lambda m: "carbon",
        ),
        token("mast_material", r"alloy|alumin(?:um|ium)(?: alloy)?", lambda m: "alloy"),
    ),
    "board": (
        token("board_volume_l", rf"{WATER_CAPTURE}\s*(?:l|lit(?:er|re)s?)", number),
        token(None, BOARD_DIMS, lambda m: None),
    ),
}


def read_block(text, kind):
    """All-or-nothing: never extract the last scalar from '75-85 cm'."""
    s = _water_text(text)
    pos, out = 0, []
    if not s:
        return None
    while pos < len(s):
        for field, rx, decode in GRAMMAR[kind]:
            m = rx.match(s, pos)
            if m:
                value = decode(m)
                if isinstance(value, Decimal) and value <= 0:
                    return None
                if field is not None:
                    out.append((field, value, m[0]))
                pos = m.end()
                break
        else:
            return None
        if pos < len(s):
            sep = SEP.match(s, pos)
            if not sep or sep.end() == len(s):
                return None
            pos = sep.end()
    return out


def extract_tideline_specs(product_dict):
    """Read selected spec cells. Only an exact configuration title supplies values."""
    title = product_dict["title"]
    selected = product_dict.get("selected", ())
    exact_title = product_dict.get("exact_title", False)
    kind, head, noun = classify(title)
    if kind not in GRAMMAR:
        # Crucial: a pack or complete assembly has NO primary-component
        # grammar. A mast length in its package description is not its size.
        return {"kind": kind, "values": {}, "evidence": {}, "issues": ()}

    seen = defaultdict(list)
    issues = []

    def add(rows, source, raw):
        for field, value, lexeme in rows:
            seen[field].append((value, source, raw, lexeme))

    if exact_title:
        # Only consume an entire spec block adjacent to the product noun.
        # Unrecognized brand/model prose is not mined for numbers.
        left = head[: noun.start()].strip()
        right = re.sub(r"^(?:\s+-\s+|\s*:\s*)", "", head[noun.end() :]).strip()
        for part in (left, right):
            if part:
                rows = read_block(part, kind)
                if rows is not None:
                    add(rows, "exact_title", title)

    for i, raw in enumerate(selected):
        rows = read_block(raw, kind)
        if rows is None:
            # The caller marked this as a relevant spec cell. Don't fall
            # back to the title if we cannot interpret the selected option.
            return {
                "kind": kind,
                "values": {},
                "evidence": {},
                "issues": (f"unparsed:selected[{i}]",),
            }
        add(rows, f"selected[{i}]", raw)

    values = {}
    for field, records in seen.items():
        distinct = {r[0] for r in records}
        if len(distinct) == 1:
            values[field] = distinct.pop()
        else:
            issues.append(f"conflict:{field}")  # no last-write-wins
    return {
        "kind": kind,
        "values": values,
        "evidence": dict(seen),
        "issues": tuple(issues),
    }


_EXTRACTORS = {
    "freshpowder": extract_freshpowder_specs,
    "fresh-powder": extract_freshpowder_specs,
    "bigring": extract_bigring_specs,
    "cycling": extract_bigring_specs,
    "benchdrop": extract_benchdrop_specs,
    "tools": extract_benchdrop_specs,
    "highline": extract_highline_specs,
    "camp": extract_highline_specs,
    "tideline": extract_tideline_specs,
    "watersports": extract_tideline_specs,
}


def extract_fit(vertical, product_dict):
    """Extract a selected offer's specs by vertical name or catalog slug."""
    return _EXTRACTORS[vertical.casefold()](product_dict)
