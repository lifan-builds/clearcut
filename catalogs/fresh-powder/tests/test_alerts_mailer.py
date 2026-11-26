import asyncio
import importlib.util
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from aggregator import auth_db
from aggregator.db import init_db, upsert_deals
from aggregator.models import AggregatedDeal
from aggregator.web.app import create_app
from aggregator.web.signup_routes import signup_limiter
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("send_deal_alerts", ROOT / "scripts/send_deal_alerts.py")
mailer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mailer)

DEAL_URL = "https://evo.test/qst-106"
MAIL_CONFIG = {
    "api_key": "re_test",
    "from_email": "deals@example.com",
    "secret": "test-unsubscribe-signing-secret-32-chars",
}


@pytest.fixture
def alert_db(tmp_path, monkeypatch):
    deals_db = tmp_path / "deals.db"
    auth_db_path = tmp_path / "auth.db"
    monkeypatch.setenv("AUTH_DB_PATH", str(auth_db_path))
    monkeypatch.setenv("DATABASE_PATH", str(deals_db))
    monkeypatch.setenv("SECRET_KEY", MAIL_CONFIG["secret"])
    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.setattr(auth_db, "TURSO_URL", "")
    monkeypatch.setattr(auth_db, "TURSO_AUTH_TOKEN", "")
    monkeypatch.setattr(auth_db, "_sync_enabled", False)
    signup_limiter.clear()

    now = datetime.now(UTC).replace(tzinfo=None)
    asyncio.run(init_db(deals_db))
    asyncio.run(upsert_deals([
        AggregatedDeal(None, "Evo", "Salomon QST 106 Skis 2025", DEAL_URL, 299.0, 850.0, 64.8, "skis", "181cm", 181, 181, now),
    ], deals_db))
    with closing(sqlite3.connect(auth_db_path, check_same_thread=False)) as conn:
        monkeypatch.setattr(auth_db, "_conn", conn)
        yield deals_db, conn


def test_digest_is_sent_once_and_unsubscribe_requires_confirmation(alert_db):
    deals_db, conn = alert_db
    snapshot = deals_db.read_bytes()
    sent_payloads = []

    def accept(request):
        sent_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "em_1"})

    transport = httpx.MockTransport(accept)
    with TestClient(create_app(enable_lifespan=False)) as client:
        assert client.post("/alerts/signup", data={"email": "rider@example.com"}).status_code == 200

        skipped = mailer.send_alerts(deals_db)
        assert skipped == {"status": "skipped_unconfigured", "candidates": 1, "sent": 0}
        sent = mailer.send_alerts(deals_db, **MAIL_CONFIG, transport=transport)
        assert sent == {"status": "sent", "candidates": 1, "subscribers": 1, "sent": 1, "failed": 0}
        assert "Salomon QST 106" in sent_payloads[0]["html"]
        dedup = mailer.send_alerts(deals_db, **MAIL_CONFIG, transport=transport)
        assert dedup == {"status": "no_new_rare_deals", "candidates": 0, "sent": 0}
        assert len(sent_payloads) == 1

        signed_link = urlsplit(sent_payloads[0]["text"].splitlines()[-1].removeprefix("Unsubscribe: "))
        assert signed_link.path == "/alerts/unsubscribe"
        signed_path = f"{signed_link.path}?{signed_link.query}"
        bad_params = {"email": "rider@example.com", "sig": "wrong"}
        assert client.get(signed_link.path, params=bad_params).status_code == 422
        assert client.post(signed_link.path, params=bad_params).status_code == 422
        confirm = client.get(signed_path)
        assert confirm.status_code == 200 and '<form method="post"' in confirm.text
        assert conn.execute("SELECT email FROM deal_alert_signups").fetchall() == [("rider@example.com",)]
        removed = client.post(signed_path)
        assert removed.status_code == 200 and "Unsubscribed" in removed.text

    assert conn.execute("SELECT COUNT(*) FROM deal_alert_signups").fetchone()[0] == 0
    assert deals_db.read_bytes() == snapshot


