# Common deal components

`deal_common` contains shared models, HTTP traversal, retailer parsers, rate limiting, and the read-only catalog runtime used across `catalogs/`. It does not import any catalog package.

| Component | Responsibility |
| --- | --- |
| `models.Product` | Raw product fields and percentage discount calculation |
| `offers.VariantOffer` | Variant identity, price/reference, size/options, stock evidence, and destination |
| `config.RetailerConfig` | Store identity, listing URLs, parser type, currency, and Shopify feed settings |
| `parsers.base.BaseParser` | Listing extraction, pagination, and opt-in product/evidence resolution interface |
| `parsers.shopify.ShopifyParser` | Shopify JSON feeds, variant pricing, shipping/price notices, and pagination |
| `parsers.beaver`, `parsers.racycles` | Shopify extensions reconciling purchase-panel stock and warehouse evidence |
| `parsers.bikecloset` | WooCommerce parent/variation JSON and storefront validation |
| `parsers.taylor` | HTML listing discovery with exact-SKU product page resolution |
| `parsers.skiessentials`, `parsers.thecircle` | Storefront collection feeds and Lightspeed CAD variant verification |
| `parsers.common` | Price parsing and product-name/URL slug cleanup |
| `http.fetch_products` | Paced HTTP pagination, retries, currency checks, and same-host detail fetches |
| `rate_limit` | Sliding-window request throttling for web endpoints |
| `catalog.definition` | Catalog identity, sources, classification callback, defaults, and brand aliases |
| `catalog.storage` | Catalog-bound SQLite snapshots, atomic source updates, and 45-day daily history |
| `catalog.refresh`, `catalog.audit` | Source normalization, bounded snapshot refresh, and merchant endpoint verification |
| `catalog.query`, `catalog.health` | Freshness, best-variant selection, facets, discount-first ranking, and health hashes |
| `catalog.web`, `catalog.cli` | Server-rendered FastAPI/Jinja storefront, shared CSS/JS, and `refresh`/`serve` CLI |
| `equivalence` | `text-embedding-3-large` SQLite-cached product embeddings, structural precision guards, and cross-retailer canonical grouping |

Install from the repository root with `pip install -e '.[dev]'` and run `.venv/bin/pytest common/tests`.
