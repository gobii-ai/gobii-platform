import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, ProgrammingError


logger = logging.getLogger(__name__)

_CACHE_KEY = "system_settings:v1"
_CACHE_TTL_SECONDS = 300

VALUE_TYPE_INT = "int"
VALUE_TYPE_FLOAT = "float"
VALUE_TYPE_BOOL = "bool"


@dataclass(frozen=True)
class SystemSettingDefinition:
    key: str
    label: str
    description: str
    value_type: str
    env_var: str
    default_getter: Callable[[], int | float | bool]
    category: str
    unit: str | None = None
    min_value: int | float | None = None
    disable_value: int | float | None = None

    def coerce(self, value: Any) -> int | float | bool:
        if self.value_type == VALUE_TYPE_INT:
            coerced = _coerce_int(value)
        elif self.value_type == VALUE_TYPE_FLOAT:
            coerced = _coerce_float(value)
        elif self.value_type == VALUE_TYPE_BOOL:
            coerced = _coerce_bool(value)
        else:
            raise ValueError(f"Unsupported value type: {self.value_type}")
        if self.disable_value is not None and coerced == self.disable_value:
            return coerced
        if self.min_value is not None and isinstance(coerced, (int, float)) and coerced < self.min_value:
            raise ValueError(f"Value must be at least {self.min_value}.")
        return coerced


SYSTEM_SETTING_DEFINITIONS = (
    SystemSettingDefinition(
        key="MAX_FILE_SIZE",
        label="Max file size",
        description="Maximum file size allowed for uploads and downloads.",
        value_type=VALUE_TYPE_INT,
        env_var="MAX_FILE_SIZE",
        default_getter=lambda: settings.MAX_FILE_SIZE,
        category="Files",
        unit="bytes",
        min_value=1,
        disable_value=-1,
    ),
    SystemSettingDefinition(
        key="MCP_HTTP_REQUEST_TIMEOUT_SECONDS",
        label="MCP HTTP timeout",
        description="Default timeout for MCP HTTP tool execution.",
        value_type=VALUE_TYPE_FLOAT,
        env_var="MCP_HTTP_REQUEST_TIMEOUT_SECONDS",
        default_getter=lambda: settings.MCP_HTTP_REQUEST_TIMEOUT_SECONDS,
        category="MCP",
        unit="seconds",
        min_value=0.1,
    ),
    SystemSettingDefinition(
        key="MCP_STDIO_REQUEST_TIMEOUT_SECONDS",
        label="MCP stdio timeout",
        description="Default timeout for MCP stdio tool execution.",
        value_type=VALUE_TYPE_FLOAT,
        env_var="MCP_STDIO_REQUEST_TIMEOUT_SECONDS",
        default_getter=lambda: settings.MCP_STDIO_REQUEST_TIMEOUT_SECONDS,
        category="MCP",
        unit="seconds",
        min_value=0.1,
    ),
    SystemSettingDefinition(
        key="LITELLM_TIMEOUT_SECONDS",
        label="LiteLLM timeout",
        description="Default timeout for LiteLLM requests.",
        value_type=VALUE_TYPE_INT,
        env_var="LITELLM_TIMEOUT_SECONDS",
        default_getter=lambda: settings.LITELLM_TIMEOUT_SECONDS,
        category="LLM",
        unit="seconds",
        min_value=1,
    ),
    SystemSettingDefinition(
        key="ACCOUNT_ALLOW_REGISTRATION",
        label="Allow account signup",
        description="Allow new users to create accounts.",
        value_type=VALUE_TYPE_BOOL,
        env_var="ACCOUNT_ALLOW_REGISTRATION",
        default_getter=lambda: settings.ACCOUNT_ALLOW_REGISTRATION,
        category="Accounts",
    ),
)

SYSTEM_SETTING_DEFINITIONS_BY_KEY = {definition.key: definition for definition in SYSTEM_SETTING_DEFINITIONS}


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Value must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("Value must be a whole number.")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Value cannot be empty.")
        return int(text)
    raise ValueError("Value must be an integer.")


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Value must be a number.")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Value cannot be empty.")
        return float(text)
    raise ValueError("Value must be a number.")


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        raise ValueError("Value must be true or false.")
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "y", "on"):
            return True
        if text in ("false", "0", "no", "n", "off"):
            return False
        raise ValueError("Value must be true or false.")
    raise ValueError("Value must be true or false.")


