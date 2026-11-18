"""Playwright-based browser scraping for JS-rendered and anti-bot sites."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re
from urllib.parse import urljoin, urlparse

from deal_common.models import Product
from deal_common.parsers.common import clean_product_name, product_slug_words
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright

from aggregator.reviews import _extract_brand

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

VIEWPORT = {"width": 1440, "height": 900}

_GENERIC_URL_PREFIXES = {
    "products",
    "product",
    "item",
    "shop",
    "mens",
    "womens",
    "men",
    "women",
}

_JS_PARSE_PRICE = """
const parsePrice = (el) => {
    if (!el) return null;
    const m = el.textContent.match(/[\\d,]+\\.\\d{2}/);
    return m ? parseFloat(m[0].replace(/,/g, '')) : null;
};
"""

STORE_CONFIGS: dict[str, tuple[str, str, str | None]] = {
    "backcountry": (
        ".chakra-linkbox .price",
        """() => {
            const results = [];
            const seen = new Set();
            document.querySelectorAll('.chakra-linkbox').forEach(card => {
                const link = card.querySelector('a.chakra-linkbox__overlay');
                if (!link || seen.has(link.href)) return;
                seen.add(link.href);
                const pe = card.querySelector('.price');
                if (!pe) return;
                const priceText = pe.textContent;
                const currentMatch = priceText.trim().match(/^(?:Current price:\\s*)?\\$([\\d,]+\\.?\\d*)/);
                const origMatch = priceText.match(/Original price:\\s*\\$([\\d,]+\\.?\\d*)/);
                const currentPrice = currentMatch ? parseFloat(currentMatch[1].replace(/,/g, '')) : null;
                const originalPrice = origMatch ? parseFloat(origMatch[1].replace(/,/g, '')) : null;
                if (!currentPrice) return;
                let name = '';
                const nameSelectors = [
                    '[data-id="title"]',
                    '[data-id="product-title"]',
                    '[data-testid="product-title"]',
                    '[class*="productTitle"]',
                    '[class*="product-title"]',
                    'h2',
                    'h3'
                ];
                for (const sel of nameSelectors) {
                    const el = card.querySelector(sel);
                    if (el && el.textContent.trim().length > 5) {
                        name = el.textContent.trim();
                        break;
                    }
                }
                if (!name && link.getAttribute('aria-label')) {
                    name = link.getAttribute('aria-label').trim();
                }
                card.querySelectorAll('img[alt]').forEach(img => {
                    const alt = (img.alt || '').trim();
                    if (!name && alt.length > 5 && !/gearhead top pick|top pick|product image/i.test(alt)) {
                        name = alt;
                    }
                });
                if (!name) return;
                const brand = card.querySelector('[data-id="brandName"]')?.textContent.trim() || '';
                if (brand && !name.toLowerCase().startsWith(brand.toLowerCase())) name = brand + ' ' + name;
                const imgEl = card.querySelector('img[data-id="image"]') ||
                    Array.from(card.querySelectorAll('img[alt]')).find(img => !/top pick|badge/i.test(img.alt));
                results.push({
                    name, url: link.href, current_price: currentPrice,
                    brand,
                    original_price: originalPrice !== currentPrice ? originalPrice : null,
                    image_url: imgEl ? (imgEl.getAttribute('data-src') || imgEl.src || null) : null,
                });
            });
            return results;
        }""",
        'a[rel="next"], a:has-text("Next Page"), .pagination .next a, [class*="next-page"]',
    ),
    "thehouse": (
        ".product-tile-card, [data-gtmdata]",
        """() => {
            const seen = new Set();
            const results = [];
            document.querySelectorAll('div.product[data-gtmdata]').forEach(t => {
                let data;
                try { data = JSON.parse(t.getAttribute('data-gtmdata')); } catch(e) { return; }
                if (!data || !data.name || !data.price || seen.has(data.name)) return;
                if (!['ski', 'snowboard'].includes(data.category)) return;
                if (data.dimension20 && data.dimension20 !== 'IN_STOCK') return;
                seen.add(data.name);
                const linkEl = t.querySelector('a[href*=".html"]');
                const originalPrice = parseFloat(data.price);
                let salePrice = data.dimension4 ? parseFloat(data.dimension4) : null;
                const currentPrice = salePrice || originalPrice;
                const imgEl = t.querySelector('img');
                results.push({
                    name: data.name, url: linkEl ? linkEl.href : '',
                    current_price: currentPrice,
                    brand: data.brand || null,
                    product_type: data.subcategory || null,
                    original_price: (originalPrice !== currentPrice) ? originalPrice : null,
                    image_url: imgEl ? (imgEl.getAttribute('data-src') || imgEl.src || null) : null,
                });
            });
            return results;
        }""",
        "button.show-more-observed-element[data-url]",
    ),
    "bigcommerce": (
        ".card-title",
        f"""() => {{
            {_JS_PARSE_PRICE}
            const seen = new Set();
            const results = [];
            document.querySelectorAll('.card, .product, .productGrid .product').forEach(card => {{
                const nameEl = card.querySelector('.card-title, h4, h3, h2, [class*="title"], [class*="name"]');
                const name = nameEl ? nameEl.textContent.trim() : '';
                if (!name || seen.has(name)) return;
                seen.add(name);
                const linkEl = card.querySelector('a.card-figure__link, a[href]');
                const currentEl = card.querySelector('.price--withoutTax');
                const origEl = card.querySelector('.price--non-sale, .price--rrp');
                const current = parsePrice(currentEl);
                if (!current) return;
                const imgEl = card.querySelector('img');
                results.push({{
                    name, url: linkEl ? linkEl.href : '',
                    current_price: current, original_price: parsePrice(origEl),
                    image_url: imgEl ? (imgEl.getAttribute('data-src') || imgEl.src || null) : null,
                }});
            }});
            return results;
        }}""",
        'a[rel="next"], .pagination-item--next a, .next a, .pagination .next a',
    ),
    "mec": (
        "script#__NEXT_DATA__",
        """() => {
            const el = document.getElementById('__NEXT_DATA__');
            if (!el) return [];
            try {
                const data = JSON.parse(el.textContent);
                const results = data.props.pageProps.serverState.initialResults.products_en.results;
                const products = [];
                for (const group of results) {
                    for (const hit of (group.hits || [])) {
                        const title = (hit.title || '').trim();
                        const urlPath = hit.url || '';
                        const salePrice = hit.salePrice;
                        if (!title || !urlPath || !salePrice || salePrice <= 0) continue;
                        const invStatus = hit.inventoryStatus;
                        if (invStatus && invStatus !== 'IN_STOCK' && invStatus !== 'LOW_STOCK') continue;
                        const price = hit.price;
                        products.push({
                            name: title,
                            url: urlPath.startsWith('http') ? urlPath : 'https://www.mec.ca' + urlPath,
                            current_price: salePrice,
                            original_price: (price && price > salePrice) ? price : null,
                            image_url: hit.image || hit.swatch || null,
                        });
                    }
                }
                return products;
            } catch(e) { return []; }
        }""",
        ".ais-Pagination-item--nextPage a",
    ),
}

STORE_CONFIGS["corbetts"] = STORE_CONFIGS["bigcommerce"]
STORE_CONFIGS["peterglenn"] = STORE_CONFIGS["bigcommerce"]

_ANTI_BOT_TYPES = {"backcountry", "corbetts", "mec", "thehouse"}


def prefix_brand_from_url(name: str | None, url: str) -> str:
    """Prefix a model-only name with the URL's leading brand slug when safe."""
    cleaned = clean_product_name(name, url)
    if not cleaned:
        return ""

    words = product_slug_words(url)
    while words and re.fullmatch(r"20\d{2}", words[0]):
        words = words[1:]
    if len(words) < 2:
        return cleaned

    slug_name = " ".join(words).lower()
    candidate = _extract_brand(slug_name)
    if not candidate or not slug_name.startswith(candidate + " "):
        return cleaned
    name_lower = cleaned.lower()
    if candidate in _GENERIC_URL_PREFIXES or name_lower.startswith(candidate + " "):
        return cleaned

    rest = {w.lower() for w in words[1:] if len(w) > 2}
    name_words = set(re.findall(r"[a-z0-9]+", name_lower))
    if not rest.intersection(name_words):
        return cleaned

    return f"{candidate.title()} {cleaned}"


