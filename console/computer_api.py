from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin

from api.computer_http import parse_computer_json_payload
from api.models import (
    ComputerDevice,
    ComputerDeviceAssignment,
    ComputerPairingSession,
    OrganizationMembership,
    PersistentAgent,
)
from api.services.computer_relay import (
    approve_device_apps,
    approve_pairing,
    assign_device,
    computer_cpp_enabled_for_user,
    computer_rate_limited,
    deny_pairing,
    manageable_agents_for_user,
    pairing_user_code_matches,
    revoke_assignment,
    revoke_device,
    serialize_device,
    update_device_properties,
)
from api.services.organization_permissions import ORG_AGENT_CONFIG_AUTHORITY_ROLES
from console.context_helpers import build_console_context


def _enabled_or_response(request: HttpRequest):
    if computer_cpp_enabled_for_user(request.user):
        return None
    return JsonResponse({"enabled": False})


def _pairing_attempt_limited(request: HttpRequest, pairing_id) -> bool:
    return computer_rate_limited(
        f"computer-pairing-code-attempt:{pairing_id}:{request.user.id}",
        limit=settings.COMPUTER_CPP_CODE_ATTEMPTS_PER_PAIRING_USER,
        window_seconds=settings.COMPUTER_CPP_PAIRING_TTL_SECONDS,
    )


def _owner_device(request: HttpRequest, device_id) -> ComputerDevice:
    return get_object_or_404(
        ComputerDevice.objects.select_related("owner").prefetch_related("apps"),
        id=device_id,
        owner=request.user,
        revoked_at__isnull=True,
    )


def _serialize_agent(agent: PersistentAgent) -> dict:
    return {
        "id": str(agent.id),
        "name": agent.name,
        "organization_id": str(agent.organization_id) if agent.organization_id else None,
        "organization_name": agent.organization.name if agent.organization else None,
    }


class ComputerListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest):
        disabled = _enabled_or_response(request)
        if disabled:
            return disabled
        context = build_console_context(request)
        base = (
            ComputerDevice.objects.filter(revoked_at__isnull=True)
            .select_related(
                "owner",
                "assignment__agent",
                "assignment__organization",
            )
            .prefetch_related("apps")
            .order_by("display_name")
        )
        owner_actions = context.current_context.type == "personal"
        if owner_actions:
            devices = base.filter(owner=request.user)
        elif context.can_manage_org_agents:
            devices = base.filter(
                assignment__organization_id=context.current_context.id,
                assignment__status=ComputerDeviceAssignment.Status.ACTIVE,
            )
        else:
            devices = base.none()

        agent_id = str(request.GET.get("agent_id") or "").strip()
        if agent_id:
            devices = devices.filter(
                assignment__agent_id=agent_id,
                assignment__status=ComputerDeviceAssignment.Status.ACTIVE,
            )

        release_base_url = settings.COMPUTER_CPP_RELEASE_BASE_URL.rstrip("/")
        return JsonResponse(
            {
                "enabled": True,
                "downloads": {
                    "macos": {
                        "url": f"{release_base_url}/computer.cpp-macos-arm64.zip",
                    },
                    "windows": {
                        "url": f"{release_base_url}/computer.cpp-windows-x64.msi",
                        "portable_url": f"{release_base_url}/computer.cpp-windows-x64.zip",
                    },
                    "minimum_version": settings.COMPUTER_CPP_MINIMUM_CLIENT_VERSION,
                },
                "context": {
                    "type": context.current_context.type,
                    "id": context.current_context.id,
                    "can_manage_org_grants": context.can_manage_org_agents,
                },
                "agents": [_serialize_agent(agent) for agent in manageable_agents_for_user(request.user)],
                "devices": [
                    serialize_device(device, owner_actions=owner_actions and device.owner_id == request.user.id)
                    for device in devices
                ],
            }
        )


class ComputerPairingApprovalAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, pairing_id):
        disabled = _enabled_or_response(request)
        if disabled:
            return disabled
        pairing = get_object_or_404(ComputerPairingSession, id=pairing_id)
        if pairing.status != ComputerPairingSession.Status.PENDING or pairing.expires_at <= timezone.now():
            return JsonResponse({"error": "pairing_expired"}, status=410)
        return JsonResponse(
            {
                "enabled": True,
                "pairing": {
                    "id": str(pairing.id),
                    "display_name": pairing.display_name,
                    "platform": pairing.platform,
                    "architecture": pairing.architecture,
                    "client_version": pairing.client_version,
                    "protocol_version": pairing.protocol_version,
                    "apps": pairing.app_manifest,
                    "expires_at": pairing.expires_at.isoformat(),
                },
                "agents": [_serialize_agent(agent) for agent in manageable_agents_for_user(request.user)],
            }
        )

    def post(self, request: HttpRequest, pairing_id):
        if not computer_cpp_enabled_for_user(request.user):
            raise PermissionDenied("Computer connections are not enabled for this account")
        if _pairing_attempt_limited(request, pairing_id):
            return JsonResponse({"error": "rate_limited"}, status=429)
        pairing = get_object_or_404(ComputerPairingSession, id=pairing_id)
        try:
            payload = parse_computer_json_payload(request)
            agent = get_object_or_404(PersistentAgent, id=payload.get("agent_id"))
            selected = payload.get("selected_app_keys")
            if not isinstance(selected, list):
                selected = [
                    app["key"]
                    for app in pairing.app_manifest
                    if app.get("type") == "bundled"
                ]
            approve_pairing(
                pairing,
                user=request.user,
                user_code=str(payload.get("user_code") or ""),
                agent=agent,
                selected_app_keys=[str(value) for value in selected],
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        return JsonResponse({"approved": True, "pairing_id": str(pairing.id)})


class ComputerPairingDenyAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, pairing_id):
        if not computer_cpp_enabled_for_user(request.user):
            raise PermissionDenied("Computer connections are not enabled for this account")
        if _pairing_attempt_limited(request, pairing_id):
            return JsonResponse({"error": "rate_limited"}, status=429)
        pairing = get_object_or_404(ComputerPairingSession, id=pairing_id)
        try:
            payload = parse_computer_json_payload(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if not pairing_user_code_matches(pairing, payload.get("user_code")):
            return HttpResponseBadRequest("The verification code does not match")
        try:
            deny_pairing(pairing)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return JsonResponse({"denied": True})


class ComputerDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, device_id):
        if not computer_cpp_enabled_for_user(request.user):
            raise PermissionDenied("Computer connections are not enabled for this account")
        device = _owner_device(request, device_id)
        try:
            payload = parse_computer_json_payload(request)
            display_name = None
            if "display_name" in payload:
                display_name = str(payload["display_name"]).strip()[:128]
                if not display_name:
                    raise ValueError("display_name cannot be empty")
            paused = bool(payload["paused"]) if "paused" in payload else None
            update_device_properties(
                device,
                display_name=display_name,
                paused=paused,
            )

            if "approved_apps" in payload:
                if not isinstance(payload["approved_apps"], list):
                    raise ValueError("approved_apps must be a list")
                approve_device_apps(device, payload["approved_apps"])
            if payload.get("agent_id"):
                agent = get_object_or_404(PersistentAgent, id=payload["agent_id"])
                assign_device(device, agent, granted_by=request.user)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        refreshed = _owner_device(request, device.id)
        return JsonResponse({"device": serialize_device(refreshed, owner_actions=True)})

    def delete(self, request: HttpRequest, device_id):
        if not computer_cpp_enabled_for_user(request.user):
            raise PermissionDenied("Computer connections are not enabled for this account")
        device = _owner_device(request, device_id)
        revoke_device(device)
        return JsonResponse({"revoked": True})


class ComputerAssignmentAPIView(LoginRequiredMixin, View):
    http_method_names = ["delete"]

    def delete(self, request: HttpRequest, device_id):
        if not computer_cpp_enabled_for_user(request.user):
            raise PermissionDenied("Computer connections are not enabled for this account")
        device = get_object_or_404(
            ComputerDevice.objects.select_related(
                "owner",
                "assignment__organization",
                "assignment__agent",
            ).prefetch_related("apps"),
            id=device_id,
            revoked_at__isnull=True,
        )
        allowed = device.owner_id == request.user.id
        assignment = getattr(device, "assignment", None)
        if not allowed and assignment and assignment.organization_id:
            allowed = OrganizationMembership.objects.filter(
                user=request.user,
                org_id=assignment.organization_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                role__in=ORG_AGENT_CONFIG_AUTHORITY_ROLES,
            ).exists()
        if not allowed:
            raise PermissionDenied("You cannot revoke this computer assignment")
        revoke_assignment(device, revoked_by=request.user)
        return JsonResponse({"revoked": True})
