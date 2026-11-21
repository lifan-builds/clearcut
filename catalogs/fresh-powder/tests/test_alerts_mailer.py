import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import httpx
from aggregator.auth_db import alert_signup_db
from aggregator.db import init_db, upsert_deals
from aggregator.models import AggregatedDeal
from aggregator.web.app import create_app
from aggregator.web.signup_routes import unsubscribe_signature
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("send_deal_alerts", ROOT / "scripts/send_deal_alerts.py")
mailer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mailer)


def test_rare_deal_mailer_sends_once_and_one_click_unsubscribe_removes_email(tmp_path, monkeypatch):
    deals_db = tmp_path / "deals.db"
    auth_db = tmp_path / "auth.db"
    secret = "test-unsubscribe-signing-secret-32-chars"
    monkeypatch.setenv("AUTH_DB_PATH", str(auth_db))
    monkeypatch.setenv("DATABASE_PATH", str(deals_db))
    monkeypatch.setenv("SECRET_KEY", secret)
    monkeypatch.setenv("PUBLIC_MODE", "1")

    now = datetime.now(UTC).replace(tzinfo=None)
    asyncio.run(init_db(deals_db))
    asyncio.run(
        upsert_deals(
            [AggregatedDeal(None, "Evo", "Salomon QST 106 Skis 2025", "https://evo.test/qst-106", 299.0, 850.0, 64.8, "skis", "181cm", 181, 181, now)],
            deals_db,
        )
    )
    import aggregator.auth_db as auth_db_mod
    from aggregator.web.signup_routes import signup_limiter

    auth_db_mod._conn = None
    auth_db_mod.TURSO_URL = ""
    auth_db_mod.TURSO_AUTH_TOKEN = ""
    signup_limiter.clear()
    with TestClient(create_app()) as client:
        assert client.post("/alerts/signup", data={"email": "rider@example.com"}).status_code == 200
    auth_db_mod._conn = None

    skipped = mailer.send_alerts(deals_db, api_key="", from_email="", secret="")
    assert skipped["status"] == "skipped_unconfigured" and skipped["candidates"] == 1

    sent_payloads = []
    transport = httpx.MockTransport(lambda req: (sent_payloads.append(req.read().decode()), httpx.Response(200, json={"id": "em_1"}))[1])
    res = mailer.send_alerts(deals_db, api_key="re_test", from_email="deals@clifan.com", secret=secret, transport=transport)
    assert res == {"status": "sent", "candidates": 1, "subscribers": 1, "sent": 1}
    assert "Salomon QST 106" in sent_payloads[0]

    dedup = mailer.send_alerts(deals_db, api_key="re_test", from_email="deals@clifan.com", secret=secret, transport=transport)
    assert dedup["status"] == "no_new_rare_deals"
    auth_db_mod._conn = None

    sig = unsubscribe_signature("rider@example.com", secret)
    with TestClient(create_app()) as client:
        bad = client.get("/alerts/unsubscribe", params={"email": "rider@example.com", "sig": "wrong"})
        assert bad.status_code == 422
        ok = client.get("/alerts/unsubscribe", params={"email": "rider@example.com", "sig": sig})
        assert ok.status_code == 200 and "Unsubscribed" in ok.text
    auth_db_mod._conn = None
    assert alert_signup_db().execute("SELECT COUNT(*) FROM deal_alert_signups").fetchone()[0] == 0
    auth_db_mod._conn = None
