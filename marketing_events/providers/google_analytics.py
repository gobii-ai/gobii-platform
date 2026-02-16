import re

from .base import post_json


class GoogleAnalyticsMP:
    def __init__(self, measurement_id: str, api_secret: str):
        self.measurement_id = measurement_id
        self.api_secret = api_secret
        self.url = "https://www.google-analytics.com/mp/collect"

    @staticmethod
    def _map_event_name(name: str) -> str | None:
        # Map internal marketing events to GA4 recommended/custom events.
        mapping = {
            "CompleteRegistration": "sign_up",
            "InitiateCheckout": "begin_checkout",
            "Subscribe": "purchase",
            "CancelSubscription": "cancel_subscription",
            "StartTrial": "start_trial",
        }
        return mapping.get(name)

    @staticmethod
    def _extract_transaction_id(properties: dict | None) -> str | None:
        if not properties:
            return None

        candidate = properties.get("stripe.invoice_id")
        if candidate is None:
            return None

        transaction_id = str(candidate).strip()
        if not transaction_id:
            return None
        return transaction_id[:100]

    @staticmethod
    def _sanitize_params(properties: dict | None) -> dict:
        if not properties:
            return {}

        cleaned: dict = {}
        name_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
        for key, value in properties.items():
            if not isinstance(key, str):
                continue
            key = key.strip()
            if not key or not name_pattern.match(key):
                continue
            if isinstance(value, bool):
                cleaned[key] = value
            elif isinstance(value, (int, float)):
                cleaned[key] = value
            elif isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    cleaned[key] = trimmed[:100]
        return cleaned

    @staticmethod
    def _event_time_micros(event_time: int | float | str | None) -> int | None:
        if event_time is None:
            return None
        if isinstance(event_time, str):
            try:
                event_time = float(event_time)
            except ValueError:
                return None
        try:
            event_time_int = int(event_time)
        except (TypeError, ValueError):
            return None

        # Support incoming milliseconds as well as seconds.
        if event_time_int >= 10**12:
            event_time_int = event_time_int // 1000
        return event_time_int * 1_000_000

    @staticmethod
    def _fallback_client_id(evt: dict) -> str:
        network = evt.get("network") or {}
        candidate = (network.get("ga_client_id") or "").strip()
        if candidate:
            return candidate

        ids = evt.get("ids") or {}
        external_id = (ids.get("external_id") or "").strip()
        if external_id:
            return external_id

        return str(evt.get("event_id") or "anonymous")

    def send(self, evt: dict):
        if not evt.get("consent", True):
            return

        event_name = self._map_event_name(evt.get("event_name", ""))
        if not event_name:
            return

        properties = evt.get("properties") or {}
        params = self._sanitize_params(properties)
        if event_name == "purchase":
            transaction_id = self._extract_transaction_id(properties)
            if not transaction_id:
                return
            params["transaction_id"] = transaction_id
        params["event_id"] = str(evt.get("event_id") or "")
        params.setdefault("engagement_time_msec", 1)

        body = {
            "client_id": self._fallback_client_id(evt),
            "events": [{"name": event_name, "params": params}],
        }

        ids = evt.get("ids") or {}
        user_id = (ids.get("external_id") or "").strip()
        if user_id:
            body["user_id"] = user_id

        event_time_micros = self._event_time_micros(evt.get("event_time"))
        if event_time_micros:
            body["timestamp_micros"] = event_time_micros

        return post_json(
            self.url,
            json=body,
            params={
                "measurement_id": self.measurement_id,
                "api_secret": self.api_secret,
            },
            headers={"Content-Type": "application/json"},
        )
