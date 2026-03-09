import logging
from typing import Iterable, Optional

from api.models import CommsChannel, PersistentAgentCommsEndpoint
from api.services.agent_email_aliases import get_default_agent_email_endpoint


logger = logging.getLogger(__name__)


def get_agent_primary_endpoint(
    agent,
    channel: str | CommsChannel,
) -> Optional[PersistentAgentCommsEndpoint]:
    channel_value = channel.value if isinstance(channel, CommsChannel) else channel
    return (
        agent.comms_endpoints.filter(channel=channel_value, is_primary=True).first()
        or agent.comms_endpoints.filter(channel=channel_value).first()
    )


def resolve_agent_email_sender_endpoint(
    agent,
    *,
    to_address: str = "",
    has_cc_or_bcc: bool = False,
    log_context: str = "",
) -> Optional[PersistentAgentCommsEndpoint]:
    """
    Return the preferred agent email endpoint for outbound delivery.

    Default behavior uses the agent's primary email endpoint.

    Self-send fallback:
    - If there are no CC/BCC recipients, and the normalized `to_address` equals
      the selected sender address, switch sender to the agent's default-domain
      email endpoint (e.g. @my.gobii.ai) when available.
    """
    primary_endpoint = get_agent_primary_endpoint(agent, CommsChannel.EMAIL)
    if primary_endpoint is None:
        return None

    if has_cc_or_bcc:
        return primary_endpoint

    normalized_to = (to_address or "").strip().lower()
    normalized_from = (primary_endpoint.address or "").strip().lower()
    if not normalized_to or normalized_to != normalized_from:
        return primary_endpoint

    default_endpoint = get_default_agent_email_endpoint(agent)
    if default_endpoint and (default_endpoint.address or "").strip().lower() != normalized_from:
        return default_endpoint

    logger.warning(
        "Email self-send fallback could not switch sender for agent %s. context=%s",
        getattr(agent, "id", None),
        log_context or "unspecified",
    )
    return primary_endpoint


def resolve_agent_email_sender_endpoint_for_message(
    agent,
    *,
    to_endpoint: PersistentAgentCommsEndpoint | None,
    cc_endpoints: Iterable[PersistentAgentCommsEndpoint] | None = None,
    has_bcc: bool = False,
    log_context: str = "",
) -> Optional[PersistentAgentCommsEndpoint]:
    cc_present = False
    if cc_endpoints is not None:
        if hasattr(cc_endpoints, "exists"):
            cc_present = bool(cc_endpoints.exists())
        else:
            cc_present = any(True for _ in cc_endpoints)
    to_address = (getattr(to_endpoint, "address", "") or "").strip()
    return resolve_agent_email_sender_endpoint(
        agent,
        to_address=to_address,
        has_cc_or_bcc=cc_present or has_bcc,
        log_context=log_context,
    )
