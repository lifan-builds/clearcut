import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deal_common.config import RetailerConfig
from deal_common.models import Product


def manufacturer_name(value: str | None, *, brand_aliases: tuple[tuple[str, str], ...] = ()) -> str:
    """Reject fulfillment labels, then apply only the catalog's exact aliases."""
    name = (value or "Unspecified").strip()
    if re.fullmatch(r"in stock|out of stock|esd[:：].*", name, re.IGNORECASE):
        return "Unspecified"
    return dict(brand_aliases).get(name.casefold(), name)


@dataclass(frozen=True)
class Classification:
    category: str
    format: str = ""
    platform: str = ""


@dataclass(frozen=True)
class Catalog:
    slug: str
    name: str
    eyebrow: str
    headline: str
    description: str
    categories: tuple[str, ...]
    sources: tuple[RetailerConfig, ...]
    classify: Callable[[Product], Classification | None]
    default_reference: int
    assets: Path
    art_label: str
    theme_color: str
    mark: str = "↗"
    size_label: str = "Size"
    formats: tuple[str, ...] = ()
    buying_note: str = ""
    coverage_note: str = ""
    size_option_names: tuple[str, ...] = ()
    size_value_pattern: str = ""
    brand_aliases: tuple[tuple[str, str], ...] = ()
    opt_in_categories: tuple[str, ...] = ()
    collapse_duplicates: bool = False
    reconcile_product: Callable[[dict], dict | None] | None = None
    variant_fallback: Callable[[dict], str] | None = None
