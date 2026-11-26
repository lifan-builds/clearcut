"""Shared utilities for HTML parsers."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

PLACEHOLDER_PRODUCT_NAMES = {
    "gearhead top pick",
    "top pick",
    "product image",
    "image",
}


_FOREIGN_CURRENCY_RE = re.compile(
    r"[€£¥₹₩₽₺₪₫₱฿₴₦₲₵₡₸]"
    r"|\b(?:AU|NZ|HK|MX|SG|NT|TT|RD|BZ|FJ|GY|JM|KY|LR|NA|SB|SR|TW|ZW)\s*\$"
    r"|(?<![A-Za-z])[ARS]\s*\$"
    r"|\b(?:EUR|GBP|AUD|NZD|JPY|CHF|SEK|NOK|DKK|MXN|HKD|SGD|CNY|RMB|KRW|INR|BRL|ZAR|PLN|CZK)\b",
    re.IGNORECASE,
)
_NON_PRICE_COPY_RE = re.compile(
    r"\b(?:you\s+)?save\s*:?\s*(?:up\s+to\s*)?(?:(?:US|CA|C)\s*)?\$?\s*[\d,]+(?:\.\d+)?\s*%?"
    r"|(?:(?:US|CA|C)\s*)?\$\s*[\d,]+(?:\.\d+)?\s*off\b"
    r"|\b\d+(?:\.\d+)?\s*%(?:\s*off\b)?"
    r"|\b(?:as\s+low\s+as|from)\s*(?:(?:US|CA|C)\s*)?\$?\s*[\d,]+(?:\.\d+)?\s*(?:/|\s*per\s*)mo(?:nth)?\b"
    r"|(?:(?:US|CA|C)\s*)?\$?\s*[\d,]+(?:\.\d+)?\s*/\s*mo(?:nth)?\b",
    re.IGNORECASE,
)
_EXPLICIT_USD_CAD_RE = re.compile(
    r"(?<![A-Za-z])(?:(?:US|CA|C)\s*)?\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?",
    re.IGNORECASE,
)
_BARE_NUMBER_RE = re.compile(r"(?<![A-Za-z$\d.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![A-Za-z%\d])")


def parse_price(text: str) -> float | None:
    """Extract a numeric USD/CAD price from text like '$269.99', 'C$485.99', or '579.99'."""
    if not text:
        return None
    raw = text.strip()
    if not raw or _FOREIGN_CURRENCY_RE.search(raw):
        return None
    cleaned = _NON_PRICE_COPY_RE.sub(" ", raw)
    match = _EXPLICIT_USD_CAD_RE.search(cleaned) or _BARE_NUMBER_RE.search(cleaned)
    if not match:
        return None
    whole = match.group(1).replace(",", "")
    frac = match.group(2)
    value = float(f"{whole}.{frac}" if frac is not None else whole)
    return value if value >= 0 else None


def product_slug_words(url: str) -> list[str]:
    """Return normalized words from a product URL slug."""
    if not url:
        return []
    path = unquote(urlparse(url).path)
    slug = re.sub(r"\.(?:html?|php)$", "", path.rstrip("/").split("/")[-1], flags=re.IGNORECASE).strip("-_ ")
    return [w for w in re.split(r"[-_]+", slug) if w] if slug else []


def product_name_from_url(url: str) -> str | None:
    """Build a readable product name from a product URL slug."""
    words = product_slug_words(url)
    return " ".join(words).title() if words else None


def clean_product_name(name: str | None, url: str = "") -> str:
    """Normalize product names and recover from known placeholder labels."""
    cleaned = re.sub(r"\s+", " ", name or "").strip()
    if cleaned.lower() in PLACEHOLDER_PRODUCT_NAMES:
        return product_name_from_url(url) or ""
    return cleaned
