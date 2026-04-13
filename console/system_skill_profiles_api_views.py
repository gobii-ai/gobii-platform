"""Console API views for owner-scoped system skill profile management."""

import json
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.views import View

from api.agent.system_skills import get_system_skill_definition
from api.models import SystemSkillProfile
from api.services.system_skill_profiles import (
    set_default_system_skill_profile,
    summarize_profile_status,
    system_skill_profiles_queryset_for_owner,
    upsert_system_skill_profile_values,
)
from console.context_helpers import build_console_context


def _resolve_profiles_owner(request: HttpRequest):
    context = build_console_context(request)
    if context.current_context.type == "organization":
        membership = context.current_membership
        if membership is None or not context.can_manage_org_agents:
            raise PermissionDenied("You do not have permission to manage organization system skill profiles.")
        return ("organization", None, membership.org)
    return ("user", request.user, None)


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    return payload


def _serialize_definition(skill_key: str) -> dict[str, object]:
    definition = get_system_skill_definition(skill_key)
    if definition is None:
        raise ValidationError({"skill_key": "Unknown system skill."})
    return {
        "skill_key": definition.skill_key,
        "name": definition.name,
        "search_summary": definition.search_summary,
        "fields": [
            {
                "key": field.key,
                "name": field.name,
                "description": field.description,
                "required": field.required,
                "default": field.default,
            }
            for field in definition.profile_fields()
        ],
        "default_values": dict(definition.default_values),
        "setup_instructions": definition.setup_instructions,
    }


def _serialize_profile(profile: SystemSkillProfile) -> dict[str, object]:
    definition = get_system_skill_definition(profile.skill_key)
    if definition is None:
        raise ValidationError({"skill_key": "Unknown system skill."})
    status = summarize_profile_status(profile, definition=definition)
    return {
        "id": str(profile.id),
        "skill_key": profile.skill_key,
        "profile_key": profile.profile_key,
        "label": profile.label,
        "is_default": profile.is_default,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "complete": bool(status["complete"]),
        "present_keys": list(status["present_keys"]),
        "missing_required_keys": list(status["missing_required_keys"]),
    }


class SystemSkillProfileListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, skill_key: str, *args: Any, **kwargs: Any):
        scope, owner_user, owner_org = _resolve_profiles_owner(request)
        definition = _serialize_definition(skill_key)
        profiles = system_skill_profiles_queryset_for_owner(owner_user, owner_org, skill_key=skill_key).order_by(
            "-is_default",
            "label",
            "profile_key",
        )
        return JsonResponse(
            {
                "owner_scope": scope,
                "definition": definition,
                "profiles": [_serialize_profile(profile) for profile in profiles],
            }
        )

    def post(self, request: HttpRequest, skill_key: str, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        definition = _serialize_definition(skill_key)
        scope, owner_user, owner_org = _resolve_profiles_owner(request)
        existing_qs = system_skill_profiles_queryset_for_owner(owner_user, owner_org, skill_key=skill_key)
        should_default = bool(payload.get("is_default")) or not existing_qs.exists()

        profile = SystemSkillProfile(
            user=owner_user,
            organization=owner_org,
            skill_key=skill_key,
            profile_key=(payload.get("profile_key") or "").strip(),
            label=(payload.get("label") or "").strip(),
            is_default=False,
        )
        values = payload.get("values") or {}
        if not isinstance(values, dict):
            return JsonResponse({"errors": {"values": ["values must be an object."]}}, status=400)

        try:
            with transaction.atomic():
                profile.save()
                if should_default:
                    set_default_system_skill_profile(profile)
                upsert_system_skill_profile_values(profile, values, definition=get_system_skill_definition(skill_key))
        except ValidationError as exc:
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
            return JsonResponse({"errors": errors}, status=400)
        except IntegrityError:
            return JsonResponse(
                {"errors": {"profile_key": ["A profile with that key already exists for this system skill."]}},
                status=400,
            )

        return JsonResponse(
            {
                "owner_scope": scope,
                "definition": definition,
                "profile": _serialize_profile(profile),
                "message": "System skill profile created.",
            },
            status=201,
        )


class SystemSkillProfileDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def _get_profile(self, request: HttpRequest, skill_key: str, profile_id):
        _scope, owner_user, owner_org = _resolve_profiles_owner(request)
        qs = system_skill_profiles_queryset_for_owner(owner_user, owner_org, skill_key=skill_key)
        try:
            return qs.get(pk=profile_id)
        except SystemSkillProfile.DoesNotExist:
            return None

    def patch(self, request: HttpRequest, skill_key: str, profile_id, *args: Any, **kwargs: Any):
        profile = self._get_profile(request, skill_key, profile_id)
        if profile is None:
            return JsonResponse({"error": "Profile not found."}, status=404)

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "label" in payload:
            profile.label = (payload.get("label") or "").strip()
        if "is_default" in payload and not payload.get("is_default"):
            return JsonResponse(
                {"errors": {"is_default": ["Use the default profile action to choose a different default profile."]}},
                status=400,
            )

        values = payload.get("values", None)
        if values is not None and not isinstance(values, dict):
            return JsonResponse({"errors": {"values": ["values must be an object."]}}, status=400)

        try:
            with transaction.atomic():
                profile.save()
                if payload.get("is_default"):
                    set_default_system_skill_profile(profile)
                if values is not None:
                    upsert_system_skill_profile_values(
                        profile,
                        values,
                        definition=get_system_skill_definition(skill_key),
                    )
        except ValidationError as exc:
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
            return JsonResponse({"errors": errors}, status=400)

        return JsonResponse({"profile": _serialize_profile(profile), "message": "System skill profile updated."})

    def delete(self, request: HttpRequest, skill_key: str, profile_id, *args: Any, **kwargs: Any):
        profile = self._get_profile(request, skill_key, profile_id)
        if profile is None:
            return JsonResponse({"error": "Profile not found."}, status=404)

        was_default = profile.is_default
        scope_qs = system_skill_profiles_queryset_for_owner(profile.user, profile.organization, skill_key=skill_key).exclude(
            pk=profile.pk
        )
        remaining_ids = list(scope_qs.values_list("id", flat=True))
        profile.delete()
        if was_default and len(remaining_ids) == 1:
            SystemSkillProfile.objects.filter(pk=remaining_ids[0]).update(is_default=True)
        return JsonResponse({"ok": True, "message": "System skill profile deleted."})


class SystemSkillProfileSetDefaultAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, skill_key: str, profile_id, *args: Any, **kwargs: Any):
        detail_view = SystemSkillProfileDetailAPIView()
        profile = detail_view._get_profile(request, skill_key, profile_id)
        if profile is None:
            return JsonResponse({"error": "Profile not found."}, status=404)

        with transaction.atomic():
            set_default_system_skill_profile(profile)
        return JsonResponse({"profile": _serialize_profile(profile), "message": "Default profile updated."})
