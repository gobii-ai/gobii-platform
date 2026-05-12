import re
from collections.abc import Mapping
from typing import Any


_ENVIRONMENT_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_valid_environment_variable_name(name: Any) -> bool:
    return isinstance(name, str) and bool(_ENVIRONMENT_VARIABLE_NAME_RE.fullmatch(name))


def invalid_environment_variable_names(environment: Mapping[Any, Any]) -> list[str]:
    return [str(name) for name in environment if not is_valid_environment_variable_name(name)]


def environment_variables_with_non_string_values(environment: Mapping[Any, Any]) -> list[str]:
    return [str(name) for name, value in environment.items() if not isinstance(value, str)]


def format_invalid_environment_variable_names(names: list[str]) -> str:
    preview = ", ".join(repr(name) for name in names[:5])
    if len(names) > 5:
        preview = f"{preview}, ..."
    return (
        f"Invalid environment variable name(s): {preview}. "
        "Use letters, numbers, and underscores, start with a letter or underscore, "
        "and enter only the variable name, not KEY=value."
    )


def format_non_string_environment_variable_values(names: list[str]) -> str:
    preview = ", ".join(repr(name) for name in names[:5])
    if len(names) > 5:
        preview = f"{preview}, ..."
    return f"Environment variable value(s) must be strings for: {preview}."


def validate_environment_mapping(environment: Mapping[Any, Any]) -> list[str]:
    errors: list[str] = []
    invalid_names = invalid_environment_variable_names(environment)
    if invalid_names:
        errors.append(format_invalid_environment_variable_names(invalid_names))
    non_string_values = environment_variables_with_non_string_values(environment)
    if non_string_values:
        errors.append(format_non_string_environment_variable_values(non_string_values))
    return errors


def validate_mcp_metadata_environment_references(metadata: Mapping[Any, Any]) -> list[str]:
    errors: list[str] = []
    fallback_map = metadata.get("env_fallback")
    if fallback_map is not None:
        if not isinstance(fallback_map, Mapping):
            errors.append("metadata.env_fallback must be a JSON object.")
        else:
            invalid_names = invalid_environment_variable_names(fallback_map)
            if invalid_names:
                errors.append(
                    "metadata.env_fallback contains invalid MCP environment keys. "
                    f"{format_invalid_environment_variable_names(invalid_names)}"
                )
            invalid_fallback_env_names = [
                str(env_name)
                for env_name in fallback_map.values()
                if not is_valid_environment_variable_name(env_name)
            ]
            if invalid_fallback_env_names:
                errors.append(
                    "metadata.env_fallback contains invalid fallback environment variable names. "
                    f"{format_invalid_environment_variable_names(invalid_fallback_env_names)}"
                )

    fallback_zone_env = metadata.get("brightdata_search_fallback_zone_env")
    if fallback_zone_env is not None and not is_valid_environment_variable_name(fallback_zone_env):
        errors.append(
            "brightdata_search_fallback_zone_env must be an environment variable name like "
            "WEB_UNLOCKER_ZONE_FALLBACK."
        )
    return errors
