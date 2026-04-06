import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from api.models import Organization, PersistentAgent, PersistentAgentSecret
from console.context_helpers import build_console_context
from constants.security import SecretLimits

logger = logging.getLogger(__name__)


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    return payload


def _resolve_global_owner(request):
    """Return (user, organization) for the active console context."""
    resolved = build_console_context(request)
    if resolved.current_context.type == "organization" and resolved.current_membership:
        return None, resolved.current_membership.org
    return request.user, None


def _serialize_secret(secret: PersistentAgentSecret) -> dict:
    return {
        "id": str(secret.id),
        "name": secret.name,
        "key": secret.key,
        "description": secret.description,
        "secret_type": secret.secret_type,
        "domain_pattern": secret.domain_pattern if secret.secret_type != "env_var" else None,
        "visibility": secret.visibility,
        "requested": secret.requested,
        "created_at": secret.created_at.isoformat() if secret.created_at else None,
        "updated_at": secret.updated_at.isoformat() if secret.updated_at else None,
        "agent_id": str(secret.agent_id) if secret.agent_id else None,
        "agent_name": secret.agent.name if secret.agent else None,
    }


def _global_secrets_qs(user, organization):
    """Return queryset of global secrets for the given owner."""
    qs = PersistentAgentSecret.objects.filter(visibility=PersistentAgentSecret.Visibility.GLOBAL)
    if organization:
        qs = qs.filter(organization=organization)
    else:
        qs = qs.filter(user=user, organization__isnull=True)
    return qs.order_by("secret_type", "domain_pattern", "name")


class GlobalSecretsListCreateAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def handle_no_permission(self):
        return JsonResponse({"error": "Authentication required"}, status=401)

    def get(self, request: HttpRequest):
        user, org = _resolve_global_owner(request)
        secrets = _global_secrets_qs(user, org)
        return JsonResponse({"secrets": [_serialize_secret(s) for s in secrets]})

    def post(self, request: HttpRequest):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        user, org = _resolve_global_owner(request)

        current_count = _global_secrets_qs(user, org).count()
        if current_count >= SecretLimits.MAX_GLOBAL_SECRETS:
            return JsonResponse({"error": f"Maximum {SecretLimits.MAX_GLOBAL_SECRETS} global secrets allowed."}, status=400)

        secret_type = payload.get("secret_type", "credential")
        domain_pattern = payload.get("domain_pattern", "")
        name = (payload.get("name") or "").strip()
        description = (payload.get("description") or "").strip()
        value = payload.get("value", "")

        if not name:
            return JsonResponse({"error": "Name is required."}, status=400)
        if not value:
            return JsonResponse({"error": "Value is required."}, status=400)

        try:
            with transaction.atomic():
                secret = PersistentAgentSecret(
                    agent=None,
                    user=user,
                    organization=org,
                    visibility=PersistentAgentSecret.Visibility.GLOBAL,
                    secret_type=secret_type,
                    domain_pattern=domain_pattern,
                    name=name,
                    description=description,
                )
                secret.full_clean()
                secret.set_value(value)
                secret.save()
        except Exception as exc:
            logger.warning("Failed to create global secret: %s", exc)
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"secret": _serialize_secret(secret)}, status=201)


class GlobalSecretDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["put", "delete"]

    def handle_no_permission(self):
        return JsonResponse({"error": "Authentication required"}, status=401)

    def _get_secret(self, request, secret_id):
        user, org = _resolve_global_owner(request)
        qs = _global_secrets_qs(user, org)
        return get_object_or_404(qs, pk=secret_id)

    def put(self, request: HttpRequest, secret_id):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        secret = self._get_secret(request, secret_id)

        try:
            with transaction.atomic():
                if "name" in payload:
                    secret.name = payload["name"].strip()
                if "description" in payload:
                    secret.description = (payload["description"] or "").strip()
                if "secret_type" in payload:
                    secret.secret_type = payload["secret_type"]
                if "domain_pattern" in payload:
                    secret.domain_pattern = payload["domain_pattern"]
                secret.full_clean()
                if "value" in payload and payload["value"]:
                    secret.set_value(payload["value"])
                secret.save()
        except Exception as exc:
            logger.warning("Failed to update global secret %s: %s", secret_id, exc)
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"secret": _serialize_secret(secret)})

    def delete(self, request: HttpRequest, secret_id):
        secret = self._get_secret(request, secret_id)
        secret.delete()
        return JsonResponse({"ok": True})


