"""Console endpoints for native Discord bot OAuth."""

import json
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.views import View

from api.agent.system_skills.defaults import DISCORD_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.models import PersistentAgentDiscordChannelSubscription, PersistentAgentDiscordOAuthSession, PersistentAgentSystemSkillState
from api.services.discord_bot import DiscordBotIntegrationError, build_discord_bot_invite_url, build_discord_oauth_start_url, disconnect_discord_native_integration, disable_subscription, discover_channels, ensure_subscription, handle_discord_oauth_callback, list_claimed_guilds, list_subscriptions, start_discord_oauth
from console.agent_chat.access import resolve_manageable_agent_for_request
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body
from console.context_helpers import build_console_context


def _discord_permission_denied_response(message: str = "Not permitted to manage this agent.") -> JsonResponse:
    return JsonResponse({"error": message}, status=403)


def _discord_skill_enabled(agent) -> bool:
    return PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key=DISCORD_NATIVE_SYSTEM_SKILL_KEY,
        is_enabled=True,
    ).exists()


def _serialize_discord_app(agent) -> dict[str, Any]:
    subscriptions = list_subscriptions(agent)
    active_subscriptions = [
        subscription for subscription in subscriptions if subscription.get("status") == PersistentAgentDiscordChannelSubscription.Status.ACTIVE
    ]
    guilds = list_claimed_guilds(agent)
    return {
        "provider_key": "discord",
        "display_name": "Discord",
        "description": "Connect Discord servers and subscribe this agent to selected channels.",
        "icon": "discord",
        "native": True,
        "connected": bool(guilds),
        "subscribed": bool(active_subscriptions),
        "skill_enabled": _discord_skill_enabled(agent),
        "guilds": guilds,
        "subscriptions": subscriptions,
        "active_subscription_count": len(active_subscriptions),
        "guild_count": len(guilds),
        "connect_url": build_discord_oauth_start_url(agent),
        "bot_invite_url": build_discord_bot_invite_url(),
    }


def _resolve_discord_agent(request: HttpRequest, agent_id: str):
    return resolve_manageable_agent_for_request(
        request,
        agent_id,
        allow_delinquent_personal_chat=True,
    )


def _resolve_discord_owner(request: HttpRequest):
    context = build_console_context(request)
    if context.current_context.type == "organization":
        membership = context.current_membership
        if membership is None or not context.can_manage_org_agents:
            raise PermissionDenied("You do not have permission to manage organization integrations.")
        return None, membership.org
    return request.user, None


def _enable_discord_native_skill(agent) -> dict[str, object]:
    return enable_system_skills(agent, [DISCORD_NATIVE_SYSTEM_SKILL_KEY])


