"""Crawlable model/brand destinations and a plain-language methodology page."""
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BeforeValidator

from aggregator.comparisons import (
    BRANDS,
    INDEXABLE_BRANDS,
    PRODUCTS,
    catalog_summary,
    product_detail,
)
from aggregator.web.routes import templates
from aggregator.web.seo import site_url

comparison_router = APIRouter()
ReportedSize = Annotated[str | None, Query(pattern=r"^(?:1\d{2}|2[01]\d|220)W?$"),
                         BeforeValidator(lambda value: None if value == "" else value)]


@comparison_router.get('/products', response_class=HTMLResponse)
async def products(request: Request):
    items = await catalog_summary()
    all_brands = dict(BRANDS)
    for item in items:
        all_brands.setdefault(re.sub(r"[^a-z0-9]+", "-", item["brand"].lower()).strip("-"), item["brand"])
    return templates.TemplateResponse(request=request, name='products.html', context={
        'products': items, 'brands': dict(sorted(all_brands.items(), key=lambda kv: kv[1])),
        'page_title': 'Compare Ski & Snowboard Prices Across Shops | FreshPowder',
        'page_description': 'Compare ski, snowboard, and boot deals side by side across retailers, ranked by deepest discount.',
    })


@comparison_router.get('/brands/{slug}', response_class=HTMLResponse)
async def brand_page(request: Request, slug: str):
    items = await catalog_summary()
    all_brands = dict(BRANDS)
    for item in items:
        all_brands.setdefault(re.sub(r"[^a-z0-9]+", "-", item["brand"].lower()).strip("-"), item["brand"])
    brand = all_brands.get(slug)
    if not brand:
        raise HTTPException(404)
    return templates.TemplateResponse(request=request, name='products.html', context={
        'products': [p for p in items if p['brand'] == brand],
        'brand': brand, 'brands': dict(sorted(all_brands.items(), key=lambda kv: kv[1])),
        'noindex': slug not in INDEXABLE_BRANDS or bool(request.url.query),
        'page_title': f'{brand} Ski & Snowboard Deals Across Shops | FreshPowder',
        'page_description': f'Compare {brand} ski and snowboard deals across specialist retailers.',
    })


@comparison_router.get('/products/{slug}', response_class=HTMLResponse)
async def product_page(request: Request, slug: str,
                       currency: str = Query('USD', pattern='^(USD|CAD)$'),
                       size: ReportedSize = None):
    product = await product_detail(slug, currency, size)
    if not product:
        raise HTTPException(404)
    canonical = site_url() + '/products/' + slug
    schema = {'@context': 'https://schema.org', '@type': 'Product', 'name': product['name'],
              'url': canonical, 'brand': {'@type': 'Brand', 'name': product['brand']},
              'description': f"Compare {product['name']} retailer listing prices and reported lengths. Prices may vary by size; check the retailer for the final total."}
    if product['image']:
        schema['image'] = product['image']
    if product['offers']:
        prices = [o['current_price'] for o in product['offers']]
        schema['offers'] = {'@type': 'AggregateOffer', 'priceCurrency': currency,
                            'lowPrice': min(prices), 'highPrice': max(prices),
                            'offerCount': len(prices), 'url': canonical}
    brand_slug = next((s for s, b in BRANDS.items() if b == product['brand']),
                      re.sub(r"[^a-z0-9]+", "-", product['brand'].lower()).strip("-"))
    related = [p for p in PRODUCTS.values() if p['slug'] != slug and p['brand'] == product['brand']][:4]
    return templates.TemplateResponse(request=request, name='product.html', context={
        'product': product, 'related': related,
        'brand_slug': brand_slug,
        'page_title': f"{product['name']} — Shop Price Comparison | FreshPowder",
        'page_description': f"Compare {product['name']} prices and available sizes across shops.",
        'canonical_url': canonical,
        'structured_data': schema,
        'breadcrumb_data': {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Compare models', 'item': site_url()+'/products'},
            {'@type': 'ListItem', 'position': 2, 'name': product['brand'], 'item': site_url()+'/brands/'+brand_slug},
            {'@type': 'ListItem', 'position': 3, 'name': product['name'], 'item': canonical},
        ]},
    })


@comparison_router.get('/how-it-works', response_class=HTMLResponse)
async def methodology(request: Request):
    return templates.TemplateResponse(request=request, name='methodology.html', context={
        'page_title': 'How It Works | FreshPowder',
        'page_description': 'How FreshPowder scans 28+ snow shops daily, matches exact sizes, filters out spare-part clutter, and ranks the biggest markdowns first.',
    })
