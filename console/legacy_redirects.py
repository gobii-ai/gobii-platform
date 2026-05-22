from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.views import View

from api.models import OrganizationMembership


def _append_query_params(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    query_pairs.extend((key, value) for key, value in params.items() if value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_pairs), parts.fragment))


def _with_original_query(request, target_path: str) -> str:
    query = request.META.get("QUERY_STRING", "")
    if not query:
        return target_path
    separator = "&" if "?" in target_path else "?"
    return f"{target_path}{separator}{query}"


def _set_org_context(request, org_id) -> bool:
    if not request.user.is_authenticated:
        return False
    membership = (
        OrganizationMembership.objects.select_related("org")
        .filter(
            user=request.user,
            org_id=org_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        .first()
    )
    if not membership:
        messages.error(request, "You don't have access to that organization.")
        return False

    request.session["context_type"] = "organization"
    request.session["context_id"] = str(membership.org.id)
    request.session["context_name"] = membership.org.name
    request.session.modified = True
    return True


def _organization_context_target_path(request, target_path: str, org_id: str) -> str:
    if not request.user.is_authenticated or _set_org_context(request, org_id):
        return _append_query_params(
            target_path,
            {"context_type": "organization", "context_id": org_id},
        )
    return target_path


_CONSOLE_PAGE_REDIRECTS = {
    "/console/": "/app",
    "/console/agents/": "/app/agents",
    "/console/billing/": "/app/billing",
    "/console/profile/": "/app/profile",
    "/console/usage/": "/app/usage",
    "/console/api-keys/": "/app/api-keys",
    "/console/secrets/": "/app/secrets",
    "/console/advanced/mcp-servers/": "/app/integrations",
    "/console/organizations/": "/app/organization",
    "/console/organizations/add/": "/app/organization",
}


def get_legacy_console_redirect_path(request) -> str | None:
    path = request.path
    if path.startswith("/console/api/"):
        return None
    if path in {"/console/switch-context/", "/console/mcp/oauth/callback/", "/console/email/oauth/callback/"}:
        return None
    if path.startswith(("/console/staff/", "/console/tasks/", "/console/agent-", "/console/agents/create/")):
        return None

    target_path = _CONSOLE_PAGE_REDIRECTS.get(path)
    if target_path:
        if path == "/console/billing/":
            target_path = _billing_target_path(request, target_path)
        return _with_original_query(request, target_path)

    agent_prefix = "/console/agents/"
    if path.startswith(agent_prefix):
        remainder = path[len(agent_prefix):].strip("/")
        parts = remainder.split("/") if remainder else []
        if not parts:
            return None
        agent_id = parts[0]
        if len(parts) == 1:
            return _with_original_query(request, f"/app/agents/{agent_id}/settings")
        if len(parts) >= 2 and parts[1] == "chat":
            if len(parts) > 3:
                return None
            subview = parts[2] if len(parts) >= 3 else ""
            allowed_subviews = {"settings", "secrets", "email", "files", "contact-requests"}
            suffix = f"/{subview}" if subview in allowed_subviews else ""
            return _with_original_query(request, f"/app/agents/{agent_id}{suffix}")
        if len(parts) >= 3 and parts[1] == "secrets" and parts[2] == "request":
            if len(parts) == 3:
                return _with_original_query(request, f"/app/agents/{agent_id}/secrets/request")
            if len(parts) >= 4 and parts[3] == "remove":
                return _with_original_query(request, f"/app/agents/{agent_id}/secrets/request")
            return None
        if len(parts) == 2 and parts[1] in {"email", "files", "secrets", "contact-requests"}:
            return _with_original_query(request, f"/app/agents/{agent_id}/{parts[1]}")
        return None

    org_prefix = "/console/organizations/"
    if path.startswith(org_prefix):
        remainder = path[len(org_prefix):].strip("/")
        parts = remainder.split("/") if remainder else []
        if len(parts) == 3 and parts[0] == "invites" and parts[2] == "accept":
            return _with_original_query(request, f"/app/organizations/invites/{parts[1]}/accept")
        if not remainder or "/" in remainder:
            return None
        target_path = _organization_context_target_path(request, "/app/organization", remainder)
        return _with_original_query(request, target_path)

    return None


def _billing_target_path(request, target_path: str) -> str:
    org_id = (request.GET.get("org_id") or "").strip()
    if org_id:
        return _organization_context_target_path(request, target_path, org_id)
    return target_path


class LegacyConsoleRedirectView(View):
    http_method_names = ["get", "head"]

    def get(self, request, *args, **kwargs):
        if not settings.LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED:
            raise Http404()
        target_path = get_legacy_console_redirect_path(request)
        if target_path is None:
            raise Http404()
        return HttpResponseRedirect(target_path)