def _discord_oauth_complete_response(*, agent_id: str, guild_count: int) -> HttpResponse:
    payload = json.dumps(
        {
            "type": "gobii:discord_oauth_complete",
            "status": "success",
            "agent_id": agent_id,
            "guild_count": guild_count,
        }
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Discord Connected</title>
</head>
<body>
  <p id="status">Discord connected. This tab will close automatically.</p>
  <button type="button" id="close-button">Close tab</button>
  <script>
    (function() {{
      var payload = {payload};
      function closeTab() {{
        window.close();
        window.setTimeout(function() {{
          document.getElementById("status").textContent = "Discord connected. You can close this tab.";
        }}, 500);
      }}
      if (window.opener && !window.opener.closed) {{
        window.opener.postMessage(payload, window.location.origin);
      }}
      document.getElementById("close-button").addEventListener("click", closeTab);
      closeTab();
    }}());
  </script>
</body>
</html>"""
    return HttpResponse(html, content_type="text/html")


class DiscordOAuthStartView(ApiLoginRequiredMixin, View):
    def get(self, request):
        agent_id = str(request.GET.get("agent_id") or "").strip()
        if not agent_id:
            return HttpResponseBadRequest("agent_id is required.")
        try:
            agent = resolve_manageable_agent_for_request(
                request,
                agent_id,
                allow_delinquent_personal_chat=True,
            )
            return HttpResponseRedirect(start_discord_oauth(agent, request.user))
        except PermissionDenied:
            return _discord_permission_denied_response()
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)


class DiscordOAuthCallbackView(ApiLoginRequiredMixin, View):
    def get(self, request):
        error = str(request.GET.get("error") or "").strip()
        if error:
            return JsonResponse({"error": error}, status=400)
        state = str(request.GET.get("state") or "").strip()
        code = str(request.GET.get("code") or "").strip()
        if not state or not code:
            return HttpResponseBadRequest("state and code are required.")
        try:
            session = PersistentAgentDiscordOAuthSession.objects.select_related("agent").get(state=state)
            agent_id = str(session.agent_id)
            resolve_manageable_agent_for_request(
                request,
                agent_id,
                allow_delinquent_personal_chat=True,
            )
            result = handle_discord_oauth_callback(
                state=state,
                code=code,
                selected_guild_id=str(request.GET.get("guild_id") or ""),
                selected_permissions=str(request.GET.get("permissions") or ""),
            )
        except PersistentAgentDiscordOAuthSession.DoesNotExist:
            return JsonResponse({"error": "Discord authorization state was not found."}, status=404)
        except PermissionDenied:
            return _discord_permission_denied_response()
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return _discord_oauth_complete_response(agent_id=agent_id, guild_count=result.claimed_count)


class AgentDiscordAppView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_discord_agent(request, agent_id)
        except PermissionDenied:
            return _discord_permission_denied_response()
        return JsonResponse(_serialize_discord_app(agent))


class AgentDiscordConnectView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_discord_agent(request, agent_id)
            skill_result = _enable_discord_native_skill(agent)
        except PermissionDenied:
            return _discord_permission_denied_response()
        if skill_result.get("status") != "success" or skill_result.get("invalid"):
            return JsonResponse({"error": "Unable to enable Discord for this agent."}, status=400)
        return JsonResponse(
            {
                "connect_url": build_discord_oauth_start_url(agent),
                "skill_enabled": True,
                "app": _serialize_discord_app(agent),
            }
        )


class DiscordDisconnectView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            owner_user, owner_org = _resolve_discord_owner(request)
        except PermissionDenied:
            return _discord_permission_denied_response("Not permitted to manage Discord integrations.")
        result = disconnect_discord_native_integration(owner_user=owner_user, organization=owner_org)
        return JsonResponse({"revoked": True, **result})


class AgentDiscordChannelsView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, guild_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_discord_agent(request, agent_id)
            result = discover_channels(
                agent,
                guild_id=str(guild_id or "").strip(),
                query=str(request.GET.get("q") or "").strip(),
                limit=200,
            )
        except PermissionDenied:
            return _discord_permission_denied_response()
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"status": "error", "message": str(exc), "channels": []}, status=400)
        return JsonResponse(result)


class AgentDiscordSubscriptionsView(ApiLoginRequiredMixin, View):
    http_method_names = ["patch"]

    def patch(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
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
            guild_id = str(raw_subscription.get("guild_id") or "").strip()
            channel_id = str(raw_subscription.get("channel_id") or "").strip()
            channel_name = str(raw_subscription.get("channel_name") or "").strip()
            if not guild_id or not channel_id:
                return HttpResponseBadRequest("guild_id and channel_id are required for every subscription.")
            desired[(guild_id, channel_id)] = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "channel_name": channel_name,
            }

        try:
            agent = _resolve_discord_agent(request, agent_id)
        except PermissionDenied:
            return _discord_permission_denied_response()

        try:
            skill_result = _enable_discord_native_skill(agent)
            if skill_result.get("status") != "success" or skill_result.get("invalid"):
                return JsonResponse({"error": "Unable to enable Discord for this agent."}, status=400)

            for subscription in desired.values():
                ensure_subscription(
                    agent,
                    guild_id=subscription["guild_id"],
                    channel_id=subscription["channel_id"],
                    channel_name=subscription["channel_name"],
                )

            active_subscriptions = PersistentAgentDiscordChannelSubscription.objects.select_related("guild").filter(
                agent=agent,
                status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
            )
            desired_keys = set(desired)
            for subscription in active_subscriptions:
                key = (subscription.guild.guild_id, subscription.channel_id)
                if key not in desired_keys:
                    disable_subscription(agent, str(subscription.id))
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse(_serialize_discord_app(agent))
