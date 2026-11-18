# Big Ring — cycling deals

Cycling storefront built on `common/deal_common/catalog/`, deployed at [bigring.clifan.com](https://bigring.clifan.com). Scope covers new complete mountain, road, gravel, electric, and other adult bikes, with framesets listed separately. The default retailer-reference floor is $1,000, ranked by percentage off.

## Run from the workspace root

```bash
pip install -e '.[dev]'
cycling-deals refresh --database catalogs/cycling/data/catalog.db
cycling-deals serve --database catalogs/cycling/data/catalog.db --port 8766
```

Open http://127.0.0.1:8766/. Scraping and serving are separate; serving reads snapshots read-only and hides offers older than 24 hours.

## Catalog ownership

`src/cycling_deals/catalog.py` owns categories, classification rules, frame-size matching, brand aliases, and the 21 USD retailer sources. `src/cycling_deals/static/` owns the green/lime theme and bicycle artwork; shared ingestion, storage, filtering, ranking, pagination, and templates live in `common/deal_common/`.

- **Scope & exclusions:** Excludes kids' bikes, apparel, components, accessories, used/demo/refurbished/open-box/blemished stock, and explicit pickup-only or no-ship listings.
- **Sizing:** Exact-fit filters require both a recognized frame-size option name and a plausible frame-size value so wheel dimensions and mislabeled color options remain in variant descriptions without polluting size facets.
- **Pricing:** Reference prices are retailer compare-at prices on the exact variant, not historical lows. Confirm geometry, components, shipping, assembly, and return terms with the retailer.
