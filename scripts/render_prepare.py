"""Download and compact all Clearcut catalog snapshots for container deployment."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
for rel in ("common", "catalogs/fresh-powder/src"):
    path_text = str(ROOT / rel)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

BASE_RELEASE_URL = "https://github.com/lifan-builds/clearcut-data/releases/download/latest-data"
SNAPSHOT_FILES = ("deals.db", "cycling.db", "tools.db", "camp.db", "watersports.db")


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
        conn.execute("VACUUM")
    print(f"[render_prepare] deals.db compacted to {path.stat().st_size:,} bytes", flush=True)


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
        else:
            print(f"[render_prepare] {filename}: {dest.stat().st_size:,} bytes", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
