from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from deal_common.catalog.price_integrity import evaluate
from deal_common.catalog.storage import read_offer_history, save_source

DAY = date(2026, 11, 27)


def row(age, price=100, reference=150, stock="in_stock"):
    stamp = datetime.combine(DAY - timedelta(days=age), time(12), UTC)
    return {"observed_at": stamp.isoformat(), "price": price, "reference": reference,
            "availability": stock, "availability_note": "", "price_note": ""}


def history(count, **kwargs):
    return [row(age, **kwargs) for age in range(1, count + 1)]


def run(rows):
    return evaluate(rows, as_of=DAY)


def test_empty_history():
    result = run([])
    assert (result.low_30d, result.low_90d, result.days_30d, result.days_90d) == (None, None, 0, 0)
    assert (result.verified_atl, result.inflated_msrp, result.badge) == (False, False, None)


def test_calendar_window_edges():
    result = run([row(age, price) for age, price in
                  [(-1, 1), (0, 2), (1, 100), (30, 80), (31, 75), (90, 60), (91, 50)]])
    assert (result.low_30d, result.days_30d) == (80, 2)
    assert (result.low_90d, result.days_90d) == (60, 4)


def test_utc_bucket_crosses_local_midnight():
    today = dict(row(1, 50), observed_at="2026-11-26T20:00:00-08:00")
    result = run([*history(7), today])
    assert result.verified_atl
    assert result.low_30d == 100


@pytest.mark.parametrize("count,price,verified", [(6, 99, False), (7, 100, True),
                                                  (7, 99.99, True), (7, 100.01, False)])
def test_atl_boundaries(count, price, verified):
    result = run([*history(count), row(0, price)])
    assert result.verified_atl is verified
    assert result.badge == ("Verified ATL" if verified else None)
    assert result.reason


def test_sparse_days_and_missing_reference_allow_atl():
    rows = [row(age, reference=None) for age in range(0, 22, 3)]
    assert run(rows).verified_atl


def test_older_low_blocks_atl():
    result = run([row(91, 95), *history(7), row(0, 99)])
    assert result.low_90d == 100
    assert not result.verified_atl


def test_duplicates_cannot_supply_missing_days():
    rows = history(6) * 3 + [row(0)] * 3
    result = run(rows)
    assert (result.days_30d, result.verified_atl, result.inflated_msrp) == (6, False, False)


def test_conflicting_prices_are_order_independent():
    rows = [*history(7), row(1, 105), row(1), row(0)]
    assert run(rows) == run(reversed(rows))
    assert run(rows).days_30d == 6


def test_conflicting_current_reference_preserves_atl():
    result = run([*history(14), row(0), row(0, reference=200)])
    assert result.verified_atl
    assert not result.inflated_msrp


@pytest.mark.parametrize("stock", ["out_of_stock", "unknown"])
def test_unconfirmed_stock_cannot_establish_low(stock):
    result = run([*history(7), row(8, 1, stock=stock), row(0, stock=stock)])
    assert result.low_30d == 100
    assert not result.verified_atl


@pytest.mark.parametrize("price", [None, 0, -2, True, float("nan"), float("inf"), 100.001])
def test_invalid_selling_price_is_not_evidence(price):
    result = run([row(1, price)])
    assert result.low_90d is None
    assert result.days_90d == 0


def test_decimal_cents_survive_storage_floats():
    assert run([row(1, 19.99)]).low_30d == Decimal("19.99")


@pytest.mark.parametrize("count,warning", [(13, False), (14, True)])
def test_warning_requires_distinct_prior_days(count, warning):
    result = run([*history(count), row(1), row(0)])
    assert result.inflated_msrp is warning


@pytest.mark.parametrize("high,warning", [(100.99, True), (101, True), (101.01, False)])
def test_one_percent_spread_boundary(high, warning):
    result = run([*history(13), row(14, high), row(0, 90)])
    assert result.inflated_msrp is warning


@pytest.mark.parametrize("reference", [None, 0, 99, 100])
def test_missing_or_reached_reference_blocks_warning(reference):
    result = run([*history(14), row(0, 90, reference)])
    assert not result.inflated_msrp


def test_current_price_reaches_reference():
    assert not run([*history(14), row(0, 150)]).inflated_msrp


def test_only_prior_90_days_supply_warning_evidence():
    assert not run([*history(13), row(91), row(0)]).inflated_msrp
    assert run([*history(13), row(90), row(91, 150), row(0)]).inflated_msrp


def test_missing_today_never_uses_latest_prior_row():
    result = run([*history(14), row(-1, 90)])
    assert result.badge is None
    assert not result.verified_atl and not result.inflated_msrp
    assert "evaluation day" in result.reason


def test_warning_badge_explains_both_signals():
    result = run([*history(14), row(0, 90)])
    assert result.verified_atl and result.inflated_msrp
    assert result.badge == "Inflated MSRP"
    assert "14 prior observed days" in result.reason
    assert "lowest observed price" in result.reason


def test_warning_without_atl():
    result = run([*history(14), row(0, 102)])
    assert result.inflated_msrp and not result.verified_atl
    assert result.badge == "Inflated MSRP"


def test_exact_variant_history_from_storage(tmp_path):
    path = tmp_path / "offers.sqlite"
    offer = {"variant_key": "M", "url": "https://example.org/p/m", "options": "M",
             "current_price": 99.99, "original_price": 125, "availability": "in_stock"}
    other = dict(offer, variant_key="L", current_price=40)
    product = {"url": "https://example.org/p", "brand": "Example", "name": "Board",
               "offers": [other, offer]}
    for age in range(14, -1, -1):
        save_source(path, "test", "shop", row(age)["observed_at"], [product], "complete", {})
    rows = read_offer_history(path, "test", "shop", product, offer,
                              now=datetime.fromisoformat(row(0)["observed_at"]), days=90)
    result = run(rows)
    assert (result.low_90d, result.days_90d) == (Decimal("99.99"), 14)
    assert result.verified_atl and result.inflated_msrp
