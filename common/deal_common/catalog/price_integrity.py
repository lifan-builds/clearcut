"""Price evidence for one exact offer from storage.read_offer_history."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class Integrity:
    low_30d: Decimal | None
    low_90d: Decimal | None
    days_30d: int
    days_90d: int
    verified_atl: bool
    inflated_msrp: bool
    badge: str | None
    reason: str


def _amount(value: float | None) -> Decimal | None:
    if type(value) not in (int, float):
        return None
    amount = Decimal(str(value))
    if amount.is_finite() and amount > 0 and amount * 100 == (amount * 100).to_integral_value():
        return amount
    return None


def evaluate(rows: Iterable[dict], *, as_of: date) -> Integrity:
    """Use all supplied prior days for ATL and the prior 90 days for anchoring.

    A stored reference is the retailer's listed comparison price. The warning
    describes the observed selling prices relative to that comparison price.
    """
    daily = {}
    for row in rows:
        day = datetime.fromisoformat(row["observed_at"]).astimezone(UTC).date()
        if day > as_of:
            continue
        price = _amount(row["price"]) if row["availability"] == "in_stock" else None
        reference = _amount(row["reference"]) if day == as_of else None
        if day in daily:
            old_price, old_reference = daily[day]
            price = price if price == old_price else None
            reference = reference if reference == old_reference else None
        daily[day] = price, reference

    today, reference = daily.pop(as_of, (None, None))
    prior = {day: price for day, (price, _) in daily.items() if price is not None}
    prices_90d = [price for day, price in prior.items() if day >= as_of - timedelta(days=90)]
    prices_30d = [price for day, price in prior.items() if day >= as_of - timedelta(days=30)]
    low_90d = min(prices_90d, default=None)
    verified_atl = today is not None and len(prior) >= 7 and today <= min(prior.values())
    inflated_msrp = False
    if today is not None and reference is not None and len(prices_90d) >= 14:
        high = max(prices_90d)
        inflated_msrp = high <= low_90d * Decimal("1.01") and max(high, today) < reference

    badge = None
    if inflated_msrp:
        badge = "Inflated MSRP"
        reason = (f"Across {len(prices_90d)} prior observed days, prices varied by at most 1% "
                  f"and never reached the listed reference of {reference}.")
        if verified_atl:
            reason += " Today's price also matches or beats the lowest observed price."
    elif verified_atl:
        badge = "Verified ATL"
        reason = f"Today's price matches or beats the low across {len(prior)} prior observed days."
    elif today is None:
        reason = "No confirmed in-stock price for the evaluation day."
    elif len(prior) < 7:
        reason = f"Only {len(prior)} prior observed days; verifying a low requires 7."
    else:
        reason = "Today's price exceeds the lowest observed price."

    return Integrity(min(prices_30d, default=None), low_90d, len(prices_30d), len(prices_90d),
                     verified_atl, inflated_msrp, badge, reason)
