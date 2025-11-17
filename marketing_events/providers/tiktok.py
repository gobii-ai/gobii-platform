from datetime import datetime, timezone

from django.conf import settings

from .base import post_json


class TikTokCAPI:
    url = "https://business-api.tiktok.com/open_api/v1.3/pixel/track/"

    def __init__(self, pixel_id: str, token: str):
        self.pixel_id = pixel_id
        self.token = token

    def _map_event_name(self, name: str) -> str:
        # pass-through; customize mapping if TikTok naming diverges
        return name

    @staticmethod
    def _format_timestamp(ts: int | float | str) -> str:
        if isinstance(ts, str):
            ts = float(ts)
        return (
            datetime.fromtimestamp(float(ts), tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _build_context(evt: dict) -> dict:
        network = evt.get("network") or {}
        context = {}
        page_url = network.get("page_url")
        if page_url:
            context["page"] = {"url": page_url}
        ip = network.get("client_ip")
        if ip:
            context["ip"] = ip
        user_agent = network.get("user_agent")
        if user_agent:
            context["user_agent"] = user_agent
        ttclid = network.get("ttclid")
        if ttclid:
            context["ad"] = {"callback": ttclid}
        return context

    @staticmethod
    def _build_user(evt: dict) -> dict:
        ids = evt.get("ids") or {}
        user = {}
        if ids.get("external_id"):
            user["external_id"] = [ids["external_id"]]
        if ids.get("em"):
            user["email"] = [ids["em"]]
        if ids.get("ph"):
            user["phone_number"] = [ids["ph"]]
        return user

    def send(self, evt: dict):
        if not evt.get("consent", True):
            return

        headers = {
            "Access-Token": self.token,
            "Content-Type": "application/json",
        }

        properties = (evt.get("properties") or {}).copy()
        properties.pop("event_time", None)
        properties.pop("event_id", None)

        payload = {
            "pixel_code": self.pixel_id,
            "event": self._map_event_name(evt["event_name"]),
            "event_id": evt["event_id"],
            "timestamp": self._format_timestamp(evt["event_time"]),
            "context": self._build_context(evt),
            "properties": properties,
            "user": self._build_user(evt),
            "event_source": "PIXEL_EVENTS",
            "event_channel": "web",
        }

        if not payload["properties"]:
            payload.pop("properties")
        if not payload["context"]:
            payload.pop("context")
        if not payload["user"]:
            payload.pop("user")

        test_mode = bool(getattr(settings, "TIKTOK_CAPI_TEST_MODE", False))
        test_code = getattr(settings, "TIKTOK_TEST_EVENT_CODE", "") or ""
        if test_mode and isinstance(test_code, str) and test_code.strip():
            payload["test_event_code"] = test_code.strip()

        return post_json(self.url, json=payload, headers=headers)
