"""Tests for SQLite database operations."""

import asyncio
from datetime import datetime

import pytest
from aggregator.db import (
    get_all_reviews,
    get_brands,
    init_db,
    query_deals,
    store_status,
    upsert_deals,
    upsert_reviews,
)
from aggregator.models import AggregatedDeal


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def _make_deal(name="Atomic Bent 100", store="Evo", price=499.99, orig=599.99, scraped_at=None, **kw):
    return AggregatedDeal(
        id=None,
        store=store,
        name=name,
        url=kw.get("url", f"https://example.com/{name.replace(' ', '-').lower()}"),
        current_price=price,
        original_price=orig,
        discount_pct=round((1 - price / orig) * 100, 1) if orig else 0,
        category=kw.get("category", "skis"),
        sizes=kw.get("sizes"),
        length_min=kw.get("length_min"),
        length_max=kw.get("length_max"),
        scraped_at=scraped_at or datetime.now(),
    )


class TestDB:
    def test_query_deals(self, db_path):
        async def _run():
            await init_db(db_path)
            inserted = await upsert_deals([
                _make_deal("Atomic Bent 100", "Evo", 499.99, 599.99, category="skis"),
                _make_deal("Burton Custom", "Backcountry", 399.99, 499.99, category="snowboards",
                           url="https://example.com/burton-custom"),
            ], db_path)
            assert inserted == 2

            # Query all
            all_deals = await query_deals(db_path=db_path)
            assert len(all_deals) == 2

            # Filter by category
            skis = await query_deals(category="skis", db_path=db_path)
            assert len(skis) == 1
            assert skis[0].name == "Atomic Bent 100"

            # Filter by store
            evo = await query_deals(store="Evo", db_path=db_path)
            assert len(evo) == 1

            # Count only
            count = await query_deals(count_only=True, db_path=db_path)
            assert count == 2

        asyncio.run(_run())

    def test_upsert_updates_existing(self, db_path):
        async def _run():
            await init_db(db_path)
            deal = _make_deal("Atomic Bent 100", price=499.99, orig=599.99)
            await upsert_deals([deal], db_path)

            # Update price and corrected scraped name for the same product URL.
            deal_updated = _make_deal(
                "Atomic Bent 100 Skis",
                price=449.99,
                orig=599.99,
                url=deal.url,
            )
            await upsert_deals([deal_updated], db_path)

            deals = await query_deals(db_path=db_path)
            assert len(deals) == 1
            assert deals[0].name == "Atomic Bent 100 Skis"
            assert deals[0].current_price == 449.99

        asyncio.run(_run())

    def test_brand_query(self, db_path):
        async def _run():
            await init_db(db_path)
            # brand column is populated at scrape time via _extract_brand; set explicitly here
            now = datetime.now()
            atomic = _make_deal("Atomic Bent 100", url="https://example.com/atomic", scraped_at=now)
            atomic.brand = "atomic"
            salomon = _make_deal("Salomon QST 98", url="https://example.com/salomon", scraped_at=now)
            salomon.brand = "salomon"
            await upsert_deals([atomic, salomon], db_path)

            brands = await get_brands(db_path)
            assert "atomic" in brands
            assert "salomon" in brands

        asyncio.run(_run())

    def test_reviews_crud(self, db_path):
        async def _run():
            await init_db(db_path)
            reviews = [{
                "product_name": "Atomic Bent 100",
                "brand": "Atomic",
                "score": 85,
                "award": "Editors' Choice",
                "review_url": "https://ogl.com/atomic-bent-100",
                "category": "skis",
                "scraped_at": datetime.now().isoformat(),
            }]
            count = await upsert_reviews(reviews, db_path)
            assert count == 1

            all_reviews = await get_all_reviews(db_path)
            assert len(all_reviews) == 1
            assert all_reviews[0]["score"] == 85

        asyncio.run(_run())

    def test_store_status(self, db_path):
        async def _run():
            await init_db(db_path)
            now = datetime.now()
            await upsert_deals([
                _make_deal("Atomic Bent 100", "Evo", scraped_at=now),
                _make_deal("Salomon QST 98", "Evo", url="https://example.com/qst", scraped_at=now),
            ], db_path)

            statuses = await store_status(db_path)
            assert len(statuses) == 1
            assert statuses[0]["store"] == "Evo"
            assert statuses[0]["deal_count"] == 2

        asyncio.run(_run())

    def test_text_search(self, db_path):
        async def _run():
            await init_db(db_path)
            now = datetime.now()
            await upsert_deals([
                _make_deal("Atomic Bent 100", scraped_at=now),
                _make_deal("Salomon QST 98", url="https://example.com/qst", scraped_at=now),
            ], db_path)

            results = await query_deals(q="Bent", db_path=db_path)
            assert len(results) == 1
            assert "Bent" in results[0].name

        asyncio.run(_run())

    def test_min_discount_filter(self, db_path):
        async def _run():
            await init_db(db_path)
            now = datetime.now()
            await upsert_deals([
                _make_deal("Big Discount", price=300, orig=600, scraped_at=now),  # 50% off
                _make_deal("Small Discount", price=550, orig=600, url="https://example.com/small", scraped_at=now),  # ~8% off
            ], db_path)

            results = await query_deals(min_discount=20, db_path=db_path)
            assert len(results) == 1
            assert results[0].name == "Big Discount"

        asyncio.run(_run())


