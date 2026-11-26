from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from deal_common.models import Product
from deal_common.parsers.base import BaseParser
from deal_common.parsers.common import parse_price as _parse_price

log = logging.getLogger(__name__)


class ColoradoDiscountParser(BaseParser):
    """Parser for coloradodiscountskis.com product listing pages."""

    def parse_listing_page(self, html: str, page_url: str) -> list[Product]:
        soup = BeautifulSoup(html, "lxml")
        products: list[Product] = []

        for li in soup.find_all("li"):
            product = self._parse_li(li, page_url)
            if product:
                products.append(product)

        log.info("Parsed %d products from %s", len(products), page_url)
        return products

    def get_next_page_url(self, html: str, current_url: str) -> str | None:
        # This site uses separate pages per brand/year; no pagination within
        # a single listing page.
        return None

    _SOLD_OUT_CLASSES = frozenset({"sold-out", "soldout", "out-of-stock", "outofstock"})
    _SOLD_OUT_TEXT_RE = re.compile(r"\b(?:sold\s+out|out\s+of\s+stock)\b", re.IGNORECASE)
    _UPSELL_AND_SAVINGS_RE = re.compile(
        r"\(\s*(?:add|with|w/|upgrade)\b[^)]*\)"
        r"|\+\s*\$[\d,]+\.?\d*"
        r"|\b(?:you\s+)?save\s*:?\s*\$[\d,]+\.?\d*"
        r"|\$[\d,]+\.?\d*\s*off\b"
        r"|\b(?:as\s+low\s+as|from)\s*\$[\d,]+\.?\d*\s*(?:/|\s*per\s*)mo(?:nth)?\b"
        r"|\$[\d,]+\.?\d*\s*/\s*mo(?:nth)?\b",
        re.IGNORECASE,
    )
    _USD_TOKEN_RE = re.compile(r"(?<![A-Za-z])(?:US)?\$[\d,]+\.?\d*")

    def _parse_li(self, li: Tag, page_url: str) -> Product | None:
        classes = {c.lower() for c in (li.get("class") or [])}
        if classes & self._SOLD_OUT_CLASSES:
            return None
        links = li.find_all("a")
        if not links:
            return None

        # Find the first <a> with a title attribute for the product URL
        titled_link: Tag | None = None
        for link in links:
            if link.get("title"):
                titled_link = link
                break

        # Product name: prefer second <a> text, fall back to title attr
        name: str | None = None
        name_link = li.select_one("a.bb-prodnme")
        if name_link:
            name = name_link.get_text(strip=True)
        elif len(links) >= 2:
            name = links[1].get_text(strip=True)
        if not name and titled_link:
            name = titled_link.get("title", "").strip()
        if not name:
            return None

        # Product URL
        if titled_link and titled_link.get("href"):
            url = urljoin(page_url, titled_link["href"])
        else:
            url = urljoin(page_url, links[0].get("href", ""))

        li_text = li.get_text(" ", strip=True)
        if self._SOLD_OUT_TEXT_RE.search(li_text):
            return None

        cleaned_text = self._UPSELL_AND_SAVINGS_RE.sub(" ", li_text)
        price_matches = self._USD_TOKEN_RE.findall(cleaned_text)
        if not price_matches:
            return None

        parsed_prices = []
        for pm in price_matches:
            p = _parse_price(pm)
            if p is not None and p > 0:
                parsed_prices.append(p)

        if not parsed_prices:
            return None

        current_price: float
        original_price: float | None = None

        if len(parsed_prices) == 1:
            current_price = parsed_prices[0]
        else:
            # First price is original, second is sale price
            original_price = parsed_prices[0]
            current_price = parsed_prices[1]
            if original_price <= current_price:
                original_price = None

        return Product(
            name=name,
            url=url,
            current_price=current_price,
            original_price=original_price,
            image_url=urljoin(page_url, img.get("data-src") or img.get("src") or "")
            if (img := li.select_one("img[data-src], img[src]")) else None,
            sizes=[length + "cm" for group in re.findall(r"\b((?:\d{3}\s*[,/]\s*)*\d{3})\s*cm\b", name, re.IGNORECASE)
                   for length in re.findall(r"\d{3}", group)] or None,
        )
