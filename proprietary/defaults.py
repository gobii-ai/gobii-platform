"""Proprietary deployment defaults.

Community builds leave these values empty or generic; proprietary deployments
can import the defaults module to supply brand, support, and analytics
identifiers without exposing them in the main OSS settings file.
"""
from __future__ import annotations

from typing import Dict, Mapping

DEFAULTS: Dict[str, Dict[str, str]] = {
    "brand": {
        "PUBLIC_BRAND_NAME": "Gobii",
        "PUBLIC_SITE_URL": "https://gobii.ai",
        "PUBLIC_CONTACT_EMAIL": "hello@gobii.ai",
        "PUBLIC_SUPPORT_EMAIL": "support@gobii.ai",
        "PUBLIC_GITHUB_URL": "https://github.com/gobii-ai",
        "PUBLIC_DISCORD_URL": "https://discord.gg/yyDB8GwxtE",
        "PUBLIC_X_URL": "https://x.com/gobii_ai",
    },
    "support": {
        "DEFAULT_FROM_EMAIL": "Gobii <noreply@mg.getgobii.com>",
        "MAILGUN_SENDER_DOMAIN": "mg.getgobii.com",
        "SUPPORT_EMAIL": "support@gobii.ai",
    },
    "analytics": {
        # Real keys are injected via environment in production deployments.
        "SEGMENT_WRITE_KEY": "",
        "SEGMENT_WEB_WRITE_KEY": "",
        "REDDIT_PIXEL_ID": "",
        "META_PIXEL_ID": "",
        "MIXPANEL_PROJECT_TOKEN": "",
        "LINKEDIN_PARTNER_ID": "",
    },
}


def get(section: str) -> Mapping[str, str]:
    """Return defaults for the requested section if defined."""
    return DEFAULTS.get(section, {})
