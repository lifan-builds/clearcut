# Fresh Powder

Find deeply discounted ski and snowboard gear across specialist North American retailers, ranked by percentage off. Original price, sale price, and dollar savings make each opportunity visible; exact-size variant filtering and expert review links help shoppers decide whether to act.

![Fresh Powder](docs/images/freshpowder-live-deals.png)

## Package layout

- `src/aggregator/`: snow taxonomy, retailer registry, model/brand rules, exclusion rules, browser/store adapters, review matching, SQLite snapshot/query layer, auth, and FastAPI/Jinja2/htmx web UI.
- `tests/`: Python (`pytest`) and JavaScript (`node --test`) regression suites.
- `tampermonkey/`: standalone browser userscript (`snow-deals.user.js`).

## Local development

Run from the repository root:

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest catalogs/fresh-powder/tests
node --test catalogs/fresh-powder/tests/*.cjs
.venv/bin/ruff check catalogs/fresh-powder
PUBLIC_MODE=1 .venv/bin/uvicorn aggregator.web.app:create_app --factory --reload
```

## Data and deployment

Default local database paths are `catalogs/fresh-powder/data/deals.db` and `catalogs/fresh-powder/data/auth.db` (overridable via `DATABASE_PATH` and `AUTH_DB_PATH`). Auth uses Turso when `TURSO_URL` is configured and local SQLite otherwise. `PUBLIC_MODE=1` (default) serves the catalog publicly; invite-gated mode (`PUBLIC_MODE=0`) requires `SECRET_KEY`.

Production deploys on Vercel via root `vercel.json` -> `api/index.py` -> `app.py`. `scripts/vercel_build.py` copies static assets to `public/static` and bundles a compressed, SHA-256-verified read-only SQLite snapshot (`deals.db.gz` + `deals.db.manifest.json`) materialized at cold start by `aggregator.bundled_snapshot`.
