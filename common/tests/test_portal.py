import importlib.util
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from deal_common.catalog.storage import save_source
from deal_common.portal import create_portal_app, default_portal_catalogs
from deal_common.portal.definition import (
    read_shared_catalog_snapshot,
    read_snow_snapshot,
)
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def test_portal_homepage_assets_and_validated_handoff_redirects(tmp_path):
    absent = tmp_path / "absent.db"
    catalogs = default_portal_catalogs(snow_db=absent, cycling_db=absent, tools_db=absent)
    client = TestClient(create_portal_app(catalogs, public_url="https://clearcut.clifan.com"))

    home = client.get("/")
    assert home.status_code == 200
    assert not absent.exists()
    assert "Choose your terrain." in home.text
    for world_name in ("FreshPowder", "Big Ring", "Bench Drop", "Highline", "Tideline"):
        assert world_name in home.text
    assert "default-src 'self'" in home.headers["Content-Security-Policy"]
    assert "X-Robots-Tag" not in home.headers

    cycling_tab = client.get("/?catalog=cycling")
    assert cycling_tab.status_code == 200
    assert "noindex" in cycling_tab.headers["X-Robots-Tag"]
    assert 'data-active-world="cycling"' in cycling_tab.text

    assert client.get("/portal-static/portal.css").status_code == 200
    assert client.get("/portal-static/portal.js").status_code == 200
    assert client.get("/static/fonts/Manrope.ttf").status_code == 200
    assert client.get("/healthz").json() == {"status": "ready", "service": "clearcut-portal", "catalogs": 5}
    assert "Sitemap: https://clearcut.clifan.com/sitemap.xml" in client.get("/robots.txt").text
    assert "https://clearcut.clifan.com/" in client.get("/sitemap.xml").text

    snow_go = client.get("/go", params={"catalog": "snow", "q": "QST 98", "size": "178", "min_discount": "50"}, follow_redirects=False)
    assert snow_go.status_code == 302
    assert snow_go.headers["location"] == "https://freshpowder.clifan.com/?q=QST+98&size=178&min_discount=50"

    bike_go = client.get("/go", params={"catalog": "cycling", "q": "Cervelo", "size": "54cm"}, follow_redirects=False)
    assert bike_go.status_code == 302
    assert bike_go.headers["location"] == "https://bigring.clifan.com/?q=Cervelo&size=54cm#deals"

    tool_go = client.get("/go", params={"catalog": "tools", "q": "track saw", "size": "Milwaukee M18"}, follow_redirects=False)
    assert tool_go.status_code == 302
    assert tool_go.headers["location"] == "https://benchdrop.clifan.com/?q=track+saw&platform=Milwaukee+M18#deals"

    assert client.get("/go", params={"catalog": "https://evil.example"}, follow_redirects=False).status_code == 422


def test_portal_reads_read_only_snow_and_catalog_snapshots(tmp_path):
    now = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)
    snow_db = tmp_path / "snow.db"
    with sqlite3.connect(snow_db) as conn:
        conn.execute(
            "CREATE TABLE deals (store TEXT, name TEXT, url TEXT, current_price REAL, "
            "original_price REAL, category TEXT, sizes TEXT, currency TEXT, scraped_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO deals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("evo", "Salomon QST 98 Skis 2025", "https://www.evo.com/skis/qst-98", 349.0, 700.0, "skis", "176, 183", "USD", "2026-11-19T16:00:00"),
                ("evo", "Penny Deposit Glitch", "https://www.evo.com/skis/glitch", 0.50, 700.0, "skis", "176", "USD", "2026-11-19T16:00:00"),
                ("evo", "Stale Skis", "https://www.evo.com/skis/stale", 200.0, 800.0, "skis", "180", "USD", "2026-11-10T00:00:00"),
            ],
        )
    snow_bytes = snow_db.read_bytes()
    snow_summary = read_snow_snapshot(snow_db, now=now)
    assert snow_summary is not None and snow_summary.live_deals == 1
    assert snow_summary.highlights[0].name == "Salomon QST 98 Skis 2025"
    assert snow_db.read_bytes() == snow_bytes

    cycling_db = tmp_path / "cycling.db"
    save_source(
        cycling_db,
        "cycling",
        "RA Cycles",
        now.isoformat(),
        [
            {
                "name": "Cervelo Caledonia-5 Ultegra Di2",
                "url": "https://racycles.com/products/caledonia-5",
                "brand": "Cervelo",
                "category": "Road bikes",
                "format": "",
                "platform": "",
                "offers": [
                    {
                        "variant_key": "54",
                        "url": "https://racycles.com/products/caledonia-5?variant=54",
                        "current_price": 4199,
                        "original_price": 7500,
                        "size": "54cm",
                        "options": "54cm / Five Black",
                        "availability": "in_stock",
                        "image_url": None,
                        "reference_basis": "retailer_compare_at",
                    }
                ],
            }
        ],
        "complete",
        {},
    )
    cycling_summary = read_shared_catalog_snapshot(cycling_db, "cycling", min_reference=1000, now=now)
    assert cycling_summary is not None and cycling_summary.live_deals == 1
    assert cycling_summary.highlights[0].name == "Cervelo Caledonia-5 Ultegra Di2"


def test_root_entrypoint_routes_clearcut_host_to_portal_and_preserves_freshpowder(monkeypatch):
    monkeypatch.setenv("DISABLE_APP_LIFESPAN", "1")
    monkeypatch.setenv("PUBLIC_MODE", "1")
    spec = importlib.util.spec_from_file_location("clearcut_root_app_test", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.app.title == "FreshPowder"

    with TestClient(module.app) as default_client:
        snow_robots = default_client.get("/robots.txt", headers={"host": "freshpowder.clifan.com"})
        portal_resp = default_client.get("/", headers={"host": "clearcut.clifan.com"})
        portal_robots = default_client.get("/robots.txt", headers={"host": "clearcut.clifan.com"})
        portal_go = default_client.get(
            "/go?catalog=tools&q=M18",
            headers={"host": "clearcut.clifan.com"},
            follow_redirects=False,
        )

    assert snow_robots.status_code == 200 and "freshpowder.clifan.com" in snow_robots.text
    assert portal_resp.status_code == 200 and "Choose your terrain." in portal_resp.text
    assert portal_robots.status_code == 200 and "clearcut.clifan.com" in portal_robots.text
    assert portal_go.status_code == 302
    assert portal_go.headers["location"] == "https://benchdrop.clifan.com/?q=M18#deals"
