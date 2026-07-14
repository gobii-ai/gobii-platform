import json
import base64
import hashlib
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views import View

from api.services.native_integrations import (
    GOOGLE_DRIVE_PROVIDER,
    NativeIntegrationAuthError,
    NativeIntegrationConfigurationError,
    NativeIntegrationFileListError,
    NativeIntegrationTokenRequestError,
    build_native_integration_permission_summary,
    build_oauth_credentials_bundle,
    delete_native_integration_credentials,
    disable_overlapping_pipedream_tools_for_native_integration,
    get_native_integration_provider,
    get_native_integration_secret,
    list_native_integration_credential_fields,
    list_native_integration_providers,
    load_native_integration_credentials,
    list_google_drive_accessible_files,
    native_integration_client_credentials,
    new_oauth_state,
    refresh_oauth_credentials_if_needed,
    request_oauth_token,
    save_native_integration_credentials,
    trigger_agents_for_native_integration_change,
    upsert_manual_native_integration_credentials,
)
from api.models import NativeIntegrationOAuthSession
from api.services.agent_email_integrations import (
    EMAIL_NATIVE_PROVIDER_KEYS,
    connect_agent_email_oauth,
    disconnect_agent_email_oauth,
    resolve_email_oauth_identity,
    serialize_agent_email_connection,
)
from api.services.pipedream_apps import owner_agents_queryset
from api.services.native_integration_events import normalize_native_integration_event_files, record_native_integration_agent_event, resolve_native_integration_event_agent
from console.context_helpers import build_console_context
from console.agent_chat.access import resolve_manageable_agent_for_request

NATIVE_INTEGRATION_STATE_MAX_AGE_SECONDS = 10 * 60


def _permission_denied_response(exc: PermissionDenied) -> JsonResponse:
    messages = getattr(exc, "args", None)
    message = str(messages[0]) if messages else "Permission denied."
    return JsonResponse({"error": message}, status=403)


def _validation_error_response(exc: ValidationError, *, status: int = 400) -> JsonResponse:
    errors = exc.message_dict if hasattr(exc, "message_dict") else {"non_field_errors": exc.messages}
    return JsonResponse({"errors": errors}, status=status)


def _resolve_native_integration_owner(request: HttpRequest):
    context = build_console_context(request)
    if context.current_context.type == "organization":
        membership = context.current_membership
        if membership is None or not context.can_manage_org_agents:
            raise PermissionDenied("You do not have permission to manage organization integrations.")
        return "organization", None, membership.org
    return "user", request.user, None


def _owner_id(owner_user, owner_org) -> str:
    return str(owner_org.id if owner_org is not None else owner_user.id)


