"""
Context Hints - Lightning-fast decision accelerators for tool results.

Philosophy: Every byte earns its place.
Goal: Agent makes instant decisions without extra extraction steps.

This is purely OPTIMISTIC - if we can't extract a useful hint, we return None.
No hint is better than a bad hint.
"""

import re
from typing import Optional


# Domains that are noise in search results
_NOISE_DOMAINS = frozenset({
    'google.com', 'gstatic.com', 'googleapis.com', 'googleusercontent.com',
    'youtube.com',  # Usually not helpful for product searches
    'facebook.com', 'twitter.com', 'x.com',  # Social rarely has prices
})

# URL patterns to extract from messy markdown
_URL_PATTERNS = [
    # Standard markdown: [title](url)
    re.compile(r'\[([^\]]{2,80})\]\((https?://[^)]+)\)'),
    # Reference-style: [title][ref] followed by [ref]: url
    re.compile(r'\[([^\]]{2,80})\]:\s*(https?://\S+)'),
]

# Bare URL pattern (fallback)
_BARE_URL = re.compile(r'https?://[^\s\)\]"\'<>]{10,200}')

# Price patterns to detect
_PRICE_PATTERN = re.compile(r'\$[\d,]+(?:\.\d{2})?|\d+(?:,\d{3})*(?:\.\d{2})?\s*(?:USD|EUR|GBP)')


def _domain_from_url(url: str) -> str:
    """Extract clean domain from URL."""
    # Remove protocol
    clean = re.sub(r'^https?://(www\.)?', '', url)
    # Get domain only
    domain = clean.split('/')[0].split('?')[0]
    return domain.lower()


def _title_from_url(url: str) -> str:
    """Derive readable title from URL path when no title available."""
    # Get path after domain
    match = re.search(r'https?://[^/]+/(.+)', url)
    if not match:
        return _domain_from_url(url)

    path = match.group(1).split('?')[0].split('#')[0]
    # Take last meaningful segment
    segments = [s for s in path.split('/') if s and len(s) > 2]
    if not segments:
        return _domain_from_url(url)

    # Clean the segment
    title = segments[-1]
    title = re.sub(r'[-_]', ' ', title)
    title = re.sub(r'\.\w{2,4}$', '', title)  # Remove extension
    return title[:50].strip()


def _is_useful_url(url: str) -> bool:
    """Check if URL is worth including in hint."""
    domain = _domain_from_url(url)

    # Skip noise domains
    for noise in _NOISE_DOMAINS:
        if noise in domain:
            return False

    # Skip very short URLs (usually broken)
    if len(url) < 20:
        return False

    return True


def _extract_serp_items(text: str, max_items: int = 5) -> list[dict]:
    """Extract search result items from messy markdown.

    Returns list of {t: title, u: url, d: domain} dicts.
    Aggressive extraction that handles various markdown formats.
    """
    items = []
    seen_domains = set()

    # Try standard markdown links first
    for pattern in _URL_PATTERNS:
        for match in pattern.finditer(text):
            title, url = match.groups()

            if not _is_useful_url(url):
                continue

            domain = _domain_from_url(url)

            # Skip duplicate domains (keep first occurrence)
            if domain in seen_domains:
                continue

            # Clean title
            title = title.strip()
            if len(title) < 3 or title.lower() in ('read more', 'click here', 'learn more', 'link'):
                title = _title_from_url(url)

            seen_domains.add(domain)
            items.append({
                't': title[:60],
                'u': url[:200],
                'd': domain,
            })

            if len(items) >= max_items:
                return items

    # Fallback: extract bare URLs and derive titles
    if len(items) < max_items:
        for match in _BARE_URL.finditer(text):
            url = match.group(0).rstrip('.,;:')

            if not _is_useful_url(url):
                continue

            domain = _domain_from_url(url)
            if domain in seen_domains:
                continue

            seen_domains.add(domain)
            items.append({
                't': _title_from_url(url),
                'u': url[:200],
                'd': domain,
            })

            if len(items) >= max_items:
                break

    return items