def _parse_raw_products(raw: list[dict], base_url: str) -> list[Product]:
    """Convert raw JS-extracted dicts to Product instances, filtering invalid."""
    products: list[Product] = []
    for item in raw:
        url = (item.get("url") or "").strip()
        name = prefix_brand_from_url(item.get("name"), url)
        current_price = item.get("current_price")

        try:
            current_price = float(current_price)
            original_price = float(item["original_price"]) if item.get("original_price") is not None else None
        except (TypeError, ValueError):
            continue
        if not name or not url or not math.isfinite(current_price) or current_price <= 0:
            continue

        if url and not url.startswith("http"):
            url = urljoin(base_url, url)

        if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
            continue
        if original_price is not None and (not math.isfinite(original_price) or original_price <= 0):
            original_price = None

        image_url = (item.get("image_url") or "").strip() or None
        if image_url:
            image_url = urljoin(base_url, image_url)

        products.append(
            Product(
                name=name,
                url=url,
                current_price=round(current_price, 2),
                original_price=round(original_price, 2) if original_price else None,
                image_url=image_url,
                sizes=[str(size) for size in item["sizes"]] if isinstance(item.get("sizes"), list) else None,
                product_type=item.get("product_type") or None,
                brand=item.get("brand") or None,
            )
        )
    return products


