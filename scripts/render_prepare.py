"""Download and compact all Clearcut catalog snapshots for container deployment."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
for rel in (
    "common",
    "catalogs/fresh-powder/src",
    "catalogs/cycling/src",
    "catalogs/tools/src",
    "catalogs/camp/src",
    "catalogs/watersports/src",
):
    path_text = str(ROOT / rel)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

BASE_RELEASE_URL = "https://github.com/lifan-builds/clearcut-data/releases/download/latest-data"
SNAPSHOT_FILES = ("deals.db", "cycling.db", "tools.db", "camp.db", "watersports.db")
SHARED_CATALOG_MODULES = {
    "cycling.db": "cycling_deals.catalog",
    "tools.db": "tool_deals.catalog",
    "camp.db": "camp_deals.catalog",
    "watersports.db": "water_deals.catalog",
}


def _download(url: str, dest: Path, token: str = "") -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "ClearcutRenderBuild/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urlopen(req, timeout=180) as resp, tmp_path.open("wb") as out:
            shutil.copyfileobj(resp, out)
        tmp_path.replace(dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _prepare_deals_db(path: Path) -> None:
    from aggregator.db import init_db, repair_deal_categories
    from aggregator.reviews import compute_and_store_deal_reviews

    asyncio.run(init_db(path))
    asyncio.run(repair_deal_categories(path))
    asyncio.run(compute_and_store_deal_reviews(path))
    with sqlite3.connect(path) as conn:
        # Keep only current-scrape deals & their offers/history in the read-only web container
        # so queries stay fast and low-memory on 512 MB Free instances.
        conn.execute(
            """
            DELETE FROM deals
            WHERE (store, scraped_at) NOT IN (
                SELECT store, MAX(scraped_at) FROM deals GROUP BY store
            )
            """
        )
        conn.execute("DELETE FROM deal_offers WHERE product_url NOT IN (SELECT url FROM deals)")
        conn.execute("DELETE FROM offer_observations WHERE product_url NOT IN (SELECT url FROM deals)")
        conn.execute("DELETE FROM price_history WHERE url NOT IN (SELECT url FROM deals)")
        conn.execute("DELETE FROM deal_reviews WHERE deal_id NOT IN (SELECT id FROM deals)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_store_scraped ON deals(store, scraped_at)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_offer_obs_lookup "
            "ON offer_observations(product_url, variant_key, currency, product_name, observed_at DESC)"
        )
        conn.commit()
        conn.execute("VACUUM")
    print(f"[render_prepare] deals.db compacted to {path.stat().st_size:,} bytes", flush=True)


def _prepare_shared_catalog_db(path: Path, module_name: str) -> None:
    catalog = __import__(module_name, fromlist=["CATALOG"]).CATALOG
    reconcile = catalog.reconcile_product
    size_re = re.compile(catalog.size_value_pattern, re.IGNORECASE) if catalog.size_value_pattern else None
    if reconcile is None and size_re is None:
        return
    updated_rows = 0
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT name, products FROM sources").fetchall()
        for source_name, raw_products in rows:
            products = json.loads(raw_products)
            changed = False
            next_products = []
            for product in products:
                reconciled = reconcile(product) if reconcile else product
                if reconciled is None:
                    changed = True
                    continue
                if reconciled is not product:
                    changed = True
                if size_re is not None:
                    offers = []
                    for offer in reconciled.get("offers") or ():
                        sz = offer.get("size")
                        if sz and not size_re.fullmatch(str(sz).strip()):
                            offer = {**offer, "size": None}
                            changed = True
                        offers.append(offer)
                    if changed:
                        reconciled = {**reconciled, "offers": offers}
                next_products.append(reconciled)
            if changed:
                conn.execute(
                    "UPDATE sources SET products=? WHERE name=?",
                    (json.dumps(next_products, allow_nan=False), source_name),
                )
                updated_rows += 1
        conn.commit()
    print(f"[render_prepare] {path.name}: reconciled {updated_rows} sources ({path.stat().st_size:,} bytes)", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    token = os.environ.get("DATA_REPO_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    base_url = os.environ.get("CLEARCUT_RELEASE_URL", BASE_RELEASE_URL).rstrip("/")

    for filename in SNAPSHOT_FILES:
        dest = args.output / filename
        url = f"{base_url}/{filename}"
        print(f"[render_prepare] Downloading {url}", flush=True)
        _download(url, dest, token=token)
        if filename == "deals.db":
            _prepare_deals_db(dest)
        elif filename in SHARED_CATALOG_MODULES:
            _prepare_shared_catalog_db(dest, SHARED_CATALOG_MODULES[filename])
        else:
            print(f"[render_prepare] {filename}: {dest.stat().st_size:,} bytes", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
