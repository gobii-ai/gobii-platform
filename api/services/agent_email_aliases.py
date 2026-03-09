from config import settings

from api.models import CommsChannel, PersistentAgentCommsEndpoint


def get_default_agent_email_domain() -> str:
    return (settings.DEFAULT_AGENT_EMAIL_DOMAIN or "").strip().lower()


def get_default_agent_email_suffix() -> str:
    domain = get_default_agent_email_domain()
    if not domain:
        return ""
    return f"@{domain}"


def is_default_agent_email_address(address: str | None) -> bool:
    normalized = (address or "").strip().lower()
    suffix = get_default_agent_email_suffix()
    if not normalized or not suffix:
        return False
    return normalized.endswith(suffix)


def get_default_agent_email_endpoint(agent) -> PersistentAgentCommsEndpoint | None:
    suffix = get_default_agent_email_suffix()
    if not suffix:
        return None
    return (
        agent.comms_endpoints
        .filter(channel=CommsChannel.EMAIL, address__iendswith=suffix)
        .order_by("-is_primary", "address")
        .first()
    )