async def _random_delay(base: float = 1.0, jitter: float = 2.0) -> None:
    await asyncio.sleep(base + random.random() * jitter)


async def _try_next_page(page: Page, next_selector: str | None) -> bool:
    """Attempt to navigate to the next page. Returns True if navigation happened."""
    if next_selector == "":
        return False
    for selector in (next_selector or "").split(","):
        selector = selector.strip()
        if not selector:
            continue
        try:
            el = await page.query_selector(selector)
            if not el:
                continue
            if await el.get_attribute("aria-disabled") == "true" or await el.get_attribute("disabled") is not None:
                continue
            href = await el.get_attribute("href")
            data_url = await el.get_attribute("data-url")
            if data_url:
                target = urljoin(page.url, data_url)
                if urlparse(target).netloc != urlparse(page.url).netloc:
                    raise ValueError("Pagination changed retailer")
                await page.goto(target, wait_until="domcontentloaded")
                return True
            if href and href.startswith("#"):
                await el.evaluate("el => el.click()")
                await asyncio.sleep(3.0)
                return True
            if await el.is_visible():
                if href:
                    abs_href = await el.evaluate("el => el.href")
                    if abs_href == page.url:
                        continue
                    await page.goto(abs_href or href, wait_until="domcontentloaded")
                    return True
                await el.click()
                await page.wait_for_load_state("domcontentloaded")
                return True
        except (PlaywrightError, RuntimeError, ValueError, TypeError, OSError) as exc:
            raise RuntimeError("Next-page navigation failed") from exc

    try:
        next_url = await page.evaluate("""() => {
            const current = new URL(window.location.href);
            const basePath = path => path.replace(/\\/page\\d+(?:\\.html)?\\/?$/, '').replace(/\\/$/, '');
            const sameCollection = target => target.origin === current.origin && basePath(target.pathname) === basePath(current.pathname);
            for (const a of document.querySelectorAll('a')) {
                const txt = a.textContent.trim().toLowerCase();
                if ((txt === 'next page' || txt === 'next' || txt === '›' || txt === '»')
                    && a.getAttribute('aria-disabled') !== 'true'
                    && a.href && a.href !== current.href && sameCollection(new URL(a.href))) {
                    return a.href;
                }
            }
            const currentUrl = window.location.href;
            const pageMatch = currentUrl.match(/[?&]page=(\\d+)|\\/page(\\d+)/);
            const currentPage = pageMatch ? parseInt(pageMatch[1] || pageMatch[2]) : 1;
            const nextPage = currentPage + 1;
            for (const a of document.querySelectorAll('a[href]')) {
                const href = a.href;
                const target = new URL(href);
                const pathPage = target.pathname.match(/\\/page(\\d+)(?:\\.html)?\\/?$/);
                if (sameCollection(target) &&
                    (Number(target.searchParams.get('page')) === nextPage ||
                     (pathPage && Number(pathPage[1]) === nextPage))) {
                    return href;
                }
            }
            return null;
        }""")
        if next_url:
            await page.goto(next_url, wait_until="domcontentloaded")
            return True
    except (PlaywrightError, RuntimeError, ValueError, TypeError, OSError) as exc:
        raise RuntimeError("Next-page navigation failed") from exc

    return False


