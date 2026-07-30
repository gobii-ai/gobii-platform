import json

from django.http import HttpRequest


def parse_computer_json_payload(request: HttpRequest) -> dict:
    if len(request.body) > 64 * 1024:
        raise ValueError("Request body is too large")
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload
