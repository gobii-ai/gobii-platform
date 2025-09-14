import logging
import secrets
from datetime import datetime, timezone
from typing import Tuple, Optional

import requests
from django.contrib.sites.models import Site
from django.urls import reverse
from django.conf import settings

from api.models import PersistentAgent
from api.models import PipedreamConnectSession
from api.agent.tools.mcp_manager import get_mcp_manager


logger = logging.getLogger(__name__)


def _https_base_url() -> str:
    current_site = Site.objects.get_current()
    domain = current_site.domain.strip().rstrip('/')
    return f"https://{domain}"


def create_connect_session(agent: PersistentAgent, app_slug: str) -> Tuple[PipedreamConnectSession, Optional[str]]:
    """
    Create a Pipedream Connect token and persist a session row with a one‑time webhook.

    Returns (session, final_connect_url) where final_connect_url already includes &app={app_slug}.
    If token creation fails, returns (session, None).
    """
    # Build a pending session with a per‑session webhook secret
    session = PipedreamConnectSession.objects.create(
        agent=agent,
        app_slug=app_slug or "",
        external_user_id=str(agent.id),
        conversation_id=str(agent.id),  # matches current identity choice
        webhook_secret=secrets.token_urlsafe(16),
        status=PipedreamConnectSession.Status.PENDING,
    )
    logger.info(
        "PD Connect: create token start agent=%s app=%s session=%s",
        str(agent.id), app_slug or "", str(session.id)
    )

    try:
        manager = get_mcp_manager()
        token = manager._get_pipedream_access_token() or ""
        if not token:
            logger.warning(
                "PD Connect: missing access token; cannot create token agent=%s session=%s",
                str(agent.id), str(session.id)
            )
            return session, None

        project_id = getattr(settings, "PIPEDREAM_PROJECT_ID", "")
        if not project_id:
            logger.warning(
                "PD Connect: PIPEDREAM_PROJECT_ID not set; cannot create token agent=%s session=%s",
                str(agent.id), str(session.id)
            )
            return session, None

        # Build absolute webhook path without reverse() to avoid URLConf import edge cases in tests
        webhook_path = f"/api/v1/webhooks/pipedream/connect/{session.id}/"
        webhook_uri = f"{_https_base_url()}{webhook_path}?t={session.webhook_secret}"
        logger.info(
            "PD Connect: webhook prepared agent=%s session=%s path=%s",
            str(agent.id), str(session.id), webhook_path
        )

        headers = {
            "Authorization": f"Bearer {token}",
            "x-pd-environment": getattr(settings, "PIPEDREAM_ENVIRONMENT", "development"),
        }
        payload = {
            "external_user_id": str(agent.id),
            "webhook_uri": webhook_uri,
        }

        logger.info(
            "PD Connect: POST create token project=%s env=%s session=%s",
            project_id, getattr(settings, "PIPEDREAM_ENVIRONMENT", "development"), str(session.id)
        )
        resp = requests.post(
            f"https://api.pipedream.com/v1/connect/{project_id}/tokens",
            json=payload,
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        ctok = data.get("token")
        link = data.get("connect_link_url")
        expires_at = data.get("expires_at")

        if not (ctok and link):
            logger.warning(
                "PD Connect: missing fields in response token/link session=%s", str(session.id)
            )
            return session, None

        # Persist details on session
        session.connect_token = str(ctok)
        session.connect_link_url = str(link)
        try:
            if isinstance(expires_at, str):
                session.expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except Exception:
            # Non-fatal
            pass
        session.save(update_fields=["connect_token", "connect_link_url", "expires_at", "updated_at"])

        # Append &app=...
        app = (app_slug or "").strip()
        final_url = f"{link}{'&' if '?' in link else '?'}app={app}" if app else link
        logger.info(
            "PD Connect: token created session=%s app=%s expires_at=%s final_url_has_app=%s",
            str(session.id), app or "", str(session.expires_at) if session.expires_at else "", "app=" in final_url
        )
        return session, final_url
    except Exception as e:
        logger.error("PD Connect: create token failed session=%s error=%s", str(session.id), e)
        return session, None
