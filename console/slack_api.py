"""Console and public endpoints for native Slack integration."""

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from api.agent.system_skills.defaults import SLACK_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.models import (
    PersistentAgentSlackChannelSubscription,
    PersistentAgentSystemSkillState,
)
from api.services.slack_bot import (
    SlackIntegrationError,
    disable_subscription,
    discover_channels,
    ensure_subscription,
    ingest_event_message,
    list_claimed_workspaces,
    list_subscriptions,
    slack_event_message_from_payload,
    verify_slack_signature,
)
from console.agent_chat.access import resolve_manageable_agent_for_request
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body


def _slack_permission_denied_response(message: str = "Not permitted to manage this agent.") -> JsonResponse:
    return JsonResponse({"error": message}, status=403)


def _slack_skill_enabled(agent) -> bool:
    return PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key=SLACK_NATIVE_SYSTEM_SKILL_KEY,
        is_enabled=True,
    ).exists()


def _serialize_slack_app(agent, request: HttpRequest | None = None) -> dict[str, Any]:
    subscriptions = list_subscriptions(agent)
    active_subscriptions = [
        subscription
        for subscription in subscriptions
        if subscription.get("status") == PersistentAgentSlackChannelSubscription.Status.ACTIVE
    ]
    workspaces = list_claimed_workspaces(agent)
    connect_url = reverse("console-native-integration-connect", args=["slack"])
    if request is not None:
        connect_url = request.build_absolute_uri(connect_url)
    return {
        "provider_key": "slack",
        "display_name": "Slack",
        "description": "Connect Slack workspaces and subscribe this agent to selected channels.",
        "icon": "slack",
        "native": True,
        "connected": bool(workspaces),
        "subscribed": bool(active_subscriptions),
        "skill_enabled": _slack_skill_enabled(agent),
        "workspaces": workspaces,
        "subscriptions": subscriptions,
        "active_subscription_count": len(active_subscriptions),
        "workspace_count": len(workspaces),
        "connect_url": connect_url,
        "identity_note": (
            "Slack replies can use this agent's name and avatar as message display identity, "
            "but Slack does not create separate mentionable bot users per agent."
        ),
    }


def _resolve_slack_agent(request: HttpRequest, agent_id: str):
    return resolve_manageable_agent_for_request(
        request,
        agent_id,
        allow_delinquent_personal_chat=True,
    )


def _enable_slack_native_skill(agent) -> dict[str, object]:
    return enable_system_skills(agent, [SLACK_NATIVE_SYSTEM_SKILL_KEY])


class AgentSlackAppView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_slack_agent(request, agent_id)
        except PermissionDenied:
            return _slack_permission_denied_response()
        return JsonResponse(_serialize_slack_app(agent, request=request))


class AgentSlackConnectView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_slack_agent(request, agent_id)
            skill_result = _enable_slack_native_skill(agent)
        except PermissionDenied:
            return _slack_permission_denied_response()
        if skill_result.get("status") != "success" or skill_result.get("invalid"):
            return JsonResponse({"error": "Unable to enable Slack for this agent."}, status=400)
        return JsonResponse(
            {
                "connect_url": request.build_absolute_uri(reverse("console-native-integration-connect", args=["slack"])),
                "skill_enabled": True,
                "app": _serialize_slack_app(agent, request=request),
            }
        )


class AgentSlackChannelsView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_slack_agent(request, agent_id)
            result = discover_channels(
                agent,
                query=str(request.GET.get("q") or "").strip(),
                limit=200,
            )
        except PermissionDenied:
            return _slack_permission_denied_response()
        except SlackIntegrationError as exc:
            return JsonResponse({"status": "error", "message": str(exc), "channels": []}, status=400)
        return JsonResponse(result)


class AgentSlackSubscriptionsView(ApiLoginRequiredMixin, View):
    http_method_names = ["post", "patch"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        return self._replace_subscriptions(request, agent_id)

    def patch(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        return self._replace_subscriptions(request, agent_id)

    def _replace_subscriptions(self, request: HttpRequest, agent_id: str):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        subscriptions = payload.get("subscriptions")
        if not isinstance(subscriptions, list):
            return HttpResponseBadRequest("subscriptions must be an array.")

        desired: dict[tuple[str, str], dict[str, str]] = {}
        for index, raw_subscription in enumerate(subscriptions):
            if not isinstance(raw_subscription, dict):
                return HttpResponseBadRequest(f"subscriptions[{index}] must be an object.")
            workspace_id = str(raw_subscription.get("workspace_id") or "").strip()
            channel_id = str(raw_subscription.get("channel_id") or "").strip()
            channel_name = str(raw_subscription.get("channel_name") or "").strip()
            channel_type = str(raw_subscription.get("channel_type") or "").strip()
            if not workspace_id or not channel_id:
                return HttpResponseBadRequest("workspace_id and channel_id are required for every subscription.")
            desired[(workspace_id, channel_id)] = {
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "channel_type": channel_type,
            }

        try:
            agent = _resolve_slack_agent(request, agent_id)
        except PermissionDenied:
            return _slack_permission_denied_response()

        try:
            skill_result = _enable_slack_native_skill(agent)
            if skill_result.get("status") != "success" or skill_result.get("invalid"):
                return JsonResponse({"error": "Unable to enable Slack for this agent."}, status=400)

            for subscription in desired.values():
                ensure_subscription(
                    agent,
                    workspace_id=subscription["workspace_id"],
                    channel_id=subscription["channel_id"],
                    channel_name=subscription["channel_name"],
                    channel_type=subscription["channel_type"],
                )

            active_subscriptions = PersistentAgentSlackChannelSubscription.objects.select_related("workspace").filter(
                agent=agent,
                status=PersistentAgentSlackChannelSubscription.Status.ACTIVE,
            )
            desired_keys = set(desired)
            for subscription in active_subscriptions:
                key = (str(subscription.workspace_id), subscription.channel_id)
                if key not in desired_keys:
                    disable_subscription(agent, str(subscription.id))
        except SlackIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse(_serialize_slack_app(agent, request=request))


@method_decorator(csrf_exempt, name="dispatch")
class SlackEventsView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if not verify_slack_signature(
            body=request.body,
            timestamp=str(request.headers.get("X-Slack-Request-Timestamp") or ""),
            signature=str(request.headers.get("X-Slack-Signature") or ""),
        ):
            return JsonResponse({"error": "Invalid Slack signature."}, status=403)

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        event_type = str(payload.get("type") or "").strip()
        if event_type == "url_verification":
            challenge = str(payload.get("challenge") or "")
            return JsonResponse({"challenge": challenge})

        if event_type != "event_callback":
            return JsonResponse({"ok": True, "ignored": True, "reason": "unsupported_type"})

        message = slack_event_message_from_payload(payload)
        if message is None:
            return JsonResponse({"ok": True, "ignored": True, "reason": "ignored_event"})

        result = ingest_event_message(message)
        return JsonResponse({"ok": True, **result})
