"""SQLite schema and CRUD operations via aiosqlite."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import aiosqlite
from deal_common.offers import HISTORY_DAYS, discount, price_signal

from aggregator.config import EQUIPMENT_CATEGORIES, STORES, env_flag
from aggregator.models import AggregatedDeal, model_year
from aggregator.offers import SCHEMA as OFFER_SCHEMA
from aggregator.offers import selection_sql, write_offers
from aggregator.sizes import matches_length

DB_PATH = Path(os.environ.get("DATABASE_PATH", Path(__file__).resolve().parents[2] / "data" / "deals.db"))
_READONLY_CACHE: dict[tuple, object] = {}
_READONLY_CACHE_TTL_SECONDS = 300


def _readonly_cache_key(name: str, db_path: Path) -> tuple | None:
    if not _deal_db_read_only():
        return None
    try:
        st = Path(db_path).stat()
    except OSError:
        return None
    return (name, str(db_path), st.st_mtime_ns, st.st_size, int(time.time() // _READONLY_CACHE_TTL_SECONDS))

SCHEMA = """\
CREATE TABLE IF NOT EXISTS deals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    store         TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    url           TEXT    NOT NULL UNIQUE,
    current_price REAL    NOT NULL,
    original_price REAL,
    discount_pct  REAL    NOT NULL DEFAULT 0,
    category      TEXT,
    sizes         TEXT,
    length_min    INTEGER,
    length_max    INTEGER,
    scraped_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discount  ON deals (discount_pct DESC);
CREATE INDEX IF NOT EXISTS idx_category  ON deals (category);
CREATE INDEX IF NOT EXISTS idx_store     ON deals (store);

CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name  TEXT    NOT NULL,
    brand         TEXT    NOT NULL DEFAULT '',
    score         INTEGER NOT NULL,
    award         TEXT,
    review_url    TEXT    NOT NULL UNIQUE,
    category      TEXT,
    scraped_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS deal_reviews (
    deal_id     INTEGER NOT NULL,
    review_id   INTEGER NOT NULL,
    score       INTEGER NOT NULL,
    award       TEXT,
    review_url  TEXT    NOT NULL,
    PRIMARY KEY (deal_id)
);

