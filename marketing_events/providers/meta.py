from .base import post_json, TemporaryError, PermanentError


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
        body = {
            "data": [{
                "event_name": name,
                "event_time": evt["event_time"],
                "event_id": evt["event_id"],
                "action_source": "website",
                "event_source_url": evt["network"]["page_url"],
                "user_data": user_data,
                "custom_data": evt["properties"] or {},
            }]
        }
        return post_json(self.url, json=body, params={"access_token": self.token})
