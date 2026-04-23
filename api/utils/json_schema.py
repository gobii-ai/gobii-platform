"""Utilities for normalizing JSON schemas sent to LLM providers."""

import json
from typing import Any, Dict, Optional

_JSON_SCHEMA_TYPE_NAMES = frozenset({
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
})
_INFERRED_REQUIRED_PROPERTY_DESCRIPTION = (
    "Required parameter inferred from schema.required because no explicit property definition was provided."
)


def _normalize_json_schema_type_value(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _JSON_SCHEMA_TYPE_NAMES:
            return lowered
        return value

    if isinstance(value, list):
        normalized: list[Any] = []
        changed = False
        for item in value:
            normalized_item = _normalize_json_schema_type_value(item)
            if normalized_item != item:
                changed = True
            normalized.append(normalized_item)
        if changed:
            return normalized
    return value


def _normalize_json_schema_key(key: Any) -> Any:
    if not isinstance(key, str):
        return key

    stripped = key.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, str):
        return parsed

    if stripped.startswith('"') or stripped.endswith('"'):
        return stripped.strip('"')
    return key


def _normalize_json_schema_keys(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_json_schema_keys(item) for item in value]

    if not isinstance(value, dict):
        return value

    normalized: Dict[Any, Any] = {}
    for key, item in value.items():
        normalized_key = _normalize_json_schema_key(key)
        normalized_item = _normalize_json_schema_keys(item)
        existing = normalized.get(normalized_key)
        if isinstance(existing, dict) and isinstance(normalized_item, dict):
            normalized[normalized_key] = {**existing, **normalized_item}
        elif isinstance(existing, dict):
            continue
        elif isinstance(normalized_item, dict):
            normalized[normalized_key] = normalized_item
        else:
            normalized[normalized_key] = normalized_item
    return normalized


def _schema_type_includes(schema: Dict[str, Any], schema_type: str) -> bool:
    value = schema.get("type")
    if isinstance(value, str):
        return value == schema_type
    if isinstance(value, list):
        return schema_type in value
    return False


def _property_schema_from_value(value: Any) -> Any:
    if isinstance(value, str):
        return {"type": "string", "description": value}
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return _normalize_json_schema_node(value)
    return {"type": "string"}


def _normalize_json_schema_node(value: Any) -> Any:
    if isinstance(value, list):
        normalized_items: list[Any] = []
        changed = False
        for item in value:
            normalized_item = _normalize_json_schema_node(item)
            if normalized_item != item:
                changed = True
            normalized_items.append(normalized_item)
        if changed:
            return normalized_items
        return value

    if not isinstance(value, dict):
        return value

    schema = dict(value)

    if "type" in schema:
        normalized_type = _normalize_json_schema_type_value(schema["type"])
        if normalized_type != schema["type"]:
            schema["type"] = normalized_type

    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["properties"] = {
            key: _property_schema_from_value(property_schema)
            for key, property_schema in properties.items()
        }

    items = schema.get("items")
    if isinstance(items, (dict, list)):
        schema["items"] = _normalize_json_schema_node(items)
    elif _schema_type_includes(schema, "array"):
        schema["items"] = {"type": "string"}

    additional_properties = schema.get("additionalProperties")
    if isinstance(additional_properties, dict):
        schema["additionalProperties"] = _normalize_json_schema_node(additional_properties)

    for combinator in ("oneOf", "anyOf", "allOf"):
        variants = schema.get(combinator)
        if isinstance(variants, list):
            schema[combinator] = [_normalize_json_schema_node(variant) for variant in variants]

    properties = schema.get("properties")
    required = schema.get("required")
    if isinstance(required, list) and (properties is None or isinstance(properties, dict)):
        if properties is None:
            properties = {}
            schema["properties"] = properties
        for required_name in required:
            if isinstance(required_name, str) and required_name not in properties:
                properties[required_name] = {
                    "type": "string",
                    "description": _INFERRED_REQUIRED_PROPERTY_DESCRIPTION,
                }

    return schema


def normalize_parameters_schema(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    schema = _normalize_json_schema_node(_normalize_json_schema_keys(value))
    schema_type = schema.get("type")
    if schema_type in (None, ""):
        schema["type"] = "object"
    elif schema_type != "object":
        return None
    properties = schema.get("properties")
    if properties is None:
        schema["properties"] = {}
    elif not isinstance(properties, dict):
        return None
    required = schema.get("required")
    if required is None:
        schema["required"] = []
    elif not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return None
    else:
        normalized_required: list[str] = []
        seen: set[str] = set()
        for item in required:
            if item in seen:
                continue
            seen.add(item)
            normalized_required.append(item)
        schema["required"] = normalized_required
    return schema


def sanitize_tool_parameters_schema_for_llm(value: Any) -> Dict[str, Any]:
    normalized = normalize_parameters_schema(value)
    if normalized is None:
        return {"type": "object", "properties": {}, "required": []}
    return normalized
