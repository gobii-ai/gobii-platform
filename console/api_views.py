from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    build_web_agent_address,
    build_web_user_address,
)
from api.services.web_sessions import (
    WEB_SESSION_TTL_SECONDS,
    end_web_session,
    heartbeat_web_session,
    start_web_session,
    touch_web_session,
)

from console.agent_chat.access import resolve_agent
from console.agent_chat.timeline import (
    DEFAULT_PAGE_SIZE,
    TimelineDirection,
    compute_processing_status,
    fetch_timeline_window,
    serialize_message_event,
)


def _ensure_console_endpoints(agent: PersistentAgent, user) -> tuple[str, str]:
    """Ensure dedicated console endpoints exist and return (sender, recipient) addresses."""
    channel = CommsChannel.WEB
    sender_address = build_web_user_address(user.id, agent.id)
    recipient_address = build_web_agent_address(agent.id)

    agent_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=channel,
        address=recipient_address,
        defaults={
            "owner_agent": agent,
            "is_primary": bool(
                agent.preferred_contact_endpoint
                and agent.preferred_contact_endpoint.channel == CommsChannel.WEB
            ),
        },
    )
    updates = []
    if agent_endpoint.owner_agent_id != agent.id:
        agent_endpoint.owner_agent = agent
        updates.append("owner_agent")
    if not agent_endpoint.address:
        agent_endpoint.address = recipient_address
        updates.append("address")
    if updates:
        agent_endpoint.save(update_fields=updates)

    PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=channel,
        address=sender_address,
        defaults={"owner_agent": None, "is_primary": False},
    )
    return sender_address, recipient_address


@method_decorator(csrf_exempt, name="dispatch")
class AgentTimelineAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent(request.user, request.session, agent_id)

        direction_raw = (request.GET.get("direction") or "initial").lower()
        direction: TimelineDirection
        if direction_raw not in {"initial", "older", "newer"}:
            return HttpResponseBadRequest("Invalid direction parameter")
        direction = direction_raw  # type: ignore[assignment]

        cursor = request.GET.get("cursor") or None
        try:
            limit = int(request.GET.get("limit", DEFAULT_PAGE_SIZE))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")

        window = fetch_timeline_window(
            agent,
            cursor=cursor,
            direction=direction,
            limit=limit,
        )
        payload = {
            "events": window.events,
            "oldest_cursor": window.oldest_cursor,
            "newest_cursor": window.newest_cursor,
            "has_more_older": window.has_more_older,
            "has_more_newer": window.has_more_newer,
            "processing_active": window.processing_active,
        }
        return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class AgentMessageCreateAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent(request.user, request.session, agent_id)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        message_text = (body.get("body") or "").strip()
        if not message_text:
            return HttpResponseBadRequest("Message body is required")

        sender_address, recipient_address = _ensure_console_endpoints(agent, request.user)

        # Keep the web session alive whenever the user sends a message from the console UI.
        touch_web_session(
            agent,
            request.user,
            source="message",
            create=True,
            ttl_seconds=WEB_SESSION_TTL_SECONDS,
        )

        if not agent.is_sender_whitelisted(CommsChannel.WEB, sender_address):
            return HttpResponseForbidden("You are not allowed to message this agent.")

        parsed = ParsedMessage(
            sender=sender_address,
            recipient=recipient_address,
            subject=None,
            body=message_text,
            attachments=[],
            raw_payload={"source": "console", "user_id": request.user.id},
            msg_channel=CommsChannel.WEB,
        )
        info = ingest_inbound_message(CommsChannel.WEB, parsed)
        event = serialize_message_event(info.message)
        return JsonResponse({"event": event}, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class AgentProcessingStatusAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent(request.user, request.session, agent_id)
        return JsonResponse({"processing_active": compute_processing_status(agent)})


def _parse_ttl(payload: dict | None) -> int:
    if not payload:
        return WEB_SESSION_TTL_SECONDS
    ttl_raw = payload.get("ttl_seconds")
    if ttl_raw is None:
        return WEB_SESSION_TTL_SECONDS
    try:
        ttl = int(ttl_raw)
    except (TypeError, ValueError):
        raise ValueError("ttl_seconds must be an integer")
    return max(10, ttl)


def _parse_session_key(payload: dict | None) -> str:
    key = (payload or {}).get("session_key")
    if not key:
        raise ValueError("session_key is required")
    return str(key)


def _session_response(result) -> JsonResponse:
    session = result.session
    payload = {
        "session_key": str(session.session_key),
        "ttl_seconds": result.ttl_seconds,
        "expires_at": result.expires_at.isoformat(),
        "last_seen_at": session.last_seen_at.isoformat(),
        "last_seen_source": session.last_seen_source,
    }
    if session.ended_at:
        payload["ended_at"] = session.ended_at.isoformat()
    return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class AgentWebSessionStartAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent(request.user, request.session, agent_id)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        try:
            ttl = _parse_ttl(body)
            result = start_web_session(agent, request.user, ttl_seconds=ttl)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        return _session_response(result)


@method_decorator(csrf_exempt, name="dispatch")
class AgentWebSessionHeartbeatAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent(request.user, request.session, agent_id)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        try:
            ttl = _parse_ttl(body)
            session_key = _parse_session_key(body)
            result = heartbeat_web_session(
                session_key,
                agent,
                request.user,
                ttl_seconds=ttl,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        return _session_response(result)


@method_decorator(csrf_exempt, name="dispatch")
class AgentWebSessionEndAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent(request.user, request.session, agent_id)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        try:
            session_key = _parse_session_key(body)
            result = end_web_session(session_key, agent, request.user)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        return _session_response(result)
