from typing import Any

from api.agent.tools.skill_utils import (
    format_skill_secret_requirement,
    normalize_skill_secret_requirements,
)
from api.models import GlobalAgentSkill, GlobalSecret

GLOBAL_SKILL_EVAL_SUITE_SLUG = "global_skill_eval"
GLOBAL_SKILL_EVAL_SCENARIO_SLUG = "global_skill_eval"
GLOBAL_SKILL_EVAL_RUBRIC_VERSION = "v1"


def global_skill_eval_global_secrets_queryset(owner_user, owner_org):
    if owner_org is not None:
        return GlobalSecret.objects.filter(organization=owner_org)
    return GlobalSecret.objects.filter(user=owner_user, organization__isnull=True)


def build_global_skill_eval_secret_status(
    skill: GlobalAgentSkill,
    *,
    owner_user,
    owner_org,
) -> dict[str, Any]:
    normalized_secrets = list(normalize_skill_secret_requirements(skill.secrets))
    global_secrets = list(
        global_skill_eval_global_secrets_queryset(owner_user, owner_org).order_by("created_at", "id")
    )

    env_secret_ids = {
        secret.key: str(secret.id)
        for secret in global_secrets
        if secret.secret_type == GlobalSecret.SecretType.ENV_VAR
    }
    credential_secret_ids = {
        (secret.key, secret.domain_pattern): str(secret.id)
        for secret in global_secrets
        if secret.secret_type == GlobalSecret.SecretType.CREDENTIAL
    }

    required_secret_status: list[dict[str, Any]] = []
    missing_required_secrets: list[str] = []

    for secret in normalized_secrets:
        label = format_skill_secret_requirement(secret)
        matched_secret_id: str | None = None
        if secret["secret_type"] == GlobalSecret.SecretType.ENV_VAR:
            matched_secret_id = env_secret_ids.get(secret["key"])
        else:
            matched_secret_id = credential_secret_ids.get((secret["key"], secret["domain_pattern"]))

        status = "available" if matched_secret_id else "missing"
        if not matched_secret_id:
            missing_required_secrets.append(label)

        required_secret_status.append(
            {
                "label": label,
                "name": secret["name"],
                "key": secret["key"],
                "secret_type": secret["secret_type"],
                "domain_pattern": secret.get("domain_pattern"),
                "description": secret.get("description", ""),
                "status": status,
                "matched_secret_id": matched_secret_id,
            }
        )

    return {
        "required_secrets": normalized_secrets,
        "required_secret_status": required_secret_status,
        "missing_required_secrets": missing_required_secrets,
        "launchable": not missing_required_secrets,
    }


def serialize_global_skill_eval_skill(
    skill: GlobalAgentSkill,
    *,
    owner_user,
    owner_org,
) -> dict[str, Any]:
    secret_status = build_global_skill_eval_secret_status(
        skill,
        owner_user=owner_user,
        owner_org=owner_org,
    )
    return {
        "id": str(skill.id),
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "effective_tool_ids": list(skill.get_effective_tool_ids()),
        "required_secrets": secret_status["required_secrets"],
        "required_secret_status": secret_status["required_secret_status"],
        "missing_required_secrets": secret_status["missing_required_secrets"],
        "launchable": secret_status["launchable"],
    }


def build_skill_eval_summary(launch_config: dict[str, Any] | None) -> dict[str, Any] | None:
    config = launch_config or {}
    skill_name = str(config.get("global_skill_name") or "").strip()
    if not skill_name:
        return None

    return {
        "global_skill_id": config.get("global_skill_id"),
        "global_skill_name": skill_name,
        "task_prompt": config.get("task_prompt") or "",
        "rubric_version": config.get("rubric_version") or GLOBAL_SKILL_EVAL_RUBRIC_VERSION,
        "required_secret_status": list(config.get("required_secret_status") or []),
        "effective_tool_ids": list(config.get("effective_tool_ids") or []),
    }
