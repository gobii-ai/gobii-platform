"""
Context Hints - Adaptive extraction of key info from arbitrary JSON structures.

Philosophy: Every byte earns its place.
Goal: Agent sees the essential info instantly, decides what to explore.

The system auto-detects data shapes and extracts what matters:
- Arrays of people → names, titles, URLs
- Arrays of products → names, prices
- Single entities → identity, metrics, location
- Nested structures → recursively find the interesting parts

Hard caps ensure hints stay small (~400 bytes max).
No hint is better than a bad hint.
"""

import re
from typing import Any, Optional

from .json_goldilocks import goldilocks_summary
from .text_focus import barbell_focus
from .text_digest import digest as digest_text


# =============================================================================
# Configuration: Field Categories & Weights
# =============================================================================

# Fields that identify an entity (highest priority)
IDENTITY_FIELDS = frozenset({
    'name', 'full_name', 'display_name', 'title', 'product_name',
    'username', 'screen_name', 'nickname', 'unique_id', 'handle',
    'channel_name', 'company_name', 'organization_name', 'seller_name',
    'author_name', 'brand', 'asin', 'sku', 'item_name',
})

# Fields that describe role/position (second priority)
ROLE_FIELDS = frozenset({
    'headline', 'position', 'role', 'job_title', 'subtitle',
    'tagline', 'short_description', 'summary', 'slogan',
    'employment_type', 'seniority_level', 'function', 'specialties',
})

# Fields with numeric metrics (shown as stats)
METRIC_FIELDS = frozenset({
    'followers', 'followers_count', 'follower_count', 'following', 'following_count',
    'subscribers', 'subscriber_count', 'connections', 'connections_count',
    'linkedin_followers', 'linkedin_employees',
    'likes', 'likes_count', 'heart_count', 'favorite_count', 'num_likes',
    'views', 'view_count', 'plays', 'play_count',
    'posts', 'posts_count', 'media_count', 'video_count', 'videos', 'total_posts',
    'comments', 'comment_count', 'reply_count', 'retweet_count', 'num_comments',
    'applicants', 'applications',
    'price', 'cost', 'amount', 'total_funding', 'total_funding_usd', 'final_price',
    'rating', 'score', 'stars', 'reviews_count', 'average_rating',
    'employees', 'employee_count', 'company_size', 'num_employees_enum', 'size',
})

# Fields containing URLs (shown for easy access). The order prefers item-level
# destinations over source or image URLs when a record exposes several.
URL_FIELD_PRIORITY = (
    'profile_url', 'listing_url', 'detail_url', 'item_url',
    'linkedin_url', 'twitter_url', 'github_url', 'apply_link', 'job_link',
    'product_url', 'company_url', 'url', 'link', 'href', 'website', 'homepage',
    'source_url', 'image_url',
)
URL_FIELDS = frozenset(URL_FIELD_PRIORITY)
NON_ITEM_URL_FIELDS = frozenset({'source_url', 'feed_url', 'page_url', 'origin_url'})

# Fields with location info
LOCATION_FIELDS = frozenset({
    'location', 'city', 'country', 'headquarters', 'headquarters_location',
    'address', 'region', 'state', 'posted_location', 'job_location',
})

# Fields with org/company info
ORG_FIELDS = frozenset({
    'company', 'organization', 'employer', 'industry',
    'company_name', 'current_company', 'funding_stage', 'last_funding_type',
})

# Fields with text content (bio, description)
TEXT_FIELDS = frozenset({
    'bio', 'biography', 'description', 'about', 'text', 'content',
    'full_text', 'body', 'message',
})

# Hard limits - conservative for scalability with long histories
MAX_HINT_BYTES = 500  # Enough for 4 items + URLs, not more
MAX_ITEMS = 4
MAX_FIELD_LEN = 50
MAX_LINE_LEN = 120
BARBELL_TARGET_BYTES = 8000
GOLDILOCKS_MIN_BYTES = 8000  # Trigger for mid-sized messy JSON (was 20KB)
GOLDILOCKS_MAX_BYTES = 6000  # Cap output to avoid context bloat
GOLDILOCKS_HINT_PREFIX = "JSON_FOCUS:"


