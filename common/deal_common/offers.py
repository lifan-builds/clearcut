"""Variant offer facts; an unknown stock value is never a stock confirmation."""
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

MAX_REFERENCE_PRICE = 99_999.0
MIN_OFFER_PRICE = 1.0
MAX_DISCOUNT_PCT = 98.0
SIBLING_PRICE_RATIO = 25
SIBLING_REFERENCE_FLOOR = 250.0
DROP_WINDOW = timedelta(hours=36)
HISTORY_DAYS = 45
NEW_LOW_MIN_TRACKED = timedelta(days=7)

_SIZE_ALIASES = {
    'xx-small': 'xxs', 'xxsmall': 'xxs', '2x-small': 'xxs', '2xs': 'xxs',
    'x-small': 'xs', 'extra-small': 'xs', 'xsmall': 'xs', 'extrasmall': 'xs',
    'small': 's', 'sm': 's',
    'medium': 'm', 'md': 'm',
    'large': 'l', 'lg': 'l',
    'extra-large': 'xl', 'x-large': 'xl', 'xlarge': 'xl', 'extralarge': 'xl',
    'xx-large': 'xxl', 'xxlarge': 'xxl', 'extra-extra-large': 'xxl', '2x-large': 'xxl', '2xl': 'xxl',
    'xxx-large': 'xxxl', 'xxxlarge': 'xxxl', '3x-large': 'xxxl', '3xl': 'xxxl',
}
_ADDON_OPTION_RE = re.compile(
    r"(?<!\bno )(?<!\bwithout )\b(?:"
    r"gift[- ]?(?:wraps?|wrapping|box(?:es)?|bags?|cards?|certificates?)|"
    r"(?:[1-5][- ]?yr|[1-5][- ]?year|extended|accident|lifetime) warrant(?:y|ies)|"
    r"warrant(?:y|ies) (?:plans?|only|extensions?)|"
    r"(?:extend|accident|shipping|package|order|damage) protection(?: plans?)?|"
    r"protection plans?|navidium|route protection|"
    r"(?:reservation|pre[- ]?order|refundable|layaway|build) deposits?|deposit only|"
    r"(?:(?:bike|ski|binding|professional|custom|white[- ]glove) )?(?:assembly|mounting|tuning|waxing) (?:services?|fees?|charges?|only)|"
    r"touch[- ]?up paints?|(?:color|paint|fabric|finish) swatch(?:es)?|sample only"
    r")\b",
    re.IGNORECASE,
)


def size_key(value: str | None) -> str:
    value = re.sub(r'\s+', '', (value or '').casefold())
    value = re.sub(r'^mondo(?=\d)', '', value)
    value = re.sub(r'(?<=\d)(?:mondopoint|mondo|mp)$', '', value)
    value = re.sub(r'^(\d{3})(?:(?:cm)?wide|wcm)$', r'\1w', value)
    value = re.sub(r'(cm|mondo)$', '', value)
    return _SIZE_ALIASES.get(value, value)


def discount(price: float, reference: float | None) -> float:
    """Calculate from decimal price inputs; only presentation may round."""
    if reference is None or not all(math.isfinite(x) for x in (price, reference)) or not 0 < price < reference:
        return 0.0
    sale, base = Decimal(str(price)), Decimal(str(reference))
    return float(100 * (base - sale) / base)


def price_signal(price: float, previous: float | None, previous_at: str | None,
                 low: float | None, tracked_since: str | None, observed_at: str) -> str | None:
    """Dropped: below the same variant's previous check within the daily freshness window.
    New low: that drop also undercuts every price kept for the variant over at least a week."""
    if previous is None or Decimal(str(previous)) <= Decimal(str(price)):
        return None
    now = datetime.fromisoformat(observed_at)
    if not timedelta(0) < now - datetime.fromisoformat(previous_at) <= DROP_WINDOW:
        return None
    if price < low and now - datetime.fromisoformat(tracked_since) >= NEW_LOW_MIN_TRACKED:
        return "new_low"
    return "dropped"


def plausible_offer(price: float, reference: float | None, *, max_discount: float = MAX_DISCOUNT_PCT) -> bool:
    if reference is None or not all(math.isfinite(x) for x in (price, reference)):
        return False
    if price < MIN_OFFER_PRICE or reference >= MAX_REFERENCE_PRICE:
        return False
    return 0 < discount(price, reference) < max_discount


def is_addon_variant(options: str | None) -> bool:
    cleaned = re.sub(r"\s+", " ", (options or "").strip())
    return bool(cleaned and _ADDON_OPTION_RE.search(cleaned))


def is_price_collapse_addon(reference: float, max_reference: float) -> bool:
    return max_reference >= SIBLING_REFERENCE_FLOOR and reference * SIBLING_PRICE_RATIO <= max_reference


@dataclass
class VariantOffer:
    variant_key: str
    url: str
    current_price: float
    original_price: float
    size: str | None = None
    options: str = ''
    availability: str = 'unknown'
    image_url: str | None = None
    reference_basis: str = 'retailer_compare_at'
    availability_note: str = ''
    price_note: str = ''

    @property
    def discount_pct(self) -> float:
        return discount(self.current_price, self.original_price)
