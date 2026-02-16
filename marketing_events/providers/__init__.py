from django.conf import settings

from .google_analytics import GoogleAnalyticsMP
from .meta import MetaCAPI
from .reddit import RedditCAPI
from .tiktok import TikTokCAPI


def get_providers():
    providers = []
    if getattr(settings, "FACEBOOK_ACCESS_TOKEN", None) and getattr(settings, "META_PIXEL_ID", None):
        providers.append(MetaCAPI(pixel_id=settings.META_PIXEL_ID, token=settings.FACEBOOK_ACCESS_TOKEN))
    if getattr(settings, "REDDIT_ACCESS_TOKEN", None) and getattr(settings, "REDDIT_ADVERTISER_ID", None):
        providers.append(RedditCAPI(pixel_id=settings.REDDIT_ADVERTISER_ID, token=settings.REDDIT_ACCESS_TOKEN))
    if getattr(settings, "TIKTOK_ACCESS_TOKEN", None) and getattr(settings, "TIKTOK_PIXEL_ID", None):
        providers.append(TikTokCAPI(pixel_id=settings.TIKTOK_PIXEL_ID, token=settings.TIKTOK_ACCESS_TOKEN))
    if settings.GA_MEASUREMENT_ID and settings.GA_MEASUREMENT_API_SECRET:
        providers.append(
            GoogleAnalyticsMP(
                measurement_id=settings.GA_MEASUREMENT_ID,
                api_secret=settings.GA_MEASUREMENT_API_SECRET,
            )
        )
    return providers
