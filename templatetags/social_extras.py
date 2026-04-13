from django import template
from django.contrib.sites.models import Site
from allauth.socialaccount.adapter import get_adapter

register = template.Library()

SOCIAL_AUTH_PROVIDER_METADATA = (
    {
        "id": "linkedin",
        "label": "LinkedIn",
        "analytics_label": "linkedin",
    },
    {
        "id": "microsoft",
        "label": "Microsoft",
        "analytics_label": "microsoft",
    },
    {
        "id": "google",
        "label": "Google",
        "analytics_label": "google",
    },
    {
        "id": "facebook",
        "label": "Facebook",
        "analytics_label": "facebook",
    },
)


def _provider_app_exists(context, provider: str) -> bool:
    """Return True when a provider has any configured app."""

    request = context.get("request")
    try:
        Site.objects.get_current(request) if request is not None else Site.objects.get_current()
    except Site.DoesNotExist:
        return False
    return bool(get_adapter().list_apps(request, provider=provider))


@register.simple_tag(takes_context=True)
def provider_app_exists(context, provider: str) -> bool:
    """Return True if the given social provider is configured.

    Checks either settings-based APP config or a SocialApp bound to the current Site.
    """
    return _provider_app_exists(context, provider)


@register.simple_tag(takes_context=True)
def configured_social_auth_providers(context) -> list[dict[str, str]]:
    """Return configured social auth providers in the UI's fixed display order."""

    providers: list[dict[str, str]] = []
    for metadata in SOCIAL_AUTH_PROVIDER_METADATA:
        if _provider_app_exists(context, metadata["id"]):
            providers.append(dict(metadata))
    return providers
