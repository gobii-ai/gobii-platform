from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

from constants.feature_flags import FISH_COLLATERAL
from waffle import switch_is_active


LEGACY_MANIFEST_ICONS = (
    {"src": "/static/images/favicon-16x16.png", "sizes": "16x16", "type": "image/png"},
    {"src": "/static/images/favicon-32x32.png", "sizes": "32x32", "type": "image/png"},
    {"src": "/static/images/favicon-192x192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/images/gobii_swoosh_white_on_blue_512.png", "sizes": "512x512", "type": "image/png"},
)

FISH_MANIFEST_ICONS = (
    {"src": "/static/images/gobii_fish_favicon_16.png", "sizes": "16x16", "type": "image/png"},
    {"src": "/static/images/gobii_fish_favicon_32.png", "sizes": "32x32", "type": "image/png"},
    {"src": "/static/images/gobii_fish_icon_192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/images/gobii_fish_icon_512.png", "sizes": "512x512", "type": "image/png"},
)


def is_fish_collateral_enabled(*, default: bool = False) -> bool:
    try:
        return switch_is_active(FISH_COLLATERAL)
    except (DatabaseError, ImproperlyConfigured):
        return default


def build_web_manifest_payload(*, fish_collateral_enabled: bool) -> dict:
    return {
        "name": "Gobii",
        "short_name": "Gobii",
        "description": "Gobii Platform",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#0ea5e9",
        "icons": list(FISH_MANIFEST_ICONS if fish_collateral_enabled else LEGACY_MANIFEST_ICONS),
    }
