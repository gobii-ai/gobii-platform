from django.conf import settings

from .meta import MetaCAPI
from .reddit import RedditCAPI


def get_providers():
    providers = []
    if getattr(settings, "META_CAPI_TOKEN", None) and getattr(settings, "META_PIXEL_ID", None):
        providers.append(MetaCAPI(pixel_id=settings.META_PIXEL_ID, token=settings.META_CAPI_TOKEN))
    if getattr(settings, "REDDIT_CONVERSIONS_TOKEN", None) and getattr(settings, "REDDIT_AD_ACCOUNT", None):
        providers.append(RedditCAPI(ad_account=settings.REDDIT_AD_ACCOUNT, token=settings.REDDIT_CONVERSIONS_TOKEN))
    return providers
