"""Atomic, catalog-bound snapshots. Serving never creates or changes a database."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deal_common.catalog.definition import manufacturer_name
from deal_common.offers import HISTORY_DAYS


@contextmanager
def database(path: Path, catalog: str, *, writable: bool = False):
    if writable:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    else:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if writable:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (catalog TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (
                    name TEXT PRIMARY KEY, checked_at TEXT, successful_at TEXT,
                    status TEXT NOT NULL, detail TEXT NOT NULL,
                    products TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS observations (
                    source TEXT NOT NULL, observed_at TEXT NOT NULL,
                    products TEXT NOT NULL,
                    PRIMARY KEY (source, observed_at)
                );
            """)
        identity = connection.execute("SELECT catalog FROM metadata").fetchone()
        if identity is None and writable:
            connection.execute("INSERT INTO metadata VALUES (?)", (catalog,))
            connection.commit()
        elif identity is None or identity[0] != catalog:
            raise ValueError("Snapshot belongs to a different catalog")
        yield connection
    finally:
        connection.close()


def _offer_key(product: dict, offer: dict, brand_aliases) -> tuple:
    return (product["url"], product["name"], manufacturer_name(product["brand"], brand_aliases=brand_aliases),
            offer["variant_key"], offer["options"], offer["url"])


def _daily_offers(rows, brand_aliases, *, start: datetime, end: datetime) -> dict[tuple, dict[str, dict]]:
    """Latest observation per UTC day for every exact product and variant identity."""
    daily = {}
    for row in rows:
        stamp = datetime.fromisoformat(row["observed_at"])
        if stamp.tzinfo is None or not start <= stamp <= end:
            continue
        day = stamp.astimezone(UTC).date().isoformat()
        for historical in json.loads(row["products"]):
            for candidate in historical["offers"]:
                daily.setdefault(_offer_key(historical, candidate, brand_aliases), {})[day] = {
                    "observed_at": row["observed_at"],
                    "price": candidate["current_price"],
                    "reference": candidate["original_price"],
                    "availability": candidate["availability"],
                    "availability_note": candidate.get("availability_note", ""),
                    "price_note": candidate.get("price_note", ""),
                }
    return daily


def _with_history(connection, name: str, observed_at: str, products: list[dict], brand_aliases) -> list[dict]:
    """Attach prior-day evidence to each current offer so serving can rank recent drops without rescanning."""
    now = datetime.fromisoformat(observed_at)
    today = datetime.combine(now.astimezone(UTC).date(), datetime.min.time(), UTC)
    rows = connection.execute("SELECT observed_at, products FROM observations WHERE source=? ORDER BY observed_at", (name,))
    daily = _daily_offers(rows, brand_aliases, start=now - timedelta(days=HISTORY_DAYS),
                          end=today - timedelta(microseconds=1))

    def summary(days: dict[str, dict] | None) -> dict | None:
        if not days:
            return None
        ordered = [days[day] for day in sorted(days)]
        return {"previous_price": ordered[-1]["price"], "previous_at": ordered[-1]["observed_at"],
                "low": min(entry["price"] for entry in ordered), "tracked_since": ordered[0]["observed_at"]}

    return [{**product, "offers": [{**offer, "history": summary(daily.get(_offer_key(product, offer, brand_aliases)))}
                                   for offer in product["offers"]]} for product in products]


def save_source(path: Path, catalog: str, name: str, observed_at: str,
                products: list[dict], status: str, detail: dict, *,
                brand_aliases: tuple[tuple[str, str], ...] = ()):
    with database(path, catalog, writable=True) as connection, connection:
        connection.execute("""
            INSERT INTO sources (name, checked_at, status, detail) VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET checked_at=excluded.checked_at,
                status=excluded.status, detail=excluded.detail
        """, (name, observed_at, status, json.dumps(detail)))
        if status == "complete":
            products = _with_history(connection, name, observed_at, products, brand_aliases)
            payload = json.dumps(products, allow_nan=False)
            connection.execute("UPDATE sources SET products=?, successful_at=? WHERE name=?",
                               (payload, observed_at, name))
            connection.execute("INSERT OR REPLACE INTO observations VALUES (?, ?, ?)",
                               (name, observed_at, payload))
            connection.execute("""
                DELETE FROM observations WHERE source=? AND (
                    datetime(observed_at) < datetime(?, '-45 days') OR
                    (date(observed_at)=date(?) AND datetime(observed_at)<datetime(?)))
            """, (name, observed_at, observed_at, observed_at))


def read_sources(path: Path, catalog: str) -> list[dict]:
    if not path.exists():
        return []
    with database(path, catalog) as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM sources ORDER BY name")]
    for row in rows:
        row["products"] = json.loads(row["products"])
        row["detail"] = json.loads(row["detail"])
    return rows


def read_offer_history(path: Path, catalog: str, source: str, product: dict, offer: dict,
                       *, now: datetime, days: int = HISTORY_DAYS,
                       brand_aliases: tuple[tuple[str, str], ...] = ()) -> list[dict]:
    """One successful observation per UTC day, for the exact current identity only."""
    cutoff = now - timedelta(days=days)
    with database(path, catalog) as connection:
        rows = connection.execute("""
            SELECT observed_at, products FROM observations
            WHERE source=? AND observed_at>=? AND observed_at<=?
            ORDER BY observed_at
        """, (source, cutoff.isoformat(), now.isoformat())).fetchall()
    daily = _daily_offers(rows, brand_aliases, start=cutoff, end=now).get(_offer_key(product, offer, brand_aliases), {})
    return [daily[day] for day in sorted(daily, reverse=True)]