def test_partial_delivery_retries_only_unsent_subscription(alert_db, monkeypatch):
    deals_db, conn = alert_db
    with TestClient(create_app(enable_lifespan=False)) as client:
        for email in ("first@example.com", "second@example.com"):
            assert client.post("/alerts/signup", data={"email": email}).status_code == 200
    subscriptions = dict(conn.execute("SELECT email, removal_hash FROM deal_alert_signups").fetchall())
    requests = []
    delays = []
    monkeypatch.setattr(mailer, "sleep", delays.append)
    responses = iter([
        httpx.Response(200),
        httpx.Response(429, headers={"Retry-After": "3", "RateLimit-Reset": "9"}),
        httpx.Response(429, headers={"RateLimit-Reset": "4"}),
        httpx.Response(429),
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200),
    ])

    def reply(request):
        recipient = json.loads(request.content)["to"][0]
        requests.append((recipient, request.headers["Idempotency-Key"]))
        return next(responses)

    transport = httpx.MockTransport(reply)
    partial_delivery = mailer.send_alerts(deals_db, **MAIL_CONFIG, transport=transport)
    assert partial_delivery == {"status": "partial_failure", "candidates": 1, "subscribers": 2, "sent": 1, "failed": 1}
    assert conn.execute("SELECT removal_hash, deal_url FROM deal_alert_deliveries").fetchall() == [
        (subscriptions["first@example.com"], DEAL_URL),
    ]
    assert delays == [3, 4]
    assert [recipient for recipient, _ in requests] == ["first@example.com"] + ["second@example.com"] * 3

    caught_up = mailer.send_alerts(deals_db, **MAIL_CONFIG, transport=transport)
    assert caught_up == {"status": "sent", "candidates": 1, "subscribers": 2, "sent": 1, "failed": 0}
    assert delays == [3, 4, 2]
    assert len(requests) == 6 and requests[1:] == [requests[1]] * 5
    assert requests[0][1] != requests[1][1]
    assert set(conn.execute("SELECT removal_hash, deal_url FROM deal_alert_deliveries")) == {
        (removal_hash, DEAL_URL) for removal_hash in subscriptions.values()
    }


@pytest.mark.parametrize(("response", "attempts", "waits"), [
    (httpx.Response(401), 1, []),
    (httpx.Response(429, headers={"Retry-After": "120"}), 1, []),
    (httpx.Response(429), 3, [1, 2]),
])
def test_cli_returns_failure_when_every_attempt_fails(alert_db, monkeypatch, capsys, response, attempts, waits):
    deals_db, conn = alert_db
    with TestClient(create_app(enable_lifespan=False)) as client:
        assert client.post("/alerts/signup", data={"email": "rider@example.com"}).status_code == 200
    requests = []
    delays = []

    def reject(request):
        requests.append(request)
        return response

    monkeypatch.setattr(mailer, "sleep", delays.append)
    monkeypatch.setattr(mailer, "send_alerts", partial(mailer.send_alerts, transport=httpx.MockTransport(reject)))
    monkeypatch.setattr(sys, "argv", ["send_deal_alerts.py", "--deals-db", str(deals_db)])
    monkeypatch.setenv("RESEND_API_KEY", MAIL_CONFIG["api_key"])
    monkeypatch.setenv("ALERT_FROM_EMAIL", MAIL_CONFIG["from_email"])

    assert mailer.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "send_failed", "candidates": 1, "subscribers": 1, "sent": 0, "failed": 1,
    }
    assert len(requests) == attempts and delays == waits
    assert conn.execute("SELECT COUNT(*) FROM deal_alert_deliveries").fetchone()[0] == 0


def test_legacy_sent_urls_are_preserved(alert_db):
    deals_db, _ = alert_db
    with closing(sqlite3.connect(deals_db)) as conn, conn:
        conn.execute("CREATE TABLE sent_deal_alerts (url TEXT PRIMARY KEY, sent_at TEXT NOT NULL)")
        conn.execute("INSERT INTO sent_deal_alerts VALUES (?, ?)", (DEAL_URL, datetime.now(UTC).isoformat()))
    snapshot = deals_db.read_bytes()

    result = mailer.send_alerts(deals_db, **MAIL_CONFIG)
    assert result == {"status": "no_new_rare_deals", "candidates": 0, "sent": 0}
    assert deals_db.read_bytes() == snapshot
