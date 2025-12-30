"""
Heuristic mappings for auto-enabling site-specific tools based on keyword mentions.

This module provides efficient text matching to automatically enable relevant
BrightData (and other) MCP tools when users mention specific sites or services.
Auto-enabling only occurs when there is capacity in the agent's tool budget -
it will never evict existing tools.
"""

import re
from typing import Set

# Each entry maps a set of keyword variations to a list of full tool names.
# Tool names are explicit and complete - no prefix manipulation.
AUTOTOOL_HEURISTICS: list[dict] = [
    # Amazon
    {
        "keywords": ["amazon", "amzn"],
        "tools": [
            "mcp_brightdata_web_data_amazon_product",
            "mcp_brightdata_web_data_amazon_product_reviews",
            "mcp_brightdata_web_data_amazon_product_search",
        ],
    },
    # LinkedIn
    {
        "keywords": ["linkedin"],
        "tools": [
            "mcp_brightdata_web_data_linkedin_person_profile",
            "mcp_brightdata_web_data_linkedin_company_profile",
            "mcp_brightdata_web_data_linkedin_job_listings",
            "mcp_brightdata_web_data_linkedin_posts",
            "mcp_brightdata_web_data_linkedin_people_search",
        ],
    },
    # Instagram
    {
        "keywords": ["instagram", "insta"],
        "tools": [
            "mcp_brightdata_web_data_instagram_profiles",
            "mcp_brightdata_web_data_instagram_posts",
            "mcp_brightdata_web_data_instagram_reels",
            "mcp_brightdata_web_data_instagram_comments",
        ],
    },
    # TikTok
    {
        "keywords": ["tiktok", "tik tok"],
        "tools": [
            "mcp_brightdata_web_data_tiktok_profiles",
            "mcp_brightdata_web_data_tiktok_posts",
            "mcp_brightdata_web_data_tiktok_shop",
            "mcp_brightdata_web_data_tiktok_comments",
        ],
    },
    # Twitter/X
    {
        "keywords": ["twitter", "x.com"],
        "tools": [
            "mcp_brightdata_web_data_x_posts",
        ],
    },
    # YouTube
    {
        "keywords": ["youtube", "youtu.be"],
        "tools": [
            "mcp_brightdata_web_data_youtube_profiles",
            "mcp_brightdata_web_data_youtube_videos",
            "mcp_brightdata_web_data_youtube_comments",
        ],
    },
    # Facebook
    {
        "keywords": ["facebook", "fb.com"],
        "tools": [
            "mcp_brightdata_web_data_facebook_posts",
            "mcp_brightdata_web_data_facebook_marketplace_listings",
            "mcp_brightdata_web_data_facebook_company_reviews",
            "mcp_brightdata_web_data_facebook_events",
        ],
    },
    # Reddit
    {
        "keywords": ["reddit"],
        "tools": [
            "mcp_brightdata_web_data_reddit_posts",
        ],
    },
    # Walmart
    {
        "keywords": ["walmart"],
        "tools": [
            "mcp_brightdata_web_data_walmart_product",
            "mcp_brightdata_web_data_walmart_seller",
        ],
    },
    # eBay
    {
        "keywords": ["ebay"],
        "tools": [
            "mcp_brightdata_web_data_ebay_product",
        ],
    },
    # Etsy
    {
        "keywords": ["etsy"],
        "tools": [
            "mcp_brightdata_web_data_etsy_products",
        ],
    },
    # Best Buy
    {
        "keywords": ["bestbuy", "best buy"],
        "tools": [
            "mcp_brightdata_web_data_bestbuy_products",
        ],
    },
    # Home Depot
    {
        "keywords": ["homedepot", "home depot"],
        "tools": [
            "mcp_brightdata_web_data_homedepot_products",
        ],
    },
    # Zara
    {
        "keywords": ["zara"],
        "tools": [
            "mcp_brightdata_web_data_zara_products",
        ],
    },
    # Zillow
    {
        "keywords": ["zillow"],
        "tools": [
            "mcp_brightdata_web_data_zillow_properties_listing",
        ],
    },
    # Crunchbase
    {
        "keywords": ["crunchbase"],
        "tools": [
            "mcp_brightdata_web_data_crunchbase_company",
        ],
    },
    # ZoomInfo
    {
        "keywords": ["zoominfo"],
        "tools": [
            "mcp_brightdata_web_data_zoominfo_company_profile",
        ],
    },
    # Booking.com
    {
        "keywords": ["booking.com", "booking com"],
        "tools": [
            "mcp_brightdata_web_data_booking_hotel_listings",
        ],
    },
    # GitHub
    {
        "keywords": ["github"],
        "tools": [
            "mcp_brightdata_web_data_github_repository_file",
        ],
    },
    # Google Maps
    {
        "keywords": ["google maps", "maps.google"],
        "tools": [
            "mcp_brightdata_web_data_google_maps_reviews",
        ],
    },
    # Google Play Store
    {
        "keywords": ["google play", "play store", "play.google"],
        "tools": [
            "mcp_brightdata_web_data_google_play_store",
        ],
    },
    # Apple App Store
    {
        "keywords": ["app store", "apps.apple"],
        "tools": [
            "mcp_brightdata_web_data_apple_app_store",
        ],
    },
    # Google Shopping
    {
        "keywords": ["google shopping", "shopping.google"],
        "tools": [
            "mcp_brightdata_web_data_google_shopping",
        ],
    },
    # Reuters
    {
        "keywords": ["reuters"],
        "tools": [
            "mcp_brightdata_web_data_reuter_news",
        ],
    },
    # Yahoo Finance
    {
        "keywords": ["yahoo finance", "finance.yahoo"],
        "tools": [
            "mcp_brightdata_web_data_yahoo_finance_business",
        ],
    },
    # File exports
    {
        "keywords": ["csv"],
        "tools": [
            "create_csv",
        ],
    },
    {
        "keywords": ["pdf"],
        "tools": [
            "create_pdf",
        ],
    },
]


def _build_keyword_pattern(keywords: list[str]) -> re.Pattern:
    """Build a compiled regex pattern for word-boundary matching of keywords."""
    # Escape special regex characters and join with alternation
    escaped = [re.escape(kw) for kw in keywords]
    pattern = r'\b(' + '|'.join(escaped) + r')\b'
    return re.compile(pattern, re.IGNORECASE)


# Pre-compile patterns at module load for efficiency
_COMPILED_HEURISTICS: list[tuple[re.Pattern, list[str]]] = [
    (_build_keyword_pattern(entry["keywords"]), entry["tools"])
    for entry in AUTOTOOL_HEURISTICS
]


def find_matching_tools(text: str) -> Set[str]:
    """
    Find all tools that should be auto-enabled based on keyword matches in text.

    Uses word-boundary matching so "ig" won't match "pig" or "big".

    Args:
        text: The text to scan for keyword mentions (typically user message).

    Returns:
        Set of full tool names that matched.
    """
    if not text:
        return set()

    matched_tools: Set[str] = set()

    for pattern, tools in _COMPILED_HEURISTICS:
        if pattern.search(text):
            matched_tools.update(tools)

    return matched_tools
