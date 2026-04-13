"""Helpers for owner-scoped system skill profiles."""

from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from api.agent.system_skills.registry import get_system_skill_definition
from api.models import PersistentAgent, SystemSkillProfile, SystemSkillProfileSecret


def resolve_system_skill_profile_owner_for_agent(agent: PersistentAgent):
    if agent.organization_id:
        return None, agent.organization
    return agent.user, None


def system_skill_profiles_queryset_for_agent(
    agent: PersistentAgent,
    *,
    skill_key: Optional[str] = None,
):
    owner_user, owner_org = resolve_system_skill_profile_owner_for_agent(agent)
    return system_skill_profiles_queryset_for_owner(owner_user, owner_org, skill_key=skill_key)


def system_skill_profiles_queryset_for_owner(owner_user, owner_org, *, skill_key: Optional[str] = None):
    if owner_org is not None:
        qs = SystemSkillProfile.objects.filter(organization=owner_org)
    else:
        qs = SystemSkillProfile.objects.filter(user=owner_user, organization__isnull=True)
    if skill_key:
        qs = qs.filter(skill_key=skill_key)
    return qs


def get_system_skill_profile_definition(skill_key: str):
    definition = get_system_skill_definition(skill_key)
    if definition is None:
        raise ValidationError({"skill_key": "Unknown system skill."})
    return definition


def summarize_profile_status(profile: SystemSkillProfile, *, definition=None) -> dict[str, object]:
    definition = definition or get_system_skill_profile_definition(profile.skill_key)
    existing_keys = set(profile.secrets.values_list("key", flat=True))
    missing_required_keys = [
        field.key
        for field in definition.required_profile_fields
        if field.key not in existing_keys and field.default is None and field.key not in definition.default_values
    ]
    present_keys = sorted(existing_keys)
    return {
        "complete": not missing_required_keys,
        "present_keys": present_keys,
        "missing_required_keys": missing_required_keys,
    }


def list_system_skill_profiles_for_agent(agent: PersistentAgent, skill_key: str) -> list[SystemSkillProfile]:
    return list(
        system_skill_profiles_queryset_for_agent(agent, skill_key=skill_key)
        .prefetch_related("secrets")
        .order_by("-is_default", "label", "profile_key")
    )


def get_system_skill_profile_values(profile: SystemSkillProfile, *, definition=None) -> dict[str, str]:
    definition = definition or get_system_skill_profile_definition(profile.skill_key)
    values = dict(definition.default_values)
    for secret in profile.secrets.all():
        values[secret.key] = secret.get_value()
    return values


def resolve_system_skill_profile_for_agent(
    agent: PersistentAgent,
    skill_key: str,
    *,
    profile_key: Optional[str] = None,
) -> dict[str, object]:
    definition = get_system_skill_profile_definition(skill_key)
    profiles = list_system_skill_profiles_for_agent(agent, skill_key)
    available_profile_keys = [profile.profile_key for profile in profiles]

    selected_profile = None
    if profile_key:
        normalized_profile_key = str(profile_key).strip()
        selected_profile = next(
            (profile for profile in profiles if profile.profile_key == normalized_profile_key),
            None,
        )
        if selected_profile is None:
            return {
                "status": "profile_not_found",
                "available_profile_keys": available_profile_keys,
            }
    elif not profiles:
        return {"status": "missing_profile", "available_profile_keys": []}
    elif len(profiles) == 1:
        selected_profile = profiles[0]
    else:
        selected_profile = next((profile for profile in profiles if profile.is_default), None)
        if selected_profile is None:
            return {
                "status": "multiple_profiles",
                "available_profile_keys": available_profile_keys,
            }

    profile_status = summarize_profile_status(selected_profile, definition=definition)
    if not profile_status["complete"]:
        return {
            "status": "incomplete_profile",
            "profile": selected_profile,
            "available_profile_keys": available_profile_keys,
            "missing_required_keys": list(profile_status["missing_required_keys"]),
        }

    return {
        "status": "ok",
        "profile": selected_profile,
        "available_profile_keys": available_profile_keys,
        "values": get_system_skill_profile_values(selected_profile, definition=definition),
    }


def upsert_system_skill_profile_values(profile: SystemSkillProfile, values: dict[str, str], *, definition=None) -> None:
    definition = definition or get_system_skill_profile_definition(profile.skill_key)
    allowed_keys = {field.key for field in definition.profile_fields()} | set(definition.default_values.keys())
    normalized_values = {
        str(key or "").strip().upper(): value
        for key, value in (values or {}).items()
        if str(key or "").strip()
    }
    invalid_keys = sorted(key for key in normalized_values.keys() if key not in allowed_keys)
    if invalid_keys:
        raise ValidationError({"values": [f"Unknown field(s): {', '.join(invalid_keys)}"]})

    with transaction.atomic():
        for key, value in normalized_values.items():
            existing_secret = SystemSkillProfileSecret.objects.filter(profile=profile, key=key).first()
            if value is None or str(value) == "":
                if existing_secret is not None:
                    existing_secret.delete()
                continue
            value_text = str(value)
            secret = existing_secret or SystemSkillProfileSecret(profile=profile, key=key)
            secret.set_value(value_text)
            secret.save()


def set_default_system_skill_profile(profile: SystemSkillProfile) -> None:
    owner_qs = system_skill_profiles_queryset_for_owner(
        profile.user,
        profile.organization,
        skill_key=profile.skill_key,
    )
    owner_qs.exclude(pk=profile.pk).filter(is_default=True).update(is_default=False)

    if profile.is_default:
        return

    profile.is_default = True
    profile.updated_at = timezone.now()
    profile.save(update_fields=["is_default", "updated_at"])