def _serialize_provider(provider, owner_user, owner_org) -> dict[str, Any]:
    if provider.connection_scope == "agent":
        agents = owner_agents_queryset(
            "organization" if owner_org is not None else "user",
            owner_user=owner_user,
            owner_org=owner_org,
        )
        connected_count = sum(
            1
            for agent in agents
            if serialize_agent_email_connection(agent, provider.key)["connected"]
        )
        return {
            "provider_key": provider.key,
            "display_name": provider.display_name,
            "description": provider.description,
            "auth_type": provider.auth_type,
            "icon": provider.icon,
            "api_hosts": [],
            "scopes": list(provider.scopes),
            "connection_scope": "agent",
            "connected": connected_count > 0,
            "connected_agent_count": connected_count,
            "scope": "",
            "granted_scopes": [],
            "requested_scopes": list(provider.scopes),
            "available_capabilities": [],
            "missing_capabilities": [],
            "missing_scopes": [],
            "credential_fields": [],
            "present_credential_fields": [],
            "missing_credential_fields": [],
            "capability_summary": f"Connected to {connected_count} agent{'s' if connected_count != 1 else ''}",
            "setup_url": "/app/integrations",
            "expires_at": None,
            "connect_url": reverse("console-native-integration-connect", args=[provider.key]),
            "agent_connections_url": reverse("console-native-integration-agent-connections", args=[provider.key]),
            "files_url": "",
            "picker_token_url": "",
            "agent_event_url": "",
            "revoke_url": reverse("console-native-integration-revoke", args=[provider.key]),
        }

    secret = get_native_integration_secret(provider.key, owner_user, owner_org)
    credentials: dict[str, Any] = {}
    credentials_valid = False
    if secret is not None:
        try:
            credentials = load_native_integration_credentials(secret)
            credentials_valid = True
        except NativeIntegrationAuthError:
            credentials = {}
    permission_summary = build_native_integration_permission_summary(
        provider,
        credentials if credentials_valid else None,
        connected=secret is not None and credentials_valid,
    )

    return {
        "provider_key": provider.key,
        "display_name": provider.display_name,
        "description": provider.description,
        "auth_type": provider.auth_type,
        "connection_scope": provider.connection_scope,
        "connected_agent_count": 0,
        "icon": provider.icon,
        "api_hosts": list(provider.api_hosts),
        "scopes": list(provider.scopes),
        "connected": bool(permission_summary["connected"]),
        "scope": credentials.get("scope") or "",
        "granted_scopes": permission_summary["granted_scopes"],
        "requested_scopes": permission_summary["requested_scopes"],
        "available_capabilities": permission_summary["available_capabilities"],
        "missing_capabilities": permission_summary["missing_capabilities"],
        "missing_scopes": permission_summary["missing_scopes"],
        "credential_fields": [
            field.to_dict()
            for field in list_native_integration_credential_fields(provider.key)
        ],
        "present_credential_fields": permission_summary["present_credential_fields"],
        "missing_credential_fields": permission_summary["missing_credential_fields"],
        "capability_summary": permission_summary["status_text"],
        "setup_url": permission_summary["setup_url"],
        "expires_at": credentials.get("expires_at"),
        "connect_url": reverse("console-native-integration-connect", args=[provider.key]),
        "files_url": reverse("console-native-integration-files", args=[provider.key]),
        "picker_token_url": reverse("console-native-integration-picker-token", args=[provider.key]),
        "agent_event_url": reverse("console-native-integration-agent-events", args=[provider.key]),
        "revoke_url": reverse("console-native-integration-revoke", args=[provider.key]),
        "agent_connections_url": "",
    }


def _load_oauth_session(
    request: HttpRequest,
    provider_key: str,
    state: str,
    owner_scope: str,
    owner_user,
    owner_org,
) -> NativeIntegrationOAuthSession:
    try:
        session = NativeIntegrationOAuthSession.objects.select_related("agent").get(state=state)
    except NativeIntegrationOAuthSession.DoesNotExist as exc:
        raise ValidationError({"state": "OAuth session is invalid. Restart the flow."}) from exc
    if session.expires_at <= timezone.now():
        session.delete()
        raise ValidationError({"state": "OAuth session expired. Restart the flow."})
    if session.provider_key != provider_key:
        raise ValidationError({"state": "OAuth provider mismatch."})
    if session.initiated_by_id != request.user.id:
        raise PermissionDenied("OAuth session belongs to another user.")
    expected_org_id = owner_org.id if owner_org is not None else None
    if session.organization_id != expected_org_id:
        raise PermissionDenied("OAuth session belongs to another workspace context.")
    if owner_org is None and session.user_id != owner_user.id:
        raise PermissionDenied("OAuth session belongs to another workspace context.")
    return session