CREATE TABLE IF NOT EXISTS price_history (
    url TEXT NOT NULL, observed_at TEXT NOT NULL, price REAL NOT NULL,
    PRIMARY KEY (url, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_history_url ON price_history(url, observed_at);
CREATE TABLE IF NOT EXISTS scrape_runs (
    store TEXT PRIMARY KEY, checked_at TEXT NOT NULL, status TEXT NOT NULL,
    deals INTEGER NOT NULL, errors INTEGER NOT NULL, pages INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deal_reviews_score ON deal_reviews (score DESC);

"""


MIGRATIONS = [
    "ALTER TABLE deals ADD COLUMN first_seen TEXT",
    "ALTER TABLE deals ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD'",
    "ALTER TABLE deals ADD COLUMN sizes TEXT",
    "ALTER TABLE deals ADD COLUMN length_min INTEGER",
    "ALTER TABLE deals ADD COLUMN length_max INTEGER",
    "ALTER TABLE deals ADD COLUMN image_url TEXT",
    "ALTER TABLE deals ADD COLUMN brand TEXT",
    "ALTER TABLE deals ADD COLUMN product_type TEXT",
    "ALTER TABLE deals ADD COLUMN exclusion_reason TEXT",
    "CREATE INDEX IF NOT EXISTS idx_brand ON deals (brand)",
]


def _deal_db_read_only() -> bool:
    """Return True when deal data should be opened read-only."""
    return env_flag("DEALS_DB_READ_ONLY") or bool(os.environ.get("VERCEL"))


def _sqlite_uri(db_path: Path, mode: str) -> str:
    """Build a SQLite file URI for paths that may contain spaces."""
    path = Path(db_path).resolve().as_posix()
    return f"file:{quote(path, safe='/')}?mode={mode}"


def _connect(db_path: Path, *, read_only: bool = False):
    """Open a SQLite connection, optionally forcing read-only mode."""
    if read_only:
        return aiosqlite.connect(_sqlite_uri(db_path, "ro"), uri=True)
    return aiosqlite.connect(db_path)


async def ensure_deals_db_ready(db_path: Path = DB_PATH) -> None:
    """Validate the bundled deals database without writing to it."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        raise RuntimeError(
            f"Deal database not found at {db_path}. "
            "Run scripts/vercel_build.py during deployment or set DATABASE_PATH."
        )

    async with _connect(db_path, read_only=True) as db:
        cursor = await db.execute("PRAGMA integrity_check")
        row = await cursor.fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise RuntimeError(f"Deal database integrity check failed: {row!r}")

        try:
            cursor = await db.execute("SELECT COUNT(*) FROM deals")
            await cursor.fetchone()
            await db.execute("SELECT product_url FROM deal_offers LIMIT 1")
            await db.execute("SELECT product_url FROM offer_observations LIMIT 1")
        except (sqlite3.Error, aiosqlite.Error) as exc:
            raise RuntimeError(
                f"Deal database at {db_path} is missing required deal/offer tables. Rebuild the snapshot before serving."
            ) from exc


async def _prune_observation_bloat(db: aiosqlite.Connection, *, now: datetime | None = None) -> None:
    anchor = (now or datetime.now(UTC).replace(tzinfo=None))
    recent_cutoff = (anchor - timedelta(hours=48)).isoformat()
    obs_cutoff = (anchor - timedelta(days=HISTORY_DAYS)).isoformat()
    hist_cutoff = (anchor - timedelta(days=180)).isoformat()

    # Older variant evidence keeps only price changes, so the minimum over kept rows
    # is the window low; the latest row before the window carries its opening price.
    await db.execute(
        """
        DELETE FROM offer_observations WHERE rowid IN (
            SELECT rowid FROM (
                SELECT rowid, observed_at,
                       (price, reference_price, currency, availability, reference_basis, product_name)
                       IS (LAG(price) OVER w, LAG(reference_price) OVER w, LAG(currency) OVER w,
                           LAG(availability) OVER w, LAG(reference_basis) OVER w, LAG(product_name) OVER w)
                       AS unchanged
                FROM offer_observations
                WINDOW w AS (PARTITION BY product_url, variant_key ORDER BY observed_at)
            ) WHERE observed_at < ? AND unchanged
        )
        """,
        (recent_cutoff,),
    )
    await db.execute(
        """
        DELETE FROM offer_observations WHERE rowid IN (
            SELECT rowid FROM (
                SELECT rowid, ROW_NUMBER() OVER (
                    PARTITION BY product_url, variant_key ORDER BY observed_at DESC) AS newest
                FROM offer_observations WHERE observed_at < ?
            ) WHERE newest > 1
        )
        """,
        (obs_cutoff,),
    )
    await db.execute("DELETE FROM price_history WHERE observed_at < ?", (hist_cutoff,))
    await db.execute(
        """
        DELETE FROM price_history
        WHERE observed_at < ?
          AND rowid NOT IN (
              SELECT MIN(rowid) FROM (
                  SELECT rowid, url, substr(observed_at, 1, 10) AS obs_day, price,
                         MIN(price) OVER (PARTITION BY url, substr(observed_at, 1, 10)) AS day_min
                  FROM price_history
                  WHERE observed_at < ?
              )
              WHERE price = day_min
              GROUP BY url, obs_day
          )
        """,
        (recent_cutoff, recent_cutoff),
    )


async def repair_deal_categories(db_path: Path) -> int:
    """Reclassify the downloaded snapshot while preserving price evidence and age."""
    from aggregator.categorizer import categorize, exclusion_reason
    from aggregator.sizes import extract_lengths

    async with _connect(db_path) as db:
        rows = await (await db.execute(
            "SELECT id, name, url, product_type, brand, category, exclusion_reason, sizes, length_min, length_max FROM deals"
        )).fetchall()
        corrections = []
        for deal_id, name, url, product_type, brand, category, old_reason, sizes, _lower, _upper in rows:
            reason = exclusion_reason(name, url, product_type or '')
            corrected = None if reason else categorize(name, url, product_type or '', brand=brand or '')
            if corrected != category or reason != old_reason:
                lengths = extract_lengths(sizes) if corrected in {'skis', 'snowboards'} else (None, None)
                corrections.append((corrected, reason, *lengths, deal_id))
        await db.executemany(
            "UPDATE deals SET category = ?, exclusion_reason = ?, length_min = ?, length_max = ? WHERE id = ?",
            corrections,
        )
        await db.executemany("DELETE FROM deal_reviews WHERE deal_id = ?",
                             [(row[-1],) for row in corrections])
        await _prune_observation_bloat(db)
        await db.commit()
    return len(corrections)


async def init_db(db_path: Path = DB_PATH) -> None:
    """Create tables and apply migrations."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with _connect(db_path) as db:
        await db.executescript(SCHEMA + OFFER_SCHEMA)
        for migration in MIGRATIONS:
            with contextlib.suppress(sqlite3.OperationalError, aiosqlite.OperationalError):
                await db.execute(migration)
        await db.execute("UPDATE deals SET first_seen = scraped_at WHERE first_seen IS NULL")
        for store in STORES:
            await db.execute("UPDATE deals SET currency = ? WHERE store = ?", (store.currency, store.name))
        await db.commit()


async def upsert_deals(deals: list[AggregatedDeal], db_path: Path = DB_PATH) -> int:
    """Insert or update deals. Returns count of rows affected."""
    from aggregator.categorizer import (
        categorize,
        category_from_product_type,
        exclusion_reason,
    )
    from aggregator.sizes import extract_lengths
    async with _connect(db_path) as db:
        count = 0
        for d in deals:
            # An opaque code or generic "Boots" must not erase reviewed taxonomy
            # for the same URL and title when a retailer omits detail later.
            product_type = d.product_type
            if (category_from_product_type(product_type or '') in {None, 'boots'}
                    and not exclusion_reason('', product_type=product_type or '')):
                product_type = None
            currency = next((store.currency for store in STORES if store.name == d.store), d.currency)
            prior = await (await db.execute("SELECT current_price, scraped_at, name, product_type, brand FROM deals WHERE url = ?", (d.url,))).fetchone()
            category, lengths = d.category, (d.length_min, d.length_max)
            brand = d.brand
            if prior and prior[2] == d.name:
                brand = brand or prior[4]
                if not product_type and prior[3]:
                    product_type = prior[3]
                    category = categorize(d.name, d.url, product_type, brand=brand or '')
                    lengths = extract_lengths(d.sizes) if category in {'skis', 'snowboards'} else (None, None)
            reason = exclusion_reason(d.name, d.url, product_type or '')
            if reason:
                category, lengths = None, (None, None)
            if prior:
                await db.execute("INSERT OR IGNORE INTO price_history VALUES (?, ?, ?)", (d.url, prior[1], prior[0]))
            await db.execute("INSERT OR REPLACE INTO price_history VALUES (?, ?, ?)", (d.url, d.scraped_at.isoformat(), d.current_price))
            await db.execute(
                """\
                INSERT INTO deals (store, name, url, current_price, original_price,
                                   discount_pct, category, sizes, length_min, length_max,
                                   scraped_at, image_url, brand, first_seen, currency, product_type, exclusion_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    store          = excluded.store,
                    name           = excluded.name,
                    current_price  = excluded.current_price,
                    original_price = excluded.original_price,
                    discount_pct   = excluded.discount_pct,
                    category       = excluded.category,
                    sizes          = excluded.sizes,
                    length_min     = excluded.length_min,
                    length_max     = excluded.length_max,
                    scraped_at     = excluded.scraped_at,
                    image_url      = excluded.image_url,
                    brand          = excluded.brand,
                    currency       = excluded.currency,
                    product_type   = excluded.product_type,
                    exclusion_reason = excluded.exclusion_reason
                """,
                (
                    d.store, d.name, d.url, d.current_price, d.original_price,
                    d.discount_pct, category, d.sizes, *lengths,
                    d.scraped_at.isoformat(), d.image_url, brand, d.scraped_at.isoformat(), currency, product_type, reason,
                ),
            )
            d.currency = currency
            await write_offers(db, d)
            count += 1
        await _prune_observation_bloat(db)
        await db.commit()
    return count


async def upsert_deal_reviews(
    rows: list[dict],
    db_path: Path = DB_PATH,
) -> int:
    """Insert or replace deal→review matches. Each row: deal_id, review_id, score, award, review_url."""
    async with _connect(db_path) as db:
        count = 0
        for r in rows:
            await db.execute(
                """\
                INSERT OR REPLACE INTO deal_reviews (deal_id, review_id, score, award, review_url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (r["deal_id"], r["review_id"], r["score"], r.get("award"), r["review_url"]),
            )
            count += 1
        await db.commit()
    return count


async def count_with_length(db_path: Path = DB_PATH) -> int:
    """Return count of deals that have length data."""
    cache_key = _readonly_cache_key("count_with_length", db_path)
    if cache_key is not None and cache_key in _READONLY_CACHE:
        return _READONLY_CACHE[cache_key]
    async with _connect(db_path, read_only=_deal_db_read_only()) as db:
        cursor = await db.execute(
            """SELECT COUNT(*) FROM deals d
               WHERE exclusion_reason IS NULL AND length_min IS NOT NULL AND category IN ('skis', 'snowboards')
               AND scraped_at = (SELECT MAX(scraped_at) FROM deals WHERE store = d.store)
               AND datetime(scraped_at) <= datetime('now')
               AND datetime(scraped_at) >= datetime('now', '-36 hours')"""
        )
        row = await cursor.fetchone()
    result = row[0]
    if cache_key is not None:
        _READONLY_CACHE[cache_key] = result
    return result


async def get_brands(db_path: Path = DB_PATH) -> list[str]:
    """Return distinct brand names from the brand column, sorted."""
    cache_key = _readonly_cache_key("get_brands", db_path)
    if cache_key is not None and cache_key in _READONLY_CACHE:
        return list(_READONLY_CACHE[cache_key])
    async with _connect(db_path, read_only=_deal_db_read_only()) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT deals.brand FROM deals
            INNER JOIN (SELECT store, MAX(scraped_at) as max_s FROM deals GROUP BY store) d2 
              ON deals.store = d2.store AND deals.scraped_at = d2.max_s
            WHERE deals.exclusion_reason IS NULL AND deals.brand IS NOT NULL AND deals.brand != ''
            AND datetime(deals.scraped_at) <= datetime('now')
            AND datetime(deals.scraped_at) >= datetime('now', '-36 hours')
            ORDER BY deals.brand
            """
        )
        rows = await cursor.fetchall()
    result = [row[0] for row in rows if row[0]]
    if cache_key is not None:
        _READONLY_CACHE[cache_key] = tuple(result)
    return result


async def get_category_counts(db_path: Path = DB_PATH) -> dict[str, int]:
    """Return {category: deal_count} for all non-null categories."""
    cache_key = _readonly_cache_key("get_category_counts", db_path)
    if cache_key is not None and cache_key in _READONLY_CACHE:
        return dict(_READONLY_CACHE[cache_key])
    async with _connect(db_path, read_only=_deal_db_read_only()) as db:
        cursor = await db.execute(
            """
            SELECT deals.category, COUNT(*) FROM deals
            INNER JOIN (SELECT store, MAX(scraped_at) as max_s FROM deals GROUP BY store) d2 
              ON deals.store = d2.store AND deals.scraped_at = d2.max_s
            WHERE deals.exclusion_reason IS NULL AND deals.category IS NOT NULL
            AND datetime(deals.scraped_at) <= datetime('now')
            AND datetime(deals.scraped_at) >= datetime('now', '-36 hours')
            GROUP BY deals.category
            """
        )
        rows = await cursor.fetchall()
    result = {row[0]: row[1] for row in rows}
    if cache_key is not None:
        _READONLY_CACHE[cache_key] = dict(result)
    return result


def _prior(expression: str, tail: str = "", *, window: bool = False) -> str:
    """Same variant (or, for single-price listings, same URL) before this snapshot.

    Variant evidence is change-point compressed with one carried opening row, so its
    minimum is already the window low; daily product history needs an explicit window."""
    recent = f" AND datetime(obs.observed_at) >= datetime(deals.scraped_at, '-{HISTORY_DAYS} days')" if window else ""
    return f"""CASE WHEN deals.variant_key IS NOT NULL THEN
        (SELECT {expression} FROM offer_observations obs
         WHERE obs.product_url = deals.url AND obs.variant_key = deals.variant_key
         AND obs.currency = deals.currency AND obs.product_name = deals.name
         AND obs.observed_at < deals.scraped_at {tail})
        ELSE (SELECT {expression} FROM price_history obs WHERE obs.url = deals.url
         AND obs.observed_at < deals.scraped_at{recent} {tail}) END"""


async def query_deals(
    *,
    category: str | None = None,
    store: str | None = None,
    brand: str | None = None,
    min_discount: float = 0,
    min_price: float = 0,
    min_reference: float = 0,
    year_from: int | None = None,
    size: str = "",
    max_price: float = 0,
    sort_by: str = "discount_pct",
    limit: int = 200,
    offset: int = 0,
    tax_free_only: bool = False,
    tax_free_stores: set[str] | None = None,
    q: str = "",
    size_min: int | None = None,
    size_max: int | None = None,
    reviewed_only: bool = False,
    count_only: bool = False,
    count_by_store: bool = False,
    include_clothing: bool = True,
    currency: str | None = None,
    db_path: Path = DB_PATH,
) -> list[AggregatedDeal] | int | dict[str, int]:
    """Query visible deals, or count them before pagination.

    count_by_store returns retailer facet counts, ignoring only the store filter.
    """
    clauses: list[str] = [
        "deals.exclusion_reason IS NULL",
        "deals.discount_pct >= ?",
        "deals.current_price >= 1 AND deals.original_price < 99999 AND deals.discount_pct < 95",
        "datetime(deals.scraped_at) <= datetime('now')",
        "datetime(deals.scraped_at) >= datetime('now', '-36 hours')",
    ]
    params: list[object] = [min_discount]

    if currency in {"USD", "CAD"}:
        clauses.append("deals.currency = ?")
        params.append(currency)
    # Never compare nominal USD and CAD prices as if they were the same currency.
    elif sort_by in {"price_low", "price_high"} or min_price > 0 or max_price > 0 or min_reference > 0:
        clauses.append("deals.currency = 'USD'")

    if min_price > 0:
        clauses.append("deals.current_price >= ?")
        params.append(min_price)

    if year_from is not None:
        clauses.append("model_year(deals.name) >= ?")
        params.append(year_from)
    if min_reference > 0:
        clauses.append("deals.original_price >= ?")
        params.append(min_reference)
    if size:
        clauses.append("deals.variant_key IS NOT NULL AND deals.availability = 'in_stock'")
    if max_price > 0:
        clauses.append("deals.current_price <= ?")
        params.append(max_price)

    if not include_clothing and not category:
        placeholders = ", ".join("?" for _ in EQUIPMENT_CATEGORIES)
        clauses.append(f"deals.category IN ({placeholders})")
        params.extend(EQUIPMENT_CATEGORIES)

    if category:
        clauses.append("deals.category = ?")
        params.append(category)
    if store and not count_by_store:
        clauses.append("deals.store = ?")
        params.append(store)
    if brand:
        clauses.append("deals.brand = ?")
        params.append(brand)
    if tax_free_only and tax_free_stores:
        placeholders = ", ".join("?" for _ in tax_free_stores)
        clauses.append(f"deals.store IN ({placeholders})")
        params.extend(tax_free_stores)
    if q:
        clauses.append("deals.name LIKE ?")
        params.append(f"%{q}%")
    if size_min is not None or size_max is not None:
        clauses.append("deals.category IN ('skis', 'snowboards') AND matches_length(deals.sizes, ?, ?) = 1")
        params.extend([size_min, size_max])
    if reviewed_only:
        clauses.append("dr.deal_id IS NOT NULL")

    where = " AND ".join(clauses)
    
    # Hide stale deals by joining against max scraped_at per store
    latest_join = "INNER JOIN (SELECT store, MAX(scraped_at) as max_s FROM deals GROUP BY store) d2 ON deals.store = d2.store AND deals.scraped_at = d2.max_s"

    selection, offer_params = selection_sql(size=size, size_min=size_min, size_max=size_max,
                                            min_price=min_price, max_price=max_price,
                                            min_reference=min_reference, min_discount=min_discount)
    params = offer_params + params
    if count_only or count_by_store:
        async with _connect(db_path, read_only=_deal_db_read_only()) as db:
            await db.create_function("price_discount", 2, discount, deterministic=True)
            await db.create_function("matches_length", 3, matches_length, deterministic=True)
            await db.create_function("model_year", 1, model_year, deterministic=True)
            columns = 'deals.store, COUNT(*)' if count_by_store else 'COUNT(*)'
            grouping = ' GROUP BY deals.store ORDER BY deals.store' if count_by_store else ''
            cursor = await db.execute(
                selection + f"SELECT {columns} FROM effective deals {latest_join} LEFT JOIN deal_reviews dr ON deals.id = dr.deal_id WHERE {where}{grouping}",
                params,
            )
            rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows} if count_by_store else rows[0][0]

    discount_order = (
        "deals.discount_pct DESC, CASE WHEN deals.currency = 'USD' THEN 0 ELSE 1 END, "
        "deals.currency ASC, (deals.original_price - deals.current_price) DESC, "
        "(deals.image_url IS NOT NULL AND deals.image_url != '') DESC, deals.id ASC"
    )
    sort_map = {
        "discount_pct": discount_order,
        "dropped": "CASE price_signal WHEN 'new_low' THEN 0 WHEN 'dropped' THEN 1 ELSE 2 END, " + discount_order,
        "top_reviewed": "deals.review_score DESC NULLS LAST, deals.discount_pct DESC",
        "price_low": "deals.current_price ASC",
        "price_high": "deals.current_price DESC",
        "store": "deals.store ASC, deals.discount_pct DESC",
        "newest": "deals.first_seen DESC, deals.id DESC",
    }
    order = sort_map.get(sort_by, sort_map["discount_pct"])

    async with _connect(db_path, read_only=_deal_db_read_only()) as db:
        await db.create_function("price_discount", 2, discount, deterministic=True)
        await db.create_function("matches_length", 3, matches_length, deterministic=True)
        await db.create_function("model_year", 1, model_year, deterministic=True)
        await db.create_function("price_signal", 6, price_signal, deterministic=True)
        db.row_factory = aiosqlite.Row
        if sort_by == "dropped":
            sql = selection + f"""\
            SELECT deals.*, price_signal(deals.current_price, deals.previous_price, deals.previous_at,
                                         deals.prior_low, deals.tracked_since, deals.scraped_at) AS price_signal
            FROM (
                SELECT deals.*, dr.score AS review_score, dr.award AS review_award,
                       dr.review_url AS review_url,
                       {_prior("obs.price", "ORDER BY obs.observed_at DESC LIMIT 1")} AS previous_price,
                       {_prior("obs.observed_at", "ORDER BY obs.observed_at DESC LIMIT 1")} AS previous_at,
                       {_prior("MIN(obs.price)", window=True)} AS prior_low,
                       {_prior("MIN(obs.observed_at)")} AS tracked_since
                FROM effective deals
                {latest_join}
                LEFT JOIN deal_reviews dr ON deals.id = dr.deal_id
                WHERE {where}
            ) deals
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """
        else:
            # Order and paginate before evaluating historical observation subqueries so
            # _prior() and price_signal() run on the page slice rather than the full catalog.
            sql = selection + f"""\
            SELECT deals.*, price_signal(deals.current_price, deals.previous_price, deals.previous_at,
                                         deals.prior_low, deals.tracked_since, deals.scraped_at) AS price_signal
            FROM (
                SELECT deals.*,
                       {_prior("obs.price", "ORDER BY obs.observed_at DESC LIMIT 1")} AS previous_price,
                       {_prior("obs.observed_at", "ORDER BY obs.observed_at DESC LIMIT 1")} AS previous_at,
                       {_prior("MIN(obs.price)", window=True)} AS prior_low,
                       {_prior("MIN(obs.observed_at)")} AS tracked_since
                FROM (
                    SELECT deals.*, dr.score AS review_score, dr.award AS review_award,
                           dr.review_url AS review_url
                    FROM effective deals
                    {latest_join}
                    LEFT JOIN deal_reviews dr ON deals.id = dr.deal_id
                    WHERE {where}
                    ORDER BY {order}
                    LIMIT ? OFFSET ?
                ) deals
            ) deals
            ORDER BY {order}
            """
        cursor = await db.execute(sql, (*params, limit, offset))
        rows = await cursor.fetchall()

    return [
        AggregatedDeal(
            first_seen=row["first_seen"], previous_price=row["previous_price"], currency=row["currency"],
            price_signal=row["price_signal"],
            offer_url=row['offer_url'], offer_options=row['offer_options'],
            variant_key=row['variant_key'],
            availability=row['availability'], reference_basis=row['reference_basis'],
            id=row["id"],
            store=row["store"],
            name=row["name"],
            url=row["url"],
            current_price=row["current_price"],
            original_price=row["original_price"],
            discount_pct=row["discount_pct"],
            category=row["category"],
            sizes=row["sizes"],
            length_min=row["length_min"],
            length_max=row["length_max"],
            scraped_at=datetime.fromisoformat(row["scraped_at"]),
            image_url=row["image_url"],
            brand=row["brand"],
            product_type=row["product_type"],
            review_score=row["review_score"],
            review_award=row["review_award"],
            review_url=row["review_url"],
        )
        for row in rows
    ]


async def upsert_reviews(reviews: list, db_path: Path = DB_PATH) -> int:
    """Insert or update reviews. Accepts dicts or ReviewData NamedTuples."""
    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    async with _connect(db_path) as db:
        count = 0
        for r in reviews:
            if isinstance(r, dict):
                name, brand, score = r["product_name"], r["brand"], r["score"]
                award, url, cat = r["award"], r["review_url"], r["category"]
                scraped = r.get("scraped_at", now)
            else:
                name, brand, score = r.product_name, r.brand, r.score
                award, url, cat = r.award, r.url, r.category
                scraped = now
            await db.execute(
                """\
                INSERT INTO reviews (product_name, brand, score, award, review_url, category, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_url) DO UPDATE SET
                    product_name = excluded.product_name,
                    brand        = excluded.brand,
                    score        = excluded.score,
                    award        = excluded.award,
                    category     = excluded.category,
                    scraped_at   = excluded.scraped_at
                """,
                (name, brand, score, award, url, cat, scraped),
            )
            count += 1
        await db.commit()
    return count


async def get_all_reviews(db_path: Path = DB_PATH) -> list[dict]:
    """Return all reviews from the database."""
    async with _connect(db_path, read_only=_deal_db_read_only()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, product_name, brand, score, award, review_url, category FROM reviews ORDER BY score DESC"
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def store_status(db_path: Path = DB_PATH) -> list[dict]:
    """Return per-store stats: deal count, deals with discounts, last scraped time."""
    async with _connect(db_path, read_only=_deal_db_read_only()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""\
            SELECT store,
                   COUNT(*)                          AS deal_count,
                   SUM(CASE WHEN discount_pct > 0 THEN 1 ELSE 0 END) AS discount_count,
                   MAX(scraped_at)                   AS last_scraped
            FROM deals d
            INNER JOIN (SELECT store AS latest_store, MAX(scraped_at) AS latest_time FROM deals GROUP BY store) latest
                ON d.store = latest.latest_store AND d.scraped_at = latest.latest_time
            GROUP BY store
            ORDER BY store
        """)
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def save_scrape_run(report, outcomes: dict, checked_at: str, db_path: Path = DB_PATH) -> None:
    async with _connect(db_path) as db:
        for item in report.store_summaries:
            result = outcomes.get(item.store, {})
            errors = result.get("errors", 0)
            status = "unavailable" if not item.deals else ("partial" if errors else "ok")
            await db.execute("INSERT OR REPLACE INTO scrape_runs VALUES (?, ?, ?, ?, ?, ?)",
                             (item.store, checked_at, status, item.deals, errors, result.get("pages", 0)))
        await db.commit()


async def refresh_status(db_path: Path = DB_PATH) -> list[dict]:
    async with _connect(db_path, read_only=True) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM scrape_runs ORDER BY store")).fetchall()
        return [dict(row) for row in rows]
