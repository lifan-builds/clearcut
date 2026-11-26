import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

import pytest
from aggregator import auth_db
from aggregator.web.app import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def auth_store(tmp_path, monkeypatch):
    path = tmp_path / "auth.db"
    monkeypatch.setattr(auth_db, "_conn", None)
    monkeypatch.setattr(auth_db, "TURSO_URL", "")
    monkeypatch.setenv("AUTH_DB_PATH", str(path))
    return path


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_admin_and_invite_cookies(auth_store, monkeypatch, scheme):
    monkeypatch.setenv("PUBLIC_MODE", "0")
    monkeypatch.setenv("SECRET_KEY", "integration-test-signing-key-32-bytes")
    monkeypatch.setenv("ADMIN_KEY", "local-admin")
    asyncio.run(auth_db.init_auth_db())
    asyncio.run(auth_db.create_invite_codes(["FRESH"], max_uses=1))
    auth_db._conn.close()
    auth_db._conn = None

    app = create_app(enable_lifespan=False)
    with TestClient(app, base_url=f"{scheme}://localhost", follow_redirects=False) as client:
        response = client.get("/admin/codes", params={"admin_key": "local-admin"})
        assert response.status_code == 302
        assert "set-cookie" not in response.headers
        assert client.post("/admin/login", data={"admin_key": "wrong"}).status_code == 403

        login = client.post("/admin/login", data={"admin_key": "local-admin"})
        assert login.status_code == 303
        assert client.get(login.headers["location"]).status_code == 200
        client.cookies.clear()
        invite = client.post("/invite", data={"code": "fresh"})
        assert invite.status_code == 302
        for response in (login, invite):
            flags = response.headers["set-cookie"].lower().split("; ")
            assert "httponly" in flags and "samesite=lax" in flags
            assert ("secure" in flags) == (scheme == "https")


def test_invite_limit_preserves_existing_uses_and_is_atomic(auth_store, monkeypatch):
    with sqlite3.connect(auth_store) as conn:
        conn.executescript("""
            CREATE TABLE invite_codes (code TEXT PRIMARY KEY, created_at TEXT, max_uses INTEGER);
            CREATE TABLE sessions (token TEXT PRIMARY KEY, invite_code TEXT, created_at TEXT);
            INSERT INTO invite_codes VALUES ('LAST', 'yesterday', 2);
            INSERT INTO sessions VALUES ('first', 'LAST', 'yesterday');
        """)
    asyncio.run(auth_db.init_auth_db())
    connections = local()
    barrier = Barrier(2)

    def redeem():
        connections.conn = sqlite3.connect(auth_store)
        try:
            barrier.wait()
            return asyncio.run(auth_db.redeem_invite_code("LAST"))
        finally:
            connections.conn.close()

    with monkeypatch.context() as patch, ThreadPoolExecutor(2) as pool:
        patch.setattr(auth_db, "_get_conn", lambda: connections.conn)
        attempts = [pool.submit(redeem) for _ in range(2)]
        assert sorted(attempt.result() for attempt in attempts) == [False, True]

    asyncio.run(auth_db.init_auth_db())
    assert not asyncio.run(auth_db.redeem_invite_code("LAST"))
    assert not asyncio.run(auth_db.redeem_invite_code("UNKNOWN"))
    assert asyncio.run(auth_db.list_invite_codes())[0]["use_count"] == 2


def test_duplicate_waitlist_signup_closes_transaction(auth_store):
    asyncio.run(auth_db.init_auth_db())
    assert asyncio.run(auth_db.add_to_waitlist("rider@example.com"))
    assert not asyncio.run(auth_db.add_to_waitlist(" Rider@example.com "))
    assert not auth_db._get_conn().in_transaction
    with sqlite3.connect(auth_store, timeout=0) as conn:
        conn.execute("INSERT INTO waitlist VALUES ('other@example.com', 'today')")