def _parse_json_object(request: HttpRequest) -> dict[str, Any]:
    try:
        body = request.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError({"body": "Invalid request body."}) from exc
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise ValidationError({"body": "Invalid JSON body."}) from exc
    if not isinstance(payload, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    return payload


class NativeIntegrationListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        providers = [
            _serialize_provider(provider, owner_user, owner_org)
            for provider in list_native_integration_providers()
        ]
        owner_label = owner_org.name if owner_org is not None else (request.user.get_full_name() or request.user.username)
        return JsonResponse(
            {
                "owner_scope": owner_scope,
                "owner_label": owner_label,
                "providers": providers,
            }
        )


class NativeIntegrationConnectAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        if provider.auth_type == "manual":
            try:
                body = request.body.decode("utf-8")
            except UnicodeDecodeError:
                return HttpResponseBadRequest("Invalid request body")

            try:
                payload = json.loads(body or "{}")
            except json.JSONDecodeError:
                return HttpResponseBadRequest("Invalid JSON body")
            if not isinstance(payload, dict):
                return HttpResponseBadRequest("Invalid JSON payload: expected an object")

            credentials = payload.get("credentials") or {}
            try:
                _, owner_user, owner_org = _resolve_native_integration_owner(request)
                secret = upsert_manual_native_integration_credentials(
                    provider,
                    owner_user,
                    owner_org,
                    credentials,
                )
                stored = load_native_integration_credentials(secret)
                permission_summary = build_native_integration_permission_summary(provider, stored)
            except ValidationError as exc:
                return _validation_error_response(exc)
            except PermissionDenied as exc:
                return _permission_denied_response(exc)
            except NativeIntegrationAuthError as exc:
                return JsonResponse({"error": str(exc)}, status=400)

            with transaction.atomic():
                disable_overlapping_pipedream_tools_for_native_integration(provider.key, owner_user, owner_org)
                if permission_summary["connected"]:
                    trigger_agents_for_native_integration_change(provider.key, owner_user, owner_org)

            return JsonResponse(
                {
                    "connected": bool(permission_summary["connected"]),
                    "provider_key": provider.key,
                    "secret_id": str(secret.id),
                    "present_credential_fields": permission_summary["present_credential_fields"],
                    "missing_credential_fields": permission_summary["missing_credential_fields"],
                },
                status=201,
            )

        if provider.auth_type != "oauth2":
            return HttpResponseBadRequest("This provider does not use OAuth 2.0.")

        client_id, client_secret = native_integration_client_credentials(provider)
        if not client_id or not client_secret:
            return JsonResponse({"error": f"{provider.display_name} OAuth is not configured."}, status=400)

        try:
            owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
            agent = None
            if provider.connection_scope == "agent":
                payload = _parse_json_object(request)
                agent_id = str(payload.get("agent_id") or "").strip()
                if not agent_id:
                    raise ValidationError({"agent_id": "An agent is required for this integration."})
                agent = resolve_manageable_agent_for_request(request, agent_id)
                existing = serialize_agent_email_connection(agent)
                if existing["active_mode"] == "custom":
                    raise ValidationError({"provider": "Disable custom SMTP/IMAP before connecting an email provider."})
                if existing["connected"] and existing["provider"] != provider.key:
                    raise ValidationError({"provider": "Disconnect the other email provider before connecting this one."})
        except ValidationError as exc:
            return _validation_error_response(exc)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        state = new_oauth_state()
        redirect_uri = request.build_absolute_uri(reverse("console-native-integration-oauth-callback-view"))
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        session = NativeIntegrationOAuthSession(
            agent=agent,
            provider_key=provider.key,
            initiated_by=request.user,
            organization=owner_org,
            user=request.user,
            state=state,
            redirect_uri=redirect_uri,
            scope=provider.scope_string,
            code_challenge=challenge,
            code_challenge_method="S256",
            token_endpoint=provider.token_endpoint,
            client_id=client_id,
            metadata={"owner_scope": owner_scope, "owner_id": _owner_id(owner_user, owner_org)},
            expires_at=timezone.now() + timedelta(seconds=NATIVE_INTEGRATION_STATE_MAX_AGE_SECONDS),
        )
        session.client_secret = client_secret
        session.code_verifier = verifier
        session.save()

        query = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": provider.scope_string,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        query.update(provider.authorization_params)
        authorization_url = f"{provider.authorization_endpoint}?{urlencode(query)}"

        return JsonResponse(
            {
                "provider_key": provider.key,
                "authorization_url": authorization_url,
                "state": state,
                "agent_id": str(agent.pk) if agent else None,
                "expires_at": (timezone.now() + timedelta(seconds=NATIVE_INTEGRATION_STATE_MAX_AGE_SECONDS)).isoformat(),
            },
            status=201,
        )


class NativeIntegrationCallbackAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            body = request.body.decode("utf-8")
        except UnicodeDecodeError:
            return HttpResponseBadRequest("Invalid request body")

        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")
        if not isinstance(payload, dict):
            return HttpResponseBadRequest("Invalid JSON payload: expected an object")

        code = str(payload.get("authorization_code") or "").strip()
        state = str(payload.get("state") or "").strip()
        if not code or not state:
            return HttpResponseBadRequest("authorization_code and state are required")

        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        try:
            owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
            session = _load_oauth_session(request, provider.key, state, owner_scope, owner_user, owner_org)
        except ValidationError as exc:
            return _validation_error_response(exc)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        client_id = session.client_id
        client_secret = session.client_secret
        if not client_id or not client_secret:
            return JsonResponse({"error": f"{provider.display_name} OAuth is not configured."}, status=400)

        redirect_uri = session.redirect_uri
        try:
            token_payload = request_oauth_token(
                provider,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": session.code_verifier,
                },
                request_error_message="Token exchange failed",
                endpoint_error_message="Token endpoint returned an error",
                invalid_json_message="Token endpoint returned non-JSON payload",
            )
        except NativeIntegrationTokenRequestError as exc:
            error_payload = {"error": str(exc)}
            if exc.detail:
                error_payload["detail"] = exc.detail
            if exc.response_body:
                error_payload["status_code"] = exc.status_code
                error_payload["body"] = exc.response_body
            return JsonResponse(error_payload, status=exc.status_code)

        if provider.connection_scope == "agent":
            if session.agent is None:
                return JsonResponse({"error": "OAuth session does not identify an agent."}, status=400)
            try:
                identity = resolve_email_oauth_identity(provider.key, token_payload, client_id)
                account = connect_agent_email_oauth(
                    agent=session.agent,
                    provider_key=provider.key,
                    identity=identity,
                    token_payload=token_payload,
                    client_id=client_id,
                    client_secret=client_secret,
                    user=request.user,
                    organization=owner_org,
                    token_endpoint=provider.token_endpoint,
                    requested_scope=provider.scope_string,
                )
                from console.email_settings.views import _validate_agent_imap_connection, _validate_agent_smtp_connection

                smtp_ok, smtp_error = _validate_agent_smtp_connection(account)
                imap_ok, imap_error = _validate_agent_imap_connection(account)
                now = timezone.now()
                account.smtp_last_ok_at = now if smtp_ok else None
                account.smtp_error = smtp_error
                account.imap_last_ok_at = now if imap_ok else None
                account.imap_error = imap_error
                account.connection_last_ok_at = now if smtp_ok or imap_ok else None
                account.connection_error = "; ".join(
                    error for error in (smtp_error, imap_error) if error
                )
                account.save(update_fields=[
                    "smtp_last_ok_at", "smtp_error", "imap_last_ok_at", "imap_error",
                    "connection_last_ok_at", "connection_error", "updated_at",
                ])
            except ValidationError as exc:
                session.delete()
                return _validation_error_response(exc, status=502)
            session.delete()
            return JsonResponse(
                {
                    "connected": True,
                    "provider_key": provider.key,
                    "agent_id": str(account.endpoint.owner_agent_id),
                    "mailbox_address": account.endpoint.address,
                    "scope": account.oauth_credential.scope,
                    "expires_at": account.oauth_credential.expires_at.isoformat() if account.oauth_credential.expires_at else None,
                }
            )

        existing_credentials = {}
        existing_secret = get_native_integration_secret(provider.key, owner_user, owner_org)
        if existing_secret is not None:
            try:
                existing_credentials = load_native_integration_credentials(existing_secret)
            except NativeIntegrationAuthError:
                existing_credentials = {}

        try:
            credentials = build_oauth_credentials_bundle(
                provider,
                token_payload,
                existing_credentials=existing_credentials,
            )
        except ValidationError as exc:
            return _validation_error_response(exc, status=502)

        with transaction.atomic():
            secret = save_native_integration_credentials(provider, owner_user, owner_org, credentials)
            disable_overlapping_pipedream_tools_for_native_integration(provider.key, owner_user, owner_org)
            session.delete()

        return JsonResponse(
            {
                "connected": True,
                "provider_key": provider.key,
                "secret_id": str(secret.id),
                "scope": credentials.get("scope") or "",
                "expires_at": credentials.get("expires_at"),
            }
        )


class NativeIntegrationRevokeAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        try:
            _, owner_user, owner_org = _resolve_native_integration_owner(request)
            if provider.connection_scope == "agent":
                payload = _parse_json_object(request)
                agent_id = str(payload.get("agent_id") or "").strip()
                if not agent_id:
                    raise ValidationError({"agent_id": "An agent is required for this integration."})
                agent = resolve_manageable_agent_for_request(request, agent_id)
                deleted = disconnect_agent_email_oauth(agent, provider.key)
                return JsonResponse({"revoked": deleted, "agent_id": str(agent.pk)})
        except ValidationError as exc:
            return _validation_error_response(exc)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        deleted = delete_native_integration_credentials(provider.key, owner_user, owner_org)
        return JsonResponse({"revoked": deleted})


class NativeIntegrationAgentConnectionsAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)
        if provider.key not in EMAIL_NATIVE_PROVIDER_KEYS:
            return JsonResponse({"error": "This integration is not configured per agent."}, status=400)
        try:
            owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        agents = owner_agents_queryset(
            owner_scope,
            owner_user=owner_user,
            owner_org=owner_org,
        ).order_by("name", "id")
        connections = []
        for agent in agents:
            connection = serialize_agent_email_connection(agent, provider.key)
            connection["settings_url"] = f"/app/agents/{agent.pk}/email"
            connections.append(connection)
        return JsonResponse({"provider_key": provider.key, "agents": connections})


class NativeIntegrationPickerTokenAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        if provider.key != GOOGLE_DRIVE_PROVIDER.key:
            return JsonResponse({"error": f"{provider.display_name} does not support file picking."}, status=400)

        if not settings.GOOGLE_PICKER_API_KEY or not settings.GOOGLE_PICKER_APP_ID:
            return JsonResponse({"error": "Google Picker is not configured."}, status=400)

        try:
            _, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        secret = get_native_integration_secret(provider.key, owner_user, owner_org)
        if secret is None:
            return JsonResponse({"error": f"{provider.display_name} is not connected."}, status=404)

        try:
            credentials = load_native_integration_credentials(secret)
            credentials = refresh_oauth_credentials_if_needed(provider, secret, credentials)
        except NativeIntegrationConfigurationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except NativeIntegrationAuthError as exc:
            return JsonResponse({"error": str(exc)}, status=401)

        access_token = str(credentials.get("access_token") or "")
        if not access_token:
            return JsonResponse({"error": f"{provider.display_name} must be reconnected."}, status=401)

        response = JsonResponse(
            {
                "access_token": access_token,
                "developer_key": settings.GOOGLE_PICKER_API_KEY,
                "app_id": settings.GOOGLE_PICKER_APP_ID,
                "scope": credentials.get("scope") or provider.scope_string,
                "expires_at": credentials.get("expires_at"),
            }
        )
        response["Cache-Control"] = "no-store"
        return response


class NativeIntegrationFilesAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        if provider.key != GOOGLE_DRIVE_PROVIDER.key:
            return JsonResponse({"error": f"{provider.display_name} does not expose files."}, status=400)

        try:
            _, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        secret = get_native_integration_secret(provider.key, owner_user, owner_org)
        if secret is None:
            return JsonResponse({"error": f"{provider.display_name} is not connected."}, status=404)

        try:
            files = list_google_drive_accessible_files(secret)
        except NativeIntegrationConfigurationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except NativeIntegrationFileListError as exc:
            return JsonResponse({"error": str(exc)}, status=exc.status_code)
        except NativeIntegrationAuthError as exc:
            return JsonResponse({"error": str(exc)}, status=401)

        response = JsonResponse({"provider_key": provider.key, "files": [file.to_dict() for file in files]})
        response["Cache-Control"] = "no-store"
        return response


class NativeIntegrationAgentEventAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            body = request.body.decode("utf-8")
        except UnicodeDecodeError:
            return HttpResponseBadRequest("Invalid request body")

        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")
        if not isinstance(payload, dict):
            return HttpResponseBadRequest("Invalid JSON payload: expected an object")

        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        try:
            _, owner_user, owner_org = _resolve_native_integration_owner(request)
            agent = resolve_native_integration_event_agent(
                str(payload.get("agent_id") or "").strip(),
                owner_user=owner_user,
                owner_org=owner_org,
            )
            files = normalize_native_integration_event_files(payload.get("files"))
            step = record_native_integration_agent_event(
                agent=agent,
                provider=provider,
                event_type=str(payload.get("event_type") or "").strip(),
                files=files,
                source="console.native_integrations_api.NativeIntegrationAgentEventAPIView",
            )
        except ValidationError as exc:
            return _validation_error_response(exc)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        return JsonResponse(
            {
                "recorded": True,
                "provider_key": provider.key,
                "agent_id": str(agent.id),
                "step_id": str(step.id),
            },
            status=201,
        )
