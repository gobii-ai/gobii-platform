from typing import Any

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import signing
from django.urls import reverse

PUBLIC_AGENT_AVATAR_THUMBNAIL_SALT = "public_agent_avatar_thumbnail"


def build_public_agent_avatar_thumbnail_token(agent: Any) -> str:
    version = agent.get_avatar_thumbnail_version()
    if not version:
        return ""
    return signing.dumps(
        {
            "agent_id": str(agent.id),
            "version": version,
        },
        salt=PUBLIC_AGENT_AVATAR_THUMBNAIL_SALT,
        compress=True,
    )


def validate_public_agent_avatar_thumbnail_token(agent: Any, token: str) -> bool:
    if not token:
        return False
    try:
        payload = signing.loads(token, salt=PUBLIC_AGENT_AVATAR_THUMBNAIL_SALT)
    except signing.BadSignature:
        return False
    if not isinstance(payload, dict):
        return False
    return (
        str(payload.get("agent_id") or "") == str(agent.id)
        and str(payload.get("version") or "") == str(agent.get_avatar_thumbnail_version() or "")
    )


def build_public_agent_avatar_thumbnail_url(agent: Any) -> str:
    if not getattr(agent, "has_avatar", False):
        return ""
    token = build_public_agent_avatar_thumbnail_token(agent)
    if not token:
        return ""
    path = reverse("agent_avatar_public_thumbnail", kwargs={"pk": agent.id})
    base_url = str(settings.PUBLIC_SITE_URL or "").strip().rstrip("/")
    if not base_url:
        current_site = Site.objects.get_current()
        base_url = f"https://{current_site.domain}"
    return f"{base_url}{path}?token={token}"
