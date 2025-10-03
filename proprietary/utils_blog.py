from functools import lru_cache
from pathlib import Path

import frontmatter
from django.utils.html import strip_tags

from config import settings
from pages.utils_markdown import _extract_slug_from_path, md_converter, _resolve_markdown_file, _parse_datetime

BLOGS_ROOT = Path(settings.BASE_DIR, "proprietary", "content") / "blogs"


@lru_cache(maxsize=100)
def load_blog_post(slug: str):
    slug = slug.strip("/")
    if not BLOGS_ROOT.exists():
        raise FileNotFoundError("Blog content directory is missing.")

    file_path = _resolve_markdown_file(slug, root=BLOGS_ROOT)
    post = frontmatter.load(file_path)
    html = md_converter.reset().convert(post.content)

    meta = post.metadata
    if 'title' not in meta:
        meta['title'] = slug.replace('-', ' ').replace('_', ' ').capitalize()

    summary = meta.get("description") or meta.get("summary") or meta.get("excerpt")
    if not summary:
        text_content = strip_tags(html).strip()
        if text_content:
            first_line = text_content.splitlines()[0]
            summary = first_line[:197] + "…" if len(first_line) > 200 else first_line

    published_at = _parse_datetime(meta.get("date") or meta.get("published") or meta.get("published_at"))

    return {
        "slug": _extract_slug_from_path(file_path, root=BLOGS_ROOT),
        "meta": meta,
        "html": html,
        "toc_html": md_converter.toc,
        "summary": summary,
        "published_at": published_at,
    }

@lru_cache(maxsize=1)
def get_all_blog_posts():
    posts = []
    if not BLOGS_ROOT.exists():
        return posts

    for path in BLOGS_ROOT.rglob("*.md"):
        if not path.is_file() or BLOGS_ROOT not in path.resolve().parents:
            continue

        slug = _extract_slug_from_path(path, root=BLOGS_ROOT)
        try:
            post = load_blog_post(slug)
        except FileNotFoundError:
            continue

        title = post["meta"].get("title", slug.replace('-', ' ').replace('_', ' ').capitalize())
        posts.append({
            "slug": post["slug"],
            "title": title,
            "summary": post.get("summary"),
            "published_at": post.get("published_at"),
            "meta": post["meta"],
            "url": f"/blog/{post['slug'].strip('/')}/",
        })

    def sort_key(post):
        published = post.get("published_at")
        timestamp = -published.timestamp() if published else 0
        return (published is None, timestamp, post["title"].lower())

    posts.sort(key=sort_key)
    return posts