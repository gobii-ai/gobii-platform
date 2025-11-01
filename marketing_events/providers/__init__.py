from django.conf import settings

from .meta import MetaCAPI
from .reddit import RedditCAPI


def get_providers():
    providers = []
    if getattr(settings, "FACEBOOK_ACCESS_TOKEN", None) and getattr(settings, "META_PIXEL_ID", None):
        providers.append(MetaCAPI(pixel_id=settings.META_PIXEL_ID, token=settings.FACEBOOK_ACCESS_TOKEN))
    if getattr(settings, "REDDIT_ACCESS_TOKEN", None) and getattr(settings, "REDDIT_ADVERTISER_ID", None):
        providers.append(RedditCAPI(pixel_id=settings.REDDIT_ADVERTISER_ID, token=settings.REDDIT_ACCESS_TOKEN))
    return providers
