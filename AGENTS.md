## Project guidance

- Deal Aggregator workspace: `common/deal_common/` owns category-independent models, parsers, HTTP traversal, and storefront runtime (must never import a catalog); `catalogs/fresh-powder/` (`src/aggregator/`) owns Fresh Powder, `catalogs/cycling/` owns Big Ring, and `catalogs/tools/` owns Bench Drop.
- Rank strictly by percentage off first; reviews, images, or commissions must never outrank discount depth (`docs/METHODOLOGY.md`).
- Select a matching variant before filtering, sorting, counting, and pagination so price, reference, size, stock status, and purchase URL agree; unknown stock cannot satisfy an exact-size filter.
- Install once from root with `pip install -e '.[dev]'`; run offline checks via `.venv/bin/python scripts/check.py -q` (see `docs/project-guidance/`).
- Never read, stage, mutate, or publish ignored credentials, browser state, deployment state, or database files during validation.
