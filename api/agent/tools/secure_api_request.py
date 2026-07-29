from typing import Any

from django.db import transaction
from django.utils import timezone

from api.models import DelegatedSecureValue, PersistentAgent
from api.services.delegated_secure_values import (
    DEFAULT_SECURE_VALUE_TTL_SECONDS,
    MAX_SECURE_VALUE_TTL_SECONDS,
    SecureValueError,
    create_delegated_secure_value,
)

from .http_request import execute_http_request


SECURE_API_REQUEST_TOOL_NAME = "secure_api_request"
SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL_KEY = "secure_credential_delegation"
MAX_SECURE_RESPONSE_ITEMS = 50
MAX_EXTRACTED_FIELDS = 12
_MISSING = object()
_SENSITIVE_PATH_TERMS = {
    "accesstoken",
    "apikey",
    "apppassword",
    "authorization",
    "clientsecret",
    "credential",
    "credentials",
    "otp",
    "otpcode",
    "password",
    "refreshtoken",
    "secret",
    "token",
}


def get_secure_api_request_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": SECURE_API_REQUEST_TOOL_NAME,
            "description": (
                "Call a JSON API and place selected secret response fields directly into short-lived encrypted "
                "handoff references. The raw response and secret values are never returned. Use JSON Pointer paths "
                "such as `/results`, `/address`, and `/appPassword`. Return only non-sensitive scalar fields through "
                "public_fields or root-level pagination metadata through response_fields. Page output distinguishes "
                "the provider's page from this tool's local item cap and never claims the provider is exhausted. Use "
                "this instead of http_request whenever a response contains credentials, passwords, tokens, OTPs, or "
                "other values that must be assigned to another Gobii."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP method, such as GET or POST."},
                    "url": {"type": "string", "description": "API URL. Secret placeholders are supported."},
                    "headers": {"type": "object", "description": "Request headers. Use secret placeholders for credentials."},
                    "body": {"description": "Optional JSON object, array, or string request body."},
                    "collection_pointer": {
                        "type": "string",
                        "default": "",
                        "description": "JSON Pointer to a response array. Leave empty for a root object or root array.",
                    },
                    "public_fields": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Safe output names mapped to scalar JSON Pointer paths relative to each item.",
                    },
                    "response_fields": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": (
                            "Safe output names mapped to scalar JSON Pointer paths relative to the response root. "
                            "Use for provider pagination fields such as total, offset, limit, cursor, or has_more."
                        ),
                    },
                    "secret_fields": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Secret labels mapped to scalar JSON Pointer paths relative to each item.",
                    },
                    "max_items": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SECURE_RESPONSE_ITEMS,
                        "default": MAX_SECURE_RESPONSE_ITEMS,
                    },
                    "ttl_seconds": {
                        "type": "integer",
                        "minimum": 60,
                        "maximum": 86400,
                        "default": DEFAULT_SECURE_VALUE_TTL_SECONDS,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true when another tool call or user-facing reply will follow.",
                    },
                },
                "required": [
                    "method",
                    "url",
                    "public_fields",
                    "secret_fields",
                    "will_continue_work",
                ],
                "additionalProperties": False,
            },
        },
    }


