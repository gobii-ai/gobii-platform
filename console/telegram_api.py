"""Console and webhook endpoints for native Telegram managed bots."""

import json
from typing import Any, Mapping

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from api.models import PersistentAgentTelegramBotIdentity
from api.services.telegram_bot import (
    TelegramBotIntegrationError,
    active_telegram_identity,
    complete_managed_bot_provisioning,
    disconnect_telegram_native_integration,
    ingest_agent_bot_update,
    serialize_telegram_app,
    send_telegram_manager_message,
    start_telegram_connect,
    sync_telegram_bot_profile,
    upsert_telegram_user_link_from_start,
    validate_telegram_link_payload,
)
from console.agent_chat.access import resolve_manageable_agent_for_request
from console.api_helpers import ApiLoginRequiredMixin


def _telegram_permission_denied_response(message: str = "Not permitted to manage this agent.") -> JsonResponse:
    return JsonResponse({"error": message}, status=403)


def _resolve_telegram_agent(request: HttpRequest, agent_id: str):
    return resolve_manageable_agent_for_request(
        request,
        agent_id,
        allow_delinquent_personal_chat=True,
    )


def _json_body(request: HttpRequest) -> Mapping[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON body.") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("JSON body must be an object.")
    return payload


class AgentTelegramAppView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_telegram_agent(request, agent_id)
            return JsonResponse(serialize_telegram_app(agent))
        except PermissionDenied:
            return _telegram_permission_denied_response()
        except TelegramBotIntegrationError as exc:
            return JsonResponse(
                {
                    "provider_key": "telegram",
                    "display_name": "Telegram",
                    "description": "Create a managed Telegram bot identity for this agent.",
                    "icon": "telegram",
                    "native": True,
                    "connected": False,
                    "subscribed": False,
                    "skill_enabled": False,
                    "user_linked": False,
                    "status": "configuration_error",
                    "error": str(exc),
                    "bot_username": "",
                    "bot_display_name": "",
                    "profile_sync_status": "",
                    "profile_sync_error": "",
                    "manager_link_url": "",
                    "create_bot_url": "",
                    "chats": [],
                    "active_chat_count": 0,
                },
                status=200,
            )


class AgentTelegramConnectView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_telegram_agent(request, agent_id)
            result = start_telegram_connect(agent, request.user)
        except PermissionDenied:
            return _telegram_permission_denied_response()
        except TelegramBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse(
            {
                "status": result.status,
                "manager_link_url": result.manager_link_url,
                "create_bot_url": result.create_bot_url,
                "user_linked": result.user_linked,
                "suggested_username": result.suggested_username,
                "suggested_name": result.suggested_name,
                "message": result.message,
                "app": serialize_telegram_app(agent),
            }
        )


class AgentTelegramSyncProfileView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_telegram_agent(request, agent_id)
            identity = active_telegram_identity(agent)
            if identity is None:
                return JsonResponse({"error": "No active Telegram bot is connected for this agent."}, status=400)
            result = sync_telegram_bot_profile(identity)
        except PermissionDenied:
            return _telegram_permission_denied_response()
        except TelegramBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        status_code = 200 if result.get("status") == "success" else 400
        return JsonResponse({"profile_sync": result, "app": serialize_telegram_app(agent)}, status=status_code)


class AgentTelegramDisconnectView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_telegram_agent(request, agent_id)
        except PermissionDenied:
            return _telegram_permission_denied_response()
        result = disconnect_telegram_native_integration(agent)
        return JsonResponse({"revoked": True, **result, "app": serialize_telegram_app(agent)})


@method_decorator(csrf_exempt, name="dispatch")
class TelegramManagerWebhookView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        expected = settings.TELEGRAM_MANAGER_WEBHOOK_SECRET.strip()
        if expected:
            provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if provided != expected:
                return JsonResponse({"error": "Invalid Telegram webhook secret."}, status=403)
        try:
            payload = _json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        try:
            message = payload.get("message")
            if isinstance(message, Mapping):
                text = str(message.get("text") or "").strip()
                sender = message.get("from")
                chat = message.get("chat")
                chat_id = str(chat.get("id") or "") if isinstance(chat, Mapping) else ""
                if text == "/start" or text.startswith("/start@"):
                    send_telegram_manager_message(
                        chat_id,
                        "Open Telegram from Gobii's agent integration screen, or paste the full /start command shown there.",
                    )
                    return JsonResponse({"ignored": True, "reason": "missing_start_token"})
                if text.startswith("/start ") and isinstance(sender, Mapping):
                    try:
                        link_payload = validate_telegram_link_payload(text.split(" ", 1)[1].strip())
                        result = upsert_telegram_user_link_from_start(link_payload, sender)
                    except TelegramBotIntegrationError as exc:
                        send_telegram_manager_message(chat_id, f"Telegram could not be linked: {exc}")
                        return JsonResponse({"ignored": True, "reason": "link_failed", "error": str(exc)})
                    send_telegram_manager_message(
                        chat_id,
                        "Telegram is linked to Gobii. Return to Gobii and click Connect again to create this agent's bot.",
                    )
                    return JsonResponse(result)

            if isinstance(payload.get("managed_bot"), Mapping):
                return JsonResponse(complete_managed_bot_provisioning(payload))
        except TelegramBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"ignored": True, "reason": "unsupported_update"})


@method_decorator(csrf_exempt, name="dispatch")
class TelegramAgentBotWebhookView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, identity_id: str, *args: Any, **kwargs: Any):
        try:
            identity = PersistentAgentTelegramBotIdentity.objects.select_related("agent").get(id=identity_id)
        except PersistentAgentTelegramBotIdentity.DoesNotExist:
            return JsonResponse({"error": "Telegram bot identity was not found."}, status=404)
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != identity.webhook_secret:
            return JsonResponse({"error": "Invalid Telegram webhook secret."}, status=403)
        try:
            payload = _json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        try:
            return JsonResponse(ingest_agent_bot_update(identity, payload))
        except TelegramBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
