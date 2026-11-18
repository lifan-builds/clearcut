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


def parse_price(text: str) -> float | None:
    """Extract a numeric price from text like '$269.99', 'C$485.99', or '579.99'."""
    match = re.search(r"(?:C?\$)?([\d,]+\.?\d*)", text.strip())
    return float(match.group(1).replace(",", "")) if match else None


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