def hint_from_serp(payload: dict, max_items: int = 5) -> Optional[str]:
    """Extract context hint from SERP result.

    Returns compact hint string or None if extraction fails.

    Format (optimized for quick scanning):
        🔍 domain.com: Title | domain2.com: Title2
        → url1
        → url2
    """
    # Case 1: Already have skeleton items (adapter succeeded)
    items = payload.get('items', [])
    if items and isinstance(items, list):
        items = items[:max_items]
    else:
        # Case 2: Raw markdown - extract aggressively
        markdown = payload.get('result', '')
        if not markdown or not isinstance(markdown, str):
            return None

        items = _extract_serp_items(markdown, max_items)

    if not items:
        return None

    # Build compact hint
    # Line 1: domains with titles (for quick scanning)
    summaries = []
    urls = []

    for item in items:
        domain = item.get('d') or _domain_from_url(item.get('u', ''))
        title = item.get('t', '')[:40]
        url = item.get('u', '')

        if domain and title:
            summaries.append(f"{domain}: {title}")
        elif domain:
            summaries.append(domain)

        if url:
            urls.append(url)

    if not summaries:
        return None

    # Format: summary line + URLs (agent needs URLs to scrape)
    lines = [f"🔍 {' | '.join(summaries[:3])}"]

    # Add URLs on separate lines (agent can copy-paste to scrape)
    for url in urls[:max_items]:
        lines.append(f"→ {url}")

    return '\n'.join(lines)


def hint_from_scraped_page(payload: dict) -> Optional[str]:
    """Extract context hint from scraped page result.

    For pages, extract key info like:
    - Title
    - Any prices found
    - Key headings
    """
    # Check for skeleton format first
    title = payload.get('title', '')
    items = payload.get('items', [])
    excerpt = payload.get('excerpt', '')

    if not title and not items and not excerpt:
        # Try raw markdown
        markdown = payload.get('result', '')
        if not markdown:
            return None

        # Extract title from first heading
        title_match = re.search(r'^#\s+(.+)$', markdown, re.MULTILINE)
        if title_match:
            title = title_match.group(1)[:80]

        # Look for prices
        prices = _PRICE_PATTERN.findall(markdown[:5000])
        if prices:
            unique_prices = list(dict.fromkeys(prices))[:3]
            price_str = ', '.join(unique_prices)
            if title:
                return f"📄 {title}\n💰 {price_str}"
            return f"💰 Prices found: {price_str}"

        if title:
            return f"📄 {title}"

        return None

    # Build from skeleton
    parts = []
    if title:
        parts.append(f"📄 {title[:80]}")

    # Check excerpt for prices
    if excerpt:
        prices = _PRICE_PATTERN.findall(excerpt)
        if prices:
            unique_prices = list(dict.fromkeys(prices))[:3]
            parts.append(f"💰 {', '.join(unique_prices)}")

    # Add key headings from items (if article structure)
    if items and isinstance(items, list):
        headings = [item.get('h', '')[:40] for item in items[:3] if item.get('h')]
        if headings:
            parts.append(f"§ {' | '.join(headings)}")

    if not parts:
        return None

    return '\n'.join(parts)


def extract_context_hint(tool_name: str, payload: dict) -> Optional[str]:
    """Main entry point - extract context hint based on tool type.

    Returns compact hint string or None.
    This is optimistic - no hint is better than a bad hint.
    """
    if not isinstance(payload, dict):
        return None

    # Route to appropriate extractor
    if tool_name in ('search_engine', 'mcp_brightdata_search_engine'):
        return hint_from_serp(payload)

    if tool_name in ('scrape_as_markdown', 'mcp_brightdata_scrape_as_markdown'):
        return hint_from_scraped_page(payload)

    # TODO: Add more extractors
    # - LinkedIn profiles: name, title, company
    # - Product pages: name, price, availability
    # - etc.

    return None
