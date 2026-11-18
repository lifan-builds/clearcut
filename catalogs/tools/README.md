# Bench Drop — workshop and power-tool deals

Workshop and power-tool storefront built on `common/deal_common/catalog/`. Scope covers power tools, combo kits, and stationary or heavy workshop equipment. The default retailer-reference floor is $200, ranked by percentage off.

## Run from the workspace root

```bash
pip install -e '.[dev]'
tool-deals refresh --database catalogs/tools/data/catalog.db
tool-deals serve --database catalogs/tools/data/catalog.db --port 8767
```

Open http://127.0.0.1:8767/. Scraping and serving are separate; serving reads snapshots read-only and hides offers older than 24 hours.

## Catalog ownership

`src/tool_deals/catalog.py` owns categories, classification rules, package formats, explicit battery-platform matching, brand aliases, and the 13 USD retailer sources. `src/tool_deals/static/` owns the charcoal/orange theme and drill artwork; shared ingestion, storage, filtering, ranking, pagination, and templates live in `common/deal_common/`.

- **Scope & exclusions:** Excludes manual hand tools, blades/bits/abrasives/consumables, jigs/templates/stands, standalone welding guns/feeders/torches, standalone batteries/chargers, food grinders, and used/refurbished/open-box/showroom stock.
- **Formats & platforms:** Labels bare tools, kits, bundles, and unspecified packages separately. Battery-platform facets (`Milwaukee M18`/`M12`, `DeWalt 20V MAX`/`FLEXVOLT`, `Makita LXT`/`XGT`) require both brand and explicit platform evidence rather than voltage alone.
- **Pricing:** Reference prices are retailer compare-at prices on the exact SKU/variant. Confirm package contents, voltage/phase requirements, shipping, and return terms with the retailer.
