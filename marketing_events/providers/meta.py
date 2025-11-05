import logging

from django.conf import settings

from .base import post_json, TemporaryError, PermanentError


logger = logging.getLogger(__name__)


class MetaCAPI:
    def __init__(self, pixel_id: str, token: str):
        self.pixel_id = pixel_id
        self.token = token
        self.url = f"https://graph.facebook.com/v20.0/{pixel_id}/events"

    def _map_event_name(self, name: str) -> str:
        # pass-through; customize if needed
        return name

    def send(self, evt: dict):
        if not evt.get("consent", True):
            return
        name = self._map_event_name(evt["event_name"])
        user_data = {
            "em": [evt["ids"]["em"]] if evt["ids"]["em"] else [],
            "ph": [evt["ids"]["ph"]] if evt["ids"]["ph"] else [],
            "external_id": [evt["ids"]["external_id"]] if evt["ids"]["external_id"] else [],
            "client_ip_address": evt["network"]["client_ip"],
            "client_user_agent": evt["network"]["user_agent"],
            "fbp": evt["network"]["fbp"],
            "fbc": evt["network"]["fbc"],
        }
        event_payload = {
            "event_name": name,
            "event_time": evt["event_time"],
            "event_id": evt["event_id"],
            "action_source": "website",
            "event_source_url": evt["network"]["page_url"],
            "user_data": user_data,
            "custom_data": evt["properties"] or {},
        }

        test_mode = bool(getattr(settings, "FACEBOOK_CAPI_TEST_MODE", False))
        test_code = getattr(settings, "FACEBOOK_TEST_EVENT_CODE", "") or ""

        body = {
            "data": [event_payload]
        }

        if test_mode and isinstance(test_code, str) and test_code.strip():
            body["test_event_code"] = test_code.strip()

        network = evt.get("network") or {}
        fbc_value = network.get("fbc")
        fbclid_value = network.get("fbclid")
        if fbc_value:
            fbc_source = "cookie"
        elif fbclid_value:
            fbc_source = "derived"
        else:
            fbc_source = "missing"
        logger.info(
            "Meta CAPI payload identifiers",
            extra={
                "event_name": name,
                "event_id": evt.get("event_id"),
                "fbclid": fbclid_value,
                "fbc": fbc_value,
                "fbp": network.get("fbp"),
                "fbc_source": fbc_source,
            },
        )

        return post_json(self.url, json=body, params={"access_token": self.token})
