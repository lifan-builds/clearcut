# Deal Aggregator Methodology

## Core Promise

Surface genuine deep discounts on expensive gear across specialist retailers, ranked strictly by percentage off first so exceptional markdowns stand out immediately: **70% off · originally $2,000 · now $600 · save $1,400**.

Review scores, affiliate commissions, and image availability never outrank discount depth. Product fit, size availability, and source freshness keep surfaced deals actionable rather than aspirational.

## Principles

1. **Focus on high-ticket equipment with real clearance inventory.** Favor categories with substantial reference prices, identifiable models, specialist retailers, and meaningful size or fit filters. Keep low-ticket accessories and apparel out of default equipment views unless explicitly requested.
2. **Select a matching variant before filtering, counting, or ranking.** Sale price, reference price, size/options, stock status, and outbound purchase URL must come from the same variant. One discounted size must never imply every size shares that price, and unknown stock cannot satisfy an exact-size filter.
3. **Compute discounts from exact decimal prices.** Discount percentage is `100 * (reference_price - sale_price) / reference_price` and savings is `reference_price - sale_price` in the same currency. Apply minimum-discount thresholds, sorting, and counts before display rounding so a `39.98%` markdown never qualifies for a `40%` filter.
4. **Separate scraping from serving.** Daily refresh pipelines fetch retailer feeds, normalize offers, track daily price history, and publish SQLite snapshots; web storefronts serve those snapshots directly for fast filtering and browsing.
5. **Flag fresh price cuts from the selected variant's own history.** A deal is *dropped* when its sale price is below the same variant's previous daily check, taken at most 36 hours earlier (the freshness window), so a missed refresh never makes a stale comparison look new. A drop is a *new low* when it also undercuts every price kept for that variant over the last 45 days and the variant has been tracked for at least 7 days. The optional "Dropped in 24h" sort puts new lows first, then other drops, then everything else, ordering each group by percentage off; the default ranking never changes. Variant history keeps only price changes once it is 48 hours old, plus the last change before the window opened, so the minimum over kept rows is still the true window low.
6. **Compare models across shops.** When multiple shops carry the same model, group them side by side so shoppers can immediately see the lowest price in their size.

## Catalogs

- **[Fresh Powder](../catalogs/fresh-powder/README.md)** — Ski and snowboard equipment (`freshpowder.clifan.com`)
- **[Big Ring](../catalogs/cycling/README.md)** — Road, gravel, mountain, and e-bike equipment (`bigring.clifan.com`)
- **[Bench Drop](../catalogs/tools/README.md)** — Workshop, woodworking, metalworking, and power tools
