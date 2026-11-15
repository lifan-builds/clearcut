# Black Friday OpenAI Catalog Audit & Batch Enrichment Report (2026-11-14)

## Executive Summary

Ahead of Black Friday peak traffic, we audited Fresh Powder's freshest local multi-retailer snapshot (`.local/release-rollback/2026-11-02/deals-released-703257183.db` — **13,155 active latest-per-store listings** and **25,980 variant offers** across **30 active merchants**, opened strictly read-only via `?mode=ro`) using the new reusable audit CLI (`scripts/openai_catalog_credit_audit.py`) routed exclusively through the local `quotaswitch` gateway (`http://127.0.0.1:18455/v1` with `Authorization: Bearer quotaswitch-local`).

1. **Live High-Concurrency QA (`150` offers · `30/30` merchants · `16` workers · `gpt-6.1-sol`):**
   - Completed **150 / 150** structured JSON evaluations with **0 errors** in **34.72s** wall clock (median latency `3,217.3 ms`, p95 `5,322.3 ms`, `183,694` total tokens, `X-Quotaswitch-Account-Id: api-clfgpt3-exp1129`).
   - Flagged **25 `fail` (23 severity-3)** and **68 `warn` (58 severity-2, 12 severity-1)** offers versus **57 `pass`**, uncovering four concrete, high-impact catalog defects that directly affect Fresh Powder's percentage-first default ranking (`docs/METHODOLOGY.md`).
2. **24h 50%-Discount OpenAI Batch Audit (`800` offers · `30/30` merchants · `/v1/responses` · `gpt-6.1-sol`):**
   - Uploaded `file-PKp1s8qjw2NohZMghzAeT6` and submitted Batch ID **`batch_6af9216e8a5881909b30c384f22cd4eb`** under `X-Quotaswitch-Account: api-clfgpt3-exp1129` (`X-Quotaswitch-Provider: openai-api-key`).
   - Completed **800 / 800** items with **zero validation or execution errors** (`status: completed`, `output_file_id: file-Y2fbq8CgnmpqbKuDB1e5VU`, `1,013,620` total tokens), flagging **33 `fail`**, **366 `warn`**, and **401 `pass`** offers across all 30 merchants.

---

## Snapshot & Stratified Audit Scope

| Metric | Value |
| --- | --- |
| Read-only SQLite snapshot | `.local/release-rollback/2026-11-02/deals-released-703257183.db` |
| Active latest-per-store offers | `13,155` across `30` active merchants |
| Offers with structured `deal_offers` variants | `8,055` (`61.2%`) — `4,783` non-Shopify listings lack variant rows |
| Offers with `price_history` observations | `13,155` (`100.0%`) |
| Live QA stratified slice | `150` offers (`30` merchants, `16` workers, `gpt-6.1-sol`) |
| Batch API 50%-discount slice | `800` offers (`30` merchants, `/v1/responses`, `gpt-6.1-sol`) |
| Runtime artifacts directory (gitignored) | `build/openai-catalog-audit/` |

### Live QA (`n = 150`) & Collected Batch (`n = 800`) Issue Breakdown

| Audit Axis | Live Flagged (`n=150`) | Batch Flagged (`n=800`) | Specific Issue Codes (`live` / `batch`) |
| --- | ---: | ---: | --- |
| `variant_stock_consistency` | `77` | `350` | `unverified_stock_or_missing_variants` (`58` / `271`), `variant_price_spread` (`13` / `74`), `missing_sizes_in_record` (`15` / `62`), `oos_headline_risk` (`5` / `5`) |
| `classification_accuracy` | `29` | `56` | `wrong_category` (`11` / `25`), `non_snow_item` (`9` / `14`), `youth_unmarked_in_adult` (`5` / `13`), `nordic_vs_alpine_mismatch` (`3` / `7`), `gender_mismatch` (`3` / `3`) |
| `pricing_integrity` | `18` | `32` | `inflated_reference_price` (`10` / `20`), `legacy_vintage_msrp` (`5` / `5`), `blem_or_demo_vs_new_msrp` (`3` / `7`) |
| `fulfillment_caveats` | `7` | `25` | `package_binding_ambiguity` (`4` / `12`), `blem_demo_used_condition` (`3` / `9`), `call_to_order_or_pickup_only` (`0` / `4`) |

---

## Concrete Anomalies Discovered

### 1. Inflated Reference Prices & Vintage MSRP Benchmarks Poisoning Top Default Sort (`pricing_integrity`)

Because Fresh Powder ranks descending `discount_pct` first (`docs/METHODOLOGY.md`), corrupted `compare_at_price` values and 25-year-old vintage MSRPs rise straight to page 1:

