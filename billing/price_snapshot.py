from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from djstripe.models import Price


@dataclass(frozen=True)
class StripePriceSnapshot:
    amount: Decimal
    currency: str


def get_stripe_price_snapshot(price_id: str | None) -> StripePriceSnapshot | None:
    normalized_price_id = str(price_id or "").strip()
    if not normalized_price_id:
        return None

    price = (
        Price.objects.filter(id=normalized_price_id)
        .only("unit_amount", "unit_amount_decimal", "currency")
        .first()
    )
    if price is None:
        return None

    raw_amount = price.unit_amount
    if raw_amount is None:
        raw_amount = price.unit_amount_decimal
    if raw_amount is None:
        return None

    try:
        amount = Decimal(str(raw_amount)) / Decimal("100")
    except (InvalidOperation, TypeError, ValueError):
        return None
    if amount < 0:
        return None

    return StripePriceSnapshot(
        amount=amount,
        currency=str(price.currency or "").upper(),
    )