def test_default_ranking_puts_deepest_discount_before_image_availability(tmp_path):
    async def run():
        path = tmp_path / 'nested' / 'deals.db'
        await init_db(path)
        now = datetime.now()
        deep = _make_deal('Deep discount without image', price=300, orig=1000, scraped_at=now)
        shallow = _make_deal('Shallower discount with image', price=500, orig=1000, scraped_at=now)
        shallow.image_url = 'https://example.com/image.jpg'
        await upsert_deals([shallow, deep], path)
        rows = await query_deals(db_path=path)
        assert [row.discount_pct for row in rows] == [70, 50]
        assert rows[0].image_url is None

    asyncio.run(run())


def test_observation_bloat_pruning_keeps_daily_lows_and_recent_variant_baseline(tmp_path):
    from datetime import UTC, timedelta

    import aiosqlite
    from aggregator.db import repair_deal_categories

    async def run():
        path = tmp_path / "bloat.db"
        await init_db(path)
        now = datetime.now(UTC).replace(tzinfo=None)
        day_five = (now - timedelta(days=5)).replace(hour=8, minute=0, second=0, microsecond=0)
        expired = now - timedelta(days=200)
        async with aiosqlite.connect(path) as conn:
            await conn.executemany(
                "INSERT INTO price_history VALUES (?, ?, ?)",
                [
                    ("https://example.com/ski", expired.isoformat(), 199.0),
                    ("https://example.com/ski", day_five.isoformat(), 450.0),
                    ("https://example.com/ski", (day_five + timedelta(hours=6)).isoformat(), 399.0),
                    ("https://example.com/ski", (now - timedelta(hours=2)).isoformat(), 379.0),
                    ("https://example.com/ski", now.isoformat(), 349.0),
                ],
            )
            await conn.executemany(
                "INSERT INTO offer_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("https://example.com/ski", "v1", (now - timedelta(days=30)).isoformat(), 500, 800, "USD", "in_stock", "retailer_reference", "Ski"),
                    ("https://example.com/ski", "v1", day_five.isoformat(), 450, 800, "USD", "in_stock", "retailer_reference", "Ski"),
                    ("https://example.com/ski", "v1", (day_five + timedelta(hours=6)).isoformat(), 420, 800, "USD", "in_stock", "retailer_reference", "Ski"),
                    ("https://example.com/ski", "v1", now.isoformat(), 349, 800, "USD", "in_stock", "retailer_reference", "Ski"),
                ],
            )
            await conn.commit()

        await repair_deal_categories(path)
        async with aiosqlite.connect(path) as conn:
            hist = await (await conn.execute("SELECT price FROM price_history ORDER BY observed_at")).fetchall()
            obs = await (await conn.execute("SELECT price FROM offer_observations ORDER BY observed_at")).fetchall()
        assert [r[0] for r in hist] == [399.0, 379.0, 349.0]
        assert [r[0] for r in obs] == [420.0, 349.0]

    asyncio.run(run())
