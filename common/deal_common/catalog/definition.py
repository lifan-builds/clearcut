import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from deal_common.config import RetailerConfig
from deal_common.models import Product


def clean_brand(value: str | None) -> str:
    return " ".join((value or "").split())


@lru_cache(maxsize=64)
def _compile_brand_aliases_cached(
    brand_aliases: tuple[tuple[str, str], ...],
) -> tuple[dict[str, str], dict[str, str]]:
    aliases: dict[str, str] = {}
    preferred: dict[str, str] = {}
    for raw_key, raw_target in brand_aliases:
        src = clean_brand(raw_key).casefold()
        target_label = clean_brand(raw_target)
        if not src or not target_label:
            continue
        target_key = target_label.casefold()
        existing_alias = aliases.get(src)
        if existing_alias is not None and existing_alias != target_key:
            raise ValueError(f"Conflicting brand alias for {raw_key!r}: {existing_alias!r} vs {target_key!r}")
        existing_pref = preferred.get(target_key)
        if existing_pref is not None and existing_pref != target_label:
            raise ValueError(f"Conflicting preferred brand spelling for {target_key!r}: {existing_pref!r} vs {target_label!r}")
        aliases[src] = target_key
        aliases.setdefault(target_key, target_key)
        preferred[target_key] = target_label
    return aliases, preferred


def compile_brand_aliases(
    brand_aliases: tuple[tuple[str, str], ...] | dict[str, str] = (),
) -> tuple[dict[str, str], dict[str, str]]:
    """Compile normalized spelling -> canonical brand_id and brand_id -> preferred display label."""
    if isinstance(brand_aliases, dict):
        return _compile_brand_aliases_cached(tuple(brand_aliases.items()))
    return _compile_brand_aliases_cached(brand_aliases)


def brand_id(
    value: str | None,
    brand_aliases: tuple[tuple[str, str], ...] | dict[str, str] = (),
    *,
    compiled_aliases: dict[str, str] | None = None,
) -> str:
    key = clean_brand(value).casefold()
    if compiled_aliases is not None:
        return compiled_aliases.get(key, key)
    if not brand_aliases:
        return key
    aliases, _ = compile_brand_aliases(brand_aliases)
    return aliases.get(key, key)


def display_brand(
    key: str,
    observed: Iterable[str],
    preferred: dict[str, str] | None = None,
) -> str:
    if preferred and key in preferred:
        return preferred[key]
    candidates = {cleaned for label in observed if (cleaned := clean_brand(label))}
    if not candidates:
        return key
    return min(candidates, key=lambda s: (s.isupper() or s.islower(), s))


def manufacturer_name(value: str | None, *, brand_aliases: tuple[tuple[str, str], ...] = ()) -> str:
    """Reject fulfillment labels, then apply only the catalog's exact aliases."""
    name = clean_brand(value) or "Unspecified"
    if re.fullmatch(r"in stock|out of stock|esd[:：].*", name, re.IGNORECASE):
        return "Unspecified"
    if not brand_aliases:
        return name
    aliases, preferred = compile_brand_aliases(brand_aliases)
    target_key = aliases.get(name.casefold())
    return preferred[target_key] if target_key is not None else name


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
    exact_offer_key: Callable[[dict, dict, str, str | None], tuple[str, ...] | None] | None = None

    def __post_init__(self) -> None:
        compile_brand_aliases(self.brand_aliases)
