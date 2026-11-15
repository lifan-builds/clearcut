# FreshPowder engineering contracts

FreshPowder lives under `catalogs/fresh-powder/` within the consolidated Deal Aggregator workspace (`common/deal_common/` owns shared category-independent code, alongside sibling catalogs under `catalogs/`). FreshPowder has three surfaces: the primary `catalogs/fresh-powder/src/aggregator/` FastAPI application, the secondary `catalogs/fresh-powder/src/snow_deals/` CLI, and the `catalogs/fresh-powder/tampermonkey/` userscript.

Read these specifications before changing the corresponding area:

- [Product architecture and privacy](product-architecture-and-privacy.md)
- [Aggregator data and parser contracts](aggregator-data-and-parsers.md)
- [Web, auth, and deployment contracts](web-auth-and-deployment.md)
- [Verification](verification.md)

`catalogs/fresh-powder/PLAN.md` and `catalogs/fresh-powder/history/aggregator/PLANS.md` are retained historical product planning records (distinct from active source code under `catalogs/fresh-powder/src/` and workspace tracking in root `PLANS.md`), not Trellis runtime files.
