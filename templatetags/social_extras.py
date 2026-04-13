from django import template
from django.conf import settings
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp

register = template.Library()

SOCIAL_AUTH_PROVIDER_METADATA = (
    {
        "id": "linkedin_oauth2",
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
    """Return True when a provider has either settings or site-backed config."""

    prov_cfg = settings.SOCIALACCOUNT_PROVIDERS.get(provider, {})
    app_cfg = prov_cfg.get("APP") if isinstance(prov_cfg, dict) else None
    if isinstance(app_cfg, dict):
        client_id = (app_cfg.get("client_id") or app_cfg.get("clientId") or "").strip()
        secret = (app_cfg.get("secret") or app_cfg.get("clientSecret") or "").strip()
        if client_id and secret:
            return True

    request = context.get("request")
    try:
        site = Site.objects.get_current(request) if request is not None else Site.objects.get_current()
    except Site.DoesNotExist:
        return False
    return SocialApp.objects.filter(provider=provider, sites=site).exists()


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
