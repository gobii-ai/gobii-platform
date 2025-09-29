from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.models import CommsChannel, PersistentAgent, PersistentAgentCommsEndpoint

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
    channel = CommsChannel.OTHER
    sender_address = f"console-user:{user.id}"
    recipient_address = f"console-agent:{agent.id}"

    # Ensure recipient endpoint is owned by agent for lookup
    agent_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=channel,
        address=recipient_address,
        defaults={"owner_agent": agent, "is_primary": False},
    )
    if agent_endpoint.owner_agent_id != agent.id:
        agent_endpoint.owner_agent = agent
        agent_endpoint.save(update_fields=["owner_agent"])

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

        parsed = ParsedMessage(
            sender=sender_address,
            recipient=recipient_address,
            subject=None,
            body=message_text,
            attachments=[],
            raw_payload={"source": "console"},
            msg_channel=CommsChannel.OTHER,
        )
        info = ingest_inbound_message(CommsChannel.OTHER, parsed)
        event = serialize_message_event(info.message)
        return JsonResponse({"event": event}, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class AgentProcessingStatusAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent(request.user, request.session, agent_id)
        return JsonResponse({"processing_active": compute_processing_status(agent)})
