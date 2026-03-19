import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View

from api.models import (
    AgentSlackConfig,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)
from console.api_views import ApiLoginRequiredMixin, _parse_json_body

logger = logging.getLogger(__name__)


def _resolve_owned_agent(request: HttpRequest, agent_id: str) -> PersistentAgent:
    return get_object_or_404(
        PersistentAgent.objects.non_eval().alive(),
        pk=agent_id,
        user=request.user,
    )


def _get_or_create_slack_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
    """Return the agent's primary Slack endpoint, creating one if needed."""
    endpoint = agent.comms_endpoints.filter(
        channel=CommsChannel.SLACK,
        owner_agent=agent,
    ).first()
    if endpoint:
        return endpoint
    return PersistentAgentCommsEndpoint.objects.create(
        channel=CommsChannel.SLACK,
        address="",
        owner_agent=agent,
        is_primary=True,
    )


def _get_or_create_slack_config(
    endpoint: PersistentAgentCommsEndpoint,
) -> AgentSlackConfig:
    try:
        return endpoint.slack_config
    except AgentSlackConfig.DoesNotExist:
        return AgentSlackConfig.objects.create(endpoint=endpoint)


def _serialize_slack_settings(
    agent: PersistentAgent,
    endpoint: PersistentAgentCommsEndpoint,
    config: AgentSlackConfig,
) -> dict:
    return {
        "agent_id": str(agent.id),
        "endpoint_address": endpoint.address,
        "workspace_id": config.workspace_id,
        "channel_id": config.channel_id,
        "thread_policy": config.thread_policy,
        "is_enabled": config.is_enabled,
        "has_bot_token": bool(config.bot_token_encrypted),
        "connection_last_ok_at": (
            config.connection_last_ok_at.isoformat() if config.connection_last_ok_at else None
        ),
        "connection_error": config.connection_error,
        "global_slack_enabled": settings.SLACK_ENABLED,
        "global_slack_disabled_reason": getattr(settings, "SLACK_DISABLED_REASON", ""),
    }


class AgentSlackSettingsAPIView(ApiLoginRequiredMixin, View):
    """GET/POST Slack settings for an agent."""

    def get(self, request: HttpRequest, agent_id: str) -> JsonResponse:
        agent = _resolve_owned_agent(request, agent_id)
        endpoint = _get_or_create_slack_endpoint(agent)
        config = _get_or_create_slack_config(endpoint)
        return JsonResponse(_serialize_slack_settings(agent, endpoint, config))

    def post(self, request: HttpRequest, agent_id: str) -> JsonResponse:
        agent = _resolve_owned_agent(request, agent_id)
        data = _parse_json_body(request)
        if isinstance(data, JsonResponse):
            return data

        endpoint = _get_or_create_slack_endpoint(agent)
        config = _get_or_create_slack_config(endpoint)

        # Update fields
        if "workspace_id" in data:
            config.workspace_id = (data["workspace_id"] or "").strip()
        if "channel_id" in data:
            config.channel_id = (data["channel_id"] or "").strip()
        if "thread_policy" in data:
            policy = data["thread_policy"]
            if policy in {c.value for c in AgentSlackConfig.ThreadPolicy}:
                config.thread_policy = policy
        if "is_enabled" in data:
            config.is_enabled = bool(data["is_enabled"])
        if "bot_token" in data:
            token = (data["bot_token"] or "").strip()
            if token:
                config.set_bot_token(token)
            elif data.get("clear_bot_token"):
                config.bot_token_encrypted = None

        # Update endpoint address to canonical form
        if config.workspace_id and config.channel_id:
            canonical = f"slack:{config.channel_id}#{config.workspace_id}"
            if endpoint.address != canonical:
                endpoint.address = canonical
                endpoint.save(update_fields=["address"])

        config.save()

        return JsonResponse(_serialize_slack_settings(agent, endpoint, config))


class AgentSlackSettingsTestAPIView(ApiLoginRequiredMixin, View):
    """Test the Slack connection by calling auth.test."""

    def post(self, request: HttpRequest, agent_id: str) -> JsonResponse:
        agent = _resolve_owned_agent(request, agent_id)
        endpoint = _get_or_create_slack_endpoint(agent)
        config = _get_or_create_slack_config(endpoint)

        bot_token = config.get_bot_token()
        if not bot_token:
            return JsonResponse(
                {"ok": False, "error": "No bot token configured."},
                status=400,
            )

        try:
            from slack_sdk import WebClient
            from slack_sdk.errors import SlackApiError

            client = WebClient(token=bot_token)

            # Test auth
            auth_response = client.auth_test()
            if not auth_response.get("ok"):
                error_msg = auth_response.get("error", "Unknown error")
                config.connection_error = error_msg
                config.save(update_fields=["connection_error"])
                return JsonResponse({"ok": False, "error": error_msg})

            # If channel_id is set, verify bot can access it
            if config.channel_id:
                try:
                    client.conversations_info(channel=config.channel_id)
                except SlackApiError as e:
                    error_msg = f"Bot cannot access channel {config.channel_id}: {e.response['error']}"
                    config.connection_error = error_msg
                    config.save(update_fields=["connection_error"])
                    return JsonResponse({"ok": False, "error": error_msg})

            config.connection_last_ok_at = timezone.now()
            config.connection_error = ""
            config.save(update_fields=["connection_last_ok_at", "connection_error"])

            return JsonResponse({
                "ok": True,
                "team": auth_response.get("team"),
                "bot_user_id": auth_response.get("user_id"),
                "team_id": auth_response.get("team_id"),
            })

        except SlackApiError as e:
            error_msg = str(e)
            config.connection_error = error_msg
            config.save(update_fields=["connection_error"])
            return JsonResponse({"ok": False, "error": error_msg})
        except Exception as e:
            logger.error("Slack connection test failed for agent %s: %s", agent_id, e, exc_info=True)
            return JsonResponse(
                {"ok": False, "error": "Connection test failed unexpectedly."},
                status=500,
            )
