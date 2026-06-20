from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup
import frontmatter
from PIL import Image, UnidentifiedImageError
from django.utils import timezone
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
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*", value)
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


def _extract_faq_items(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    faq_heading = None
    for heading in soup.find_all(["h2", "h3"]):
        heading_text = heading.get_text(" ", strip=True).lower()
        if heading_text in {"faq", "faqs", "frequently asked questions"}:
            faq_heading = heading
            break

    if faq_heading is None:
        return []

    items = []
    current_question = None
    current_answer_parts = []

    def flush_current_item():
        if current_question and current_answer_parts:
            answer = " ".join(part for part in current_answer_parts if part).strip()
            if answer:
                items.append({"question": current_question, "answer": answer})

    for sibling in faq_heading.find_next_siblings():
        if sibling.name in {"h1", "h2"}:
            break

        if sibling.name in {"h3", "h4"}:
            flush_current_item()
            question = sibling.get_text(" ", strip=True)
            current_question = question if question.endswith("?") else None
            current_answer_parts = []
            continue

        if current_question:
            answer_part = sibling.get_text(" ", strip=True)
            if answer_part:
                current_answer_parts.append(answer_part)

    flush_current_item()
    return items


def _uses_lightbox(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return bool(soup.select("[data-lightbox]"))


def is_blog_post_published(post: dict, *, now=None) -> bool:
    published_at = post.get("published_at")
    if published_at is None:
        return True

    current_time = now or timezone.now()
    if timezone.is_naive(published_at):
        published_at = timezone.make_aware(published_at)
    if timezone.is_naive(current_time):
        current_time = timezone.make_aware(current_time)

    return published_at <= current_time


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
        "faq_items": _extract_faq_items(html),
        "uses_lightbox": _uses_lightbox(html),
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
        if not is_blog_post_published(post):
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
