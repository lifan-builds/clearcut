# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Enthusiast and specialist gear shoppers hunting genuine deep clearance on high-ticket outdoor, cycling, and workshop equipment—skiers and snowboarders looking for skis, boards, and boots in their exact length or mondo size; road, gravel, and mountain cyclists shopping $1,000+ complete bikes and framesets in their frame size; and woodworkers, welders, and tradespeople shopping $200+ power tools, combo kits, and stationary shop machinery.

## Product Purpose

Clearcut is the multi-catalog specialist deal engine and front door powering three vertical equipment indexes: **FreshPowder** (ski & snowboard), **Big Ring** (complete bikes & framesets), and **Bench Drop** (power tools & workshop machines). Success means visitors immediately feel the craft of each gear world on the front door, smoothly choose their vertical through an attractive three-card showcase, and browse genuinely deep markdowns without wading through fake MSRPs, out-of-stock bait sizes, or low-ticket accessory clutter.

## Positioning

Unlike generic coupon sites or retailer sale grids that rank by commission or advertise "60% off" when only an XXS or a $15 spare part is discounted, Clearcut selects a matching variant before filtering, counting, or ranking—binding sale price, compare-at reference, size/package, stock status, and purchase URL to the same variant, ranked strictly by unrounded percentage off (`100 * (reference - sale) / reference`).

## Operating Context

Shoppers move from the Clearcut front door (`clearcut.clifan.com` / `clifan.com/clearcut/`)—where search is intentionally omitted in favor of three large, tactile, horizontally scrollable destination cards—into their chosen vertical storefront (`freshpowder.clifan.com`, `bigring.clifan.com`, `benchdrop.clifan.com`), all sharing one cohesive visual architecture adapted to the atmosphere of snow, cycling, or workshop tools.

## Capabilities and Constraints

- Read-only FastAPI + Jinja2/HTMX web serving backed by validated SQLite snapshots (`deals.db.gz` and standalone `package_catalog.py` bundles); zero runtime scraping or database mutation.
- Data refreshes are strictly manual (`workflow_dispatch` only; never add automatic cron schedules), with a 24-hour freshness ceiling after which stale sources leave browsing.
- Strict CSP (`default-src 'self'; img-src 'self' https:; style-src 'self'; script-src 'self'`) on shared portal and catalog runtimes—all fonts, stylesheets, scripts, and bespoke visual plates must be self-hosted.
- Retailer `compare_at` / MSRP values must be disclosed honestly as retailer references rather than verified historical street prices.

## Brand Commitments

- Umbrella identity: **Clearcut** powering **FreshPowder** (Snow), **Big Ring** (Cycling), and **Bench Drop** (Tools).
- Cohesive multi-site design language across all four surfaces, shifting material atmosphere per vertical (alpine snow for FreshPowder, road/gravel cycling for Big Ring, precision workshop tools for Bench Drop) with bespoke repository-owned visual assets and smooth, restrained motion.

## Evidence on Hand

- 62 configured specialist retailer feeds across Snow (28 shops, USD & CAD), Cycling (21 shops, $1,000+ floor), and Tools (13 dealers, $200+ floor).
- Verified offline test suite (`scripts/check.py`, 1,365+ tests) enforcing exact decimal discount ranking, variant binding, size-alias normalization, and junk/add-on exclusion.
- Public store-health and source-coverage reports (`/status` on FreshPowder, `/sources` on Big Ring & Bench Drop) and observed variant price history.

## Product Principles

1. **Discount truth before persuasion:** Unrounded percentage off leads every ranking; commissions, reviews, and imagery never reorder deals.
2. **One variant, one truth:** Price, reference, size, stock status, and outbound link always belong to the exact same purchasable variant.
3. **Choose the world first, filter inside:** The Clearcut front door lets visitors choose between Snow, Cycling, and Tools through three bold, scrollable visual cards without premature form clutter.
4. **One architecture, three atmospheres:** Every storefront shares the same structural rhythm and card craft while tuning palette and bespoke imagery to snow, cycling, or workshop tools.
