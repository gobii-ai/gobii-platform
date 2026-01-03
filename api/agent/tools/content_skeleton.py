"""
Content Skeleton - Universal structure for external data.

Inspired by demoscene: every byte earns its place.
Inspired by Carmack: one elegant structure for everything.

The insight: ALL external content can be normalized to:
    {kind, title, items[], excerpt}

Agent learns ONE query pattern:
    SELECT json_extract(value, '$.field') FROM json_each(result_json, '$.items')

That's it. That's the whole system.
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
import json


@dataclass
class ContentSkeleton:
    """Universal structure for any external content.

    Fields use short keys to minimize bytes:
        kind: content type for query hints
        title: page/query title
        items: THE insight - everything becomes items
        excerpt: raw fallback (1-2KB max)
    """
    kind: str                    # 'serp', 'article', 'product', 'list', 'raw'
    title: str = ""              # Page or query title
    items: List[Dict] = field(default_factory=list)  # Universal: everything is items
    excerpt: str = ""            # Raw fallback, truncated

    def to_json(self) -> str:
        """Compact JSON representation."""
        d = asdict(self)
        # Remove empty fields
        return json.dumps({k: v for k, v in d.items() if v},
                         ensure_ascii=False, separators=(',', ':'))

    def byte_size(self) -> int:
        return len(self.to_json().encode('utf-8'))


# ---------------------------------------------------------------------------
# SERP Extraction - Search results → items with {t, u, p, s}
# ---------------------------------------------------------------------------

_SERP_LINK_RE = re.compile(r"\[([^\]]{2,})\]\((https?://[^)]+)\)")
_GOOGLE_INTERNAL = ('google.com', 'gstatic.com', 'googleapis.com')
_USELESS_TITLES = {'read more', 'click here', 'learn more', 'see more', 'view', 'link'}


def _title_from_url(url: str) -> str:
    """Extract readable title from URL when link text is useless."""
    # Remove protocol and www
    clean = re.sub(r'^https?://(www\.)?', '', url)
    # Get domain + first path segment
    parts = clean.split('/')
    domain = parts[0]
    path = parts[1] if len(parts) > 1 else ''
    # Clean path segment
    path = re.sub(r'[#?].*', '', path)  # Remove fragments/query
    path = re.sub(r'[-_]', ' ', path)   # Dashes to spaces
    path = path.strip()
    if path and len(path) > 2:
        return f"{domain}: {path[:50]}"
    return domain


def extract_serp_skeleton(markdown: str, query: str = "") -> ContentSkeleton:
    """Extract search results into compact skeleton.

    Items have: t=title, u=url, p=position
    Uses URL-derived title when link text is useless (e.g., "Read more")
    """
    items = []
    seen_urls = set()

    for match in _SERP_LINK_RE.finditer(markdown):
        raw_title, url = match.groups()

        # Skip internal/duplicate
        if any(domain in url for domain in _GOOGLE_INTERNAL):
            continue
        # Normalize URL for dedup (strip fragment)
        base_url = url.split('#')[0].split('?')[0]
        if base_url in seen_urls:
            continue

        # Smart title: use link text or derive from URL
        title = raw_title.strip()
        if title.lower() in _USELESS_TITLES or len(title) < 4:
            title = _title_from_url(url)

        seen_urls.add(base_url)
        items.append({
            't': title[:100],
            'u': url[:300],
            'p': len(items) + 1,
        })

        if len(items) >= 12:
            break

    return ContentSkeleton(
        kind='serp',
        title=query[:100] if query else 'search',
        items=items,
        excerpt=''  # SERP doesn't need excerpt - items ARE the content
    )


# ---------------------------------------------------------------------------
# Article Extraction - Pages → items with {h, c} (heading, content)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r'^(#{1,3})\s+(.+)$', re.MULTILINE)


def extract_article_skeleton(markdown: str, title: str = "") -> ContentSkeleton:
    """Extract article structure into compact skeleton.

    Items have: h=heading, c=content preview, l=level
    """
    items = []

    # Find all headings with positions
    headings = [(m.start(), len(m.group(1)), m.group(2).strip())
                for m in _HEADING_RE.finditer(markdown)]

    if not headings:
        # No structure, just excerpt
        return ContentSkeleton(
            kind='raw',
            title=title[:100],
            items=[],
            excerpt=_clean_excerpt(markdown, 1500)
        )

    # Extract content under each heading
    for i, (pos, level, heading) in enumerate(headings[:10]):
        # Content goes until next heading or end
        end_pos = headings[i + 1][0] if i + 1 < len(headings) else len(markdown)
        content = markdown[pos:end_pos]

        # Skip the heading line itself, get content preview
        lines = content.split('\n')[1:]
        content_preview = ' '.join(lines)[:200].strip()

        if content_preview:
            items.append({
                'h': heading[:80],
                'c': content_preview,
                'l': level
            })

    return ContentSkeleton(
        kind='article',
        title=title[:100] or (headings[0][2] if headings else ''),
        items=items,
        excerpt=_clean_excerpt(markdown, 800)
    )


# ---------------------------------------------------------------------------
# Generic/Fallback Extraction
# ---------------------------------------------------------------------------

def _clean_excerpt(text: str, max_chars: int) -> str:
    """Clean and truncate text for excerpt."""
    # Remove excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)

    if len(text) <= max_chars:
        return text

    # Try to break at sentence
    truncated = text[:max_chars]
    last_period = truncated.rfind('. ')
    if last_period > max_chars * 0.7:
        return truncated[:last_period + 1]

    return truncated + '...'


def extract_skeleton(content: str, content_type: str = "", title: str = "") -> ContentSkeleton:
    """Universal extraction - detects type and extracts appropriate skeleton.

    Args:
        content: Raw content (markdown, JSON, etc.)
        content_type: Hint for content type ('serp', 'article', etc.)
        title: Optional title/query

    Returns:
        ContentSkeleton with compact, queryable structure
    """
    # Try to detect type from content
    lower = content[:2000].lower()

    if content_type == 'serp' or 'google search' in lower or 'search results' in lower:
        return extract_serp_skeleton(content, title)

    if '# ' in content:  # Has markdown headings
        return extract_article_skeleton(content, title)

    # Fallback: raw excerpt
    return ContentSkeleton(
        kind='raw',
        title=title[:100],
        items=[],
        excerpt=_clean_excerpt(content, 2000)
    )


# ---------------------------------------------------------------------------
# Query Pattern Generation - The elegant part
# ---------------------------------------------------------------------------

QUERY_PATTERNS = {
    'serp': {
        'list': "SELECT json_extract(value,'$.t') as title, json_extract(value,'$.u') as url FROM json_each(result_json,'$.items') LIMIT 12",
        'find': "SELECT json_extract(value,'$.u') FROM json_each(result_json,'$.items') WHERE json_extract(value,'$.t') LIKE '%{keyword}%' LIMIT 5",
    },
    'article': {
        'list': "SELECT json_extract(value,'$.h') as heading, json_extract(value,'$.c') as content FROM json_each(result_json,'$.items') LIMIT 10",
        'find': "SELECT json_extract(value,'$.c') FROM json_each(result_json,'$.items') WHERE json_extract(value,'$.h') LIKE '%{keyword}%' LIMIT 3",
    },
    'raw': {
        'get': "SELECT json_extract(result_json,'$.excerpt') FROM __tool_results WHERE result_id='{id}'",
    }
}


def get_query_hint(skeleton: ContentSkeleton, result_id: str) -> str:
    """Generate compact query hint for this skeleton."""
    patterns = QUERY_PATTERNS.get(skeleton.kind, QUERY_PATTERNS['raw'])

    if skeleton.kind == 'serp':
        return (
            f"SERP: {len(skeleton.items)} results\n"
            f"→ {patterns['list']}"
        )
    elif skeleton.kind == 'article':
        return (
            f"ARTICLE: {len(skeleton.items)} sections\n"
            f"→ {patterns['list']}"
        )
    else:
        return f"RAW: {len(skeleton.excerpt)} chars in $.excerpt"
