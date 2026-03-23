from decimal import Decimal, InvalidOperation


def _to_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def calculate_start_trial_values(
    base_value: object,
    *,
    ltv_multiple: object,
    conversion_rate: object,
) -> tuple[float | None, float | None]:
    """
    Return the StartTrial predicted LTV and conversion value.

    `base_value` is the underlying subscription value in major currency units.
    """
    base_decimal = _to_decimal(base_value)
    ltv_multiple_decimal = _to_decimal(ltv_multiple)
    conversion_rate_decimal = _to_decimal(conversion_rate)

    if base_decimal is None or ltv_multiple_decimal is None or conversion_rate_decimal is None:
        return None, None

    predicted_ltv = base_decimal * ltv_multiple_decimal
    conversion_value = predicted_ltv * conversion_rate_decimal
    return float(predicted_ltv), float(conversion_value)