async def scrape_with_browser(
    urls: list[str],
    store_name: str,
    store_type: str,
    *,
    max_pages: int = 25,
    delay: float = 2.0,
    outcome: dict | None = None,
) -> list[Product]:
    """Scrape product listings using a headless Chromium browser."""
    outcome = outcome if outcome is not None else {"errors": 0, "pages": 0}
    config = STORE_CONFIGS.get(store_type)
    if config is None:
        outcome["errors"] += 1
        log.warning("[%s] No browser config for store_type=%r, skipping", store_name, store_type)
        return []

    wait_selector, js_extract, next_selector = config
    all_products: list[Product] = []
    seen_urls: set[str] = set()

    is_anti_bot = store_type in _ANTI_BOT_TYPES
    nav_timeout = 45000 if is_anti_bot else 30000
    selector_timeout = 30000 if is_anti_bot else 15000

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = await browser.new_context(
                user_agent=USER_AGENT, viewport=VIEWPORT,
                java_script_enabled=True, locale="en-US", timezone_id="America/Denver",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = await context.new_page()

            for url in urls:
                collection = {"url": url, "pages": 0, "products": 0, "status": "complete"}
                outcome.setdefault("collections", []).append(collection)
                seen_pages: set[tuple[str, ...]] = set()
                log.info("[%s] Browser navigating to %s", store_name, url)
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
                    if response and response.status >= 400:
                        outcome["errors"] += 1
                        collection.update(status="http_error", http_status=response.status)
                        continue
                    if urlparse(page.url).netloc.removeprefix("www.") != urlparse(url).netloc.removeprefix("www."):
                        outcome["errors"] += 1
                        collection.update(status="unexpected_redirect", final_url=page.url)
                        continue
                except (PlaywrightError, RuntimeError, ValueError, OSError) as exc:
                    outcome["errors"] += 1
                    collection["status"] = "navigation_error"
                    log.error("[%s] Failed to load %s: %s", store_name, url, exc)
                    continue

                if is_anti_bot:
                    await asyncio.sleep(5.0)

                for page_num in range(1, max_pages + 1):
                    try:
                        wait_state = "attached" if wait_selector.startswith("script") else "visible"
                        await page.wait_for_selector(wait_selector, timeout=selector_timeout, state=wait_state)
                    except (PlaywrightError, OSError):
                        outcome["errors"] += 1
                        collection["status"] = "selector_missing"
                        log.warning("[%s] Timeout waiting for products (page %d)", store_name, page_num)
                        break

                    await asyncio.sleep(3.0)

                    try:
                        raw: list[dict] = await page.evaluate(js_extract)
                    except (PlaywrightError, RuntimeError, ValueError, TypeError) as exc:
                        outcome["errors"] += 1
                        collection["status"] = "extraction_error"
                        log.error("[%s] JS extraction error: %s", store_name, exc)
                        break

                    outcome["pages"] += 1
                    collection["pages"] += 1
                    products = _parse_raw_products(raw, page.url)
                    collection["products"] += len(products)
                    new_products = [p for p in products if p.url not in seen_urls]
                    seen_urls.update(p.url for p in new_products)
                    log.info("[%s] Page %d: %d products (%d new) from %s", store_name, page_num, len(products), len(new_products), page.url)
                    all_products.extend(new_products)

                    if not products:
                        outcome["errors"] += 1
                        collection["status"] = "empty_extraction"
                        break
                    fingerprint = tuple(sorted(p.url for p in products))
                    if fingerprint in seen_pages:
                        outcome["errors"] += 1
                        collection["status"] = "repeated_page"
                        break
                    seen_pages.add(fingerprint)

                    await _random_delay(delay)
                    try:
                        has_next = await _try_next_page(page, next_selector)
                    except RuntimeError:
                        outcome["errors"] += 1
                        collection["status"] = "pagination_error"
                        break
                    if not has_next:
                        break
                    if page_num >= max_pages:
                        outcome["errors"] += 1
                        collection["status"] = "page_limit"

                if url != urls[-1]:
                    await _random_delay(delay)

            await context.close()
            await browser.close()

    except (PlaywrightError, RuntimeError, ValueError, OSError) as exc:
        outcome["errors"] += 1
        log.error("[%s] Browser scraping failed: %s", store_name, exc)

    log.info("[%s] Browser scraping complete — %d total products", store_name, len(all_products))
    return all_products