def _get_system_setting_model():
    from api.models import SystemSetting

    return SystemSetting


def _load_db_values() -> dict[str, str]:
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached

    try:
        SystemSetting = _get_system_setting_model()
        values = {setting.key: setting.value_text for setting in SystemSetting.objects.all().only("key", "value_text")}
    except (LookupError, ImproperlyConfigured, OperationalError, ProgrammingError) as exc:
        logger.warning("Failed to load system settings from database: %s", exc)
        return {}

    cache.set(_CACHE_KEY, values, _CACHE_TTL_SECONDS)
    return values


def invalidate_system_settings_cache() -> None:
    cache.delete(_CACHE_KEY)


def get_setting_definition(key: str) -> SystemSettingDefinition | None:
    return SYSTEM_SETTING_DEFINITIONS_BY_KEY.get(key)


def _parse_db_value(definition: SystemSettingDefinition, raw_value: str | None) -> int | float | bool | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, str) and not raw_value.strip():
        return None
    try:
        return definition.coerce(raw_value)
    except ValueError as exc:
        logger.warning("Invalid system setting value for %s: %s", definition.key, exc)
        return None


def _resolve_setting(
    definition: SystemSettingDefinition,
    db_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    if db_values is None:
        db_values = _load_db_values()
    raw_db_value = db_values.get(definition.key)
    db_value = _parse_db_value(definition, raw_db_value)

    env_set = definition.env_var in os.environ
    default_value = definition.default_getter()
    fallback_source = "env" if env_set else "default"
    if db_value is not None:
        effective_value = db_value
        source = "database"
    else:
        effective_value = default_value
        source = fallback_source

    return {
        "definition": definition,
        "db_value": db_value,
        "effective_value": effective_value,
        "source": source,
        "env_set": env_set,
        "fallback_value": default_value,
        "fallback_source": fallback_source,
    }


def serialize_setting(
    definition: SystemSettingDefinition,
    db_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    resolved = _resolve_setting(definition, db_values)
    return {
        "key": definition.key,
        "label": definition.label,
        "description": definition.description,
        "category": definition.category,
        "value_type": definition.value_type,
        "unit": definition.unit,
        "min_value": definition.min_value,
        "disable_value": definition.disable_value,
        "env_var": definition.env_var,
        "env_set": resolved["env_set"],
        "db_value": resolved["db_value"],
        "effective_value": resolved["effective_value"],
        "source": resolved["source"],
        "fallback_value": resolved["fallback_value"],
        "fallback_source": resolved["fallback_source"],
    }


def list_system_settings() -> list[dict[str, Any]]:
    db_values = _load_db_values()
    return [serialize_setting(definition, db_values) for definition in SYSTEM_SETTING_DEFINITIONS]


def get_setting_value(key: str) -> int | float | bool:
    definition = SYSTEM_SETTING_DEFINITIONS_BY_KEY.get(key)
    if definition is None:
        raise KeyError(f"Unknown system setting: {key}")
    resolved = _resolve_setting(definition)
    return resolved["effective_value"]


def set_setting_value(definition: SystemSettingDefinition, value: int | float) -> None:
    SystemSetting = _get_system_setting_model()
    setting, _ = SystemSetting.objects.get_or_create(key=definition.key)
    setting.value_text = str(value)
    setting.save(update_fields=["value_text", "updated_at"])


def clear_setting_value(definition: SystemSettingDefinition) -> None:
    SystemSetting = _get_system_setting_model()
    SystemSetting.objects.filter(key=definition.key).delete()


def get_max_file_size() -> int | None:
    value = int(get_setting_value("MAX_FILE_SIZE"))
    if value <= 0:
        return None
    return value


def get_mcp_http_timeout_seconds() -> float:
    return float(get_setting_value("MCP_HTTP_REQUEST_TIMEOUT_SECONDS"))


def get_mcp_stdio_timeout_seconds() -> float:
    return float(get_setting_value("MCP_STDIO_REQUEST_TIMEOUT_SECONDS"))


def get_litellm_timeout_seconds() -> int:
    return int(get_setting_value("LITELLM_TIMEOUT_SECONDS"))


def get_account_allow_registration() -> bool:
    return bool(get_setting_value("ACCOUNT_ALLOW_REGISTRATION"))
