from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib.sites.models import Site
from django.core import signing
from django.urls import NoReverseMatch, reverse
from django.utils.http import url_has_allowed_host_and_scheme

from config import settings

IMMERSIVE_RETURN_TO_SESSION_KEY = "immersive_return_to"
IMMERSIVE_APP_BASE_PATH = "/app"
DAILY_LIMIT_ACTION_TOKEN_SALT = "agent_daily_limit_action"
DAILY_LIMIT_ACTION_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


def append_query_params(url: str, params: dict[str, str]) -> str:
    if not url or not params:
        return url
    parts = urlsplit(url)
    query_params = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        if value is None:
            continue
        value_str = str(value).strip()
        if not value_str:
            continue
        query_params[key] = value_str
    query = urlencode(query_params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def append_context_query(url: str, organization_id: str | None) -> str:
    if not organization_id:
        return url
    return append_query_params(
        url,
        {
            "context_type": "organization",
            "context_id": str(organization_id),
        },
    )


def build_daily_limit_action_token(agent_id: str, action: str) -> str:
    return signing.dumps(
        {"agent_id": str(agent_id), "action": action},
        salt=DAILY_LIMIT_ACTION_TOKEN_SALT,
        compress=True,
    )


def load_daily_limit_action_payload(token: str) -> dict | None:
    if not token:
        return None
    try:
        payload = signing.loads(
            token,
            salt=DAILY_LIMIT_ACTION_TOKEN_SALT,
            max_age=DAILY_LIMIT_ACTION_TOKEN_TTL_SECONDS,
        )
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def build_site_url(path: str) -> str:
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path

    base_url = settings.PUBLIC_SITE_URL.strip().rstrip("/")
    if not base_url:
        current_site = Site.objects.get_current()
        base_url = f"https://{current_site.domain}"

    normalized = path if path.startswith("/") else f"/{path}"
    return f"{base_url}{normalized}"


def build_agent_detail_url(agent_id: str | int, organization_id: str | None = None) -> str:
    try:
        path = reverse("agent_detail", kwargs={"pk": agent_id})
    except NoReverseMatch:
        return ""
    return append_context_query(build_site_url(path), organization_id)


def build_agent_daily_limit_action_links(agent_id: str | int, organization_id: str | None = None) -> dict[str, str]:
    settings_url = build_agent_detail_url(agent_id, organization_id)
    try:
        double_limit_url = build_site_url(
            reverse(
                "agent_daily_limit_action",
                kwargs={"pk": agent_id, "action": "double"},
            )
        )
        unlimited_limit_url = build_site_url(
            reverse(
                "agent_daily_limit_action",
                kwargs={"pk": agent_id, "action": "unlimited"},
            )
        )
    except NoReverseMatch:
        return {
            "settings_url": settings_url,
            "double_limit_url": settings_url,
            "unlimited_limit_url": settings_url,
        }

    double_limit_url = append_query_params(
        double_limit_url,
        {"token": build_daily_limit_action_token(str(agent_id), "double")},
    )
    unlimited_limit_url = append_query_params(
        unlimited_limit_url,
        {"token": build_daily_limit_action_token(str(agent_id), "unlimited")},
    )
    if organization_id:
        double_limit_url = append_context_query(double_limit_url, organization_id)
        unlimited_limit_url = append_context_query(unlimited_limit_url, organization_id)

    return {
        "settings_url": settings_url,
        "double_limit_url": double_limit_url,
        "unlimited_limit_url": unlimited_limit_url,
    }


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
