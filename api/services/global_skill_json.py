import json
from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils.text import get_valid_filename

from api.models import GlobalAgentSkill, GlobalAgentSkillCustomTool


REQUIRED_SKILL_JSON_FIELDS = (
    "name",
    "description",
    "tools",
    "secrets",
    "instructions",
    "custom_tools",
)
REQUIRED_CUSTOM_TOOL_JSON_FIELDS = (
    "name",
    "tool_name",
    "description",
    "parameters_schema",
    "timeout_seconds",
    "source_code",
)


def _read_bundled_custom_tool_source(tool: GlobalAgentSkillCustomTool) -> str:
    if not tool.source_file:
        raise ValidationError({"source_file": ["Bundled custom tool is missing source_file."]})

    try:
        with tool.source_file.open("rb") as source_file:
            raw = source_file.read()
    except OSError as exc:
        raise ValidationError({"source_file": [f"Failed to read bundled custom tool source: {exc}"]}) from exc

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError({"source_file": ["Bundled custom tool source must be UTF-8 text."]}) from exc


def serialize_global_skill_to_dict(skill: GlobalAgentSkill) -> dict:
    custom_tools = []
    for tool in skill.bundled_custom_tools.order_by("tool_name"):
        custom_tools.append(
            {
                "name": tool.name,
                "tool_name": tool.tool_name,
                "description": tool.description,
                "parameters_schema": tool.parameters_schema,
                "timeout_seconds": tool.timeout_seconds,
                "source_code": _read_bundled_custom_tool_source(tool),
            }
        )

    return {
        "name": skill.name,
        "description": skill.description,
        "tools": list(skill.tools),
        "secrets": list(skill.secrets),
        "instructions": skill.instructions,
        "custom_tools": custom_tools,
    }


def serialize_global_skill_to_json_bytes(skill: GlobalAgentSkill) -> bytes:
    payload = serialize_global_skill_to_dict(skill)
    return json.dumps(payload, indent=2).encode("utf-8")


def parse_global_skill_json_bytes(raw_bytes: bytes) -> dict:
    if not raw_bytes:
        raise ValidationError({"json_file": ["Uploaded file is empty."]})

    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError({"json_file": ["JSON file must be UTF-8 text."]}) from exc

    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValidationError({"json_file": [f"Invalid JSON: {exc.msg}."]}) from exc

    if not isinstance(payload, Mapping):
        raise ValidationError({"json_file": ["JSON root must be an object."]})

    return dict(payload)


def _validate_required_top_level_fields(payload: Mapping[str, object]) -> None:
    missing = [field for field in REQUIRED_SKILL_JSON_FIELDS if field not in payload]
    if missing:
        raise ValidationError({"json_file": [f"Missing required field(s): {', '.join(missing)}."]})


def _coerce_skill_payload(payload: Mapping[str, object]) -> dict:
    _validate_required_top_level_fields(payload)

    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    instructions = str(payload.get("instructions") or "").strip()
    tools = payload.get("tools")
    secrets = payload.get("secrets")
    custom_tools = payload.get("custom_tools")

    errors: dict[str, list[str]] = {}
    if not name:
        errors["name"] = ["name is required."]
    if not instructions:
        errors["instructions"] = ["instructions is required."]
    if not isinstance(tools, list):
        errors["tools"] = ["tools must be a JSON array."]
    if not isinstance(secrets, list):
        errors["secrets"] = ["secrets must be a JSON array."]
    if not isinstance(custom_tools, list):
        errors["custom_tools"] = ["custom_tools must be a JSON array."]
    if errors:
        raise ValidationError(errors)

    return {
        "name": name,
        "description": description,
        "instructions": instructions,
        "tools": list(tools),
        "secrets": list(secrets),
        "custom_tools": list(custom_tools),
    }


