from enum import StrEnum

from django.conf import settings

from marketing_events.api import capi
from marketing_events.constants import AD_CAPI_PROVIDER_TARGETS
from marketing_events.context import build_marketing_context_from_user


class ConfiguredCustomEvent(StrEnum):
    AGENT_CREATED = "AgentCreated"
    INBOUND_MESSAGE = "InboundMessage"
    INTEGRATION_ADDED = "IntegrationAdded"
    SECRET_ADDED = "SecretAdded"
    CLONE_GOBII = "CloneGobii"
    TEMPLATE_LAUNCHED = "TemplateLaunched"


def build_configured_custom_event_properties(
    event_name: ConfiguredCustomEvent | str,
    properties: dict | None = None,
) -> dict:
    event_properties = {
        "value": settings.CAPI_CUSTOM_EVENT_VALUES[str(event_name)],
        "currency": settings.CAPI_CUSTOM_EVENT_CURRENCY,
    }
    if properties:
        event_properties.update(properties)
    return event_properties


def emit_configured_custom_capi_event(
    user,
    event_name: ConfiguredCustomEvent | str,
    *,
    properties: dict | None = None,
    request=None,
    context: dict | None = None,
) -> None:
    resolved_context = context
    if request is None:
        resolved_context = build_marketing_context_from_user(user) | (context or {})

    capi(
        user=user,
        event_name=str(event_name),
        properties=build_configured_custom_event_properties(event_name, properties),
        request=request,
        context=resolved_context,
        provider_targets=AD_CAPI_PROVIDER_TARGETS,
    )
