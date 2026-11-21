"""Snow catalog deal model with store, review, and length metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from deal_common.offers import VariantOffer


def model_year(name: str) -> int | None:
    """Read explicit title years; never infer age from price or first observation."""
    season_4_2 = re.findall(r"\b(20\d{2})\s*[-–/]\s*(\d{2})\b(?![\d.])", name)
    if season_4_2:
        start, end = int(season_4_2[-1][0]), int(season_4_2[-1][1])
        if (start % 100) + 1 == end:
            return (start // 100) * 100 + end
    years = re.findall(r"\b(?:19|20)\d{2}\b", name)
    if years:
        return int(years[-1])
    for start_s, end_s in reversed(re.findall(
        r"(?<![\d.])\b(1[5-9]|2\d)\s*[-–/]\s*(\d{2})\b(?![\d.])(?!\s*(?:mm|cm|m\b|in\b|\"|l\b|oz\b|din\b))",
        name,
        re.IGNORECASE,
    )):
        if int(start_s) + 1 == int(end_s):
            return 2000 + int(end_s)
    apostrophe = re.findall(r"(?<![A-Za-z0-9])['’](1[89]|2\d)\b(?![\d.\"’'])", name)
    if apostrophe:
        return 2000 + int(apostrophe[-1])
    if re.search(r"\b00\s*[-–]\s*01\b", name):
        return 2001
    return None


@dataclass
class AggregatedDeal:
    """A deal record enriched with store and category metadata."""

    id: int | None
    store: str
    name: str
    url: str
    current_price: float
    original_price: float | None
    discount_pct: float
    category: str | None
    sizes: str | None  # Comma-separated available sizes
    length_min: int | None  # Shortest length in cm (extracted from sizes)
    length_max: int | None  # Longest length in cm (extracted from sizes)
    scraped_at: datetime
    image_url: str | None = None
    brand: str | None = None
    review_score: int | None = None
    review_award: str | None = None
    review_url: str | None = None
    first_seen: str | None = None
    previous_price: float | None = None
    currency: str = "USD"
    product_type: str | None = None

    offers: list[VariantOffer] = field(default_factory=list)
    offer_url: str | None = None
    offer_options: str | None = None
    availability: str = 'unknown'
    reference_basis: str = 'retailer_reference'
    variant_key: str | None = None

    @property
    def purchase_url(self) -> str:
        return self.offer_url or self.url

    @property
    def model_label(self) -> str | None:
        if re.search(r"\b00\s*[-–]\s*01\b", self.name):
            return 'Legacy model · 00–01'
        year = model_year(self.name)
        return f'Model {year}' if year else None
