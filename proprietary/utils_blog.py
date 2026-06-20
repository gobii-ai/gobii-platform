from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup
import frontmatter
from PIL import Image, UnidentifiedImageError
from django.utils.html import strip_tags

from config import settings
from pages.utils_markdown import _extract_slug_from_path, md_converter, _resolve_markdown_file, _parse_datetime

BLOGS_ROOT = Path(settings.BASE_DIR, "proprietary", "content") / "blogs"
STATIC_ROOT = Path(settings.BASE_DIR, "static")
WORD_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\u2019-]*")


def _word_count(text: str) -> int:
    return len(WORD_PATTERN.findall(text or ""))


def _local_static_path(src: str) -> Path | None:
    parsed = urlparse(src or "")
    if parsed.scheme or parsed.netloc:
        return None

    static_url = settings.STATIC_URL
    if not parsed.path.startswith(static_url):
        return None

    relative_path = parsed.path.removeprefix(static_url).lstrip("/")
    candidate = (STATIC_ROOT / relative_path).resolve()
    static_root = STATIC_ROOT.resolve()
    try:
        candidate.relative_to(static_root)
    except ValueError:
        return None
    return candidate


def _parse_svg_length(value: str | None) -> int | None:
    if not value:
        return None
    match = re.match(r"^\s*(\d+(?:\.\d+)?)", value)
    if not match:
        return None
    return round(float(match.group(1)))


def _svg_dimensions(path: Path) -> tuple[int, int] | None:
    root = ElementTree.parse(path).getroot()
    width = _parse_svg_length(root.attrib.get("width"))
    height = _parse_svg_length(root.attrib.get("height"))
    if width and height:
        return width, height

    view_box = root.attrib.get("viewBox")
    if not view_box:
        return None
    values = [float(part) for part in re.split(r"[\s,]+", view_box.strip()) if part]
    if len(values) != 4:
        return None
    return round(values[2]), round(values[3])


@lru_cache(maxsize=512)
def _image_dimensions(src: str) -> tuple[int, int] | None:
    path = _local_static_path(src)
    if path is None or not path.is_file():
        return None

    try:
        if path.suffix.lower() == ".svg":
            return _svg_dimensions(path)
        with Image.open(path) as image:
            return image.width, image.height
    except (ElementTree.ParseError, OSError, UnidentifiedImageError, ValueError):
        return None


def _first_image_alt(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    first_image = soup.find("img")
    if not first_image:
        return None
    alt = first_image.get("alt")
    return alt.strip() if alt else None


def _enrich_blog_html(html: str, fallback_alt: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    for index, image in enumerate(images):
        src = image.get("src")
        if src:
            dimensions = _image_dimensions(src)
            if dimensions:
                width, height = dimensions
                if not image.get("width"):
                    image["width"] = str(width)
                if not image.get("height"):
                    image["height"] = str(height)

        if not image.has_attr("alt") and fallback_alt:
            image["alt"] = fallback_alt
        if not image.get("decoding"):
            image["decoding"] = "async"

        if index == 0:
            if not image.get("loading"):
                image["loading"] = "eager"
            if not image.get("fetchpriority"):
                image["fetchpriority"] = "high"
        elif not image.get("loading"):
            image["loading"] = "lazy"

    return str(soup)


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
    title = meta["title"]

    html = _enrich_blog_html(html, fallback_alt=title)

    summary = meta.get("description") or meta.get("summary") or meta.get("excerpt")
    if not summary:
        text_content = strip_tags(html).strip()
        if text_content:
            first_line = text_content.splitlines()[0]
            summary = first_line[:197] + "…" if len(first_line) > 200 else first_line

    published_at = _parse_datetime(meta.get("date") or meta.get("published") or meta.get("published_at"))
    updated_at = _parse_datetime(meta.get("updated") or meta.get("modified") or meta.get("updated_at")) or published_at
    text_content = strip_tags(html).strip()

    return {
        "slug": _extract_slug_from_path(file_path, root=BLOGS_ROOT),
        "meta": meta,
        "html": html,
        "toc_html": md_converter.toc,
        "summary": summary,
        "published_at": published_at,
        "updated_at": updated_at,
        "word_count": _word_count(text_content),
        "image_alt": meta.get("image_alt") or _first_image_alt(html) or f"{title} image",
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
            "updated_at": post.get("updated_at"),
            "meta": post["meta"],
            "url": f"/blog/{post['slug'].strip('/')}/",
        })

    def sort_key(post):
        published = post.get("published_at")
        timestamp = -published.timestamp() if published else 0
        return (published is None, timestamp, post["title"].lower())

    posts.sort(key=sort_key)
    return posts
