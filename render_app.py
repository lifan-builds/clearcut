"""Multi-catalog host-routed ASGI entrypoint for Render / container hosting."""

from __future__ import annotations

import gc
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

from aggregator.web.app import create_app as create_snow_app
from deal_common.catalog.web import create_app as create_catalog_app
from deal_common.portal import create_portal_app, default_portal_catalogs

_CATALOG_HOSTS = {
    "bigring": ("cycling_deals.catalog", "cycling.db", "CYCLING_SITE_URL", "https://bigring.clifan.com"),
    "benchdrop": ("tool_deals.catalog", "tools.db", "TOOLS_SITE_URL", "https://benchdrop.clifan.com"),
    "highline": ("camp_deals.catalog", "camp.db", "CAMP_SITE_URL", "https://highline.clifan.com"),
    "tideline": ("water_deals.catalog", "watersports.db", "WATER_SITE_URL", "https://tideline.clifan.com"),
}


def _request_hosts(scope: dict) -> tuple[str, str]:
    forwarded = ""
    host = ""
    for raw_key, raw_val in scope.get("headers", ()):
        key = raw_key.lower()
        if key == b"x-forwarded-host":
            forwarded = raw_val.decode("latin-1").split(",", 1)[0].split(":", 1)[0].strip().lower()
        elif key == b"host":
            host = raw_val.decode("latin-1").split(":", 1)[0].strip().lower()
    return forwarded, host


def _scope_with_host(scope: dict, host: str) -> dict:
    headers = [(k, v) for k, v in scope.get("headers", ()) if k.lower() != b"host"]
    headers.append((b"host", host.encode("latin-1")))
    return {**scope, "headers": headers}


def create_multi_host_app(data_dir: Path | None = None):
    base_dir = Path(data_dir) if data_dir is not None else DATA_DIR
    snow_app = create_snow_app(enable_lifespan=False)
    portal_app = None
    active_catalog: tuple[str, object] | None = None

    def _get_portal():
        nonlocal portal_app
        if portal_app is None:
            snow_db = Path(os.environ.get("DATABASE_PATH", base_dir / "deals.db"))
            catalogs_by_slug = {
                slug: __import__(mod_name, fromlist=["CATALOG"]).CATALOG
                for slug, (mod_name, _, _, _) in (
                    ("cycling", _CATALOG_HOSTS["bigring"]),
                    ("tools", _CATALOG_HOSTS["benchdrop"]),
                    ("camp", _CATALOG_HOSTS["highline"]),
                    ("watersports", _CATALOG_HOSTS["tideline"]),
                )
            }
            portal_app = create_portal_app(
                default_portal_catalogs(
                    snow_db=snow_db if snow_db.is_file() else None,
                    cycling_db=base_dir / "cycling.db" if (base_dir / "cycling.db").is_file() else None,
                    tools_db=base_dir / "tools.db" if (base_dir / "tools.db").is_file() else None,
                    camp_db=base_dir / "camp.db" if (base_dir / "camp.db").is_file() else None,
                    watersports_db=base_dir / "watersports.db" if (base_dir / "watersports.db").is_file() else None,
                    catalogs_by_slug=catalogs_by_slug,
                ),
                public_url=os.environ.get("CLEARCUT_PUBLIC_URL", "https://clearcut.clifan.com"),
            )
        return portal_app

    def _get_catalog(prefix: str):
        # Keep only one standalone catalog parsed in memory at a time so the
        # consolidated container stays comfortably under Render Free's 512 MB RAM cap;
        # Cloudflare Edge caches all 6 storefronts for 24h via CDN-Cache-Control.
        nonlocal active_catalog
        if active_catalog is not None and active_catalog[0] == prefix:
            return active_catalog[1]
        active_catalog = None
        gc.collect()
        module_name, db_file, env_url, default_url = _CATALOG_HOSTS[prefix]
        mod = __import__(module_name, fromlist=["CATALOG"])
        built = create_catalog_app(
            mod.CATALOG,
            base_dir / db_file,
            public_url=os.environ.get(env_url, default_url),
        )
        active_catalog = (prefix, built)
        return built

    async def asgi(scope, receive, send):
        nonlocal active_catalog
        if scope["type"] in {"http", "websocket"}:
            forwarded, raw_host = _request_hosts(scope)
            host = forwarded or raw_host
            routed_scope = _scope_with_host(scope, forwarded) if forwarded and forwarded != raw_host else scope
            if host == "clearcut" or host.startswith("clearcut."):
                await _get_portal()(routed_scope, receive, send)
                return
            for prefix in _CATALOG_HOSTS:
                if host == prefix or host.startswith(f"{prefix}."):
                    await _get_catalog(prefix)(routed_scope, receive, send)
                    return
            # Evict standalone catalog JSON cache before serving FreshPowder SQLite queries.
            if active_catalog is not None:
                active_catalog = None
                gc.collect()
            if host == "freshpowder" or host.startswith("freshpowder."):
                await snow_app(routed_scope, receive, send)
                return
        await snow_app(scope, receive, send)

    return asgi


app = create_multi_host_app()
