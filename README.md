# Clearcut

Multi-catalog specialist deal engine powering FreshPowder, Big Ring, and Bench Drop. Find deep percentage discounts on high-ticket gear across specialist retailers, with the original reference price, current sale price, dollar savings, and available sizes shown together.

- **Clearcut Front Door:** [https://clearcut.clifan.com/](https://clearcut.clifan.com/) · [https://clifan.com/clearcut/](https://clifan.com/clearcut/)
- **FreshPowder Live Deals:** [https://freshpowder.clifan.com/](https://freshpowder.clifan.com/)
- **Overview & Walkthrough:** [https://clifan.com/freshpowder/](https://clifan.com/freshpowder/)
- **Big Ring (Cycling):** [https://bigring.clifan.com/](https://bigring.clifan.com/)
- **Bench Drop (Tools):** [https://benchdrop.clifan.com/](https://benchdrop.clifan.com/)

## Repository Layout

```text
common/
  deal_common/                # Shared models, parsers, HTTP pagination, rate limiting, and catalog runtime
  tests/                      # Offline unit tests for shared components and packaging scripts
catalogs/
  fresh-powder/
    src/aggregator/           # Fresh Powder scrapers, SQLite snapshot queries, review matching, FastAPI/HTMX web app
    tampermonkey/             # Fresh Powder browser userscript
    docs/images/              # Product screenshots
    tests/                    # Fresh Powder Python and Node.js tests
  cycling/                    # Big Ring: cycling retailer sources, taxonomy rules, branding, and CLI
  tools/                      # Bench Drop: workshop/tool retailer sources, taxonomy rules, branding, and CLI
scripts/                      # Offline test runner, snapshot packaging/restoration, source probing, and Vercel build
app.py, api/index.py          # Vercel FastAPI serverless entrypoint for Fresh Powder
vercel.json                   # Vercel build and routing configuration for freshpowder.clifan.com
docs/METHODOLOGY.md           # Ranking, variant-selection, and data-integrity rules
```

See [Methodology](docs/METHODOLOGY.md), [Common Components](common/README.md), [Fresh Powder](catalogs/fresh-powder/README.md), [Big Ring](catalogs/cycling/README.md), and [Bench Drop](catalogs/tools/README.md).

## Development

Requires Python 3.11+ and Node.js 22+. Install once from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run offline verification (uses isolated temporary databases and makes zero network requests):

```bash
.venv/bin/python scripts/check.py -q
.venv/bin/ruff check .
node --test catalogs/fresh-powder/tests/*.cjs
```

### Local Preview & CLI

Run a local Fresh Powder preview populated with synthetic multi-variant offers on `http://127.0.0.1:8765`:

```bash
.venv/bin/python scripts/preview_offers.py
```

Or start the FastAPI web app directly:

```bash
PUBLIC_MODE=1 .venv/bin/uvicorn aggregator.web.app:create_app --factory --reload
```

Installed CLI entrypoints:

- `clearcut-hub` — Clearcut multi-catalog front-door hub (`clearcut-hub serve --port 8760`) (`common/deal_common/portal`)
- `snow-deals-agg` — Fresh Powder scraper, review matcher, and local server (`catalogs/fresh-powder`)
- `cycling-deals` — Big Ring `refresh --database PATH` and `serve --database PATH` (`catalogs/cycling`)
- `tool-deals` — Bench Drop `refresh --database PATH` and `serve --database PATH` (`catalogs/tools`)

## Deployment & Data Refresh

- **Fresh Powder (`freshpowder.clifan.com`):** Deployed on Vercel via `vercel.json` -> `api/index.py` -> `app.py`. During build, `scripts/vercel_build.py` copies static assets into `public/static/` and bundles a validated compressed SQLite snapshot (`deals.db.gz` + manifest) for read-only serverless serving.
- **Big Ring & Bench Drop:** Packaged into standalone read-only Vercel bundles via `scripts/package_catalog.py`.
- **Refreshes:** Data refreshes run daily on staggered GitHub Actions schedules (`10:15 UTC` in `.github/workflows/scrape.yml` and `11:15 UTC` in `.github/workflows/catalog-refresh.yml`, with a 36-hour freshness gate) and can also be triggered on demand via `workflow_dispatch`.

## License

MIT — see [LICENSE](LICENSE).
