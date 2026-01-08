from urllib.parse import urlencode

from django.utils.http import url_has_allowed_host_and_scheme

IMMERSIVE_RETURN_TO_SESSION_KEY = "immersive_return_to"
IMMERSIVE_APP_BASE_PATH = "/app"


def normalize_return_to(request, raw_value: str | None) -> str | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    if url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return None


def build_immersive_chat_url(
    request,
    agent_id,
    *,
    return_to: str | None = None,
    embed: bool = False,
) -> str:
    path = f"{IMMERSIVE_APP_BASE_PATH}/agents/{agent_id}"
    params: dict[str, str] = {}
    resolved_return_to = normalize_return_to(request, return_to)
    if resolved_return_to:
        params["return_to"] = resolved_return_to
    if embed:
        params["embed"] = "1"
    if params:
        return f"{path}?{urlencode(params)}"
    return path
