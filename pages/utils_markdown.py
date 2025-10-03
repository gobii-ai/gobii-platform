from functools import lru_cache
from pathlib import Path
from datetime import datetime, date
from typing import Optional

import frontmatter
import markdown
from django.conf import settings
from django.utils import timezone

CONTENT_ROOT = Path(settings.BASE_DIR, "pages", "content")

md_converter = markdown.Markdown(
    extensions=["extra", "toc", "codehilite"],
    extension_configs={"codehilite": {"guess_lang": False}},
    output_format="html5",
)

def _resolve_markdown_file(slug_or_path_part: str, root: Path = CONTENT_ROOT) -> Path:
    """
    Resolve a slug or partial path to a markdown file within the specified content root.
    
    Args:
        slug_or_path_part: The slug or part of the path (e.g., 'guides/introduction' or Path('guides/introduction.md'))
        
    Returns:
        Path object to the markdown file
        
    Raises:
        FileNotFoundError: If the file doesn't exist or path traversal is attempted
    """
    # Ensure no leading/trailing slashes for slugs and remove .md if present
    slug_str = str(slug_or_path_part).strip('/').replace(".md", "")
    
    # Construct the full path
    # If slug_or_path_part was already a Path object from glob, it might have .md
    # If it's a slug string, it won't. with_suffix ensures .md is present.
    path = (root / slug_str).with_suffix(".md")

    # Security check: ensure the resolved path is within root
    resolved_path = path.resolve()
    if not path.is_file() or (root != resolved_path and root not in resolved_path.parents):
        # Try finding file if it's a directory with an index.md or readme.md
        index_path = (root / slug_str / "index.md")
        readme_path = (root / slug_str / "readme.md")
        if index_path.is_file() and root in index_path.resolve().parents:
            path = index_path
        elif readme_path.is_file() and root in readme_path.resolve().parents:
            path = readme_path
        else:
            raise FileNotFoundError(f"File not found or path traversal detected: {slug_or_path_part}")
    return path

def _extract_slug_from_path(path: Path, root: Path = CONTENT_ROOT) -> str:
    """Extracts slug from a file path relative to the provided root."""
    relative = path.relative_to(root)
    return str(relative).replace(".md", "").replace("index", "").strip('/')

@lru_cache(maxsize=100)
def load_page(slug: str):
    """
    Load and parse a markdown page.
    
    Args:
        slug: The slug of the page (e.g., 'guides/introduction')
        
    Returns:
        Dict containing the parsed page with keys:
        - slug: The original slug
        - meta: Metadata from frontmatter (title, prev, next, etc.)
        - html: HTML content converted from markdown
        - toc_html: HTML for the table of contents
    """
    try:
        file_path = _resolve_markdown_file(slug, root=CONTENT_ROOT)
        post = frontmatter.load(file_path)
        html = md_converter.reset().convert(post.content)
        
        # Ensure 'title' is in meta, default to slug if not
        meta = post.metadata
        if 'title' not in meta:
            meta['title'] = slug.replace('-', ' ').replace('_', ' ').capitalize()

        return {
            "slug": _extract_slug_from_path(file_path, root=CONTENT_ROOT),
            "meta": meta,
            "html": html,
            "toc_html": md_converter.toc,
        }
    except FileNotFoundError:
        # Propagate FileNotFoundError to be caught by the view
        raise

