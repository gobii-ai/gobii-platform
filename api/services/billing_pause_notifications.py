import logging
from typing import Any

from django.apps import apps
from django.contrib.sites.models import Site
from django.core.exceptions import MultipleObjectsReturned
from django.db import DatabaseError
from django.db.models import F
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import NoReverseMatch, reverse

from api.models import ExecutionPauseReasonChoices
from util.urls import append_context_query


logger = logging.getLogger(__name__)


_SUPPORTED_BILLING_NOTIFICATION_CHANNELS = {"email", "sms"}
_BILLING_EXECUTION_PAUSE_REASONS = frozenset(
    {
        ExecutionPauseReasonChoices.BILLING_DELINQUENCY,
        ExecutionPauseReasonChoices.TRIAL_CONVERSION_FAILED,
        ExecutionPauseReasonChoices.TRIAL_ENDED_NON_RENEWAL,
    }
)
_TRIAL_ENDED_EXECUTION_PAUSE_REASONS = frozenset(
    {
        ExecutionPauseReasonChoices.TRIAL_ENDED_NON_RENEWAL,
    }
)


def is_billing_execution_pause_reason(reason: str) -> bool:
    return str(reason or "").strip() in _BILLING_EXECUTION_PAUSE_REASONS


def send_billing_pause_auto_reply(agent, recipient_endpoint, *, reason: str) -> bool:
    channel = str(getattr(recipient_endpoint, "channel", "") or "").strip().lower()
    if channel not in _SUPPORTED_BILLING_NOTIFICATION_CHANNELS:
        logger.info(
            "Skipping billing pause auto-reply for agent %s: unsupported recipient channel %s.",
            getattr(agent, "id", None),
            channel or "-",
        )
        return False

    return _send_billing_pause_message(
        agent,
        recipient_endpoint,
        reason=reason,
        audience="sender",
    )


def send_owner_billing_pause_notification(owner) -> None:
    reason = _pause_reason_for_owner(owner)
    if not is_billing_execution_pause_reason(reason):
        return

    try:
        agent = _get_most_recent_owner_agent(owner)
        if agent is None:
            logger.info(
                "Skipping billing pause owner notification for owner %s: no eligible agent found.",
                getattr(owner, "id", None),
            )
            return

        endpoint = getattr(agent, "preferred_contact_endpoint", None)
        channel = str(getattr(endpoint, "channel", "") or "").strip().lower()
        if endpoint is None or channel not in _SUPPORTED_BILLING_NOTIFICATION_CHANNELS:
            logger.info(
                "Skipping billing pause owner notification for agent %s: preferred endpoint channel is %s.",
                getattr(agent, "id", None),
                channel or "-",
            )
            return

        _send_billing_pause_message(
            agent,
            endpoint,
            reason=reason,
            audience="owner",
        )
    except Exception:
        logger.exception(
            "Failed sending owner billing pause notification for owner %s",
            getattr(owner, "id", None),
        )


def _pause_reason_for_owner(owner) -> str:
    try:
        billing = owner.billing
    except Exception:
        billing = None
    return str(getattr(billing, "execution_pause_reason", "") or "")


def _get_most_recent_owner_agent(owner):
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    Organization = apps.get_model("api", "Organization")
    qs = (
        PersistentAgent.objects.non_eval()
        .alive()
        .select_related("preferred_contact_endpoint", "organization", "user")
        .order_by(F("last_interaction_at").desc(nulls_last=True), "-created_at")
    )

    if isinstance(owner, Organization):
        qs = qs.filter(organization_id=owner.id)
    else:
        qs = qs.filter(user_id=owner.id, organization__isnull=True)

    return qs.first()


