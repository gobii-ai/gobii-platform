import hashlib
import hmac

from django.conf import settings


def get_churnkey_auth_hash(customer_id: str) -> str | None:
    customer_id = (customer_id or "").strip()
    if not customer_id or not settings.CHURN_KEY_API_KEY:
        return None

    return hmac.new(
        settings.CHURN_KEY_API_KEY.encode("utf-8"),
        customer_id.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def build_churnkey_cancel_flow_config(
    *,
    customer_id: str | None,
    subscription_id: str | None,
    livemode: bool | None = None,
) -> dict[str, str | bool] | None:
    customer_id = (customer_id or "").strip()
    subscription_id = (subscription_id or "").strip()
    auth_hash = get_churnkey_auth_hash(customer_id)
    if not customer_id or not subscription_id or not auth_hash or not settings.CHURN_KEY_APP_ID:
        return None

    resolved_livemode = settings.STRIPE_LIVE_MODE if livemode is None else bool(livemode)
    return {
        "enabled": True,
        "appId": settings.CHURN_KEY_APP_ID,
        "customerId": customer_id,
        "subscriptionId": subscription_id,
        "authHash": auth_hash,
        "mode": "live" if resolved_livemode else "test",
        "provider": "stripe",
    }
