"""Console endpoints for native Discord bot OAuth."""

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views import View

from api.agent.system_skills.defaults import DISCORD_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.models import (
    PersistentAgentDiscordChannelSubscription,
    PersistentAgentDiscordGuild,
    PersistentAgentDiscordOAuthSession,
    PersistentAgentSystemSkillState,
    UserDiscordIdentity,
)
from api.services.discord_bot import (
    DISCORD_IDENTITY_OAUTH_STATE_PREFIX,
    DiscordBotIntegrationError,
    build_discord_oauth_start_url,
    disconnect_discord_guild_for_owner,
    disconnect_discord_native_integration,
    discord_identity_oauth_return_origin,
    disable_subscription,
    discover_channels,
    ensure_subscription,
    handle_discord_identity_oauth_callback,
    handle_discord_oauth_callback,
    list_claimed_guilds,
    list_claimed_guilds_for_owner,
    list_subscriptions,
    start_discord_oauth,
    start_discord_identity_oauth,
)
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


def _discord_oauth_complete_response(
    request: HttpRequest,
    payload: dict[str, Any],
    *,
    target_origin: str = "",
) -> HttpResponse:
    return render(
        request,
        "console/discord_oauth_callback.html",
        {
            "payload": payload,
            "target_origin": target_origin or request.build_absolute_uri("/").rstrip("/"),
        },
    )


def _discord_identity_oauth_complete_response(
    request: HttpRequest,
    *,
    status: str,
    message: str,
    target_origin: str = "",
) -> HttpResponse:
    return _discord_oauth_complete_response(
        request,
        {"type": "gobii:discord_identity_oauth_complete", "status": status, "message": message},
        target_origin=target_origin,
    )


class DiscordIdentityOAuthStartView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            return HttpResponseRedirect(
                start_discord_identity_oauth(
                    request.user,
                    return_origin=request.build_absolute_uri("/").rstrip("/"),
                )
            )
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)


class DiscordIdentityView(ApiLoginRequiredMixin, View):
    http_method_names = ["delete"]

    def delete(self, request: HttpRequest, *args: Any, **kwargs: Any):
        deleted, _details = UserDiscordIdentity.objects.filter(user=request.user).delete()
        return JsonResponse({"disconnected": bool(deleted)})


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
            return HttpResponseRedirect(
                start_discord_oauth(
                    agent,
                    request.user,
                    requested_guild_id=str(request.GET.get("guild_id") or ""),
                )
            )
        except PermissionDenied:
            return _discord_permission_denied_response()
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)


class DiscordOAuthCallbackView(ApiLoginRequiredMixin, View):
    def get(self, request):
        error = str(request.GET.get("error") or "").strip()
        state = str(request.GET.get("state") or "").strip()
        identity_flow = state.startswith(DISCORD_IDENTITY_OAUTH_STATE_PREFIX)
        if error:
            if identity_flow:
                try:
                    target_origin = discord_identity_oauth_return_origin(state, request.user)
                except DiscordBotIntegrationError:
                    target_origin = ""
                return _discord_identity_oauth_complete_response(
                    request,
                    status="error",
                    message=error,
                    target_origin=target_origin,
                )
            return JsonResponse({"error": error}, status=400)
        code = str(request.GET.get("code") or "").strip()
        if not state or not code:
            return HttpResponseBadRequest("state and code are required.")
        if identity_flow:
            target_origin = ""
            try:
                target_origin = discord_identity_oauth_return_origin(state, request.user)
                handle_discord_identity_oauth_callback(
                    state=state,
                    code=code,
                    user=request.user,
                )
            except DiscordBotIntegrationError as exc:
                return _discord_identity_oauth_complete_response(
                    request,
                    status="error",
                    message=str(exc),
                    target_origin=target_origin,
                )
            return _discord_identity_oauth_complete_response(
                request,
                status="success",
                message="Your Discord account is now linked to your Gobii profile.",
                target_origin=target_origin,
            )
        try:
            session = PersistentAgentDiscordOAuthSession.objects.select_related("agent").get(state=state)
            agent_id = str(session.agent_id)
            resolve_manageable_agent_for_request(
                request,
                agent_id,
                allow_delinquent_personal_chat=True,
            )
            handle_discord_oauth_callback(
                state=state,
                code=code,
            )
        except PersistentAgentDiscordOAuthSession.DoesNotExist:
            return JsonResponse({"error": "Discord authorization state was not found."}, status=404)
        except PermissionDenied:
            return _discord_permission_denied_response()
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return _discord_oauth_complete_response(
            request,
            {
                "type": "gobii:discord_oauth_complete",
                "status": "success",
                "agent_id": agent_id,
                "guild_count": 1,
                "message": "Discord connected. This tab will close automatically.",
            },
        )


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
        app = _serialize_discord_app(agent)
        return JsonResponse(
            {
                "connect_url": build_discord_oauth_start_url(agent),
                "skill_enabled": True,
                "oauth_required": not app["connected"],
                "app": app,
            }
        )


class DiscordContextAppView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            owner_user, owner_org = _resolve_discord_owner(request)
        except PermissionDenied:
            return _discord_permission_denied_response("Not permitted to view Discord integrations.")
        guilds = list_claimed_guilds_for_owner(owner_user=owner_user, organization=owner_org)
        return JsonResponse(
            {
                "connected": bool(guilds),
                "guild_count": len(guilds),
                "guilds": guilds,
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
        failed_guilds = result.get("failed_guilds") or []
        return JsonResponse(
            {"revoked": not failed_guilds, **result},
            status=502 if failed_guilds else 200,
        )


class DiscordGuildDisconnectView(ApiLoginRequiredMixin, View):
    http_method_names = ["delete"]

    def delete(self, request: HttpRequest, guild_id: str, *args: Any, **kwargs: Any):
        try:
            owner_user, owner_org = _resolve_discord_owner(request)
            result = disconnect_discord_guild_for_owner(
                guild_id=str(guild_id or "").strip(),
                owner_user=owner_user,
                organization=owner_org,
            )
        except PermissionDenied:
            return _discord_permission_denied_response("Not permitted to manage Discord integrations.")
        except PersistentAgentDiscordGuild.DoesNotExist:
            return JsonResponse({"error": "Discord server was not found in this context."}, status=404)
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
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
