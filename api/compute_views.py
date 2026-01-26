from typing import Optional

from django.http import Http404
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.models import ApiKey, PersistentAgent
from api.permissions import HasSandboxAccess
from api.services.compute_control import deploy_or_resume, run_command, terminate


class SandboxRunCommandSerializer(serializers.Serializer):
    command = serializers.JSONField()
    cwd = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    env = serializers.DictField(required=False)
    timeout = serializers.IntegerField(required=False, min_value=1, max_value=120)


class SandboxTerminateSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)


def _request_organization(request):
    auth = getattr(request, "auth", None)
    if isinstance(auth, ApiKey) and getattr(auth, "organization_id", None):
        return auth.organization
    return None


def _resolve_agent(request, agent_id: str) -> PersistentAgent:
    agent = PersistentAgent.objects.select_related("organization", "user").filter(id=agent_id).first()
    if not agent:
        raise Http404
    org = _request_organization(request)
    if org is not None:
        if agent.organization_id != org.id:
            raise Http404
        return agent
    if agent.user_id != getattr(request.user, "id", None):
        raise Http404
    return agent


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasSandboxAccess])
def sandbox_deploy(request, agent_id: str):
    agent = _resolve_agent(request, agent_id)
    payload = deploy_or_resume(agent)
    return Response(payload, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasSandboxAccess])
def sandbox_run_command(request, agent_id: str):
    agent = _resolve_agent(request, agent_id)
    serializer = SandboxRunCommandSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    result = run_command(
        agent,
        command=data["command"],
        cwd=data.get("cwd") or None,
        env=data.get("env") or None,
        timeout_seconds=data.get("timeout"),
    )
    return Response(result, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasSandboxAccess])
def sandbox_terminate(request, agent_id: str):
    agent = _resolve_agent(request, agent_id)
    serializer = SandboxTerminateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    reason: Optional[str] = serializer.validated_data.get("reason") or None

    result = terminate(agent, reason=reason)
    return Response(result, status=status.HTTP_200_OK)
