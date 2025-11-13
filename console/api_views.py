import json
import logging
import secrets
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.agent.tools.mcp_manager import get_mcp_manager
from api.models import (
    CommsChannel,
    MCPServerConfig,
    MCPServerOAuthCredential,
    MCPServerOAuthSession,
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

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

from console.agent_chat.access import resolve_agent
from console.agent_chat.timeline import (
    DEFAULT_PAGE_SIZE,
    TimelineDirection,
    build_processing_snapshot,
    compute_processing_status,
    fetch_timeline_window,
    serialize_message_event,
    serialize_processing_snapshot,
)
from console.context_helpers import build_console_context
from console.forms import MCPServerConfigForm
from console.views import _track_org_event_for_console, _mcp_server_event_properties
from api.services import mcp_servers as mcp_server_service


logger = logging.getLogger(__name__)


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


def _resolve_mcp_server_config(request: HttpRequest, config_id: str) -> MCPServerConfig:
    """Resolve an MCP server configuration the user is allowed to manage."""
    config = get_object_or_404(MCPServerConfig, pk=config_id)
    if config.scope == MCPServerConfig.Scope.PLATFORM:
        raise PermissionDenied("Platform-managed MCP servers cannot be modified from the console.")

    if config.scope == MCPServerConfig.Scope.USER:
        if config.user_id != request.user.id:
            raise PermissionDenied("You do not have access to this MCP server.")
    elif config.scope == MCPServerConfig.Scope.ORGANIZATION:
        context = build_console_context(request)
        membership = context.current_membership
        if (
            context.current_context.type != "organization"
            or membership is None
            or str(membership.org_id) != str(config.organization_id)
            or not context.can_manage_org_agents
        ):
            raise PermissionDenied("You do not have access to this MCP server.")
    return config


def _require_active_session(request: HttpRequest, session_id: uuid.UUID) -> MCPServerOAuthSession:
    """Fetch a pending OAuth session and enforce ownership + expiry."""
    session = get_object_or_404(MCPServerOAuthSession, pk=session_id)

    if session.initiated_by_id != request.user.id:
        raise PermissionDenied("You do not have access to this OAuth session.")

    if session.has_expired():
        session.delete()
        raise PermissionDenied("OAuth session has expired. Restart the flow.")

    # Re-check access against server configuration in case ownership changed mid-flow.
    _resolve_mcp_server_config(request, str(session.server_config_id))
    return session


def _resolve_mcp_owner(request: HttpRequest) -> tuple[str, str, object | None, object | None]:
    context = build_console_context(request)
    if context.current_context.type == "organization":
        membership = context.current_membership
        if membership is None or not context.can_manage_org_agents:
            raise PermissionDenied("You do not have permission to manage organization MCP servers.")
        return (
            "organization",
            membership.org.name,
            None,
            membership.org,
        )

    label = request.user.get_full_name() or request.user.username or request.user.email or "Personal"
    return ("user", label, request.user, None)


def _owner_queryset(owner_scope: str, owner_user, owner_org):
    queryset = MCPServerConfig.objects.select_related("oauth_credential")
    if owner_scope == "organization" and owner_org is not None:
        return queryset.filter(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization=owner_org,
        ).order_by("display_name")
    return queryset.filter(
        scope=MCPServerConfig.Scope.USER,
        user=owner_user,
    ).order_by("display_name")


def _serialize_mcp_server(
    server: MCPServerConfig,
    request: HttpRequest | None = None,
    pending_servers: set[str] | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": str(server.id),
        "name": server.name,
        "display_name": server.display_name,
        "description": server.description,
        "command": server.command,
        "command_args": server.command_args,
        "url": server.url,
        "auth_method": server.auth_method,
        "is_active": server.is_active,
        "scope": server.scope,
        "scope_label": server.get_scope_display(),
        "updated_at": server.updated_at.isoformat(),
        "created_at": server.created_at.isoformat(),
    }
    if request is not None:
        pending = False
        if (
            request.user.is_authenticated
            and server.auth_method == MCPServerConfig.AuthMethod.OAUTH2
        ):
            if pending_servers is not None:
                pending = str(server.id) in pending_servers
            else:
                pending = server.oauth_sessions.filter(
                    initiated_by=request.user,
                    expires_at__gt=timezone.now(),
                ).exists()
        credential = getattr(server, "oauth_credential", None)
        if credential is None:
            try:
                credential = server.oauth_credential
            except MCPServerOAuthCredential.DoesNotExist:
                credential = None
        data.update(
            {
                "oauth_status_url": reverse("console-mcp-oauth-status", args=[server.id]),
                "oauth_revoke_url": reverse("console-mcp-oauth-revoke", args=[server.id]),
                "oauth_connected": credential is not None,
                "oauth_pending": pending,
            }
        )
    return data


def _serialize_mcp_server_detail(server: MCPServerConfig, request: HttpRequest | None = None) -> dict[str, object]:
    data = _serialize_mcp_server(server, request=request)
    data.update(
        {
            "metadata": server.metadata or {},
            "headers": server.headers or {},
            "environment": server.environment or {},
            "prefetch_apps": server.prefetch_apps or [],
            "command": server.command,
            "command_args": server.command_args or [],
            "description": server.description,
        }
    )
    if request is not None:
        data["oauth_status_url"] = reverse("console-mcp-oauth-status", args=[server.id])
        data["oauth_revoke_url"] = reverse("console-mcp-oauth-revoke", args=[server.id])
    return data


def _form_errors(form: MCPServerConfigForm) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    for field, field_errors in form.errors.items():
        errors[field] = [str(error) for error in field_errors]
    non_field = form.non_field_errors()
    if non_field:
        errors["non_field_errors"] = [str(error) for error in non_field]
    return errors


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    return payload


def _web_chat_properties(agent: PersistentAgent, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return analytics properties annotated with agent + organization context."""

    payload: dict[str, Any] = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
    }
    if extra:
        payload.update(extra)

    return Analytics.with_org_properties(payload, organization=getattr(agent, "organization", None))


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
            "processing_snapshot": serialize_processing_snapshot(window.processing_snapshot),
            "agent_color_hex": agent.get_display_color(),
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
        session_result = touch_web_session(
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

        props = {
            "message_id": str(info.message.id),
            "message_length": len(message_text),
        }
        if session_result:
            props["session_key"] = str(session_result.session.session_key)
            props["session_ttl_seconds"] = session_result.ttl_seconds

        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.WEB_CHAT_MESSAGE_SENT,
            source=AnalyticsSource.WEB,
            properties=_web_chat_properties(agent, props),
        )

        return JsonResponse({"event": event}, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class AgentProcessingStatusAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent(request.user, request.session, agent_id)
        snapshot = build_processing_snapshot(agent)
        return JsonResponse(
            {
                "processing_active": snapshot.active,
                "processing_snapshot": serialize_processing_snapshot(snapshot),
            }
        )


class MCPServerListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        owner_scope, owner_label, owner_user, owner_org = _resolve_mcp_owner(request)
        queryset = list(_owner_queryset(owner_scope, owner_user, owner_org))
        pending_servers: set[str] = set()
        if request.user.is_authenticated and queryset:
            server_ids = [server.id for server in queryset]
            pending_servers = {
                str(server_id)
                for server_id in MCPServerOAuthSession.objects.filter(
                    server_config_id__in=server_ids,
                    initiated_by=request.user,
                    expires_at__gt=timezone.now(),
                ).values_list("server_config_id", flat=True)
            }
        servers = [_serialize_mcp_server(server, request=request, pending_servers=pending_servers) for server in queryset]
        return JsonResponse(
            {
                "owner_scope": owner_scope,
                "owner_label": owner_label,
                "result_count": len(servers),
                "servers": servers,
            }
        )

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        owner_scope, _, owner_user, owner_org = _resolve_mcp_owner(request)
        form = MCPServerConfigForm(payload, allow_commands=False)
        if form.is_valid():
            try:
                with transaction.atomic():
                    server = form.save(user=owner_user, organization=owner_org)
            except IntegrityError:
                form.add_error("name", "A server with that identifier already exists.")
            else:
                manager = get_mcp_manager()
                manager.refresh_server(str(server.id))
                _track_org_event_for_console(
                    request,
                    AnalyticsEvent.MCP_SERVER_CREATED,
                    _mcp_server_event_properties(request, server, owner_scope),
                    organization=owner_org,
                )
                return JsonResponse(
                    {
                        "server": _serialize_mcp_server_detail(server, request),
                        "message": "MCP server saved.",
                    },
                    status=201,
                )

        return JsonResponse({"errors": _form_errors(form)}, status=400)


class MCPServerDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "patch", "delete"]

    def get(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        return JsonResponse({"server": _serialize_mcp_server_detail(server, request)})

    def patch(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        form = MCPServerConfigForm(payload, instance=server, allow_commands=False)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save()
            except IntegrityError:
                form.add_error("name", "A server with that identifier already exists.")
            else:
                get_mcp_manager().refresh_server(str(updated.id))
                _track_org_event_for_console(
                    request,
                    AnalyticsEvent.MCP_SERVER_UPDATED,
                    _mcp_server_event_properties(request, updated, updated.scope),
                    organization=updated.organization,
                )
                return JsonResponse({
                    "server": _serialize_mcp_server_detail(updated, request),
                    "message": "MCP server updated.",
                })

        return JsonResponse({"errors": _form_errors(form)}, status=400)

    def delete(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        server_name = server.display_name
        organization = server.organization
        props = _mcp_server_event_properties(request, server, server.scope)
        cached_server_id = str(server.id)
        server.delete()
        get_mcp_manager().remove_server(cached_server_id)
        _track_org_event_for_console(
            request,
            AnalyticsEvent.MCP_SERVER_DELETED,
            props,
            organization=organization,
        )
        return JsonResponse({"message": f"MCP server '{server_name}' was deleted."})


class MCPOAuthStartView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        config_id = body.get("server_config_id")
        if not config_id:
            return HttpResponseBadRequest("server_config_id is required")

        config = _resolve_mcp_server_config(request, str(config_id))
        if config.auth_method != MCPServerConfig.AuthMethod.OAUTH2:
            return HttpResponseBadRequest("This MCP server is not configured for OAuth 2.0.")

        metadata = body.get("metadata") or {}
        if metadata and not isinstance(metadata, dict):
            return HttpResponseBadRequest("metadata must be a JSON object")

        scope_raw = body.get("scope") or ""
        if isinstance(scope_raw, list):
            scope = " ".join(str(part) for part in scope_raw if part)
        else:
            scope = str(scope_raw)

        expires_at = timezone.now() + timedelta(minutes=10)
        state = str(body.get("state") or secrets.token_urlsafe(32))

        callback_url = body.get("redirect_uri") or request.build_absolute_uri(reverse("console-mcp-oauth-callback-view"))

        manual_client_id = str(body.get("client_id") or "")
        manual_client_secret = str(body.get("client_secret") or "")
        client_id = manual_client_id
        client_secret = manual_client_secret

        if not client_id and metadata.get("registration_endpoint"):
            try:
                client_id, client_secret = self._register_dynamic_client(
                    request,
                    metadata,
                    callback_url,
                    config,
                )
            except ValueError as exc:
                return JsonResponse({"error": str(exc)}, status=400)
            except httpx.HTTPError as exc:
                return JsonResponse(
                    {"error": "Client registration failed", "detail": str(exc)},
                    status=502,
                )

        session = MCPServerOAuthSession(
            server_config=config,
            initiated_by=request.user,
            organization=config.organization if config.organization_id else None,
            user=config.user if config.scope == MCPServerConfig.Scope.USER else None,
            state=state,
            redirect_uri=callback_url,
            scope=scope,
            code_challenge=str(body.get("code_challenge") or ""),
            code_challenge_method=str(body.get("code_challenge_method") or ""),
            token_endpoint=str(body.get("token_endpoint") or ""),
            client_id=client_id,
            metadata=metadata,
            expires_at=expires_at,
        )

        code_verifier = body.get("code_verifier")
        if code_verifier:
            session.code_verifier = str(code_verifier)

        if client_secret:
            session.client_secret = str(client_secret)

        session.save()

        try:
            existing_credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            existing_credential = None

        payload = {
            "session_id": str(session.id),
            "state": state,
            "expires_at": expires_at.isoformat(),
            "has_existing_credentials": existing_credential is not None,
            "client_id": session.client_id or "",
        }
        return JsonResponse(payload, status=201)

    def _register_dynamic_client(self, request: HttpRequest, metadata: dict, callback_url: str, config: MCPServerConfig) -> tuple[str, str]:
        endpoint = metadata.get("registration_endpoint")
        if not endpoint:
            raise ValueError("OAuth server does not advertise a registration endpoint.")

        redirect_uri = callback_url
        payload = {
            "client_name": f"Gobii MCP - {config.display_name}",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_basic",
        }
        if metadata.get("scope"):
            payload["scope"] = metadata["scope"]
        elif metadata.get("scopes_supported"):
            payload["scope"] = " ".join(metadata["scopes_supported"])

        response = httpx.post(endpoint, json=payload, timeout=10.0)
        response.raise_for_status()
        client_info = response.json()
        client_id = client_info.get("client_id")
        client_secret = client_info.get("client_secret") or ""
        if not client_id:
            raise ValueError("Client registration response missing client_id")
        return str(client_id), str(client_secret)


class MCPOAuthSessionVerifierView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, session_id: uuid.UUID, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        code_verifier = body.get("code_verifier")
        if not code_verifier:
            return HttpResponseBadRequest("code_verifier is required")

        session = _require_active_session(request, session_id)
        session.code_verifier = str(code_verifier)

        if "code_challenge" in body:
            session.code_challenge = str(body.get("code_challenge") or "")
        if "code_challenge_method" in body:
            session.code_challenge_method = str(body.get("code_challenge_method") or "")
        session.save(update_fields=["code_verifier_encrypted", "code_challenge", "code_challenge_method", "updated_at"])
        return JsonResponse({"status": "ok"})


class MCPOAuthMetadataProxyView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        config_id = body.get("server_config_id")
        resource = body.get("resource") or body.get("path") or body.get("url")
        if not config_id or not resource:
            return HttpResponseBadRequest("server_config_id and resource are required")

        config = _resolve_mcp_server_config(request, str(config_id))
        base_url = config.url
        if not base_url:
            return HttpResponseBadRequest("This MCP server does not define a base URL.")

        target_url = urljoin(base_url, str(resource))
        parsed_base = urlparse(base_url)
        parsed_target = urlparse(target_url)

        if parsed_target.scheme not in {"http", "https"}:
            return HttpResponseBadRequest("Unsupported URL scheme for metadata request.")

        if parsed_target.netloc and parsed_target.netloc != parsed_base.netloc:
            return HttpResponseForbidden("Metadata requests must target the configured MCP host.")

        headers = body.get("headers") or {}
        if headers and not isinstance(headers, dict):
            return HttpResponseBadRequest("headers must be a JSON object")

        try:
            response = httpx.get(target_url, headers=headers or None, timeout=10.0)
        except httpx.HTTPError as exc:
            return JsonResponse(
                {"error": "Failed to contact MCP server", "detail": str(exc)},
                status=502,
            )

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            try:
                payload = response.json()
            except ValueError:
                payload = {"content": response.text}
                return JsonResponse(payload, status=response.status_code)
            else:
                safe = isinstance(payload, dict)
                return JsonResponse(payload, status=response.status_code, safe=safe)

        # Non-JSON responses are wrapped for the client to interpret.
        return JsonResponse(
            {
                "content": response.text,
                "content_type": content_type,
                "status_code": response.status_code,
            },
            status=response.status_code,
        )


class MCPOAuthCallbackView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        session_id_raw = body.get("session_id")
        authorization_code = body.get("authorization_code")
        if not session_id_raw or not authorization_code:
            return HttpResponseBadRequest("session_id and authorization_code are required")

        try:
            session_id = uuid.UUID(str(session_id_raw))
        except (ValueError, TypeError):
            return HttpResponseBadRequest("Invalid session_id")

        session = _require_active_session(request, session_id)

        state = body.get("state")
        if state and state != session.state:
            return HttpResponseBadRequest("State mismatch for OAuth session.")

        token_endpoint = body.get("token_endpoint") or session.token_endpoint
        if not token_endpoint:
            return HttpResponseBadRequest("token_endpoint is required to complete the OAuth flow.")

        client_id = body.get("client_id") or session.client_id or ""
        client_secret = body.get("client_secret") or session.client_secret or ""
        redirect_uri = body.get("redirect_uri") or session.redirect_uri or request.build_absolute_uri(reverse("console-mcp-oauth-callback-view"))
        headers = body.get("headers") or {}
        if headers and not isinstance(headers, dict):
            return HttpResponseBadRequest("headers must be a JSON object")

        data = {
            "grant_type": "authorization_code",
            "code": authorization_code,
        }
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        if session.code_verifier:
            data["code_verifier"] = session.code_verifier
        if client_id:
            data["client_id"] = client_id
        if client_secret:
            data["client_secret"] = client_secret

        try:
            response = httpx.post(token_endpoint, data=data, headers=headers or None, timeout=15.0)
        except httpx.HTTPError as exc:
            return JsonResponse({"error": "Token exchange failed", "detail": str(exc)}, status=502)

        if response.status_code >= 400:
            return JsonResponse(
                {
                    "error": "Token endpoint returned an error",
                    "status_code": response.status_code,
                    "body": response.text,
                },
                status=response.status_code,
            )

        try:
            token_payload = response.json()
        except ValueError:
            return JsonResponse(
                {"error": "Token endpoint returned non-JSON payload", "body": response.text},
                status=502,
            )

        access_token = token_payload.get("access_token")
        if not access_token:
            return JsonResponse({"error": "Token response missing access_token"}, status=502)

        config = session.server_config
        try:
            credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            credential = MCPServerOAuthCredential(server_config=config)

        credential.organization = config.organization
        credential.user = config.user
        credential.client_id = client_id
        if client_secret:
            credential.client_secret = client_secret
        credential.access_token = access_token
        credential.refresh_token = token_payload.get("refresh_token")
        credential.id_token = token_payload.get("id_token")
        credential.token_type = token_payload.get("token_type", credential.token_type)
        credential.scope = token_payload.get("scope") or session.scope

        expires_in = token_payload.get("expires_in")
        if expires_in is not None:
            try:
                expires_seconds = int(expires_in)
                credential.expires_at = timezone.now() + timedelta(seconds=max(expires_seconds, 0))
            except (TypeError, ValueError):
                credential.expires_at = None

        metadata = dict(credential.metadata or {})
        metadata_update = body.get("metadata") or {}
        if isinstance(metadata_update, dict):
            metadata.update(metadata_update)
        metadata["token_endpoint"] = token_endpoint
        metadata["last_token_response"] = {
            key: value
            for key, value in token_payload.items()
            if key not in {"access_token", "refresh_token", "id_token"}
        }
        credential.metadata = metadata
        credential.save()

        session.delete()

        try:
            get_mcp_manager().refresh_server(str(config.id))
        except Exception:
            logger.exception("Failed to refresh MCP manager after OAuth callback for %s", config.id)

        payload = {
            "connected": True,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "scope": credential.scope,
            "token_type": credential.token_type,
        }
        return JsonResponse(payload, status=200)


class MCPOAuthStatusView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, server_config_id: uuid.UUID, *args: Any, **kwargs: Any):
        config = _resolve_mcp_server_config(request, str(server_config_id))
        try:
            credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            return JsonResponse({"connected": False})

        payload = {
            "connected": True,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "scope": credential.scope,
            "token_type": credential.token_type,
            "has_refresh_token": bool(credential.refresh_token),
            "updated_at": credential.updated_at.isoformat() if credential.updated_at else None,
        }
        return JsonResponse(payload)


class MCPOAuthRevokeView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, server_config_id: uuid.UUID, *args: Any, **kwargs: Any):
        config = _resolve_mcp_server_config(request, str(server_config_id))
        try:
            credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            return JsonResponse({"revoked": False, "detail": "No stored credentials found."}, status=404)

        credential.delete()
        try:
            get_mcp_manager().refresh_server(str(config.id))
        except Exception:
            logger.exception("Failed to refresh MCP manager after OAuth revoke for %s", config.id)
        return JsonResponse({"revoked": True})


class MCPServerAssignmentsAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def _serialize_assignments(self, server: MCPServerConfig) -> dict[str, object]:
        assignable = list(mcp_server_service.assignable_agents(server))
        assigned_ids = mcp_server_service.server_assignment_agent_ids(server)
        agents_payload = []
        assigned_count = 0
        for agent in assignable:
            agent_id = str(agent.id)
            is_assigned = agent_id in assigned_ids
            if is_assigned:
                assigned_count += 1
            agents_payload.append(
                {
                    "id": agent_id,
                    "name": agent.name,
                    "description": agent.short_description or "",
                    "is_active": agent.is_active,
                    "assigned": is_assigned,
                    "organization_id": str(agent.organization_id) if agent.organization_id else None,
                    "last_interaction_at": agent.last_interaction_at.isoformat() if agent.last_interaction_at else None,
                }
            )
        return {
            "server": {
                "id": str(server.id),
                "display_name": server.display_name,
                "scope": server.scope,
                "scope_label": server.get_scope_display(),
            },
            "agents": agents_payload,
            "total_agents": len(assignable),
            "assigned_count": assigned_count,
        }

    def get(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        if server.scope == MCPServerConfig.Scope.PLATFORM:
            return HttpResponseBadRequest("Platform-managed servers do not support manual assignments.")
        payload = self._serialize_assignments(server)
        return JsonResponse(payload)

    def post(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        if server.scope == MCPServerConfig.Scope.PLATFORM:
            return HttpResponseBadRequest("Platform-managed servers do not support manual assignments.")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        agent_ids_raw = payload.get("agent_ids", [])
        if not isinstance(agent_ids_raw, list):
            return HttpResponseBadRequest("agent_ids must be a list.")
        agent_ids = [str(agent_id) for agent_id in agent_ids_raw]

        try:
            mcp_server_service.set_server_assignments(server, agent_ids)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        response_payload = self._serialize_assignments(server)
        response_payload["message"] = "Assignments updated."
        return JsonResponse(response_payload)


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

        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.WEB_CHAT_SESSION_STARTED,
            source=AnalyticsSource.WEB,
            properties=_web_chat_properties(
                agent,
                {
                    "session_key": str(result.session.session_key),
                    "session_ttl_seconds": result.ttl_seconds,
                },
            ),
        )

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
            if str(exc) == "Unknown web session.":
                return JsonResponse({"session_key": session_key, "ended": True})
            return HttpResponseBadRequest(str(exc))

        session = result.session
        props = {
            "session_key": str(session.session_key),
            "session_ttl_seconds": result.ttl_seconds,
        }
        if session.ended_at:
            props["session_ended_at"] = session.ended_at.isoformat()

        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.WEB_CHAT_SESSION_ENDED,
            source=AnalyticsSource.WEB,
            properties=_web_chat_properties(agent, props),
        )

        return _session_response(result)
