from django.utils import timezone

from api.models import UserPhoneNumber


def get_primary_phone(user):
    return UserPhoneNumber.objects.filter(user=user, is_primary=True).first()


def get_pending_phone(user):
    return UserPhoneNumber.objects.filter(user=user, is_verified=False).order_by("-created_at").first()


def get_phone_cooldown_remaining(phone, cooldown_seconds: int = 60) -> int:
    if not phone or phone.is_verified:
        return 0
    if not phone.last_verification_attempt:
        return 0
    elapsed = (timezone.now() - phone.last_verification_attempt).total_seconds()
    return max(0, int(cooldown_seconds - elapsed))


def serialize_phone(phone, cooldown_seconds: int = 60) -> dict | None:
    if not phone:
        return None
    return {
        "number": phone.phone_number,
        "isVerified": bool(phone.is_verified),
        "verifiedAt": phone.verified_at.isoformat() if phone.verified_at else None,
        "cooldownRemaining": get_phone_cooldown_remaining(phone, cooldown_seconds=cooldown_seconds),
    }


def serialize_phone_state(user) -> dict:
    primary_phone = get_primary_phone(user)
    return {
        "phone": serialize_phone(primary_phone if primary_phone and primary_phone.is_verified else None),
        "pendingPhone": serialize_phone(get_pending_phone(user)),
    }