| Deal ID | Merchant | Listing | Stored Price / Reference | Claimed % Off | Audit Diagnosis |
| ---: | --- | --- | --- | ---: | --- |
| `728`, `729`, `730` | Colorado Discount Skis | `Ogasaka 00 - 01 Keos PG / Unity USX / Unity C-VX Skis (No Bindings / Flat)` | `$59.99 / $1,000.00 USD` | `94.0%` | **25-year-old (`2000–01`) legacy skis** benchmarked against `$1,000` MSRP occupy the #1–#3 slots in the entire catalog (`variant_offer_count = 0`). |
| `7108`, `7420` | Sports Basement | `Ski Strap - Black` / `Sports Basement Ski Strap` | `$4.00 / $51.95 USD` · `$4.00 / $45.95 USD` | `92.3%` · `91.3%` | Single ski strap (`$4–$10` fair retail) paired with box/multipack `compare_at_price` (`$45.95–$51.95`). |
| `27693` | Sports Basement | `Primer Mitt` (`686`) | `$64.95 / $440.00 USD` | `85.2%` | All 8 variants sell for `$64.95` (fair MSRP `$60–$80`), but one variant carries a corrupted `$440` reference while others use `$119` (`45.4%` off). |
| `6675` | Sports Basement | `Flexi Chute` (`icebreaker`) | `$30.00 / $199.95 USD` | `85.0%` | Single merino neck gaiter (`$35–$60` fair MSRP); 3 variants at `$30` carry conflicting references (`$60`, `$90`, `$199.95`). |
| `7144` | Sports Basement | `Diamond ALC Poles` (`Fischer`) | `$59.00 / $329.95 USD` | `82.1%` | Entry Nordic poles (`$70–$90` fair MSRP); 9 variants at `$59` carry conflicting references (`$80` to `$329.95`). |
| `26691` | Sports Basement | `Women's Capilene Midweight Crew` (`Patagonia`) | `$89.00 / $459.95 USD` | `80.7%` | `$89` is standard full retail for a single Capilene Midweight Crew; `$379.95–$459.95` reference manufactures an `80.7%` discount. |
| `4027` | The House | `Nordica One RTL Women's Ski Boots` | `$99.99 / $449.95 USD` | `77.8%` | Entry rental-fleet (`RTL`) boot benchmarked against `$449.95` performance retail MSRP with `sizes = NULL`. |
| `7213` | Sports Basement | `Faction BOA` (`Salomon`) | `$196.00 / $579.95 USD` | `66.2%` | 10 variants use `$259.95` MSRP (`24.6%` off), while size `26.5` has a corrupted `$579.95` MSRP (`66.2%` off). |
| `6969` | Sports Basement | `Nomad Skins` (`Jones`) | `$239.95 / $550.00 USD` | `56.4%` | Sizes A/B/F carry `$550` reference (`56.4%` off) while size G at the exact same `$239.95` price carries the realistic `$290` reference (`17.3%` off). |

> **Codebase root cause (`common/deal_common/parsers/shopify.py#L119-L122`):** `best = min(offers, key=lambda o: (-o.discount_pct, ...))` picks the single variant with the highest `compare_at_price` typo, and then filters `Product.sizes` to `(o.current_price, o.original_price) == (price, original_price)` — simultaneously inflating the headline discount **and** dropping valid stocked sizes (`S, M, XL` on `Primer Mitt`, `10` sizes on `Faction BOA`, `G` on `Nomad Skins`) whose `compare_at_price` wasn't corrupted.

---

### 2. Variant Price Spreads, OOS Dilution & Unverified Stock (`variant_stock_consistency`)

- **Wide color/size markdown spreads masked by single headline discount:**
  - `[1330]` **The Ski Monster** — `VONN Lindsey Ski Pants // Women's`: `$279 / $930 USD` (`70.0%` off) in sizes `36, 40`, whereas stocked `Black / 42` is `$558` (`40.0%` off, `$279` spread) and omitted from `deals.sizes`.
  - `[6567]` & `[6823]` **Sports Basement** — `Men's / Women's Legendary Insulated Pant`: `$80 / $205 USD` (`61.0%` off) in select colors, while standard `Black Short` / `Washed Navy` `L, XL, XXL` cost `$164` (`20.0%` off, `$84` spread).
  - `[6629]` **Sports Basement** — `Men's Sogn Bib Shell Pant`: `$149.60 / $375 USD` (`60.1%` off) in `Black (Alt)` vs `$224.80` (`40.1%` off) in `Iron Oxide`.
