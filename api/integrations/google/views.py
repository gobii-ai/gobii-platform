import json
import logging
import secrets
import urllib.parse
from typing import List

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.sites.models import Site
from django.core import signing
from django.http import HttpRequest, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone

from api.integrations.google.auth import scope_list_for_tier
from api.models import (
    AgentGoogleWorkspaceBinding,
    GoogleWorkspaceCredential,
    PersistentAgent,
)

logger = logging.getLogger(__name__)


def _build_redirect_uri(request: HttpRequest) -> str:
    configured = getattr(settings, "GOOGLE_WORKSPACE_REDIRECT_URI", "") or ""
    if configured:
        return configured
    current_site = Site.objects.get_current()
    return f"https://{current_site.domain}{reverse('google_workspace_oauth_callback')}"


def _build_state(agent_id: str, scope_tier: str) -> str:
    payload = {"agent_id": agent_id, "scope_tier": scope_tier}
    return signing.TimestampSigner().sign(json.dumps(payload))


def _parse_state(state: str) -> dict | None:
    if not state:
        return None
    try:
        data = signing.TimestampSigner().unsign(state, max_age=600)
        payload = json.loads(data)
        return payload
    except Exception:
        return None


def _build_oauth_url(
    client_id: str,
    redirect_uri: str,
    scopes: List[str],
    state: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


@login_required
def start_oauth(request: HttpRequest, agent_id):
    if not getattr(settings, "GOOGLE_WORKSPACE_TOOLS_ENABLED", False):
        return JsonResponse({"error": "Google Workspace tools are disabled"}, status=400)

    agent_id_str = str(agent_id)
    try:
        agent = PersistentAgent.objects.get(id=agent_id_str)
    except PersistentAgent.DoesNotExist:
        return JsonResponse({"error": "Agent not found"}, status=404)

    if not (request.user.is_superuser or agent.user_id == request.user.id):
        return JsonResponse({"error": "Not authorized for this agent"}, status=403)

    client_id = getattr(settings, "GOOGLE_WORKSPACE_CLIENT_ID", "") or ""
    client_secret = getattr(settings, "GOOGLE_WORKSPACE_CLIENT_SECRET", "") or ""
    if not client_id or not client_secret:
        return JsonResponse({"error": "Google client is not configured"}, status=400)

    scope_tier = request.GET.get("scope_tier") or getattr(settings, "GOOGLE_WORKSPACE_DEFAULT_SCOPE_TIER", "minimal")
    scopes = scope_list_for_tier(scope_tier)

    redirect_uri = _build_redirect_uri(request)
    state = _build_state(agent_id_str, scope_tier)
    oauth_url = _build_oauth_url(client_id, redirect_uri, scopes, state)
    return HttpResponseRedirect(oauth_url)


@login_required
def oauth_callback(request: HttpRequest):
    if not getattr(settings, "GOOGLE_WORKSPACE_TOOLS_ENABLED", False):
        return JsonResponse({"error": "Google Workspace tools are disabled"}, status=400)

    code = request.GET.get("code")
    state = request.GET.get("state")
    error = request.GET.get("error")

    if error:
        return JsonResponse({"status": "error", "message": error}, status=400)
    if not code or not state:
        return JsonResponse({"status": "error", "message": "Missing code or state"}, status=400)

    state_payload = _parse_state(state)
    if not state_payload:
        return JsonResponse({"status": "error", "message": "Invalid state"}, status=400)
    agent_id = state_payload.get("agent_id")
    scope_tier = state_payload.get("scope_tier") or getattr(settings, "GOOGLE_WORKSPACE_DEFAULT_SCOPE_TIER", "minimal")

    try:
        agent = PersistentAgent.objects.get(id=agent_id)
    except PersistentAgent.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Agent not found"}, status=404)

    if not (request.user.is_superuser or agent.user_id == request.user.id):
        return JsonResponse({"status": "error", "message": "Not authorized for this agent"}, status=403)

    # Exchange code for tokens
    token_uri = "https://oauth2.googleapis.com/token"
    client_id = getattr(settings, "GOOGLE_WORKSPACE_CLIENT_ID", "") or ""
    client_secret = getattr(settings, "GOOGLE_WORKSPACE_CLIENT_SECRET", "") or ""
    redirect_uri = _build_redirect_uri(request)

    try:
        import requests
    except ImportError:
        return JsonResponse({"status": "error", "message": "requests not installed"}, status=500)

    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    resp = requests.post(token_uri, data=data, timeout=20)
    if resp.status_code != 200:
        return JsonResponse({"status": "error", "message": "Failed to exchange code"}, status=400)
    token_data = resp.json() or {}

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    id_token = token_data.get("id_token")
    token_type = token_data.get("token_type")
    scope_str = token_data.get("scope", "")
    scopes = scope_str.split() if scope_str else []

    # Get userinfo email
    email = ""
    try:
        userinfo_resp = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if userinfo_resp.status_code == 200:
            email = (userinfo_resp.json() or {}).get("email", "") or ""
    except Exception:
        logger.warning("Failed to fetch userinfo for Google Workspace binding", exc_info=True)

    # Persist credential
    expires_at = timezone.now() + timezone.timedelta(seconds=expires_in or 0) if expires_in else None

    credential = GoogleWorkspaceCredential.objects.create(
        user=request.user,
        organization=agent.organization,
        google_account_email=email,
        scope_tier=scope_tier,
        scopes=" ".join(scopes),
        token_type=token_type or "",
        expires_at=expires_at,
    )
    credential.access_token = access_token
    credential.refresh_token = refresh_token
    credential.id_token = id_token
    credential.save()

    AgentGoogleWorkspaceBinding.objects.update_or_create(
        agent=agent,
        defaults={
            "credential": credential,
            "scope_tier": credential.scope_tier,
        },
    )

    # Redirect to console agent page
    try:
        agent_url = reverse("console_agent_detail", kwargs={"pk": str(agent.id)})
    except Exception:
        agent_url = "/"

    return HttpResponseRedirect(agent_url)
