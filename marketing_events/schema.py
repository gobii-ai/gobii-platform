import hashlib
import time
import uuid


CANONICAL_EVENT_MAP = {
    # your clean internal names -> destination names resolved per provider
    "CompleteRegistration": "CompleteRegistration",
    "StartTrial": "StartTrial",
    "Subscribe": "Subscribe",
    "UpgradePlan": "UpgradePlan",
    "Lead": "Lead",
    "FeatureUsed": "FeatureUsed",
}


def _sha256_norm(s: str | None) -> str | None:
    if not s:
        return None
    return hashlib.sha256(s.strip().lower().encode("utf-8")).hexdigest()


def normalize_event(payload: dict) -> dict:
    now = int(time.time())
    props = payload.get("properties") or {}
    event_time = int(props.get("event_time", now))
    event_id = str(props.get("event_id") or uuid.uuid4())

    user = payload.get("user") or {}
    ctx = payload.get("context") or {}
    click = ctx.get("click_ids") or {}
    page = ctx.get("page") or {}

    return {
        "event_name": payload.get("event_name"),
        "event_time": event_time,
        "event_id": event_id,
        "properties": props,
        "ids": {
            "external_id": _sha256_norm(user.get("id")),
            "em": _sha256_norm(user.get("email")),
            "ph": _sha256_norm(user.get("phone")),
        },
        "network": {
            "client_ip": ctx.get("client_ip"),
            "user_agent": ctx.get("user_agent"),
            "page_url": page.get("url"),
            "fbp": click.get("fbp"),
            "fbc": click.get("fbc"),
            "fbclid": click.get("fbclid"),
            "rdt_cid": click.get("rdt_cid"),
        },
        "utm": ctx.get("utm") or {},
        "consent": ctx.get("consent", True),
    }
