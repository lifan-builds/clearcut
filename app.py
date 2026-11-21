"""Vercel FastAPI entrypoint for FreshPowder and Clearcut (host-routed)."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (ROOT / "common", ROOT / "catalogs" / "fresh-powder" / "src"):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from aggregator.bundled_snapshot import configure_runtime_snapshot

configure_runtime_snapshot(ROOT)

from aggregator.web.app import create_app
from deal_common.portal import create_portal_app, default_portal_catalogs


def _is_portal_host(scope: dict) -> bool:
    if os.environ.get("CLEARCUT_SITE_MODE") == "hub":
        return True
    for raw_key, raw_val in scope.get("headers", ()):
        if raw_key == b"host":
            host = raw_val.decode("latin-1").split(":", 1)[0].strip().lower()
            return host == "clearcut" or host.startswith("clearcut.")
    return False


def _build_portal_app():
    snow_db_env = os.environ.get("DATABASE_PATH")
    snow_db = Path(snow_db_env) if snow_db_env else None
    public_url = os.environ.get("CLEARCUT_PUBLIC_URL", "https://clearcut.clifan.com")
    return create_portal_app(default_portal_catalogs(snow_db=snow_db), public_url=public_url)


app = create_app()
_snow_asgi = app.build_middleware_stack()
_portal_app = None


async def _dispatch_with_portal_host(scope, receive, send):
    global _portal_app
    if scope["type"] in {"http", "websocket"} and _is_portal_host(scope):
        if _portal_app is None:
            _portal_app = _build_portal_app()
        await _portal_app(scope, receive, send)
        return
    await _snow_asgi(scope, receive, send)


app.middleware_stack = _dispatch_with_portal_host
