from django import template
from django.conf import settings
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp

register = template.Library()


@register.simple_tag(takes_context=True)
def provider_app_exists(context, provider: str) -> bool:
    """Return True if the given social provider is configured.

    Checks either settings-based APP config or a SocialApp bound to the current Site.
    """
    # 1) settings-based configuration
    prov_cfg = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {}).get(provider, {})
    app_cfg = prov_cfg.get("APP") if isinstance(prov_cfg, dict) else None
    if isinstance(app_cfg, dict):
        client_id = (app_cfg.get("client_id") or app_cfg.get("clientId") or "").strip()
        secret = (app_cfg.get("secret") or app_cfg.get("clientSecret") or "").strip()
        if client_id and secret:
            return True

    # 2) database-backed SocialApp for current site
    request = context.get("request")
    try:
        site = Site.objects.get_current(request)
    except Exception:
        site = Site.objects.get_current()
    return SocialApp.objects.filter(provider=provider, sites=site).exists()

