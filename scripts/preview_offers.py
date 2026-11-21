"""Serve synthetic offer examples on loopback, without existing data or credentials.

Run: .venv/bin/python scripts/preview_offers.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if "--serve" not in sys.argv:
        with tempfile.TemporaryDirectory(prefix="freshpowder-offer-preview-") as directory:
            env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
            env.update(
                PYTHONPATH=os.pathsep.join([str(ROOT / "common"), str(ROOT / "catalogs/fresh-powder/src")]),
                DATABASE_PATH=str(Path(directory) / "demo.db"),
                AUTH_DB_PATH=str(Path(directory) / "auth.db"),
                PUBLIC_MODE="1",
                DISABLE_APP_LIFESPAN="1",
                SITE_URL="http://127.0.0.1:8765",
            )
            return subprocess.call([sys.executable, __file__, "--serve"], env=env)

    import asyncio
    from datetime import UTC, datetime

    import uvicorn
    from aggregator.auth_db import init_auth_db
    from aggregator.db import init_db, upsert_deals
    from aggregator.models import AggregatedDeal
    from deal_common.offers import VariantOffer

    now = datetime.now(UTC).replace(tzinfo=None)
    deals = []
    for i, (name, category, size, price, original, image_url) in enumerate([
        ("Atomic Maverick 88 Ti Skis 2025", "skis", "180", 320, 1000, "https://cdn.shopify.com/s/files/1/0965/4642/files/Maverick78.jpg?v=1790372915"),
        ("XO V21 All-Mountain Skis 2024", "skis", "175", 350, 900, "https://cdn.shopify.com/s/files/1/0670/5135/6456/files/2024-xo-h21-ski-skxo0187h21-skxo0187h21.jpg?v=1769353690"),
        ("Full Tilt Kicker Ski Boots 2025", "ski boots", "27.5", 240, 800, "https://cdn.shopify.com/s/files/1/0263/6863/9023/products/J211601601_-FULL-TILT-KICKER-MENS-SKI-BOOTS-FULL-TILT-1639097949.jpg?v=1639097951"),
        ("Freeride Camber Snowboard 2025", "snowboards", "156", 280, 700, "https://cdn.shopify.com/s/files/1/0580/7410/3867/files/blk_8bc0054a-9169-4f0b-9748-a5acbeb5ef30.jpg?v=1763401832"),
        ("Black Diamond Impulse 104 Skis 00 - 01", "skis", "190", 150, 600, "https://content.backcountry.com/images/items/large/BLD/BLDZADB/ONEBLKBLK.jpg"),
        ("Blizzard Anomaly 88 Skis 2024", "skis", "176", 300, 600, "https://content.backcountry.com/images/items/large/BLD/BLDZAAD/MIDBLU.jpg"),
    ]):
        url = f"https://example.com/preview-{i}"
        offers = [
            VariantOffer("one", f"{url}?variant=one", price, original, size, f"{size} / Forest", "in_stock", image_url=image_url),
            VariantOffer("two", f"{url}?variant=two", price + 100, original, "160", "160 / Ochre", "in_stock", image_url=image_url),
        ]
        deals.append(AggregatedDeal(
            None, "Preview Store", name, url, price, original, round(100 * (1 - price / original), 1),
            category, size, None, None, now, image_url=image_url, offers=offers,
        ))
    asyncio.run(init_db())
    asyncio.run(init_auth_db())
    asyncio.run(upsert_deals(deals))
    uvicorn.run("aggregator.web.app:create_app", factory=True, host="127.0.0.1", port=8765, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
