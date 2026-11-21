"""Practical ski and snowboard deal-hunting guides."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from aggregator.web.routes import templates
from aggregator.web.seo import site_url

GUIDES = {
    "check-a-real-ski-deal": {
        "title": "How to spot a genuinely great ski or snowboard deal",
        "description": "Quickly separate real 50%+ end-of-season markdowns from leftover odd sizes and inflated list prices.",
        "sections": [
            ("Filter by your exact length or mondo size first", [
                "A headline 60% discount only matters if your size is actually in stock at that price. Enter your ski length (cm), snowboard length, or boot mondo size first so every card reflects the variant you can ride.",
                "Keep flat skis separate from ski-with-binding system packages, and wide snowboards (W) separate from standard widths—they ride and price differently.",
            ]),
            ("Compare the same model across multiple shops", [
                "Independent mountain shops regularly undercut giant chains on past-season stock, especially on popular all-mountain widths (88–106 mm) and freeride boards.",
                "Even when one shop advertises 45% off and another shows 40% off, check the actual dollar price side by side—shops occasionally start from slightly different MSRPs.",
            ]),
            ("Time the seasonal markdown waves", [
                "Late summer through Black Friday clears remaining prior-season inventory at 40%–65% off, while mid-March through May brings the first big cuts on current-year gear.",
            ]),
        ],
    },
    "new-past-season-demo-gear": {
        "title": "Prior-season closeouts vs. current-year gear",
        "description": "When paying half price for last year’s graphic is a no-brainer—and when a model actually changed.",
        "sections": [
            ("Topsheet graphics change every year; cores rarely do", [
                "Most flagship skis and snowboards keep the exact same wood core, titanal layup, and rocker profile for three to four seasons while updating only the topsheet art.",
                "When a model carries over unchanged, grabbing last season’s closeout at 45%–60% off gets you identical on-snow performance.",
            ]),
            ("Spot real construction updates", [
                "Check whether a model added a new BOA shell, revised waist width, or updated tail rocker between seasons—FreshPowder keeps model years labeled so you always know which generation you’re looking at.",
            ]),
        ],
    },
    "listed-price-and-your-size": {
        "title": "Why ski and boot prices jump by size (and USD vs. CAD)",
        "description": "How variant-level pricing, wide snowboards, and Canadian shop deals work.",
        "sections": [
            ("Retailers discount slow-moving sizes deeper", [
                "On multi-variant listings, shops often mark down 154 cm or 192 cm deeper than core 172–180 cm lengths. FreshPowder reads variant-level prices so size filtering shows the real price for your length.",
            ]),
            ("Check Canadian shops alongside US shops", [
                "Canadian specialty shops (priced in CAD) often hold deep stock on freeride skis, touring boots, and snowboards long after US shops sell out of core sizes.",
            ]),
        ],
    },
}

guide_router = APIRouter()


@guide_router.get('/guides', response_class=HTMLResponse)
async def guide_index(request: Request):
    return templates.TemplateResponse(request=request, name='guides.html', context={
        'guides': GUIDES, 'page_title': 'Ski & Snowboard Deal Guides | FreshPowder',
        'page_description': 'Fast tips on finding real ski and snowboard closeouts in your size.',
    })


@guide_router.get('/guides/{slug}', response_class=HTMLResponse)
async def guide_page(request: Request, slug: str):
    guide = GUIDES.get(slug)
    if not guide:
        raise HTTPException(404)
    canonical = site_url() + '/guides/' + slug
    return templates.TemplateResponse(request=request, name='guide.html', context={
        'guide': guide, 'guides': GUIDES, 'slug': slug,
        'page_title': guide['title'] + ' | FreshPowder', 'page_description': guide['description'],
        'canonical_url': canonical,
        'breadcrumb_data': {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Buying guides', 'item': site_url()+'/guides'},
            {'@type': 'ListItem', 'position': 2, 'name': guide['title'], 'item': canonical},
        ]},
    })


@guide_router.get('/about', response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request=request, name='about.html', context={
        'page_title': 'About | FreshPowder',
        'page_description': 'How FreshPowder finds the biggest ski and snowboard markdowns across North American shops.',
    })