def _send_billing_pause_message(agent, recipient_endpoint, *, reason: str, audience: str) -> bool:
    channel = str(getattr(recipient_endpoint, "channel", "") or "").strip().lower()
    if channel not in _SUPPORTED_BILLING_NOTIFICATION_CHANNELS:
        return False

    content = _build_billing_pause_message_content(
        agent=agent,
        reason=reason,
        audience=audience,
    )
    if content is None:
        return False

    PersistentAgentMessage = apps.get_model("api", "PersistentAgentMessage")
    kind = "billing_pause_owner_notice" if audience == "owner" else "billing_pause_auto_reply"

    if channel == "email":
        from api.agent.comms.email_endpoint_routing import (
            resolve_agent_email_sender_endpoint_for_message,
        )
        from api.agent.comms.outbound_delivery import deliver_agent_email

        from_endpoint = resolve_agent_email_sender_endpoint_for_message(
            agent,
            to_endpoint=recipient_endpoint,
            cc_endpoints=None,
            has_bcc=False,
            log_context=kind,
        )
        if from_endpoint is None:
            logger.info(
                "Skipping billing pause email for agent %s: no sender endpoint available.",
                getattr(agent, "id", None),
            )
            return False

        body = render_to_string(content["email_template"], content["context"])
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=from_endpoint,
            to_endpoint=recipient_endpoint,
            is_outbound=True,
            body=body,
            raw_payload={
                "subject": content["subject"],
                "kind": kind,
            },
        )
        deliver_agent_email(message)
        return True

    from api.agent.comms.email_endpoint_routing import get_agent_primary_endpoint
    from api.agent.comms.outbound_delivery import deliver_agent_sms

    from_endpoint = get_agent_primary_endpoint(agent, "sms")
    if from_endpoint is None:
        logger.info(
            "Skipping billing pause SMS for agent %s: no sender endpoint available.",
            getattr(agent, "id", None),
        )
        return False

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=from_endpoint,
        to_endpoint=recipient_endpoint,
        is_outbound=True,
        body=content["sms_body"],
        raw_payload={"kind": kind},
    )
    deliver_agent_sms(message)
    return True


def _build_billing_pause_message_content(*, agent, reason: str, audience: str) -> dict[str, Any] | None:
    is_trial_end = str(reason or "").strip() in _TRIAL_ENDED_EXECUTION_PAUSE_REASONS
    billing_url = _build_billing_url(agent)
    logo_url = _build_logo_url()

    if audience == "owner":
        if is_trial_end:
            subject = f"{agent.name} is paused because your trial ended"
            intro_text = "Your trial ended, so I'm paused for now."
            detail_text = "Restart billing to let me reply again."
            sms_body = f"My trial ended, so I'm paused. Restart billing to resume replies.{_sms_link_suffix(billing_url)}"
        else:
            subject = f"{agent.name} is paused until billing is resolved"
            intro_text = "I'm paused until billing is resolved."
            detail_text = "Once billing is fixed, I'll be able to reply again."
            sms_body = f"I'm paused until billing is resolved. Update billing to resume replies.{_sms_link_suffix(billing_url)}"

        return {
            "subject": subject,
            "sms_body": sms_body,
            "email_template": "emails/agent_billing_paused_owner_notice.html",
            "context": {
                "agent": agent,
                "intro_text": intro_text,
                "detail_text": detail_text,
                "billing_url": billing_url,
                "logo_url": logo_url,
            },
        }

    if audience == "sender":
        if is_trial_end:
            subject = f"{agent.name} can't reply right now"
            intro_text = f"{agent.name} is paused because the trial ended."
            detail_text = "If you're the account owner, restart billing to resume replies. Otherwise, contact the owner."
            sms_body = (
                f"{agent.name} can't reply right now because the trial ended. "
                f"If you're the account owner, restart billing.{_sms_link_suffix(billing_url)}"
            )
        else:
            subject = f"{agent.name} can't reply right now"
            intro_text = f"{agent.name} is paused because billing needs attention."
            detail_text = "If you're the account owner, update billing to resume replies. Otherwise, contact the owner."
            sms_body = (
                f"{agent.name} can't reply right now because billing needs attention. "
                f"If you're the account owner, update billing.{_sms_link_suffix(billing_url)}"
            )

        return {
            "subject": subject,
            "sms_body": sms_body,
            "email_template": "emails/agent_billing_paused_reply.html",
            "context": {
                "agent": agent,
                "intro_text": intro_text,
                "detail_text": detail_text,
                "billing_url": billing_url,
                "logo_url": logo_url,
            },
        }

    return None


def _build_billing_url(agent) -> str:
    try:
        billing_url = _build_site_url(reverse("billing"))
    except (
        NoReverseMatch,
        Site.DoesNotExist,
        MultipleObjectsReturned,
        DatabaseError,
        ValueError,
    ):
        return ""

    if getattr(agent, "organization_id", None):
        return append_context_query(billing_url, agent.organization_id)
    return billing_url


def _build_logo_url() -> str:
    try:
        return _build_site_url(static("images/gobii_fish_with_text_purple.png"))
    except (
        Site.DoesNotExist,
        MultipleObjectsReturned,
        DatabaseError,
        ValueError,
    ):
        return ""


def _build_site_url(path: str) -> str:
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path

    from django.conf import settings as django_settings

    base_url = (getattr(django_settings, "PUBLIC_SITE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        current_site = Site.objects.get_current()
        base_url = f"https://{current_site.domain}"
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{base_url}{normalized}"


def _sms_link_suffix(billing_url: str) -> str:
    if not billing_url:
        return ""
    return f" {billing_url}"
