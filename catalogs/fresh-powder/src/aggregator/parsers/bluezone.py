from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from deal_common.models import Product
from deal_common.parsers.base import BaseParser
from deal_common.parsers.common import parse_price as _parse_price

log = logging.getLogger(__name__)


class BlueZoneParser(BaseParser):
    """Parser for bluezonesports.com product listing pages."""

    def parse_listing_page(self, html: str, page_url: str) -> list[Product]:
        soup = BeautifulSoup(html, "lxml")
        products: list[Product] = []

        for card in self._find_product_cards(soup):
            product = self._parse_card(card, page_url)
            if product:
                products.append(product)

        log.info("Parsed %d products from %s", len(products), page_url)
        return products

    def get_next_page_url(self, html: str, current_url: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")

        next_link = soup.select_one(
            'a[aria-label="Next"], '
            'a[rel="next"], '
            'li.next a'
        )

        if not next_link:
            for pagination in soup.select(".pagination"):
                for link in pagination.find_all("a"):
                    text = link.get_text(strip=True).lower()
                    if text in ("next", "›", "»", ">"):
                        next_link = link
                        break
                if next_link:
                    break

        if next_link and next_link.get("href"):
            return urljoin(current_url, next_link["href"])
        return None

    def _find_product_cards(self, soup: BeautifulSoup) -> list[Tag]:
        cards = soup.select(".card.product-card")
        if cards:
            return cards

        for selector in [
            "[class*='product-card']",
            "[class*='product-item']",
            ".product",
        ]:
            cards = soup.select(selector)
            if cards:
                return cards

        return []

    _SOLD_OUT_CLASSES = frozenset({"sold-out", "soldout", "out-of-stock", "outofstock"})
    _SOLD_OUT_BADGE_RE = re.compile(r"^\s*(?:sold\s*out|out\s*of\s*stock|unavailable)\s*$", re.IGNORECASE)

    def _is_sold_out(self, card: Tag) -> bool:
        classes = {c.lower() for c in (card.get("class") or [])}
        if classes & self._SOLD_OUT_CLASSES:
            return True
        for badge in card.select("[class*='badge'], [class*='stock'], [class*='sold'], [class*='status']"):
            if self._SOLD_OUT_BADGE_RE.match(badge.get_text(" ", strip=True)):
                return True
        return False

    def _parse_card(self, card: Tag, page_url: str) -> Product | None:
        if self._is_sold_out(card):
            return None
        link_el = card.select_one("h3.product-title a[href], h3 a[href], h2 a[href]")
        if link_el:
            name = link_el.get_text(" ", strip=True)
        else:
            heading = card.select_one("a[href] h3, a[href] h2")
            if not heading:
                return None
            link_el = heading.find_parent("a", href=True)
            name = heading.get_text(" ", strip=True)
        if not link_el or not name:
            return None

        url = urljoin(page_url, link_el.get("href", ""))

        price_div = card.select_one(".product-price")
        if not price_div:
            return None

        current_price, original_price = self._extract_prices(price_div)
        if current_price is None:
            return None

        return Product(
            name=name,
            url=url,
            current_price=current_price,
            original_price=original_price,
            image_url=urljoin(page_url, img.get("data-src") or img.get("src") or "")
            if (img := card.select_one("img[data-src], img[src]")) else None,
        )

    def _extract_prices(self, price_el: Tag) -> tuple[float | None, float | None]:
        cleaned = BeautifulSoup(str(price_el), "lxml")
        for noise in cleaned.select(
            "script, style, template, [class*='upsell'], [class*='bundle'], "
            "[class*='addon'], [class*='installment'], [class*='affirm'], [class*='klarna']"
        ):
            noise.decompose()

        current_price: float | None = None
        original_price: float | None = None

        for accent in cleaned.select(".text-accent"):
            parsed = _parse_price(accent.get_text(" ", strip=True))
            if parsed is not None and parsed > 0:
                current_price = parsed
                break

        for del_el in cleaned.select("del, s, strike"):
            parsed_orig = _parse_price(del_el.get_text(" ", strip=True))
            if parsed_orig is not None and parsed_orig > 0:
                original_price = parsed_orig
                break

        if current_price is not None:
            if original_price is not None and original_price <= current_price:
                original_price = None
            return current_price, original_price

        all_prices = []
        for text_node in cleaned.find_all(string=re.compile(r"\$[\d,]+\.?\d*")):
            p = _parse_price(str(text_node))
            if p is not None and p > 0:
                is_struck = text_node.find_parent(["s", "strike", "del"]) is not None
                all_prices.append((p, is_struck))

        if not all_prices:
            return None, None

        struck = [p for p, s in all_prices if s]
        normal = [p for p, s in all_prices if not s]

        if struck and normal:
            sale, ref = min(normal), max(struck)
            return sale, (ref if ref > sale else None)
        if normal:
            return min(normal), None
        return min(struck), None