class AgentSecretsListCreateAPIView(LoginRequiredMixin, View):
    """List agent secrets (agent-scoped + global inherited) and create new ones."""
    http_method_names = ["get", "post"]

    def handle_no_permission(self):
        return JsonResponse({"error": "Authentication required"}, status=401)

    def _get_agent(self, request, pk):
        return get_object_or_404(PersistentAgent, pk=pk, user=request.user)

    def get(self, request: HttpRequest, pk):
        agent = self._get_agent(request, pk)

        agent_secrets = PersistentAgentSecret.objects.filter(
            agent=agent,
            visibility=PersistentAgentSecret.Visibility.AGENT,
        ).order_by("secret_type", "domain_pattern", "name")

        user, org = _resolve_global_owner(request)
        global_secrets = _global_secrets_qs(user, org).filter(requested=False)

        return JsonResponse({
            "agent_secrets": [_serialize_secret(s) for s in agent_secrets],
            "global_secrets": [_serialize_secret(s) for s in global_secrets],
            "agent": {
                "id": str(agent.id),
                "name": agent.name,
            },
        })

    def post(self, request: HttpRequest, pk):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        agent = self._get_agent(request, pk)
        visibility = payload.get("visibility", "agent")

        if visibility == "global":
            user, org = _resolve_global_owner(request)
            current_count = _global_secrets_qs(user, org).count()
            if current_count >= SecretLimits.MAX_GLOBAL_SECRETS:
                return JsonResponse({"error": f"Maximum {SecretLimits.MAX_GLOBAL_SECRETS} global secrets allowed."}, status=400)
        else:
            current_count = PersistentAgentSecret.objects.filter(agent=agent).count()
            if current_count >= SecretLimits.MAX_SECRETS_PER_AGENT:
                return JsonResponse({"error": f"Maximum {SecretLimits.MAX_SECRETS_PER_AGENT} secrets allowed per agent."}, status=400)

        secret_type = payload.get("secret_type", "credential")
        domain_pattern = payload.get("domain_pattern", "")
        name = (payload.get("name") or "").strip()
        description = (payload.get("description") or "").strip()
        value = payload.get("value", "")

        if not name:
            return JsonResponse({"error": "Name is required."}, status=400)
        if not value:
            return JsonResponse({"error": "Value is required."}, status=400)

        try:
            with transaction.atomic():
                if visibility == "global":
                    user, org = _resolve_global_owner(request)
                    secret = PersistentAgentSecret(
                        agent=None,
                        user=user,
                        organization=org,
                        visibility=PersistentAgentSecret.Visibility.GLOBAL,
                        secret_type=secret_type,
                        domain_pattern=domain_pattern,
                        name=name,
                        description=description,
                    )
                else:
                    secret = PersistentAgentSecret(
                        agent=agent,
                        visibility=PersistentAgentSecret.Visibility.AGENT,
                        secret_type=secret_type,
                        domain_pattern=domain_pattern,
                        name=name,
                        description=description,
                    )
                secret.full_clean()
                secret.set_value(value)
                secret.save()
        except Exception as exc:
            logger.warning("Failed to create secret for agent %s: %s", pk, exc)
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"secret": _serialize_secret(secret)}, status=201)


class AgentSecretDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["put", "delete"]

    def handle_no_permission(self):
        return JsonResponse({"error": "Authentication required"}, status=401)

    def _get_agent(self, request, pk):
        return get_object_or_404(PersistentAgent, pk=pk, user=request.user)

    def put(self, request: HttpRequest, pk, secret_id):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        agent = self._get_agent(request, pk)
        secret = get_object_or_404(
            PersistentAgentSecret, pk=secret_id, agent=agent,
            visibility=PersistentAgentSecret.Visibility.AGENT,
        )

        try:
            with transaction.atomic():
                if "name" in payload:
                    secret.name = payload["name"].strip()
                if "description" in payload:
                    secret.description = (payload["description"] or "").strip()
                if "secret_type" in payload:
                    secret.secret_type = payload["secret_type"]
                if "domain_pattern" in payload:
                    secret.domain_pattern = payload["domain_pattern"]
                secret.full_clean()
                if "value" in payload and payload["value"]:
                    secret.set_value(payload["value"])
                secret.save()
        except Exception as exc:
            logger.warning("Failed to update agent secret %s: %s", secret_id, exc)
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"secret": _serialize_secret(secret)})

    def delete(self, request: HttpRequest, pk, secret_id):
        agent = self._get_agent(request, pk)
        secret = get_object_or_404(
            PersistentAgentSecret, pk=secret_id, agent=agent,
            visibility=PersistentAgentSecret.Visibility.AGENT,
        )
        secret.delete()
        return JsonResponse({"ok": True})


class AgentSecretPromoteAPIView(LoginRequiredMixin, View):
    """Promote an agent secret to global visibility (detach from agent)."""
    http_method_names = ["post"]

    def handle_no_permission(self):
        return JsonResponse({"error": "Authentication required"}, status=401)

    def post(self, request: HttpRequest, pk, secret_id):
        agent = get_object_or_404(PersistentAgent, pk=pk, user=request.user)
        secret = get_object_or_404(
            PersistentAgentSecret, pk=secret_id, agent=agent,
            visibility=PersistentAgentSecret.Visibility.AGENT,
        )

        user, org = _resolve_global_owner(request)

        current_count = _global_secrets_qs(user, org).count()
        if current_count >= SecretLimits.MAX_GLOBAL_SECRETS:
            return JsonResponse({"error": f"Maximum {SecretLimits.MAX_GLOBAL_SECRETS} global secrets allowed."}, status=400)

        try:
            with transaction.atomic():
                secret.agent = None
                secret.user = user
                secret.organization = org
                secret.visibility = PersistentAgentSecret.Visibility.GLOBAL
                secret.full_clean()
                secret.save()
        except Exception as exc:
            logger.warning("Failed to promote secret %s to global: %s", secret_id, exc)
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"secret": _serialize_secret(secret)})
