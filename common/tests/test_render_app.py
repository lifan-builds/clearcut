"""Host-routed multi-catalog Render ASGI app tests."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from deal_common.catalog.storage import save_source
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def test_render_multi_host_routes_all_six_hosts(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("render_app_test", ROOT / "render_app.py")
    render_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_app)

    now = datetime.now(UTC).isoformat()
    sample_product = {
        "name": "Test Item",
        "url": "https://example.com/item",
        "brand": "TestBrand",
        "category": "Mountain bikes",
        "format": "",
        "platform": "",
        "offers": [{
            "variant_key": "1",
            "url": "https://example.com/item?v=1",
            "current_price": 500,
            "original_price": 1500,
            "size": "M",
            "options": "M",
            "availability": "in_stock",
            "image_url": None,
            "reference_basis": "retailer_compare_at",
        }],
    }
    for slug, filename in [
        ("cycling", "cycling.db"),
        ("tools", "tools.db"),
        ("camp", "camp.db"),
        ("watersports", "watersports.db"),
    ]:
        save_source(tmp_path / filename, slug, "Example", now, [sample_product], "complete", {})

    monkeypatch.setenv("PUBLIC_MODE", "1")
    client = TestClient(render_app.create_multi_host_app(data_dir=tmp_path))

    assert "Clearcut" in client.get("/", headers={"host": "clearcut.clifan.com"}).text
    assert client.get("/healthz", headers={"host": "bigring.clifan.com"}).status_code == 200
    assert client.get("/healthz", headers={"host": "benchdrop.clifan.com"}).status_code == 200
    assert client.get("/healthz", headers={"host": "highline.clifan.com"}).status_code == 200
    assert client.get("/healthz", headers={"host": "tideline.clifan.com"}).status_code == 200
    assert "FreshPowder" in client.get("/robots.txt", headers={"host": "freshpowder.clifan.com"}).text or (
        client.get("/robots.txt", headers={"host": "freshpowder.clifan.com"}).status_code == 200
    )