def get_all_doc_pages():
    """
    Scans the CONTENT_ROOT directory for all markdown files,
    extracts their frontmatter, and returns a sorted list of page objects.
    Each page object contains 'title', 'url', and 'icon'.
    """
    pages = []
    for path in CONTENT_ROOT.rglob("*.md"):
        if path.is_file() and CONTENT_ROOT in path.resolve().parents:
            try:
                relative_parts = path.relative_to(CONTENT_ROOT).parts
                if relative_parts and relative_parts[0] == "blogs":
                    continue
            except ValueError:
                continue
            try:
                post = frontmatter.load(path)
                slug = _extract_slug_from_path(path, root=CONTENT_ROOT)
                
                # Skip if slug is empty (e.g. an index.md at the root of content)
                if not slug and path.name.lower() in ["index.md", "readme.md"]: # Allow root index/readme if slug becomes empty
                    # For a root index.md or readme.md, the slug should be effectively empty, leading to /docs/
                    # However, we usually want a specific page like /docs/introduction/ as the index.
                    # This logic might need refinement based on how a true "root" doc page is handled.
                    # For now, we'll use the parent directory name if available, or filename.
                    if path.parent != CONTENT_ROOT:
                         slug = path.parent.name 
                    else:
                         slug = path.stem # e.g. 'introduction' from 'introduction.md'
                    if slug.lower() in ["index", "readme"]: # if slug became index or readme, try parent dir
                        if path.parent != CONTENT_ROOT:
                            slug = path.parent.name
                        else: # if still at root, might be an issue.
                            # This case should ideally be handled by a redirect or a specific 'home' doc.
                            # print(f"Warning: Root file {path.name} might not generate a desirable slug.")
                            pass # Let it be, url will be /docs/slug/
                
                if not slug.strip(): # if slug is genuinely empty or whitespace after processing.
                    # This can happen if a file is like `content/.md` or `content/index.md` becomes `""`
                    # Try to use filename without extension as a fallback if slug is empty
                    slug = path.stem
                    if slug.lower() == 'index' and path.parent != CONTENT_ROOT: # e.g. guides/index.md -> guides
                        slug = path.parent.name

                title = post.metadata.get("title", slug.replace('-', ' ').replace('_', ' ').capitalize())
                icon = post.metadata.get("icon") # Get icon if specified

                # ------------------------------------------------------------------
                #  Hierarchy helpers – determine depth relative to 'guides/' root.
                #  E.g.  guides/quickstart                -> depth_level 0  (root)
                #        guides/quickstart/ts              -> depth_level 1
                #        guides/quickstart/ts/advanced     -> depth_level 2
                #  We expose both an integer and a Tailwind margin-left class so the
                #  template can indent items generically without extra logic.
                # ------------------------------------------------------------------
                depth_level = 0
                if slug.startswith("guides/"):
                    # Subtract 1 for the root 'guides' segment and 1 so that
                    # folders directly under guides/ start at level 0.
                    depth_level = max(0, len(slug.split("/")) - 2)

                # Tailwind's spacing scale supports multiples of 4 (ml-4, ml-8 …).
                ml_class = f"ml-{depth_level * 4}" if depth_level > 0 else ""

                # Smaller font for nested items so long names fit nicely.
                text_class = "text-xs" if depth_level > 0 else "text-sm"

                order = post.metadata.get("order", float('inf')) # Get order, default to infinity for unordered pages
                
                # Ensure URL has trailing slash
                url = f"/docs/{slug.strip('/')}/" if slug.strip('/') else "/docs/"

                pages.append({
                    "title": title,
                    "url": url,
                    "icon": icon,
                    "slug": slug,
                    "order": order,
                    "depth_level": depth_level,
                    "ml_class": ml_class,
                    "text_class": text_class,
                })
            except Exception as e:
                # Log or handle errors for individual file processing if needed
                print(f"Error processing {path}: {e}") 
                continue
    
    # Sort pages: first by 'order', then by 'title' for those with the same order or no order
    pages.sort(key=lambda p: (p["order"], p["title"]))
    return pages

def get_prev_next(slug: str):
    """
    Get the previous and next pages based on the global ordering of all pages.
    This ensures prev/next navigation matches the sidebar navigation order.
    
    Args:
        slug: The slug of the current page
        
    Returns:
        Dict with prev_page and next_page information
    """
    # Get all pages in the same sorted order as the navigation
    all_pages = get_all_doc_pages()
    
    # Filter to only include guide pages (matching what's shown in the nav)
    guide_pages = [p for p in all_pages if p["slug"].startswith("guides/")]
    
    # Find the current page index
    current_index = None
    for i, page in enumerate(guide_pages):
        if page["slug"] == slug:
            current_index = i
            break
    
    result = {}
    
    # If we found the current page, determine prev/next
    if current_index is not None:
        # Previous page
        if current_index > 0:
            prev_page = guide_pages[current_index - 1]
            result["prev_page"] = {
                "title": prev_page["title"],
                "url": prev_page["url"],
            }
        
        # Next page
        if current_index < len(guide_pages) - 1:
            next_page = guide_pages[current_index + 1]
            result["next_page"] = {
                "title": next_page["title"],
                "url": next_page["url"],
            }
    
    # Fallback: if manual prev/next are specified in frontmatter, honor them
    # This allows for manual override when needed
    page = load_page(slug)
    
    # Only use manual prev if we didn't find an automatic one
    if "prev_page" not in result and "prev" in page["meta"] and page["meta"]["prev"]:
        prev_slug = page["meta"]["prev"]
        try:
            prev_page_data = load_page(prev_slug)
            result["prev_page"] = {
                "title": prev_page_data["meta"].get("title", prev_slug),
                "url": f"/docs/{prev_slug}/",
            }
        except FileNotFoundError:
            pass
    
    # Only use manual next if we didn't find an automatic one
    if "next_page" not in result and "next" in page["meta"] and page["meta"]["next"]:
        next_slug = page["meta"]["next"]
        try:
            next_page_data = load_page(next_slug)
            result["next_page"] = {
                "title": next_page_data["meta"].get("title", next_slug),
                "url": f"/docs/{next_slug}/",
            }
        except FileNotFoundError:
            pass

    return result


def _parse_datetime(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    if isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
    return None