def execute_secure_api_request(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    DelegatedSecureValue.objects.filter(
        source_agent=agent,
        expires_at__lte=timezone.now(),
    ).delete()
    public_fields = _normalize_field_map(params.get("public_fields"))
    response_fields = _normalize_field_map(params.get("response_fields"))
    secret_fields = _normalize_field_map(params.get("secret_fields"))
    if not secret_fields:
        return {"status": "error", "message": "secret_fields must contain at least one JSON Pointer mapping."}
    if len(public_fields) + len(response_fields) + len(secret_fields) > MAX_EXTRACTED_FIELDS:
        return {
            "status": "error",
            "message": (
                f"At most {MAX_EXTRACTED_FIELDS} total public, response, and secret fields may be extracted."
            ),
        }

    public_error = _validate_public_mappings(public_fields, secret_fields)
    if public_error:
        return {"status": "error", "message": public_error}
    response_error = _validate_public_mappings(response_fields, secret_fields)
    if response_error:
        return {"status": "error", "message": response_error}

    try:
        max_items = int(params.get("max_items", MAX_SECURE_RESPONSE_ITEMS))
        ttl_seconds = int(params.get("ttl_seconds", DEFAULT_SECURE_VALUE_TTL_SECONDS))
    except (TypeError, ValueError):
        return {"status": "error", "message": "max_items and ttl_seconds must be integers."}
    if max_items < 1 or max_items > MAX_SECURE_RESPONSE_ITEMS:
        return {
            "status": "error",
            "message": f"max_items must be between 1 and {MAX_SECURE_RESPONSE_ITEMS}.",
        }
    if ttl_seconds < 60 or ttl_seconds > MAX_SECURE_VALUE_TTL_SECONDS:
        return {
            "status": "error",
            "message": f"ttl_seconds must be between 60 and {MAX_SECURE_VALUE_TTL_SECONDS}.",
        }

    request_result = execute_http_request(
        agent,
        {
            "method": params.get("method"),
            "url": params.get("url"),
            "headers": params.get("headers"),
            "body": params.get("body"),
            "will_continue_work": True,
        },
    )
    status_code = request_result.get("status_code")
    if request_result.get("status") != "ok" or not isinstance(status_code, int) or not 200 <= status_code < 300:
        return {
            "status": "error",
            "message": "Secure API request failed; no response body was exposed or stored.",
            "status_code": status_code,
            "retryable": bool(request_result.get("retryable")) or status_code == 429 or bool(status_code and status_code >= 500),
        }

    content = request_result.get("content")
    collection_pointer = str(params.get("collection_pointer") or "")
    collection = _resolve_json_pointer(content, collection_pointer)
    if collection is _MISSING:
        return {"status": "error", "message": "collection_pointer did not match the JSON response."}
    if isinstance(collection, list):
        source_items = collection[:max_items]
    elif collection_pointer:
        return {"status": "error", "message": "collection_pointer must resolve to a JSON array."}
    else:
        source_items = [collection]

    output_items: list[dict[str, Any]] = []
    try:
        provider_fields = _extract_public_fields(content, response_fields)
        with transaction.atomic():
            for index, source_item in enumerate(source_items):
                if not isinstance(source_item, dict):
                    raise SecureValueError("Each selected response item must be a JSON object.")
                output_item = _extract_public_fields(source_item, public_fields)
                secure_values: dict[str, str] = {}
                for label, pointer in secret_fields.items():
                    value = _resolve_json_pointer(source_item, pointer)
                    if value is _MISSING or value is None or value == "":
                        continue
                    if isinstance(value, (dict, list)):
                        raise SecureValueError(f"Secret field `{label}` must resolve to a scalar value.")
                    secure_values[label] = create_delegated_secure_value(
                        agent,
                        label=label,
                        value=str(value),
                        ttl_seconds=ttl_seconds,
                    )
                output_item["secure_values"] = secure_values
                output_item["item_index"] = index
                output_items.append(output_item)
    except SecureValueError as exc:
        return {"status": "error", "message": str(exc)}

    return {
        "status": "ok",
        "status_code": status_code,
        "items": output_items,
        "page": {
            "provider_item_count": len(collection) if isinstance(collection, list) else 1,
            "returned_item_count": len(output_items),
            "locally_truncated": isinstance(collection, list) and len(collection) > max_items,
            "provider_completeness": "unknown",
            "provider_fields": provider_fields,
        },
        "expires_in_seconds": ttl_seconds,
    }


def _normalize_field_map(raw_value: Any) -> dict[str, str]:
    if not isinstance(raw_value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_label, raw_pointer in raw_value.items():
        label = str(raw_label or "").strip()
        pointer = str(raw_pointer or "")
        if not label or len(label) > 128:
            continue
        if pointer and not pointer.startswith("/"):
            continue
        normalized[label] = pointer
    return normalized


def _validate_public_mappings(public_fields: dict[str, str], secret_fields: dict[str, str]) -> str:
    secret_paths = set(secret_fields.values())
    for label, pointer in public_fields.items():
        if pointer in secret_paths:
            return f"Public field `{label}` overlaps a secret field path."
        normalized_terms = {
            "".join(character for character in term.lower() if character.isalnum())
            for term in [label, *_pointer_parts(pointer)]
        }
        if any(
            sensitive_term in normalized_term
            for normalized_term in normalized_terms
            for sensitive_term in _SENSITIVE_PATH_TERMS
        ):
            return f"Public field `{label}` looks sensitive; map it under secret_fields instead."
    return ""


def _extract_public_fields(source_item: dict[str, Any], public_fields: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, pointer in public_fields.items():
        value = _resolve_json_pointer(source_item, pointer)
        if value is _MISSING:
            result[label] = None
            continue
        if isinstance(value, (dict, list)):
            raise SecureValueError(f"Public field `{label}` must resolve to a scalar value.")
        result[label] = value
    return result


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer:
        return []
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _resolve_json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        return _MISSING

    current = document
    for part in _pointer_parts(pointer):
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return _MISSING
            if index < 0 or index >= len(current):
                return _MISSING
            current = current[index]
            continue
        return _MISSING
    return current