# =============================================================================
# Core: Adaptive Hint Extraction
# =============================================================================

def extract_context_hint(
    tool_name: str,
    payload: Any,
    *,
    allow_barbell: bool = False,
    allow_goldilocks: bool = False,
    payload_bytes: Optional[int] = None,
) -> Optional[str]:
    """
    Main entry point - extract context hint from any tool result.

    Routes to specialized extractors for known formats (SERP, scraped pages),
    then falls back to adaptive extraction for arbitrary JSON.
    """
    if not isinstance(payload, (dict, list)):
        return None

    # Special cases: SERP and scraped pages have unique formats
    if 'search_engine' in tool_name:
        return hint_from_serp(payload)

    if 'scrape_as_markdown' in tool_name:
        if not isinstance(payload, dict):
            return None
        return hint_from_scraped_page(payload, allow_barbell=allow_barbell)

    # Adaptive extraction for everything else
    structured_hint = hint_from_structured_data(payload)
    goldilocks_hint = None

    if allow_goldilocks and _should_use_goldilocks(payload_bytes):
        goldilocks_hint = hint_from_messy_json(payload)

    if structured_hint and goldilocks_hint:
        combined = f"{structured_hint}\n{goldilocks_hint}"
        return _enforce_limit_bytes(combined, GOLDILOCKS_MAX_BYTES)

    if structured_hint:
        return structured_hint

    return goldilocks_hint


def hint_from_structured_data(payload: Any) -> Optional[str]:
    """
    Adaptively extract hints from arbitrary JSON structures.

    Strategy:
    1. Find the "interesting" data (arrays or single objects)
    2. Extract key fields using heuristics
    3. Format compactly with hard caps
    """
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            return _hint_from_array(payload)
        return None

    if not isinstance(payload, dict):
        return None

    # Try to find an array of items first (most common pattern)
    array_data = _find_array(payload)
    if array_data and len(array_data) > 0:
        return _hint_from_array(array_data)

    # Single object - extract key fields directly
    obj_data = _find_object(payload)
    if obj_data:
        return _hint_from_object(obj_data)

    return None


def _find_array(payload: dict, max_depth: int = 3) -> Optional[list]:
    """
    Find an array of objects in the payload.

    Checks common locations: result, results, items, data, records, content.
    Recursively searches nested structures up to max_depth.
    """
    if max_depth <= 0:
        return None

    # Direct array keys (priority order)
    for key in ('result', 'results', 'items', 'data', 'records', 'content', 'entries', 'list'):
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value

    # Recurse into nested dicts
    for key, value in payload.items():
        if isinstance(value, dict):
            found = _find_array(value, max_depth - 1)
            if found:
                return found

    return None


def _find_object(payload: dict) -> Optional[dict]:
    """
    Find a single interesting object in the payload.

    Returns the payload itself if it has interesting fields,
    or checks common wrapper keys like 'result', 'data'.
    """
    # Check if payload itself is interesting
    if _has_interesting_fields(payload):
        return payload

    # Check common wrapper keys
    for key in ('result', 'data', 'content', 'item', 'profile', 'user', 'company'):
        value = payload.get(key)
        if isinstance(value, dict) and _has_interesting_fields(value):
            return value

    return None


def _has_interesting_fields(obj: dict) -> bool:
    """Check if object has fields worth extracting."""
    keys = set(k.lower() for k in obj.keys())
    interesting = IDENTITY_FIELDS | ROLE_FIELDS | METRIC_FIELDS | URL_FIELDS | TEXT_FIELDS
    return bool(keys & interesting)


# =============================================================================
# Array Hints: Multiple Items
# =============================================================================

