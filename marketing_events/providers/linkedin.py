from decimal import Decimal, InvalidOperation

from .base import post_json


LINKEDIN_CONVERSION_URN_PREFIX = "urn:lla:llaPartnerConversion:"


class LinkedInCAPI:
    url = "https://api.linkedin.com/rest/conversionEvents"

    def __init__(self, token: str, conversion_ids: dict[str, str], api_version: str):
        self.token = token
        self.conversion_urns = {
            str(event_name): self._normalize_conversion_urn(conversion_id)
            for event_name, conversion_id in (conversion_ids or {}).items()
            if conversion_id
        }
        self.api_version = api_version

    @staticmethod
    def _normalize_conversion_urn(value: object) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        if not candidate:
            return None
        if candidate.startswith(LINKEDIN_CONVERSION_URN_PREFIX):
            return candidate
        return f"{LINKEDIN_CONVERSION_URN_PREFIX}{candidate}"

    @staticmethod
    def _to_millis(ts: int | float | str) -> int:
        if isinstance(ts, str):
            ts = float(ts)
        ts = int(ts)
        return ts if ts >= 10**12 else ts * 1000

    @staticmethod
    def _amount_string(value: object) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return format(amount, "f")

    @staticmethod
    def _conversion_value(evt: dict) -> dict | None:
        properties = evt.get("properties") or {}
        raw_value = properties.get("transaction_value") if evt.get("event_name") == "Subscribe" else None
        if raw_value in (None, ""):
            raw_value = properties.get("value")

        amount = LinkedInCAPI._amount_string(raw_value)
        if amount is None:
            return None

        currency = str(properties.get("currency") or "USD").strip().upper()
        if not currency:
            currency = "USD"
        return {"currencyCode": currency, "amount": amount}

    @staticmethod
    def _user_ids(evt: dict) -> list[dict]:
        ids = evt.get("ids") or {}
        network = evt.get("network") or {}
        user_ids = []

        if ids.get("em"):
            user_ids.append({"idType": "SHA256_EMAIL", "idValue": ids["em"]})

        li_fat_id = network.get("li_fat_id")
        if li_fat_id:
            user_ids.append(
                {
                    "idType": "LINKEDIN_FIRST_PARTY_ADS_TRACKING_UUID",
                    "idValue": li_fat_id,
                }
            )

        return user_ids

    def send(self, evt: dict):
        if not evt.get("consent", True):
            return False

        conversion_urn = self.conversion_urns.get(evt.get("event_name"))
        if not conversion_urn:
            return False

        user_ids = self._user_ids(evt)
        if not user_ids:
            return False

        payload = {
            "conversion": conversion_urn,
            "conversionHappenedAt": self._to_millis(evt["event_time"]),
            "user": {"userIds": user_ids},
            "eventId": evt["event_id"],
        }

        conversion_value = self._conversion_value(evt)
        if conversion_value:
            payload["conversionValue"] = conversion_value

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Linkedin-Version": self.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        }

        return post_json(self.url, json=payload, headers=headers)
