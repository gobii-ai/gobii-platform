from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.urls import reverse
from django.views import View

from api.agent.tools.mcp_manager import get_mcp_manager
from api.models import MCPServerConfig
from api.services.pipedream_agent_apps import (
    disconnect_agent_pipedream_app,
    list_agent_pipedream_app_rows,
    list_pipedream_app_agent_connections,
    remove_agent_pipedream_app,
    start_agent_pipedream_app_connect,
)
from api.services.pipedream_apps import (
    PipedreamCatalogError,
    PipedreamCatalogService,
    get_owner_apps_state,
    serialize_owner_apps_state,
    set_owner_selected_app_slugs,
)
from api.services.pipedream_connections import PipedreamConnectionError
from console.agent_chat.access import resolve_manageable_agent_for_request
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body
from console.api_views import _resolve_mcp_owner
from util.integrations import pipedream_status


class PipedreamAppsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "patch"]

    def get(self, request: HttpRequest, *args, **kwargs):
        status = pipedream_status()
        if not status.enabled:
            return JsonResponse({"error": status.reason}, status=503)
        owner_scope, owner_label, owner_user, owner_org = _resolve_mcp_owner(request)
        state = get_owner_apps_state(owner_scope, owner_label, owner_user=owner_user, owner_org=owner_org)
        try:
            payload = serialize_owner_apps_state(state, catalog=PipedreamCatalogService())
        except PipedreamCatalogError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        return JsonResponse(payload)

    def patch(self, request: HttpRequest, *args, **kwargs):
        status = pipedream_status()
        if not status.enabled:
            return JsonResponse({"error": status.reason}, status=503)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        selected_app_slugs = payload.get("selected_app_slugs")
        if not isinstance(selected_app_slugs, list):
            return HttpResponseBadRequest("selected_app_slugs must be an array.")

        owner_scope, owner_label, owner_user, owner_org = _resolve_mcp_owner(request)
        try:
            selected = set_owner_selected_app_slugs(
                owner_scope,
                selected_app_slugs,
                owner_user=owner_user,
                owner_org=owner_org,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        manager = get_mcp_manager()
        owner_id = str(owner_org.id) if owner_scope == MCPServerConfig.Scope.ORGANIZATION else str(owner_user.id)
        manager.invalidate_pipedream_owner_cache(owner_scope, owner_id)
        manager.prewarm_pipedream_owner_cache(owner_scope, owner_id, app_slugs=selected)

        state = get_owner_apps_state(owner_scope, owner_label, owner_user=owner_user, owner_org=owner_org)
        try:
            response_data = serialize_owner_apps_state(state, catalog=PipedreamCatalogService())
        except PipedreamCatalogError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        return JsonResponse(response_data)


class AgentPipedreamAppsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args, **kwargs):
        status = pipedream_status()
        if not status.enabled:
            return JsonResponse({"error": status.reason}, status=503)

        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        try:
            payload = list_agent_pipedream_app_rows(agent, query=str(request.GET.get("q") or ""))
        except (PipedreamCatalogError, PipedreamConnectionError) as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        return JsonResponse(payload)


class AgentPipedreamAppAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["delete"]

    def delete(self, request: HttpRequest, agent_id: str, app_slug: str, *args, **kwargs):
        status = pipedream_status()
        if not status.enabled:
            return JsonResponse({"error": status.reason}, status=503)

        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        try:
            payload = remove_agent_pipedream_app(agent, app_slug)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse(payload)


class AgentPipedreamAppConnectAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, app_slug: str, *args, **kwargs):
        status = pipedream_status()
        if not status.enabled:
            return JsonResponse({"error": status.reason}, status=503)

        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        try:
            payload = start_agent_pipedream_app_connect(agent, app_slug)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        except PipedreamCatalogError as exc:
            return JsonResponse({"error": str(exc)}, status=502)

        connect_url = request.build_absolute_uri(
            reverse(
                "pipedream_jit_connect",
                kwargs={"agent_id": agent.id, "app_slug": app_slug},
            )
        )
        return JsonResponse({**payload, "connect_url": connect_url})


class AgentPipedreamAppConnectionAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["delete"]

    def delete(self, request: HttpRequest, agent_id: str, app_slug: str, *args, **kwargs):
        status = pipedream_status()
        if not status.enabled:
            return JsonResponse({"error": status.reason}, status=503)

        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        try:
            payload = disconnect_agent_pipedream_app(agent, app_slug)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        except PipedreamConnectionError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        return JsonResponse(payload)


class PipedreamAppAgentConnectionsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, app_slug: str, *args, **kwargs):
        status = pipedream_status()
        if not status.enabled:
            return JsonResponse({"error": status.reason}, status=503)

        owner_scope, _owner_label, owner_user, owner_org = _resolve_mcp_owner(request)
        try:
            payload = list_pipedream_app_agent_connections(
                owner_scope=owner_scope,
                owner_user=owner_user,
                owner_org=owner_org,
                app_slug=app_slug,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        except PipedreamConnectionError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        return JsonResponse(payload)


class PipedreamAppSearchAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args, **kwargs):
        status = pipedream_status()
        if not status.enabled:
            return JsonResponse({"error": status.reason}, status=503)
        _resolve_mcp_owner(request)
        query = str(request.GET.get("q") or "").strip()
        if not query:
            return JsonResponse({"results": []})
        catalog = PipedreamCatalogService()
        try:
            results = [app.to_dict() for app in catalog.search_apps(query)]
        except PipedreamCatalogError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        return JsonResponse({"results": results})
