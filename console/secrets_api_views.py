"""Console API views for global secrets and agent secrets management."""

import json
import logging
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.views import View

from api.models import GlobalSecret, PersistentAgentSecret
from console.agent_chat.access import resolve_manageable_agent_for_request
from console.context_helpers import build_console_context
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_secrets_owner(request: HttpRequest):
    """Resolve the current console context owner (user or org).

    Returns (scope, owner_user, owner_org).
    """
    context = build_console_context(request)
    if context.current_context.type == "organization":
        membership = context.current_membership
        if membership is None or not context.can_manage_org_agents:
            raise PermissionDenied("You do not have permission to manage organization secrets.")
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


def _serialize_global_secret(secret: GlobalSecret) -> dict:
    return {
        "id": str(secret.id),
        "name": secret.name,
        "key": secret.key,
        "secret_type": secret.secret_type,
        "domain_pattern": secret.domain_pattern,
        "description": secret.description,
        "created_at": secret.created_at.isoformat() if secret.created_at else None,
        "updated_at": secret.updated_at.isoformat() if secret.updated_at else None,
        "source": "global",
    }


def _serialize_agent_secret(secret: PersistentAgentSecret) -> dict:
    return {
        "id": str(secret.id),
        "name": secret.name,
        "key": secret.key,
        "secret_type": secret.secret_type,
        "domain_pattern": secret.domain_pattern,
        "description": secret.description,
        "requested": secret.requested,
        "created_at": secret.created_at.isoformat() if secret.created_at else None,
        "updated_at": secret.updated_at.isoformat() if secret.updated_at else None,
        "source": "agent",
    }


def _global_secrets_queryset(owner_user, owner_org):
    if owner_org:
        return GlobalSecret.objects.filter(organization=owner_org)
    return GlobalSecret.objects.filter(user=owner_user, organization__isnull=True)


def _create_global_secret(payload: dict, owner_user, owner_org) -> GlobalSecret:
    """Validate payload and create a GlobalSecret instance."""
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValidationError({"name": "Name is required."})

    secret_type = payload.get("secret_type", "credential")
    domain = (payload.get("domain_pattern") or "").strip()
    description = (payload.get("description") or "").strip()
    value = payload.get("value") or ""

    if not value:
        raise ValidationError({"value": "Secret value is required."})

    qs = _global_secrets_queryset(owner_user, owner_org)
    if qs.count() >= GlobalSecret.MAX_GLOBAL_SECRETS_PER_OWNER:
        raise ValidationError({"__all__": f"Maximum {GlobalSecret.MAX_GLOBAL_SECRETS_PER_OWNER} global secrets allowed."})

    secret = GlobalSecret(
        user=owner_user,
        organization=owner_org,
        name=name,
        secret_type=secret_type,
        domain_pattern=domain,
        description=description,
    )
    secret.set_value(value)
    secret.save()
    return secret


# ---------------------------------------------------------------------------
# Global Secret API Views
# ---------------------------------------------------------------------------

class GlobalSecretListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        scope, owner_user, owner_org = _resolve_secrets_owner(request)
        qs = _global_secrets_queryset(owner_user, owner_org).order_by("domain_pattern", "name")
        secrets = [_serialize_global_secret(s) for s in qs]
        return JsonResponse({"secrets": secrets, "owner_scope": scope})

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        scope, owner_user, owner_org = _resolve_secrets_owner(request)

        try:
            with transaction.atomic():
                secret = _create_global_secret(payload, owner_user, owner_org)
        except ValidationError as exc:
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
            return JsonResponse({"errors": errors}, status=400)
        except IntegrityError:
            return JsonResponse({"errors": {"name": ["A secret with that name already exists in this scope."]}}, status=400)

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_ADDED,
            source=AnalyticsSource.WEB,
            properties={"secret_id": str(secret.id), "scope": "global"},
        )
        return JsonResponse({"secret": _serialize_global_secret(secret), "message": "Global secret created."}, status=201)


class GlobalSecretDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def _get_secret(self, request, secret_id):
        scope, owner_user, owner_org = _resolve_secrets_owner(request)
        qs = _global_secrets_queryset(owner_user, owner_org)
        try:
            return qs.get(pk=secret_id)
        except GlobalSecret.DoesNotExist:
            return None

    def patch(self, request: HttpRequest, secret_id, *args: Any, **kwargs: Any):
        secret = self._get_secret(request, secret_id)
        if secret is None:
            return JsonResponse({"error": "Secret not found."}, status=404)

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        old_name = secret.name
        if "name" in payload:
            secret.name = (payload["name"] or "").strip()
        if "description" in payload:
            secret.description = (payload["description"] or "").strip()
        if "domain_pattern" in payload:
            secret.domain_pattern = (payload["domain_pattern"] or "").strip()
        if "secret_type" in payload:
            secret.secret_type = payload["secret_type"]
        if "value" in payload and payload["value"]:
            secret.set_value(payload["value"])
        # Only regenerate key when the name actually changed
        if secret.name != old_name:
            secret.key = ""

        try:
            secret.save()
        except ValidationError as exc:
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
            return JsonResponse({"errors": errors}, status=400)
        except IntegrityError:
            return JsonResponse({"errors": {"name": ["A secret with that name already exists in this scope."]}}, status=400)

        return JsonResponse({"secret": _serialize_global_secret(secret), "message": "Global secret updated."})

    def delete(self, request: HttpRequest, secret_id, *args: Any, **kwargs: Any):
        secret = self._get_secret(request, secret_id)
        if secret is None:
            return JsonResponse({"error": "Secret not found."}, status=404)

        secret.delete()
        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_DELETED,
            source=AnalyticsSource.WEB,
            properties={"secret_id": str(secret_id), "scope": "global"},
        )
        return JsonResponse({"ok": True, "message": "Global secret deleted."})


# ---------------------------------------------------------------------------
# Agent Secret API Views
# ---------------------------------------------------------------------------

class AgentSecretListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def _get_agent(self, request, agent_id):
        return resolve_manageable_agent_for_request(request, str(agent_id))

    def get(self, request: HttpRequest, agent_id, *args: Any, **kwargs: Any):
        agent = self._get_agent(request, agent_id)

        # Agent-level secrets
        agent_secrets = PersistentAgentSecret.objects.filter(agent=agent, requested=False).order_by("secret_type", "domain_pattern", "name")
        requested_secrets = PersistentAgentSecret.objects.filter(agent=agent, requested=True).order_by("secret_type", "domain_pattern", "name")

        # Global secrets for this agent's owner
        from django.db.models import Q
        if agent.organization_id:
            global_filter = Q(organization=agent.organization)
        else:
            global_filter = Q(user=agent.user, organization__isnull=True)
        global_secrets = GlobalSecret.objects.filter(global_filter).order_by("secret_type", "domain_pattern", "name")

        return JsonResponse({
            "agent_secrets": [_serialize_agent_secret(s) for s in agent_secrets],
            "global_secrets": [_serialize_global_secret(s) for s in global_secrets],
            "requested_secrets": [_serialize_agent_secret(s) for s in requested_secrets],
        })

    def post(self, request: HttpRequest, agent_id, *args: Any, **kwargs: Any):
        agent = self._get_agent(request, agent_id)

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        is_global = payload.get("is_global", False)

        if is_global:
            # Create as a global secret for the agent's owner
            owner_user = agent.user if not agent.organization_id else None
            owner_org = agent.organization if agent.organization_id else None
            try:
                with transaction.atomic():
                    secret = _create_global_secret(payload, owner_user, owner_org)
            except ValidationError as exc:
                errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
                return JsonResponse({"errors": errors}, status=400)
            except IntegrityError:
                return JsonResponse({"errors": {"name": ["A global secret with that name already exists."]}}, status=400)

            return JsonResponse({"secret": _serialize_global_secret(secret), "message": "Global secret created."}, status=201)

        # Create agent-level secret
        name = (payload.get("name") or "").strip()
        if not name:
            return JsonResponse({"errors": {"name": ["Name is required."]}}, status=400)

        secret_type = payload.get("secret_type", "credential")
        domain = (payload.get("domain_pattern") or "").strip()
        description = (payload.get("description") or "").strip()
        value = payload.get("value") or ""

        if not value:
            return JsonResponse({"errors": {"value": ["Secret value is required."]}}, status=400)

        from constants.security import SecretLimits
        if agent.secrets.filter(requested=False).count() >= SecretLimits.MAX_SECRETS_PER_AGENT:
            return JsonResponse({"errors": {"__all__": [f"Maximum {SecretLimits.MAX_SECRETS_PER_AGENT} secrets per agent."]}}, status=400)

        try:
            with transaction.atomic():
                secret = PersistentAgentSecret(
                    agent=agent,
                    name=name,
                    secret_type=secret_type,
                    domain_pattern=domain,
                    description=description,
                )
                secret.set_value(value)
                secret.save()
        except ValidationError as exc:
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
            return JsonResponse({"errors": errors}, status=400)
        except IntegrityError:
            return JsonResponse({"errors": {"name": ["A secret with that name already exists for this agent."]}}, status=400)

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_ADDED,
            source=AnalyticsSource.WEB,
            properties={"agent_id": str(agent.pk), "secret_id": str(secret.id), "scope": "agent"},
        )
        return JsonResponse({"secret": _serialize_agent_secret(secret), "message": "Agent secret created."}, status=201)


class AgentSecretDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def _get_agent_and_secret(self, request, agent_id, secret_id):
        agent = resolve_manageable_agent_for_request(request, str(agent_id))
        try:
            secret = PersistentAgentSecret.objects.get(pk=secret_id, agent=agent)
        except PersistentAgentSecret.DoesNotExist:
            return agent, None
        return agent, secret

    def patch(self, request: HttpRequest, agent_id, secret_id, *args: Any, **kwargs: Any):
        agent, secret = self._get_agent_and_secret(request, agent_id, secret_id)
        if secret is None:
            return JsonResponse({"error": "Secret not found."}, status=404)

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        old_name = secret.name
        if "name" in payload:
            secret.name = (payload["name"] or "").strip()
        if "description" in payload:
            secret.description = (payload["description"] or "").strip()
        if "domain_pattern" in payload:
            secret.domain_pattern = (payload["domain_pattern"] or "").strip()
        if "secret_type" in payload:
            secret.secret_type = payload["secret_type"]
        if "value" in payload and payload["value"]:
            secret.set_value(payload["value"])
        # Only regenerate key when the name actually changed
        if secret.name != old_name:
            secret.key = ""

        try:
            secret.save()
        except ValidationError as exc:
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
            return JsonResponse({"errors": errors}, status=400)
        except IntegrityError:
            return JsonResponse({"errors": {"name": ["A secret with that name already exists."]}}, status=400)

        return JsonResponse({"secret": _serialize_agent_secret(secret), "message": "Secret updated."})

    def delete(self, request: HttpRequest, agent_id, secret_id, *args: Any, **kwargs: Any):
        agent, secret = self._get_agent_and_secret(request, agent_id, secret_id)
        if secret is None:
            return JsonResponse({"error": "Secret not found."}, status=404)

        secret.delete()
        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_DELETED,
            source=AnalyticsSource.WEB,
            properties={"agent_id": str(agent.pk), "secret_id": str(secret_id), "scope": "agent"},
        )
        return JsonResponse({"ok": True, "message": "Secret deleted."})


class AgentSecretPromoteAPIView(LoginRequiredMixin, View):
    """Promote an agent-level secret to a global secret (move operation)."""
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id, secret_id, *args: Any, **kwargs: Any):
        agent = resolve_manageable_agent_for_request(request, str(agent_id))
        try:
            secret = PersistentAgentSecret.objects.get(pk=secret_id, agent=agent, requested=False)
        except PersistentAgentSecret.DoesNotExist:
            return JsonResponse({"error": "Secret not found."}, status=404)

        owner_user = agent.user if not agent.organization_id else None
        owner_org = agent.organization if agent.organization_id else None

        # Enforce the same per-owner cap as the create endpoint
        if _global_secrets_queryset(owner_user, owner_org).count() >= GlobalSecret.MAX_GLOBAL_SECRETS_PER_OWNER:
            return JsonResponse(
                {"errors": {"__all__": [f"Maximum {GlobalSecret.MAX_GLOBAL_SECRETS_PER_OWNER} global secrets allowed."]}},
                status=400,
            )

        try:
            with transaction.atomic():
                global_secret = GlobalSecret(
                    user=owner_user,
                    organization=owner_org,
                    name=secret.name,
                    secret_type=secret.secret_type,
                    domain_pattern=secret.domain_pattern,
                    description=secret.description,
                    key=secret.key,
                    encrypted_value=secret.encrypted_value,
                )
                global_secret.save()
                secret.delete()
        except ValidationError as exc:
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
            return JsonResponse({"errors": errors}, status=400)
        except IntegrityError:
            return JsonResponse({"errors": {"__all__": ["A global secret with that name already exists."]}}, status=400)

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_ADDED,
            source=AnalyticsSource.WEB,
            properties={"agent_id": str(agent.pk), "secret_id": str(global_secret.id), "scope": "global", "promoted": True},
        )
        return JsonResponse({"secret": _serialize_global_secret(global_secret), "message": "Secret promoted to global."}, status=201)
