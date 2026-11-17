# Product architecture and privacy

## Boundaries

- `catalogs/fresh-powder/src/aggregator/` owns the Fresh Powder FastAPI/Jinja2/htmx application, snow-specific HTML parsers (including BlueZone), and scraper orchestration; `catalogs/fresh-powder/tampermonkey/` owns the browser userscript.
- `catalogs/cycling/src/cycling_deals/` (Big Ring) and `catalogs/tools/src/tool_deals/` (Bench Drop) own catalog-specific retailer lists and classification rules built on `deal_common.catalog`.
- `common/deal_common/` owns the shared `Product` model, retailer configuration, platform parsers (Shopify, SkiEssentials, The Circle, Beaver, Bike Closet, Bolton, Grizzly, Harvey, RA Cycles, Rockler, Taylor), HTTP traversal, rate limiter, and shared catalog storefront/audit runtime. Common code must never import a catalog.
- Deal snapshots use SQLite; auth, invite, session, event, and waitlist persistence uses Turso when configured and local SQLite only for development.
- Scheduled scraping runs separately from web serving. Vercel serves a bundled read-only deal database; Render remains a fallback.

## Privacy contract

Never commit or expose `.env`, cookies, Turso tokens, JWT/admin secrets, browser state, auth databases, scraped database files, deploy hooks, or private operational tokens. Do not open or mutate local database or sidecar files during validation.

Environment keys are configuration, never values: `DATABASE_PATH`, `DEALS_DB_READ_ONLY`, `AUTH_DB_PATH`, `TURSO_DIRECT_CONNECTION`, `DISABLE_APP_LIFESPAN`, `DEALS_DB_DOWNLOAD_URL`, `VERCEL_SKIP_DB_DOWNLOAD`, `PUBLIC_MODE`, `ADMIN_KEY`, `TURSO_URL`, `TURSO_AUTH_TOKEN`, and `SECRET_KEY`.

Source code is public; credentials and operational/local data must never be published.
