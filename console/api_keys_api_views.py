"""JSON API views for API key management in the immersive app."""

from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.utils.translation import gettext_lazy as _
from django.views import View

from api.models import ApiKey, OrganizationMembership
from api.services.email_verification import has_verified_email
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body, _permission_denied_response, _validation_error_payload
from console.context_helpers import build_console_context
from console.forms import ApiKeyForm
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.trial_enforcement import PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE, can_user_use_personal_agents_and_api


API_KEY_MANAGE_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
}

API_KEY_VIEW_ROLES = API_KEY_MANAGE_ROLES | {
    OrganizationMembership.OrgRole.BILLING,
}


def _form_error_payload(form: ApiKeyForm) -> dict[str, list[str]]:
    return {
        field: [str(message) for message in messages]
        for field, messages in form.errors.items()
    }


def _created_by_label(api_key: ApiKey) -> str | None:
    user = api_key.created_by
    if user is None:
        return None
    return user.get_full_name() or user.email or user.username or None


def _serialize_api_key(api_key: ApiKey) -> dict[str, Any]:
    return {
        "id": str(api_key.id),
        "name": api_key.name,
        "prefix": api_key.prefix,
        "created_by": _created_by_label(api_key),
        "created_at": api_key.created_at.isoformat() if api_key.created_at else None,
        "last_used_at": api_key.last_used_at.isoformat() if api_key.last_used_at else None,
        "revoked_at": api_key.revoked_at.isoformat() if api_key.revoked_at else None,
        "is_active": api_key.is_active,
    }


def _context_payload(ctx: dict[str, Any], request: HttpRequest) -> dict[str, Any]:
    if ctx["type"] == "organization":
        return {
            "owner_scope": "organization",
            "owner_name": ctx["organization"].name,
            "can_manage": ctx.get("can_manage", False),
            "email_verified": has_verified_email(request.user),
        }
    return {
        "owner_scope": "user",
        "owner_name": request.user.get_full_name() or request.user.email or request.user.username,
        "can_manage": True,
        "email_verified": has_verified_email(request.user),
    }


def _api_key_context(request: HttpRequest) -> dict[str, Any]:
    resolved = build_console_context(request)
    if resolved.current_context.type == "organization":
        membership = resolved.current_membership
        if membership is None:
            raise PermissionDenied("Organization context is no longer available.")

        if membership.role not in API_KEY_VIEW_ROLES:
            raise PermissionDenied("You do not have access to organization API keys.")

        return {
            "type": "organization",
            "organization": membership.org,
            "membership": membership,
            "can_manage": membership.role in API_KEY_MANAGE_ROLES,
        }

    return {
        "type": "user",
        "user": request.user,
        "can_manage": True,
    }


def _ensure_can_manage(ctx: dict[str, Any]) -> None:
    if not ctx.get("can_manage"):
        raise PermissionDenied("You do not have permission to manage API keys for this organization.")


def _api_key_queryset(ctx: dict[str, Any]):
    base = ApiKey.objects.select_related("created_by")
    if ctx["type"] == "organization":
        return base.filter(organization=ctx["organization"]).order_by("-created_at")
    return base.filter(user=ctx["user"]).order_by("-created_at")


def _api_key_event_properties(ctx: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    organization = ctx["organization"] if ctx["type"] == "organization" else None
    return Analytics.with_org_properties(properties, organization=organization)


class ApiKeyListAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            ctx = _api_key_context(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        keys = [_serialize_api_key(key) for key in _api_key_queryset(ctx)]
        return JsonResponse({
            "api_keys": keys,
            **_context_payload(ctx, request),
        })

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        try:
            ctx = _api_key_context(request)
            _ensure_can_manage(ctx)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        form_kwargs = (
            {"organization": ctx["organization"]}
            if ctx["type"] == "organization"
            else {"user": request.user}
        )
        form = ApiKeyForm(data={"name": payload.get("name", "")}, **form_kwargs)
        if not form.is_valid():
            return JsonResponse({"errors": _form_error_payload(form)}, status=400)

        if not has_verified_email(request.user):
            return JsonResponse(
                {
                    "errors": {
                        "__all__": [
                            _(
                                "Email verification required to create API keys. "
                                "Please verify your email address in your account settings."
                            )
                        ],
                    },
                },
                status=400,
            )

        try:
            with transaction.atomic():
                name = form.cleaned_data["name"]
                if ctx["type"] == "organization":
                    raw_key, api_key = ApiKey.create_for_org(
                        ctx["organization"],
                        created_by=request.user,
                        name=name,
                    )
                else:
                    if not can_user_use_personal_agents_and_api(request.user):
                        return JsonResponse(
                            {"errors": {"__all__": [PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE]}},
                            status=400,
                        )
                    raw_key, api_key = ApiKey.create_for_user(
                        request.user,
                        name=name,
                        created_by=request.user,
                    )
        except ValidationError as exc:
            return JsonResponse({"errors": _validation_error_payload(exc)}, status=400)
        except IntegrityError:
            return JsonResponse({"errors": {"name": [_("An API key with that name already exists.")]}}, status=400)

        props = _api_key_event_properties(ctx, {
            "key_id": str(api_key.id),
            "key_name": api_key.name,
        })
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.API_KEY_CREATED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        if props.get("organization"):
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_API_KEY_CREATED,
                source=AnalyticsSource.WEB,
                properties=props.copy(),
            ))

        return JsonResponse({
            "api_key": _serialize_api_key(api_key),
            "raw_key": raw_key,
            "message": "API key created.",
        }, status=201)


class ApiKeyDetailAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def _get_api_key(self, request: HttpRequest, api_key_id):
        ctx = _api_key_context(request)
        return ctx, _api_key_queryset(ctx).filter(pk=api_key_id).first()

    def patch(self, request: HttpRequest, api_key_id, *args: Any, **kwargs: Any):
        try:
            ctx, api_key = self._get_api_key(request, api_key_id)
            _ensure_can_manage(ctx)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        if api_key is None:
            return JsonResponse({"error": "API key not found."}, status=404)

        api_key.revoke()
        props = _api_key_event_properties(ctx, {
            "key_id": str(api_key.id),
            "key_name": api_key.name,
        })
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=(
                AnalyticsEvent.ORGANIZATION_API_KEY_REVOKED
                if props.get("organization")
                else AnalyticsEvent.API_KEY_REVOKED
            ),
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        return JsonResponse({
            "api_key": _serialize_api_key(api_key),
            "message": f"API key '{api_key.name}' has been revoked.",
        })

    def delete(self, request: HttpRequest, api_key_id, *args: Any, **kwargs: Any):
        try:
            ctx, api_key = self._get_api_key(request, api_key_id)
            _ensure_can_manage(ctx)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        if api_key is None:
            return JsonResponse({"error": "API key not found."}, status=404)

        key_id = api_key.id
        key_name = api_key.name
        api_key.delete()

        props = _api_key_event_properties(ctx, {
            "key_id": str(key_id),
            "key_name": key_name,
        })
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.API_KEY_DELETED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        if props.get("organization"):
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_API_KEY_DELETED,
                source=AnalyticsSource.WEB,
                properties=props.copy(),
            ))

        return JsonResponse({
            "ok": True,
            "message": f"API key '{key_name}' has been permanently deleted.",
        })
