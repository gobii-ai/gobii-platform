from django.contrib.auth import get_user_model

from api.models import SmsSuppression, UserPhoneNumber

User = get_user_model()


def normalize_phone_number(phone_number: str | None) -> str:
    return (phone_number or "").strip()


def get_active_sms_suppression(phone_number: str | None) -> SmsSuppression | None:
    normalized = normalize_phone_number(phone_number)
    if not normalized:
        return None
    return SmsSuppression.objects.filter(
        phone_number__iexact=normalized,
        is_active=True,
    ).first()


def is_sms_suppressed(phone_number: str | None) -> bool:
    return get_active_sms_suppression(phone_number) is not None


def suppress_sms_number(phone_number: str | None, *, source: str) -> SmsSuppression | None:
    normalized = normalize_phone_number(phone_number)
    if not normalized:
        return None

    clean_source = (source or "").strip()[:64]
    suppression, created = SmsSuppression.objects.get_or_create(
        phone_number=normalized,
        defaults={
            "is_active": True,
            "source": clean_source,
        },
    )
    if created:
        return suppression

    update_fields: list[str] = []
    if not suppression.is_active:
        suppression.is_active = True
        update_fields.append("is_active")
    if suppression.source != clean_source:
        suppression.source = clean_source
        update_fields.append("source")
    if update_fields:
        update_fields.append("updated_at")
        suppression.save(update_fields=update_fields)

    return suppression


def unsuppress_sms_number(phone_number: str | None, *, source: str) -> SmsSuppression | None:
    normalized = normalize_phone_number(phone_number)
    if not normalized:
        return None

    suppression = SmsSuppression.objects.filter(phone_number__iexact=normalized).first()
    if suppression is None:
        return None

    clean_source = (source or "").strip()[:64]
    update_fields: list[str] = []
    if suppression.is_active:
        suppression.is_active = False
        update_fields.append("is_active")
    if suppression.source != clean_source:
        suppression.source = clean_source
        update_fields.append("source")
    if update_fields:
        update_fields.append("updated_at")
        suppression.save(update_fields=update_fields)

    return suppression


def get_user_for_phone_number(phone_number: str | None) -> User | None:
    normalized = normalize_phone_number(phone_number)
    if not normalized:
        return None

    record = (
        UserPhoneNumber.objects.select_related("user")
        .filter(phone_number__iexact=normalized)
        .first()
    )
    return record.user if record else None
