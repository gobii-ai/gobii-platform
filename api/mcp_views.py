import json
from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.auth import MCPAPIKeyAuthentication
from api.services.remote_mcp import MCPToolError, MCP_PROTOCOL_VERSION, SERVER_INFO, call_tool, list_tools, make_tool_result


JSON_RPC_PARSE_ERROR = -32700
JSON_RPC_INVALID_REQUEST = -32600
JSON_RPC_METHOD_NOT_FOUND = -32601
JSON_RPC_INVALID_PARAMS = -32602
JSON_RPC_HEADER_MISMATCH = -32001


@method_decorator(csrf_exempt, name="dispatch")
class GobiiMCPView(APIView):
    """Streamable HTTP-compatible MCP endpoint for Gobii agent infrastructure."""

    authentication_classes = [MCPAPIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "options"]

    def get(self, request, *args, **kwargs):
        if not _origin_is_allowed(request):
            return _json_rpc_error_response(
                JSON_RPC_INVALID_REQUEST,
                "Origin is not allowed.",
                http_status=403,
            )
        response = HttpResponse(status=405)
        response["Allow"] = "POST, OPTIONS"
        response["Cache-Control"] = "no-store"
        return response

    def options(self, request, *args, **kwargs):
        if not _origin_is_allowed(request):
            return _json_rpc_error_response(
                JSON_RPC_INVALID_REQUEST,
                "Origin is not allowed.",
                http_status=403,
            )
        response = HttpResponse(status=204)
        response["Allow"] = "POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name, X-Api-Key"
        )
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response["Access-Control-Max-Age"] = "600"
        origin = request.headers.get("Origin")
        if origin:
            response["Access-Control-Allow-Origin"] = origin
            response["Vary"] = "Origin"
        return response

    def post(self, request, *args, **kwargs):
        if not _origin_is_allowed(request):
            return _json_rpc_error_response(
                JSON_RPC_INVALID_REQUEST,
                "Origin is not allowed.",
                http_status=403,
            )

        try:
            payload = json.loads((request.body or b"{}").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json_rpc_error_response(
                JSON_RPC_PARSE_ERROR,
                "Parse error.",
                request_id=None,
                http_status=400,
            )

        if not isinstance(payload, dict):
            return _json_rpc_error_response(
                JSON_RPC_INVALID_REQUEST,
                "Request body must be a single JSON-RPC object.",
                request_id=None,
                http_status=400,
            )

        if _is_json_rpc_notification_or_response(payload):
            return _accepted_response()

        header_error = _validate_mcp_headers(request, payload)
        if header_error is not None:
            return _json_rpc_error_response(
                JSON_RPC_HEADER_MISMATCH,
                header_error,
                request_id=payload.get("id"),
                http_status=400,
            )

        response_payload, http_status = _handle_json_rpc_request(request, payload)
        response = Response(response_payload, status=http_status)
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Vary"] = "Authorization, X-Api-Key"
        return response


def _handle_json_rpc_request(request, payload):
    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0" or "method" not in payload:
        return _json_rpc_error_payload(
            JSON_RPC_INVALID_REQUEST,
            "Invalid JSON-RPC request.",
            request_id=request_id,
        ), 400

    method = payload.get("method")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _json_rpc_error_payload(
            JSON_RPC_INVALID_PARAMS,
            "params must be an object.",
            request_id=request_id,
        ), 200

    if method == "initialize":
        return _json_rpc_success_payload(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "Use Gobii tools to create, manage, link, message, and attach files to "
                    "persistent Gobii agents. Authenticate every request with your Gobii API key."
                ),
            },
        ), 200

    if method == "ping":
        return _json_rpc_success_payload(request_id, {}), 200

    if method == "tools/list":
        return _json_rpc_success_payload(request_id, {"tools": list_tools()}), 200

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(tool_name, str) or not tool_name:
            return _json_rpc_error_payload(
                JSON_RPC_INVALID_PARAMS,
                "tools/call requires params.name.",
                request_id=request_id,
            ), 200
        try:
            result = make_tool_result(call_tool(request, tool_name, arguments))
        except MCPToolError as exc:
            payload = {"status": "error", "message": str(exc)}
            if exc.data is not None:
                payload["details"] = exc.data
            result = make_tool_result(payload, is_error=True)
        return _json_rpc_success_payload(request_id, result), 200

    return _json_rpc_error_payload(
        JSON_RPC_METHOD_NOT_FOUND,
        f"Method not found: {method}",
        request_id=request_id,
    ), 200


def _is_json_rpc_notification_or_response(payload):
    if "method" not in payload and ("result" in payload or "error" in payload):
        return True
    if "method" in payload and "id" not in payload:
        return True
    return False


def _accepted_response():
    response = HttpResponse(status=202)
    response["Cache-Control"] = "no-store"
    return response


def _json_rpc_success_payload(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_rpc_error_payload(code, message, *, request_id=None):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _json_rpc_error_response(code, message, *, request_id=None, http_status=200):
    response = Response(
        _json_rpc_error_payload(code, message, request_id=request_id),
        status=http_status,
    )
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _validate_mcp_headers(request, payload):
    method = payload.get("method")
    header_method = request.headers.get("Mcp-Method")
    if header_method and header_method != method:
        return f"Mcp-Method header value does not match JSON-RPC method {method!r}."

    if method == "tools/call":
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        body_name = params.get("name")
        header_name = request.headers.get("Mcp-Name")
        if header_name and header_name != body_name:
            return "Mcp-Name header value does not match params.name."

    return None


def _origin_is_allowed(request):
    origin = request.headers.get("Origin")
    if not origin:
        return True

    parsed_origin = urlparse(origin)
    if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc:
        return False

    request_host = request.get_host().lower()
    origin_netloc = parsed_origin.netloc.lower()
    if origin_netloc == request_host:
        return True

    if _origin_matches_trusted_origin(origin, parsed_origin):
        return True

    return _origin_matches_allowed_host(parsed_origin)


def _origin_matches_trusted_origin(origin, parsed_origin):
    normalized_origin = origin.rstrip("/")
    for trusted in settings.CSRF_TRUSTED_ORIGINS:
        trusted_value = trusted.rstrip("/")
        if trusted_value == normalized_origin:
            return True
        trusted_parsed = urlparse(trusted_value)
        if trusted_parsed.scheme != parsed_origin.scheme:
            continue
        trusted_netloc = trusted_parsed.netloc.lower()
        origin_netloc = parsed_origin.netloc.lower()
        if trusted_netloc.startswith("*.") and origin_netloc.endswith(trusted_netloc[1:]):
            return True
    return False


def _origin_matches_allowed_host(parsed_origin):
    hostname = (parsed_origin.hostname or "").lower()
    netloc = parsed_origin.netloc.lower()
    for allowed in settings.ALLOWED_HOSTS:
        allowed_value = allowed.lower()
        if allowed_value == "*":
            continue
        if allowed_value == netloc or allowed_value == hostname:
            return True
        if allowed_value.startswith(".") and (
            hostname.endswith(allowed_value) or hostname == allowed_value[1:]
        ):
            return True
    return False
