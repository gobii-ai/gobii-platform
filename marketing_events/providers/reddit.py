from django.conf import settings

from marketing_events.providers.base import post_json

ALLOWED_METADATA_KEYS = {"conversion_id", "value", "currency", "item_count", "products"}
ALLOWED_PRODUCT_KEYS = {"id", "category"}  # 'name' is not accepted

class RedditCAPI:
    def __init__(self, pixel_id: str, token: str):
        # Reddit calls this a Pixel ID in the URL path
        self.pixel_id = pixel_id
        self.token = token
        self.url = f"https://ads-api.reddit.com/api/v3/pixels/{pixel_id}/conversion_events"

    def _clean_metadata(self, raw: dict) -> dict:
        meta = {k: v for k, v in (raw or {}).items() if k in ALLOWED_METADATA_KEYS and v not in (None, "", [])}
        # Normalize products if present
        if "products" in meta and isinstance(meta["products"], list):
            cleaned = []
            for p in meta["products"]:
                if isinstance(p, dict):
                    cleaned.append({k: p[k] for k in ALLOWED_PRODUCT_KEYS if k in p})
            meta["products"] = cleaned
            if not meta["products"]:
                meta.pop("products")
        return meta


    def _map_event_name(self, name: str) -> str | None:
        # Map your internal names to Reddit tracking types
        mapping = {
            "CompleteRegistration": "SIGN_UP",
            "Subscribe": "PURCHASE",
            # add more as needed:
            # "AddToCart": "ADD_TO_CART",
            # "Lead": "LEAD",
        }
        return mapping.get(name)

    @staticmethod
    def _to_millis(ts: int | float | str) -> int:
        # Accept seconds or milliseconds, return milliseconds
        if ts is None:
            return None
        if isinstance(ts, str):
            ts = float(ts)
        ts = int(ts)
        return ts if ts >= 10**12 else ts * 1000

    def send(self, evt: dict):
        # Honor consent
        if not evt.get("consent", True):
            return

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        # Determine event type
        internal_name = evt.get("event_name")
        mapped = self._map_event_name(internal_name)
        is_custom = mapped is None

        event_type = {
            "tracking_type": "CUSTOM" if is_custom else mapped,
        }

        if is_custom:
            # Reddit requires a name when using CUSTOM
            event_type["custom_event_name"] = internal_name

        # Build user identifiers (Reddit supports hashed email/phone, external_id, ip, user_agent, and click id)
        user = {}
        ids = evt.get("ids", {}) or {}
        net = evt.get("network", {}) or {}

        if ids.get("em"):
            user["email"] = ids["em"]               # SHA-256, lowercase email
        if ids.get("ph"):
            user["phone"] = ids["ph"]               # SHA-256 E.164
        if ids.get("external_id"):
            user["external_id"] = ids["external_id"]  # SHA-256
        if net.get("client_ip"):
            user["ip_address"] = net["client_ip"]
        if net.get("user_agent"):
            user["user_agent"] = net["user_agent"]
        if net.get("rdt_cid"):
            # Reddit click id for attribution (preferred when present)
            click_id = net["rdt_cid"]
        else:
            click_id = None

        # Clean properties into metadata
        props = (evt.get("properties") or {}).copy()
        props.pop("test_mode", False)  # This property is not used in the Reddit payload
        props.pop("event_time", None)
        props.pop("event_id", None)

        metadata = self._clean_metadata({
            **props,
            "conversion_id": evt.get("event_id"),  # keep for dedupe
            # add value/currency/item_count/products if you have them
        })

        # Event timestamp
        event_at_ms = self._to_millis(evt.get("event_time"))

        # Reddit recommends WEBSITE/APP; default to WEBSITE for server events
        action_source = evt.get("action_source") or "WEBSITE"


        event_obj = {
            "event_at": event_at_ms,
            "type": event_type,
            "metadata": metadata or {},
            "user": user,
            "action_source": action_source,
        }
        if click_id:
            event_obj["click_id"] = click_id

        payload = {"data": {"events": [event_obj]}}

        test_mode = bool(getattr(settings, "REDDIT_CAPI_TEST_MODE", False))
        test_code = getattr(settings, "REDDIT_TEST_EVENT_CODE", "") or ""
        if test_mode and isinstance(test_code, str) and test_code.strip():
            payload["data"]["test_id"] = test_code.strip()

        return post_json(self.url, json=payload, headers=headers)