def _validate_and_normalize_custom_tools(
    *,
    skill: GlobalAgentSkill,
    raw_custom_tools: list[object],
) -> list[dict]:
    normalized_tools: list[dict] = []
    seen_tool_names: set[str] = set()

    for index, raw_tool in enumerate(raw_custom_tools, start=1):
        if not isinstance(raw_tool, Mapping):
            raise ValidationError({"custom_tools": [f"custom_tools[{index}] must be an object."]})

        missing = [field for field in REQUIRED_CUSTOM_TOOL_JSON_FIELDS if field not in raw_tool]
        if missing:
            raise ValidationError(
                {"custom_tools": [f"custom_tools[{index}] is missing required field(s): {', '.join(missing)}."]}
            )

        source_code = raw_tool.get("source_code")
        if not isinstance(source_code, str) or not source_code:
            raise ValidationError({"custom_tools": [f"custom_tools[{index}].source_code must be a non-empty string."]})

        upload_name = get_valid_filename(str(raw_tool.get("tool_name") or raw_tool.get("name") or "custom_tool")) or "custom_tool"
        upload = SimpleUploadedFile(
            f"{upload_name}.py",
            source_code.encode("utf-8"),
            content_type="text/x-python",
        )
        tool = GlobalAgentSkillCustomTool(
            global_skill=skill,
            name=str(raw_tool.get("name") or "").strip(),
            tool_name=str(raw_tool.get("tool_name") or "").strip(),
            description=str(raw_tool.get("description") or "").strip(),
            source_file=upload,
            parameters_schema=raw_tool.get("parameters_schema"),
            timeout_seconds=raw_tool.get("timeout_seconds"),
        )
        tool.full_clean(validate_unique=False, validate_constraints=False)

        if tool.tool_name in seen_tool_names:
            raise ValidationError({"custom_tools": [f"Duplicate custom tool tool_name '{tool.tool_name}' in import file."]})
        seen_tool_names.add(tool.tool_name)

        normalized_tools.append(
            {
                "name": tool.name,
                "tool_name": tool.tool_name,
                "description": tool.description,
                "parameters_schema": tool.parameters_schema,
                "timeout_seconds": tool.timeout_seconds,
                "source_code": source_code,
            }
        )

    return normalized_tools


@transaction.atomic
def import_global_skill_from_payload(payload: Mapping[str, object]) -> tuple[GlobalAgentSkill, bool]:
    normalized_payload = _coerce_skill_payload(payload)
    existing_skill = (
        GlobalAgentSkill.objects.prefetch_related("bundled_custom_tools")
        .filter(name=normalized_payload["name"])
        .first()
    )
    created = existing_skill is None

    skill = existing_skill or GlobalAgentSkill(name=normalized_payload["name"])
    skill.description = normalized_payload["description"]
    skill.tools = normalized_payload["tools"]
    skill.secrets = normalized_payload["secrets"]
    skill.instructions = normalized_payload["instructions"]
    skill.full_clean()
    skill.save()

    validated_custom_tools = _validate_and_normalize_custom_tools(
        skill=skill,
        raw_custom_tools=normalized_payload["custom_tools"],
    )

    existing_tools_by_name = {
        tool.tool_name: tool
        for tool in skill.bundled_custom_tools.all()
    }
    desired_tool_names = {tool["tool_name"] for tool in validated_custom_tools}

    for tool_name, existing_tool in existing_tools_by_name.items():
        if tool_name not in desired_tool_names:
            existing_tool.delete()

    for tool_payload in validated_custom_tools:
        tool = existing_tools_by_name.get(tool_payload["tool_name"])
        if tool is None:
            tool = GlobalAgentSkillCustomTool(global_skill=skill)

        tool.name = tool_payload["name"]
        tool.tool_name = tool_payload["tool_name"]
        tool.description = tool_payload["description"]
        tool.parameters_schema = tool_payload["parameters_schema"]
        tool.timeout_seconds = tool_payload["timeout_seconds"]
        tool.source_file.save(
            f"{tool.tool_name}.py",
            ContentFile(tool_payload["source_code"].encode("utf-8")),
            save=False,
        )
        tool.full_clean()
        tool.save()

    return skill, created