def _hint_from_array(items: list) -> Optional[str]:
    """
    Generate hint from array of items.

    Format depends on detected type:
    - People: 👥 Name: Title | Name: Title
    - Products: 🛒 Product: $Price | Product: $Price
    - Generic: 📋 Title | Title | Title

    Always includes URLs if found.
    """
    if not items:
        return None

    # Sample first item to detect type
    first = items[0]
    if not isinstance(first, dict):
        return None

    # Detect type and extract accordingly
    item_type = _detect_item_type(first)

    lines = []
    extracted_items = []

    for item in items[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue

        extracted = _extract_item_fields(item, item_type)
        if extracted.get('display'):
            extracted_items.append(extracted)

    if not extracted_items:
        return None

    # Keep link availability adjacent to each entity. The prompt renderer later
    # replaces complete URLs with opaque link references.
    emoji = _emoji_for_type(item_type)
    for item in extracted_items:
        item_link = item.get('url') or 'none'
        line = f"{emoji} {item['display']} | item_link={item_link}"
        if len('\n'.join([*lines, line]).encode('utf-8')) > MAX_HINT_BYTES:
            if item.get('url'):
                fallback = f"{emoji} {item['display']} | item_link=available via query"
                if len('\n'.join([*lines, fallback]).encode('utf-8')) <= MAX_HINT_BYTES:
                    lines.append(fallback)
            continue
        lines.append(line)

    hint = '\n'.join(lines)
    return _enforce_limit(hint)


def _detect_item_type(item: dict) -> str:
    """Detect the type of item based on its fields."""
    keys = set(k.lower() for k in item.keys())

    # Check for job listing indicators (Bright Data jobs, LinkedIn jobs)
    if keys & {'employment_type', 'seniority_level', 'applicants', 'apply_link', 'job_info', 'posted_date'}:
        return 'job'
    if keys & {'job_title', 'job_link'} and keys & {'company', 'location'}:
        return 'job'

    # Check for people indicators (LinkedIn-like)
    if keys & {'headline', 'position', 'connections', 'connections_count', 'experience', 'experiences'}:
        return 'person'
    if keys & {'subtitle'} and keys & {'name', 'full_name'}:
        return 'person'
    # name + title/role is a person pattern (common in search results)
    if keys & {'name', 'full_name'} and keys & {'title', 'role', 'job_title'}:
        return 'person'

    # Check for social profile indicators
    if keys & {'followers', 'followers_count', 'follower_count', 'following', 'posts_count', 'bio', 'biography'}:
        return 'profile'
    if keys & {'linkedin_followers', 'linkedin_employees'}:
        return 'profile'

    # Check for company indicators
    if keys & {'industry', 'company_size', 'headquarters', 'funding', 'total_funding', 'employees'}:
        return 'company'
    if keys & {'key_info', 'metrics', 'stock_info'}:
        return 'company'

    # Check for product indicators (Amazon, ecommerce)
    if keys & {'asin', 'seller', 'seller_name', 'final_price'}:
        return 'product'
    if keys & {'price', 'cost', 'amount'} and keys & {'name', 'title', 'product_name', 'brand'}:
        return 'product'

    # Check for review indicators
    if keys & {'rating', 'score', 'stars'} and keys & {'text', 'review_text', 'author', 'author_name'}:
        return 'review'

    # Check for content/post indicators (social posts)
    if keys & {'post_info', 'engagement', 'num_likes', 'num_comments'}:
        return 'post'
    if keys & {'text', 'full_text', 'content', 'body', 'message'}:
        return 'post'

    # Check for link/article indicators
    if keys & {'url', 'link', 'href'} and keys & {'title', 'name'}:
        return 'link'

    # Default to generic if has identity fields
    if keys & {k.lower() for k in IDENTITY_FIELDS}:
        return 'entity'

    return 'unknown'


def _get_complete_http_url(obj: dict) -> Optional[str]:
    for field in URL_FIELD_PRIORITY:
        value = _get_nested(obj, field)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if re.match(r'^https?://[^\s]+$', value, re.IGNORECASE):
            return value
    for field, value in obj.items():
        if str(field).lower() in NON_ITEM_URL_FIELDS or not str(field).lower().endswith(('_url', '_link')):
            continue
        if isinstance(value, str) and re.match(r'^https?://[^\s]+$', value.strip(), re.IGNORECASE):
            return value.strip()
    return None


def _extract_item_fields(item: dict, item_type: str) -> dict:
    """Extract relevant fields from an item based on its type."""
    result = {'display': None, 'url': None}

    # Get identity (name/title)
    identity = _get_field(item, IDENTITY_FIELDS, MAX_FIELD_LEN)

    # Get role/description
    role = _get_field(item, ROLE_FIELDS, 40)

    # Get URL
    result['url'] = _get_complete_http_url(item)

    # Build display string based on type
    if item_type == 'job':
        # Jobs: Title @ Company (Location)
        title = _get_field(item, {'title', 'job_title'}, 35)
        company = _get_field(item, {'company', 'company_name', 'employer'}, 25)
        location = _get_field(item, LOCATION_FIELDS, 20)
        if title and company:
            display = f"{title} @ {company}"
            if location:
                display = f"{display} ({location})"
            result['display'] = display[:MAX_FIELD_LEN]
        elif title:
            result['display'] = title
    elif item_type in ('person', 'profile'):
        if identity and role:
            result['display'] = f"{identity}: {role}"
        elif identity:
            result['display'] = identity
    elif item_type == 'product':
        price = _get_field(item, {'price', 'cost', 'amount', 'final_price'}, 15)
        rating = _get_field(item, {'rating', 'average_rating', 'stars'}, 5)
        if identity and price:
            display = f"{identity}: {price}"
            if rating:
                display = f"{display} ⭐{rating}"
            result['display'] = display[:MAX_FIELD_LEN]
        elif identity:
            result['display'] = identity
    elif item_type == 'review':
        rating = _get_field(item, {'rating', 'score', 'stars'}, 5)
        text = _get_field(item, {'text', 'review_text', 'body', 'content'}, 40)
        author = _get_field(item, {'author', 'author_name', 'username'}, 15)
        if rating and text:
            result['display'] = f"⭐{rating}: {text}"
        elif author and text:
            result['display'] = f"{author}: {text}"
        elif text:
            result['display'] = text
    elif item_type == 'company':
        industry = _get_field(item, {'industry'}, 25)
        if identity and industry:
            result['display'] = f"{identity} ({industry})"
        elif identity:
            result['display'] = identity
    elif item_type == 'post':
        text = _get_field(item, TEXT_FIELDS, 50)
        author = _get_field(item, {'username', 'screen_name', 'author', 'user'}, 20)
        if author and text:
            result['display'] = f"@{author}: {text}"
        elif text:
            result['display'] = text
    else:
        # Generic: just identity or title
        if identity:
            result['display'] = identity
        elif role:
            result['display'] = role

    return result


def _emoji_for_type(item_type: str) -> str:
    """Return appropriate emoji for item type."""
    return {
        'person': '👥',
        'profile': '👤',
        'company': '🏢',
        'product': '🛒',
        'job': '💼',
        'review': '⭐',
        'post': '💬',
        'link': '🔗',
        'entity': '📋',
        'unknown': '📦',
    }.get(item_type, '📦')


# =============================================================================
# Single Object Hints
# =============================================================================

def _hint_from_object(obj: dict) -> Optional[str]:
    """
    Generate hint from a single object.

    Detects item type and formats accordingly:
    - Jobs: Title @ Company (Location)
    - Products: Name: Price ⭐Rating
    - Posts: @author: text excerpt
    - Generic: Identity — Role/Description
    """
    # Detect type first for type-specific formatting
    item_type = _detect_item_type(obj)
    lines = []

    if item_type == 'job':
        # Job: Title @ Company (Location)
        title = _get_field(obj, {'title', 'job_title'}, 40)
        company = _get_field(obj, {'company', 'company_name', 'employer'}, 30)
        location = _get_field(obj, LOCATION_FIELDS, 25)
        role = _get_field(obj, {'employment_type', 'seniority_level'}, 20)

        if title and company:
            header = f"💼 {title} @ {company}"
        elif title:
            header = f"💼 {title}"
        else:
            return None

        if role:
            header = f"{header} — {role}"
        lines.append(header[:MAX_LINE_LEN])

        if location:
            lines.append(f"📍 {location}")

    elif item_type == 'product':
        # Product: Name: Price ⭐Rating
        name = _get_field(obj, IDENTITY_FIELDS, 40)
        price = _get_field(obj, {'price', 'final_price', 'cost', 'amount'}, 15)
        rating = _get_field(obj, {'rating', 'average_rating', 'stars'}, 5)

        if name:
            header = f"🛒 {name}"
            if price:
                header = f"{header}: {price}"
            if rating:
                header = f"{header} ⭐{rating}"
            lines.append(header[:MAX_LINE_LEN])
        else:
            return None

    elif item_type == 'post':
        # Post: @author: text excerpt
        text = _get_field(obj, TEXT_FIELDS, 60)
        author = _get_field(obj, {'author', 'username', 'screen_name', 'user'}, 20)

        if text:
            if author:
                lines.append(f"💬 @{author}: {text}"[:MAX_LINE_LEN])
            else:
                lines.append(f"💬 {text}"[:MAX_LINE_LEN])
        else:
            return None

        # Add engagement if present
        metrics = _extract_metrics(obj)
        if metrics:
            lines.append(metrics)

    elif item_type == 'review':
        # Review: ⭐Rating: text
        rating = _get_field(obj, {'rating', 'score', 'stars'}, 5)
        text = _get_field(obj, {'text', 'review_text', 'body', 'content'}, 60)
        author = _get_field(obj, {'author', 'author_name', 'username'}, 20)

        if rating and text:
            lines.append(f"⭐ {rating}: {text}"[:MAX_LINE_LEN])
        elif text and author:
            lines.append(f"⭐ {author}: {text}"[:MAX_LINE_LEN])
        elif text:
            lines.append(f"⭐ {text}"[:MAX_LINE_LEN])
        else:
            return None

    else:
        # Generic: Identity — Role/Description
        identity = _get_field(obj, IDENTITY_FIELDS, 40)
        role = _get_field(obj, ROLE_FIELDS, 50)

        if identity and role:
            lines.append(f"📋 {identity} — {role}")
        elif identity:
            lines.append(f"📋 {identity}")
        elif role:
            lines.append(f"📋 {role}")
        else:
            return None

        # Line 2: Metrics (pick best ones)
        metrics = _extract_metrics(obj)
        if metrics:
            lines.append(metrics)

        # Line 3: Location
        location = _get_field(obj, LOCATION_FIELDS, 40)

        if location:
            lines.append(f"📍 {location}")

    # Make missing links explicit so nearby identifiers are not treated as URL parts.
    if item_type not in ('post', 'review'):
        url = _get_complete_http_url(obj)
        identity = _get_field(obj, IDENTITY_FIELDS, MAX_FIELD_LEN) or 'item'
        link_line = f"→ {identity} | item_link={url or 'none'}"
        if len('\n'.join([*lines, link_line]).encode('utf-8')) <= MAX_HINT_BYTES:
            lines.append(link_line)

    hint = '\n'.join(lines)
    return _enforce_limit(hint)


def _extract_metrics(obj: dict) -> Optional[str]:
    """Extract and format key metrics from an object."""
    metrics = []

    # Follower/subscriber counts
    for field in ('followers', 'followers_count', 'follower_count', 'subscribers', 'subscriber_count', 'connections_count'):
        value = _get_nested(obj, field)
        if value is not None:
            metrics.append(f"👥 {_format_count(value)}")
            break

    # Post/content counts
    for field in ('posts_count', 'media_count', 'video_count', 'videos'):
        value = _get_nested(obj, field)
        if value is not None:
            metrics.append(f"📝 {_format_count(value)}")
            break

    # Engagement (likes, etc.)
    for field in ('likes', 'heart_count', 'favorite_count'):
        value = _get_nested(obj, field)
        if value is not None:
            metrics.append(f"❤️ {_format_count(value)}")
            break

    # Funding
    for field in ('total_funding_usd', 'total_funding', 'funding'):
        value = _get_nested(obj, field)
        if value is not None:
            metrics.append(f"💰 ${_format_count(value)}")
            break

    # Company size
    for field in ('company_size', 'num_employees_enum', 'employee_count', 'size'):
        value = _get_nested(obj, field)
        if value is not None:
            metrics.append(f"📊 {value}")
            break

    if not metrics:
        return None

    return ' | '.join(metrics[:3])


# =============================================================================
# SERP Hints (Special Case)
# =============================================================================

# Domains that are noise in search results
_NOISE_DOMAINS = frozenset({
    'google.com', 'gstatic.com', 'googleapis.com', 'googleusercontent.com',
    'youtube.com', 'facebook.com', 'twitter.com', 'x.com',
})

_URL_PATTERNS = [
    re.compile(r'\[([^\]]{2,80})\]\((https?://[^)]+)\)'),
    re.compile(r'\[([^\]]{2,80})\]:\s*(https?://\S+)'),
]
_BARE_URL = re.compile(r'https?://[^\s\)\]"\'<>]{10,}')
_PRICE_PATTERN = re.compile(r'\$[\d,]+(?:\.\d{2})?|\d+(?:,\d{3})*(?:\.\d{2})?\s*(?:USD|EUR|GBP)')


def hint_from_serp(payload: dict, max_items: int = 5) -> Optional[str]:
    """Extract context hint from SERP (search engine results page)."""
    # Check for skeleton format first
    items = payload.get('items', [])
    if not items:
        items = _serp_items_from_results(payload.get('results', []), max_items)
    if items and isinstance(items, list):
        items = items[:max_items]
    else:
        # Raw markdown - extract URLs
        markdown = payload.get('result', '')
        if not markdown or not isinstance(markdown, str):
            return None
        items = _extract_serp_items(markdown, max_items)

    if not items:
        return None

    summaries = []
    linked_items = []

    for item in items:
        domain = item.get('d') or _domain_from_url(item.get('u', ''))
        title = str(item.get('t', ''))[:40]
        url = item.get('u', '')

        if domain and title:
            summaries.append(f"{domain}: {title}")
        elif domain:
            summaries.append(domain)

        if url:
            linked_items.append((title or domain, url))

    if not summaries:
        return None

    lines = [f"🔍 {' | '.join(summaries[:3])}"]
    for title, url in linked_items[:max_items]:
        line = f"→ {title}: {url}"
        if len('\n'.join([*lines, line]).encode('utf-8')) > MAX_HINT_BYTES:
            continue
        lines.append(line)

    return _enforce_limit('\n'.join(lines))


def _serp_items_from_results(results: Any, max_items: int) -> list[dict]:
    if not isinstance(results, list):
        return []
    items = []
    for index, item in enumerate(results[:max_items]):
        if not isinstance(item, dict):
            continue
        url = item.get('url') or item.get('link') or item.get('href') or ''
        title = item.get('title') or item.get('name') or ''
        if not isinstance(url, str) or not url.startswith('http'):
            continue
        items.append({'t': str(title)[:60], 'u': url, 'p': index + 1})
    return items


def _extract_serp_items(text: str, max_items: int) -> list[dict]:
    """Extract search result items from markdown."""
    items = []
    seen_domains = set()

    for pattern in _URL_PATTERNS:
        for match in pattern.finditer(text):
            title, url = match.groups()
            domain = _domain_from_url(url)

            if not _is_useful_url(url) or domain in seen_domains:
                continue

            title = title.strip()
            if len(title) < 3 or title.lower() in ('read more', 'click here', 'learn more'):
                title = _title_from_url(url)

            seen_domains.add(domain)
            items.append({'t': title[:60], 'u': url, 'd': domain})

            if len(items) >= max_items:
                return items

    # Fallback: bare URLs
    for match in _BARE_URL.finditer(text):
        url = match.group(0).rstrip('.,;:')
        domain = _domain_from_url(url)

        if not _is_useful_url(url) or domain in seen_domains:
            continue

        seen_domains.add(domain)
        items.append({'t': _title_from_url(url), 'u': url, 'd': domain})

        if len(items) >= max_items:
            break

    return items


def _domain_from_url(url: str) -> str:
    """Extract clean domain from URL."""
    clean = re.sub(r'^https?://(www\.)?', '', url)
    return clean.split('/')[0].split('?')[0].lower()


def _title_from_url(url: str) -> str:
    """Derive readable title from URL path."""
    match = re.search(r'https?://[^/]+/(.+)', url)
    if not match:
        return _domain_from_url(url)

    path = match.group(1).split('?')[0].split('#')[0]
    segments = [s for s in path.split('/') if s and len(s) > 2]
    if not segments:
        return _domain_from_url(url)

    title = segments[-1]
    title = re.sub(r'[-_]', ' ', title)
    title = re.sub(r'\.\w{2,4}$', '', title)
    return title[:50].strip()


def _is_useful_url(url: str) -> bool:
    """Check if URL is worth including."""
    domain = _domain_from_url(url)
    return len(url) >= 20 and not any(noise in domain for noise in _NOISE_DOMAINS)


# =============================================================================
# Scraped Page Hints (Special Case)
# =============================================================================

def hint_from_scraped_page(payload: dict, *, allow_barbell: bool = False) -> Optional[str]:
    """Extract context hint from scraped page."""
    title = payload.get('title', '')
    items = payload.get('items', [])
    excerpt = payload.get('excerpt', '')
    markdown = payload.get('result', '')
    url = payload.get('url', '')

    if not title and not items and not excerpt:
        if not markdown:
            return None

        # Extract title
        title_match = re.search(r'^#\s+(.+)$', markdown, re.MULTILINE)
        if title_match:
            title = title_match.group(1)[:80]

        # Look for prices
        prices = _PRICE_PATTERN.findall(markdown[:5000])
        if prices:
            unique_prices = list(dict.fromkeys(prices))[:3]
            if title:
                parts = [f"📄 {title}"]
                if isinstance(url, str) and url.startswith('http'):
                    parts.append(f"→ {url}")
                parts.append(f"💰 {', '.join(unique_prices)}")
                return _enforce_limit("\n".join(parts))
            return _enforce_limit(f"💰 Prices: {', '.join(unique_prices)}")

        if title:
            parts = [f"📄 {title}"]
            if isinstance(url, str) and url.startswith('http'):
                parts.append(f"→ {url}")
            return _enforce_limit("\n".join(parts))
        if allow_barbell:
            return hint_from_unstructured_text(markdown)
        return None

    parts = []
    if title:
        parts.append(f"📄 {title[:80]}")
    if isinstance(url, str) and url.startswith('http'):
        parts.append(f"→ {url}")

    if excerpt:
        prices = _PRICE_PATTERN.findall(excerpt)
        if prices:
            parts.append(f"💰 {', '.join(list(dict.fromkeys(prices))[:3])}")

    if items and isinstance(items, list):
        headings = [item.get('h', '')[:40] for item in items[:3] if item.get('h')]
        if headings:
            parts.append(f"§ {' | '.join(headings)}")

    if not parts:
        return None

    if allow_barbell and markdown:
        base = '\n'.join(parts)
        available = BARBELL_TARGET_BYTES - len(base.encode('utf-8')) - 1
        if available > 0:
            focus = _build_unstructured_focus_hint(markdown, max_bytes=available)
            if focus:
                combined = f"{base}\n{focus}"
                return _enforce_limit_bytes(combined, BARBELL_TARGET_BYTES)

    return _enforce_limit('\n'.join(parts))


def _build_unstructured_focus_hint(
    text: str,
    *,
    max_bytes: int,
) -> Optional[str]:
    if not text:
        return None

    digest = None
    try:
        digest = digest_text(text)
    except Exception:
        digest = None

    if digest and digest.action == "skip":
        return None

    header_parts = []
    if digest:
        header_parts.append(f"DIGEST: {digest.summary_line()}")
    header_parts.append("FOCUS:")
    header = "\n".join(header_parts)
    header_bytes = len(header.encode("utf-8")) + 1
    if max_bytes <= header_bytes:
        return None

    focused = barbell_focus(text, target_bytes=max_bytes - header_bytes)
    if not focused:
        return None
    hint = f"{header}\n{focused}"
    return _enforce_limit_bytes(hint, max_bytes)


def hint_from_unstructured_text(
    text: str,
    *,
    max_bytes: int = BARBELL_TARGET_BYTES,
) -> Optional[str]:
    """Generate a focus hint for messy, unstructured text."""
    return _build_unstructured_focus_hint(text, max_bytes=max_bytes)


def hint_from_messy_json(
    payload: Any,
    *,
    max_bytes: int = GOLDILOCKS_MAX_BYTES,
) -> Optional[str]:
    """Generate a focused JSON summary for messy, unstructured payloads."""
    try:
        summary = goldilocks_summary(payload, max_bytes=max_bytes)
    except Exception:
        return None
    if not summary:
        return None
    hint = f"{GOLDILOCKS_HINT_PREFIX}\n{summary}"
    return _enforce_limit_bytes(hint, max_bytes)


def _should_use_goldilocks(payload_bytes: Optional[int]) -> bool:
    if payload_bytes is None:
        return False
    return payload_bytes >= GOLDILOCKS_MIN_BYTES


# =============================================================================
# Helpers
# =============================================================================

def _get_field(obj: dict, field_names: set, max_len: int) -> Optional[str]:
    """Get first matching field from object, case-insensitive.

    Tries fields in a priority order for common field sets.
    """
    # Priority order for identity fields (name before title, etc.)
    priority_order = [
        'name', 'full_name', 'display_name', 'username', 'screen_name',
        'nickname', 'unique_id', 'handle', 'channel_name', 'company_name',
        'organization_name', 'product_name', 'title',
    ]

    # Try priority order first for identity fields
    for field in priority_order:
        if field in field_names and field in obj:
            value = obj[field]
            if value and isinstance(value, str):
                return value[:max_len]

    # Try exact match for other fields
    for field in field_names:
        if field in obj:
            value = obj[field]
            if value and isinstance(value, str):
                return value[:max_len]

    # Try case-insensitive
    obj_lower = {k.lower(): v for k, v in obj.items()}
    for field in priority_order:
        if field in field_names and field.lower() in obj_lower:
            value = obj_lower[field.lower()]
            if value and isinstance(value, str):
                return value[:max_len]

    for field in field_names:
        if field.lower() in obj_lower:
            value = obj_lower[field.lower()]
            if value and isinstance(value, str):
                return value[:max_len]

    return None


def _get_nested(obj: dict, field: str) -> Any:
    """Get field value, checking common nested locations."""
    # Direct
    if field in obj:
        return obj[field]

    # Case-insensitive
    for k, v in obj.items():
        if k.lower() == field.lower():
            return v

    return None


def _format_count(value) -> str:
    """Format large numbers compactly: 1500000 → 1.5M"""
    if isinstance(value, str):
        value = value.replace(',', '').replace('+', '')
        try:
            value = int(float(value))
        except ValueError:
            return value[:10]

    if not isinstance(value, (int, float)):
        return str(value)[:10]

    if value >= 1_000_000_000:
        result = value / 1_000_000_000
        return f"{int(result)}B" if result == int(result) else f"{result:.1f}B"
    if value >= 1_000_000:
        result = value / 1_000_000
        return f"{int(result)}M" if result == int(result) else f"{result:.1f}M"
    if value >= 1_000:
        result = value / 1_000
        return f"{int(result)}K" if result == int(result) else f"{result:.1f}K"
    return str(int(value))


def _enforce_limit(hint: str) -> str:
    """Enforce hard byte limit on hint."""
    if len(hint.encode('utf-8')) <= MAX_HINT_BYTES:
        return hint

    # Truncate line by line
    lines = hint.split('\n')
    while len('\n'.join(lines).encode('utf-8')) > MAX_HINT_BYTES and len(lines) > 1:
        lines.pop()

    result = '\n'.join(lines)

    # Final truncation if still over
    while len(result.encode('utf-8')) > MAX_HINT_BYTES:
        result = result[:-10] + "..."

    return result


def _enforce_limit_bytes(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
