import json
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse


class ApiLoginRequiredMixin(LoginRequiredMixin):
    """Return JSON 401 instead of redirecting to the login page."""

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required"}, status=401)
        return super().handle_no_permission()


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    return payload


def _json_error(message: str, *, status: int = 400, key: str = "error") -> JsonResponse:
    return JsonResponse({key: message}, status=status)


def _json_bad_request(message: str) -> HttpResponseBadRequest:
    return HttpResponseBadRequest(message)


def _permission_denied_response(exc: PermissionDenied) -> JsonResponse:
    messages = getattr(exc, "args", None)
    message = str(messages[0]) if messages else "Permission denied."
    return _json_error(message, status=403)


def _validation_error_payload(exc: ValidationError) -> dict[str, list[str]]:
    if hasattr(exc, "message_dict"):
        return {
            field: [str(message) for message in messages]
            for field, messages in exc.message_dict.items()
        }
    if hasattr(exc, "messages"):
        return {"__all__": [str(message) for message in exc.messages]}
    return {"__all__": [str(exc)]}


def _validation_error_response(exc: ValidationError, *, status: int = 400) -> JsonResponse:
    return JsonResponse({"errors": _validation_error_payload(exc)}, status=status)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
