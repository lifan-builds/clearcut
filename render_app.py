"""Multi-catalog host-routed ASGI entrypoint for Render / container hosting."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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

DATA_DIR = Path(os.environ.get("CLEARCUT_DATA_DIR", ROOT / "data"))
if "DATABASE_PATH" not in os.environ and (DATA_DIR / "deals.db").is_file():
    os.environ["DATABASE_PATH"] = str(DATA_DIR / "deals.db")
os.environ.setdefault("DEALS_DB_READ_ONLY", "1")
os.environ.setdefault("DISABLE_APP_LIFESPAN", "1")
os.environ.setdefault("TURSO_DIRECT_CONNECTION", "1")
os.environ.setdefault("AUTH_DB_PATH", "/tmp/auth_replica.db")

from aggregator.web.app import create_app as create_snow_app
from deal_common.catalog.web import create_app as create_catalog_app
from deal_common.portal import create_portal_app, default_portal_catalogs

_CATALOG_HOSTS = {
    "bigring": ("cycling_deals.catalog", "cycling.db", "CYCLING_SITE_URL", "https://bigring.clifan.com"),
    "benchdrop": ("tool_deals.catalog", "tools.db", "TOOLS_SITE_URL", "https://benchdrop.clifan.com"),
    "highline": ("camp_deals.catalog", "camp.db", "CAMP_SITE_URL", "https://highline.clifan.com"),
    "tideline": ("water_deals.catalog", "watersports.db", "WATER_SITE_URL", "https://tideline.clifan.com"),
}


def _request_host(scope: dict) -> str:
    forwarded = ""
    host = ""
    for raw_key, raw_val in scope.get("headers", ()):
        key = raw_key.lower()
        if key == b"x-forwarded-host":
            forwarded = raw_val.decode("latin-1").split(",", 1)[0].split(":", 1)[0].strip().lower()
        elif key == b"host":
            host = raw_val.decode("latin-1").split(":", 1)[0].strip().lower()
    return forwarded or host


def create_multi_host_app(data_dir: Path | None = None):
    base_dir = Path(data_dir) if data_dir is not None else DATA_DIR
    snow_app = create_snow_app(enable_lifespan=False)
    cache: dict[str, object] = {}

    def _get_portal():
        if "clearcut" not in cache:
            snow_db = Path(os.environ.get("DATABASE_PATH", base_dir / "deals.db"))
            cache["clearcut"] = create_portal_app(
                default_portal_catalogs(
                    snow_db=snow_db if snow_db.is_file() else None,
                    cycling_db=base_dir / "cycling.db" if (base_dir / "cycling.db").is_file() else None,
                    tools_db=base_dir / "tools.db" if (base_dir / "tools.db").is_file() else None,
                    camp_db=base_dir / "camp.db" if (base_dir / "camp.db").is_file() else None,
                    watersports_db=base_dir / "watersports.db" if (base_dir / "watersports.db").is_file() else None,
                ),
                public_url=os.environ.get("CLEARCUT_PUBLIC_URL", "https://clearcut.clifan.com"),
            )
        return cache["clearcut"]

    def _get_catalog(prefix: str):
        if prefix not in cache:
            module_name, db_file, env_url, default_url = _CATALOG_HOSTS[prefix]
            mod = __import__(module_name, fromlist=["CATALOG"])
            cache[prefix] = create_catalog_app(
                mod.CATALOG,
                base_dir / db_file,
                public_url=os.environ.get(env_url, default_url),
            )
        return cache[prefix]

    async def asgi(scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            host = _request_host(scope)
            if host == "clearcut" or host.startswith("clearcut."):
                await _get_portal()(scope, receive, send)
                return
            for prefix in _CATALOG_HOSTS:
                if host == prefix or host.startswith(f"{prefix}."):
                    await _get_catalog(prefix)(scope, receive, send)
                    return
        await snow_app(scope, receive, send)

    return asgi


app = create_multi_host_app()
