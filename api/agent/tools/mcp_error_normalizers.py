import json
from typing import Any, Optional


class MCPToolErrorNormalizer:
    """Base normalizer for turning noisy MCP tool errors into agent-facing errors."""

    server_name: Optional[str] = None
    tool_name_prefix: Optional[str] = None

    def matches(self, server_name: str, tool_name: str) -> bool:
        if self.server_name is not None and self.server_name != server_name:
            return False
        if self.tool_name_prefix is not None and not str(tool_name or "").startswith(self.tool_name_prefix):
            return False
        return True

    def normalize(self, tool_name: str, message: str) -> Optional[dict[str, Any]]:
        return None


def _iter_nested_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_nested_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nested_dicts(child)


def _extract_http_status_code(payload: Any) -> Optional[int]:
    for node in _iter_nested_dicts(payload):
        for key in ("status", "statusCode", "code"):
            raw_value = node.get(key)
            if isinstance(raw_value, bool):
                continue
            try:
                value = int(str(raw_value))
            except (TypeError, ValueError):
                continue
            if 100 <= value <= 599:
                return value
    return None


def _google_client_retried_request(payload: Any) -> bool:
    for node in _iter_nested_dicts(payload):
        retry_config = node.get("retryConfig")
        if not isinstance(retry_config, dict):
            continue
        current_attempt = retry_config.get("currentRetryAttempt")
        retry_limit = retry_config.get("retry")
        try:
            current_attempt_int = int(current_attempt)
            retry_limit_int = int(retry_limit)
        except (TypeError, ValueError):
            return True
        if current_attempt_int >= retry_limit_int:
            return True
    return False


class PipedreamGoogleSheetsErrorNormalizer(MCPToolErrorNormalizer):
    server_name = "pipedream"
    tool_name_prefix = "google_sheets-"

    def normalize(self, tool_name: str, message: str) -> Optional[dict[str, Any]]:
        if not isinstance(message, str) or not message.strip():
            return None

        payload = None
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            payload = None

        status_code = _extract_http_status_code(payload) if payload is not None else None
        if status_code in {401, 403}:
            return {
                "status": "error",
                "message": "Google Sheets authorization failed. Reconnect Google Sheets or check sheet permissions.",
                "status_code": status_code,
                "retryable": False,
            }
        if status_code == 404:
            return {
                "status": "error",
                "message": "Google Sheets could not find the requested spreadsheet, sheet, or range.",
                "status_code": status_code,
                "retryable": False,
            }
        if status_code == 429 or (status_code is not None and status_code >= 500):
            return {
                "status": "error",
                "message": (
                    "Google Sheets is temporarily unavailable or rate limited. "
                    "Retry the request, preferably with a narrower range."
                ),
                "status_code": status_code,
                "retryable": True,
            }

        lower_message = message.lower()
        if (
            "google-api-nodejs-client" in lower_message
            and ("retryconfig" in lower_message or "retry_config" in lower_message)
        ) or (payload is not None and _google_client_retried_request(payload)):
            retry_message = (
                "Google Sheets request failed after upstream retries. Retry the request, preferably with a narrower range."
            )
            if tool_name == "google_sheets-read-rows":
                retry_message = (
                    "Google Sheets read failed after upstream retries. Retry the request, narrow the range, "
                    "or use google_sheets-get-values-in-range."
                )
            return {
                "status": "error",
                "message": retry_message,
                "retryable": True,
            }

        return None


class MCPErrorNormalizerRegistry:
    def __init__(self, normalizers: Optional[list[MCPToolErrorNormalizer]] = None):
        self._normalizers = list(normalizers or [])

    @classmethod
    def default(cls) -> "MCPErrorNormalizerRegistry":
        return cls([PipedreamGoogleSheetsErrorNormalizer()])

    def normalize(self, server_name: str, tool_name: str, message: str) -> Optional[dict[str, Any]]:
        for normalizer in self._normalizers:
            if normalizer.matches(server_name, tool_name):
                result = normalizer.normalize(tool_name, message)
                if result is not None:
                    return result
        return None
