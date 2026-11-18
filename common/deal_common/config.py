"""Retailer connection settings, independent of any product taxonomy."""

from dataclasses import dataclass, field


@dataclass
class RetailerConfig:
    name: str
    domain: str
    scrape_urls: list[str] = field(default_factory=list)
    parser_type: str = "shopify"
    use_browser: bool = False
    currency: str = "USD"
    shopify_products_limit: int = 250
    verify_shopify_currency: bool = False