- **Single remaining size in raw feeds with heavy OOS dilution:**
  - `[42736]` **Freeride Boardshop** — `BATALEON Disaster Snowboard 2026` (`$296 / $423 USD`, `30.0%` off): `6 of 7` raw Shopify variants are out of stock; only `151cm` remains.
  - `[42781]` **Boardom** — `FULL TILT KICKER MENS SKI BOOTS` (`$279.99 / $399.99 CAD`, `30.0%` off): `5 of 6` raw variants are out of stock; only `25.5` remains.
- **Non-Shopify stock/size blind spot (`4,783` offers across 10 stores):**
  - Browser/HTML sources (`Peter Glenn`, `Backcountry`, `The House`, `Colorado Discount Skis`, `Steep & Cheap`, `The Circle Whistler`, `Ski Essentials`, `Sacred Ride`, `BlueZone Sports`, `Corbetts`) store `0` `deal_offers` rows (`availability = 'unknown'`).
  - `47` active offers embed explicit lengths/widths in `name` while `deals.sizes` is `NULL` (e.g., `[1231] Sacred Ride | Nitro Beast 157 Demo`, `[1234] YES 420 148 Snowboard Demo`, `[593] Colorado Discount Skis | Tyrolia 2025 Attack 14 MN Demo ... Width: 95,110mm`).

---

### 3. Non-Snow Leaks, Generic Boots, Youth & Discipline Misclassifications (`classification_accuracy`)

- **Non-snow merchandise bypassing `EXCLUDE_KEYWORDS` (`catalogs/fresh-powder/src/aggregator/config.py#L794`):**
  - **Backcountry** overland/roof-rack hardware (`category = NULL`, unexcluded): `[35574] Yakima FlushBar - 1 Bar` (`$135`), `[35644] Rhino-Rack Reconn Deck Tower - 1-Pair` (`$113.93`), `[39681] ROAM Adventure Co Rugged Case Rigid Mount` (`$55.50`).
  - **Peter Glenn** & **The Circle Whistler** non-snow items: `[42387] Roces 54mm Roller Skate Wheels 4-Pack` (classified as `accessories`), `[5137] Knit Ruffles Wrap with Real Fox Fur`, `[15640] Outerknown Chroma Blanket Shirt`, `[65076] Daggers Chino`, `[65081] Skate Loose Chinos`, `[2191] Seaside Eyelet Top`.
- **Unresolved `boots` & `layers` vs `jackets` rule collisions:**
  - All `5` remaining generic `boots` rows in the snapshot are **Peter Glenn** winter/après boots (`[41405] Moon Boot EVX Nylon Mule`, `[41500] Helly Hansen Garibaldi VL Boot`, `[41501] TNF Snowfuse Boot`, `[41505] HH Isolabella 2 Demi Boot`, `[41569] Kamik Sienna 3 Boot`) -> should map to `winter boots`.
  - `[27661]` **Sports Basement** — `Women's Terra Peak Hoodie` (`$74.67 / $240 USD`, `68.9%` off): ThermoBall insulated jacket classified as `layers` because `"hoodie"` matches `layers` when `"jacket"` is absent from title.
- **Unmarked Youth / Junior equipment in adult feeds:**
  - `[870]` & `[10131]` **Colorado Ski Shop** — `Rossignol Rallybird Soul Pro Skis w/Xpress 10 Binding` (`130cm, 150cm`) and `Rossignol Sender Soul Pro Open Skis` (`jr-skis` in URL slug, `160–180cm`): junior constructions without youth tags in title or `exclusion_reason`.
  - `[3897]` & `[4016]` **The House** — `Whitespace Freestyle Shaun White Pro ... 130cm` and `K2 Lil Kat Snowboard Bindings`: youth snowboard/bindings mixed into adult categories.
- **Gender & Nordic-vs-Alpine mismatches:**
  - `[419]` & `[420]` **BlueZone Sports** — `Volkl Women's M7 Mantra Flat Skis 2026` and `Volkl Women's Mantra 102 Flat Skis 2026`: men's/unisex Mantra models mislabeled `"Women's"` (women's equivalents are `Secret 96` / `Secret 102`).
  - `[6151]` **Aspen Ski and Board** — `Armada Trego 2L Gore-Tex Insulated Womens Ski Pants` points to `/products/armada-trego-2l-gore-tex-insulated-mens-pants`.
  - `[2094]` & `[2080]` **Skirack** — `Fischer BCX Transnordic 75 Boot` and `75mm Binding with Cable` (Nordic/telemark 75mm gear mixed into alpine `ski boots` / `bindings`).

---

### 4. Condition, Package & Purchase Caveats (`fulfillment_caveats`)

