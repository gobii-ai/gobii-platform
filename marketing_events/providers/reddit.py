from .base import post_json, TemporaryError, PermanentError


class RedditCAPI:
    def __init__(self, ad_account: str, token: str):
        self.ad_account = ad_account
        self.token = token
        self.url = "https://ads-api.reddit.com/api/v3/measurement/conversions"

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
        headers = {"Authorization": f"Bearer {self.token}"}
        name = self._map_event_name(evt["event_name"])
        ev = {
            "event_type": name,
            "event_id": evt["event_id"],
            "event_time": evt["event_time"],
            "properties": evt["properties"] or {},
            "client_ip_address": evt["network"]["client_ip"],
            "user_agent": evt["network"]["user_agent"],
            "click_id": evt["network"]["rdt_cid"],
            # Reddit can accept hashed identifiers; we'll pass hashed email if present
            "email": evt["ids"]["em"],
        }
        body = {"ad_account_id": self.ad_account, "events": [ev]}
        return post_json(self.url, json=body, headers=headers)
