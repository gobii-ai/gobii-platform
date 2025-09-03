from django.core.exceptions import ValidationError

from constants.phone_countries import SUPPORTED_REGION_CODES


def validate_and_format_e164(phone_raw: str) -> str:
    """
    Validate a phone number and return it in E.164 format.

    Raises django.core.exceptions.ValidationError with codes:
      - invalid_phone
      - unsupported_region
    """
    if not phone_raw:
        raise ValidationError("Enter a valid phone number.", code="invalid_phone")

    try:
        # Lazy import to avoid heavy import during startup in code paths that don't need it
        from phonenumbers import parse, is_valid_number, region_code_for_number, format_number, PhoneNumberFormat

        parsed = parse(phone_raw, None)  # None => no default region; expects +country-code or valid international
        if not is_valid_number(parsed):
            raise ValidationError("Enter a valid phone number.", code="invalid_phone")

        region = region_code_for_number(parsed)
        if not region or region not in SUPPORTED_REGION_CODES:
            raise ValidationError(
                "Phone numbers from this country are not yet supported.",
                code="unsupported_region",
            )

        return format_number(parsed, PhoneNumberFormat.E164)
    except ValidationError:
        raise
    except Exception:
        # Generic minimal message; don't leak parse errors
        raise ValidationError("Enter a valid phone number.", code="invalid_phone")