- **Demo & Factory Blemished hardgoods compared against New First-Quality MSRP:**
  - `[1231]` & `[1234]` **Sacred Ride** — `Nitro Beast 157 Demo` (`$399.95 / $794.95 CAD`, `49.7%` off) and `YES 420 148 Snowboard Demo` (`$349.95 / $679.95 CAD`, `48.5%` off): used/fleet demo snowboards compared directly against new retail MSRP without a structured condition badge.
  - `[8895]` **Evo** — `Season Nexus Skis - Blem 2025` (`$289.99 / $749.00 USD`, `61.3%` off, plus `14+` other `- Blem` boards/skis at Evo): factory cosmetic seconds compared against first-quality MSRP.
- **Flat (no bindings) vs System Binding ambiguity & Call-to-Order titles:**
  - `[802]` / `[888]` **Colorado Ski Shop** (`Volkl Mantra 88 / 88 W`) and `[1506]` / `[11275]` **Ski Pro AZ** (`K2 Disruption 82 Ti`, `Elan Wingman 86 TI`) carry `-flat-` only in the URL slug while omitting `(Flat / No Bindings)` from the displayed card title.
  - Supplemental raw feed inspection also caught `[3]` **Aspen Ski and Board** `Blizzard Black Pearl 88 Womens Skis 2027 | 614-848-6600` embedding a MAP phone-order restriction directly in the title alongside a `Full-Price` tag.

---

## Prioritized Remediation Recommendations

1. **Sanity-check variant `compare_at_price` outliers in `ShopifyParser` (`common/deal_common/parsers/shopify.py`):**
   - Flag or cap intra-product `compare_at_price` variance when identical-SKU/price variants report >1.5x divergent `compare_at_price` values (eliminating Sports Basement's `$440` Primer Mitt / `$579.95` Faction BOA / `$550` Nomad Skins corruption and restoring hidden stocked sizes).
2. **Demote or badge non-new conditions and 15+ year vintage models before percentage-first ranking:**
   - Extract structured `condition` (`new`, `blem`, `demo`, `used`) and require `model_year >= 2018` (or explicit vintage toggle) for default page-1 percentage-off ranking so `2000–01` Ogasaka skis (`94%` off `$1,000`) and Demo/Blem boards do not outrank genuine new hardgoods markdowns.
3. **Tighten `EXCLUDE_KEYWORDS` & category rules (`catalogs/fresh-powder/src/aggregator/config.py`):**
   - Exclude automotive rack/overland hardware (`flushbar`, `yakima`, `rhino-rack`, `roam adventure`, `roller skate`, `chino`, `blanket shirt`, `eyelet top`).
   - Add `moon boot`, `kamik`, `snowfuse`, `garibaldi vl`, `isolabella` to `winter boots` disambiguation, and match URL slugs (`-jr-`, `-junior-`, `lil-kat`) in `exclusion_reason()` for unmarked youth gear.

---

## Submitted OpenAI Batch Job & Collection Commands

| Field | Value |
| --- | --- |
| **Batch ID** | `batch_6af9216e8a5881909b30c384f22cd4eb` |
| **Status / Counts** | `completed` (`800 / 800` succeeded · `0` failed) |
| **Input File ID** | `file-PKp1s8qjw2NohZMghzAeT6` |
| **Output File ID** | `file-Y2fbq8CgnmpqbKuDB1e5VU` |
| **Quotaswitch Account Header** | `X-Quotaswitch-Account: api-clfgpt3-exp1129` (`openai-api-key`) |
| **Endpoint / Model / Window** | `/v1/responses` · `gpt-6.1-sol` · `24h` (50% Batch API discount) |
| **Offers / Merchants / Tokens** | `800` stratified offers across `30` merchants (`1,013,620` total tokens) |
| **Receipt Path** | `build/openai-catalog-audit/batches/batch_6af9216e8a5881909b30c384f22cd4eb.json` |
| **Collected Summary Path** | `build/openai-catalog-audit/batches/collected-batch_6af9216e8a5881909b30c384f22cd4eb.json` |

### Poll Status & Collect Full 800-Offer Batch Results

```bash
# Check current progress (reads X-Quotaswitch-Account from saved receipt automatically)
.venv/bin/python scripts/openai_catalog_credit_audit.py batch-status \
  --batch-id batch_6af9216e8a5881909b30c384f22cd4eb

# Wait (if needed), download output_file_id JSONL, and build collected summary JSON
.venv/bin/python scripts/openai_catalog_credit_audit.py batch-collect \
  --batch-id batch_6af9216e8a5881909b30c384f22cd4eb --wait
```
