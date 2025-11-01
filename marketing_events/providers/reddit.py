from datetime import datetime, timezone

from .base import post_json


class RedditCAPI:
    def __init__(self, ad_account: str, token: str):
        self.ad_account = ad_account
        self.token = token
        # https://ads-api.reddit.com/api/v3/pixels/a2_hb27sv7t5pa6/conversion_events
        self.url = f"https://ads-api.reddit.com/api/v3/pixels/{ad_account}/conversion_events"

    def _map_event_name(self, name: str) -> str:
        # map internal names to Reddit's expected names when applicable
        mapping = {
            "CompleteRegistration": "SignUp",
            "Subscribe": "Purchase",
        }
        return mapping.get(name, name)

    def send(self, evt: dict):
        if not evt.get("consent", True):
            return
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        custom_event = False
        name = self._map_event_name(evt["event_name"])

        if name is None:
            custom_event = True
            name = evt["event_name"]

        event_at = evt["event_time"]

        match_keys = {}
        if evt["ids"]["em"]:
            match_keys["email"] = evt["ids"]["em"]
        if evt["ids"]["ph"]:
            match_keys["phone"] = evt["ids"]["ph"]
        if evt["ids"]["external_id"]:
            match_keys["external_id"] = evt["ids"]["external_id"]
        if evt["network"]["rdt_cid"]:
            match_keys["rdt_click_id"] = evt["network"]["rdt_cid"]
        if evt["network"]["client_ip"]:
            match_keys["ip_address"] = evt["network"]["client_ip"]

        metadata = {
            key: value
            for key, value in (evt["properties"] or {}).items()
            if value not in (None, "", [])
        }
        metadata.pop("event_time", None)
        metadata.pop("event_id", None)

        body = {
            "event_at": event_at,
            "test_mode": bool((evt["properties"] or {}).get("test_mode", False)),
            "type": {}
        }

        if custom_event:
            body["type"]["tracking_type"] = "CUSTOM"
            body["type"]["custom_event_name"] = name
        else:
            body["type"]["tracking_type"] = name

        if match_keys:
            body["match_keys"] = match_keys
        if metadata:
            body["event_metadata"] = metadata

        body = {
            "data": {
                "events": [body],
                "metadata": {
                    "conversion_id": evt["event_id"],
                }
            }
        }

        response = post_json(self.url, json=body, headers=headers)
        return response
