"""Tests for ContentSkeleton - universal content structure."""

from django.test import SimpleTestCase, tag

from api.agent.tools.content_skeleton import (
    ContentSkeleton,
    extract_serp_skeleton,
    extract_article_skeleton,
    extract_skeleton,
    get_query_hint,
    _title_from_url,
)


@tag("content_skeleton")
class ContentSkeletonBasicTests(SimpleTestCase):
    """Tests for ContentSkeleton dataclass."""

    def test_to_json_compact(self):
        skeleton = ContentSkeleton(
            kind="serp",
            title="python tutorials",
            items=[{"t": "Learn Python", "u": "https://example.com", "p": 1}],
        )
        json_str = skeleton.to_json()

        # Compact: no spaces after separators
        self.assertNotIn(": ", json_str)
        self.assertNotIn(", ", json_str)
        # Contains expected data
        self.assertIn('"kind":"serp"', json_str)
        self.assertIn('"t":"Learn Python"', json_str)

    def test_to_json_omits_empty_fields(self):
        skeleton = ContentSkeleton(kind="raw", title="test")
        json_str = skeleton.to_json()

        # Empty items and excerpt should be omitted
        self.assertNotIn("items", json_str)
        self.assertNotIn("excerpt", json_str)
        self.assertIn("kind", json_str)
        self.assertIn("title", json_str)

    def test_byte_size(self):
        skeleton = ContentSkeleton(
            kind="serp",
            title="test",
            items=[{"t": "Result", "u": "https://example.com", "p": 1}],
        )
        size = skeleton.byte_size()

        self.assertGreater(size, 0)
        self.assertEqual(size, len(skeleton.to_json().encode("utf-8")))


@tag("content_skeleton")
class TitleFromUrlTests(SimpleTestCase):
    """Tests for URL-derived title fallback."""

    def test_simple_domain(self):
        title = _title_from_url("https://example.com")
        self.assertEqual(title, "example.com")

    def test_domain_with_www(self):
        title = _title_from_url("https://www.example.com")
        self.assertEqual(title, "example.com")

    def test_domain_with_path(self):
        title = _title_from_url("https://example.com/python-tutorial")
        self.assertEqual(title, "example.com: python tutorial")

    def test_path_with_underscores(self):
        title = _title_from_url("https://docs.python.org/getting_started")
        self.assertEqual(title, "docs.python.org: getting started")

    def test_strips_query_params(self):
        title = _title_from_url("https://example.com/page?utm_source=google")
        self.assertEqual(title, "example.com: page")

    def test_strips_fragment(self):
        title = _title_from_url("https://example.com/docs#section-1")
        self.assertEqual(title, "example.com: docs")

    def test_long_path_truncated(self):
        long_path = "a" * 100
        title = _title_from_url(f"https://example.com/{long_path}")
        # Path should be truncated to 50 chars
        self.assertLessEqual(len(title), 70)  # domain + ": " + 50


@tag("content_skeleton")
class SerpExtractionTests(SimpleTestCase):
    """Tests for SERP markdown extraction."""

    def test_extracts_links(self):
        markdown = """
# Search Results

[Python Tutorial](https://python.org/tutorial)
[Django Docs](https://docs.djangoproject.com)
[Flask Guide](https://flask.palletsprojects.com)
"""
        skeleton = extract_serp_skeleton(markdown, "python web framework")

        self.assertEqual(skeleton.kind, "serp")
        self.assertEqual(skeleton.title, "python web framework")
        self.assertEqual(len(skeleton.items), 3)
        self.assertEqual(skeleton.items[0]["t"], "Python Tutorial")
        self.assertEqual(skeleton.items[0]["p"], 1)
        self.assertEqual(skeleton.items[1]["p"], 2)

    def test_skips_google_internal_urls(self):
        markdown = """
[Some Result](https://example.com/page)
[Google Image](https://www.google.com/images)
[Another](https://gstatic.com/resource)
[Real Result](https://real-site.com)
"""
        skeleton = extract_serp_skeleton(markdown)

        self.assertEqual(len(skeleton.items), 2)
        urls = [item["u"] for item in skeleton.items]
        self.assertIn("https://example.com/page", urls)
        self.assertIn("https://real-site.com", urls)
        self.assertNotIn("https://www.google.com/images", urls)

    def test_deduplicates_urls(self):
        markdown = """
[First Link](https://example.com/page)
[Same Page Again](https://example.com/page)
[With Fragment](https://example.com/page#section)
"""
        skeleton = extract_serp_skeleton(markdown)

        # Should only have one result (deduped by base URL)
        self.assertEqual(len(skeleton.items), 1)

    def test_useless_titles_get_url_fallback(self):
        markdown = """
[Read more](https://python.org/getting-started)
[Click here](https://docs.example.com/tutorial)
[Learn more](https://flask.palletsprojects.com/quickstart)
[OK](https://short.title)
"""
        skeleton = extract_serp_skeleton(markdown)

        # Useless titles should be replaced with URL-derived titles
        self.assertEqual(len(skeleton.items), 4)
        self.assertIn("python.org", skeleton.items[0]["t"])
        self.assertIn("getting started", skeleton.items[0]["t"].lower())
        # "OK" is too short (< 4 chars), should get URL fallback
        self.assertIn("short.title", skeleton.items[3]["t"])

    def test_limits_to_12_results(self):
        links = "\n".join(
            f"[Result {i}](https://example{i}.com)"
            for i in range(20)
        )
        skeleton = extract_serp_skeleton(links)

        self.assertEqual(len(skeleton.items), 12)

    def test_truncates_long_titles(self):
        long_title = "A" * 200
        markdown = f"[{long_title}](https://example.com)"
        skeleton = extract_serp_skeleton(markdown)

        self.assertEqual(len(skeleton.items[0]["t"]), 100)

    def test_empty_excerpt_for_serp(self):
        markdown = "[Result](https://example.com)"
        skeleton = extract_serp_skeleton(markdown)

        # SERP shouldn't have excerpt - items ARE the content
        self.assertEqual(skeleton.excerpt, "")


@tag("content_skeleton")
class ArticleExtractionTests(SimpleTestCase):
    """Tests for article markdown extraction."""

    def test_extracts_headings_with_content(self):
        markdown = """
# Introduction

This is the intro paragraph with some content.

## Getting Started

Here's how to get started with the project.

## Installation

Run pip install to install the package.
"""
        skeleton = extract_article_skeleton(markdown, "Tutorial")

        self.assertEqual(skeleton.kind, "article")
        self.assertEqual(skeleton.title, "Tutorial")
        self.assertEqual(len(skeleton.items), 3)
        self.assertEqual(skeleton.items[0]["h"], "Introduction")
        self.assertEqual(skeleton.items[0]["l"], 1)  # h1
        self.assertIn("intro paragraph", skeleton.items[0]["c"])
        self.assertEqual(skeleton.items[1]["h"], "Getting Started")
        self.assertEqual(skeleton.items[1]["l"], 2)  # h2

    def test_limits_to_10_sections(self):
        sections = "\n".join(
            f"## Section {i}\n\nContent for section {i}."
            for i in range(15)
        )
        skeleton = extract_article_skeleton(sections)

        self.assertEqual(len(skeleton.items), 10)

    def test_falls_back_to_raw_without_headings(self):
        markdown = """
Just some plain text without any markdown headings.
More text here. No structure at all.
"""
        skeleton = extract_article_skeleton(markdown, "Plain Text")

        self.assertEqual(skeleton.kind, "raw")
        self.assertEqual(len(skeleton.items), 0)
        self.assertIn("plain text", skeleton.excerpt.lower())

    def test_includes_excerpt(self):
        markdown = """
# Main Title

Some introductory content here.

## Details

More detailed information.
"""
        skeleton = extract_article_skeleton(markdown)

        # Article should have excerpt for raw fallback
        self.assertIn("introductory content", skeleton.excerpt)


@tag("content_skeleton")
class UniversalExtractionTests(SimpleTestCase):
    """Tests for the universal extract_skeleton function."""

    def test_detects_serp_from_content_type(self):
        markdown = "[Result](https://example.com)"
        skeleton = extract_skeleton(markdown, content_type="serp", title="query")

        self.assertEqual(skeleton.kind, "serp")

    def test_detects_serp_from_indicators(self):
        markdown = """
Google Search results for: python
[Python.org](https://python.org)
[Real Python](https://realpython.com)
"""
        skeleton = extract_skeleton(markdown, title="python")

        self.assertEqual(skeleton.kind, "serp")

    def test_detects_article_from_headings(self):
        markdown = """
# Welcome

This is an article.

## Section 1

Content here.
"""
        skeleton = extract_skeleton(markdown)

        self.assertEqual(skeleton.kind, "article")

    def test_falls_back_to_raw(self):
        plain_text = "Just some plain text without structure."
        skeleton = extract_skeleton(plain_text, title="Plain")

        self.assertEqual(skeleton.kind, "raw")
        self.assertIn("plain text", skeleton.excerpt.lower())


@tag("content_skeleton")
class QueryHintTests(SimpleTestCase):
    """Tests for query hint generation."""

    def test_serp_hint(self):
        skeleton = ContentSkeleton(
            kind="serp",
            title="test",
            items=[{"t": "A", "u": "https://a.com", "p": 1}],
        )
        hint = get_query_hint(skeleton, "result-123")

        self.assertIn("SERP: 1 results", hint)
        self.assertIn("json_each", hint)
        self.assertIn("$.items", hint)

    def test_article_hint(self):
        skeleton = ContentSkeleton(
            kind="article",
            title="test",
            items=[{"h": "Intro", "c": "Content", "l": 1}],
        )
        hint = get_query_hint(skeleton, "result-456")

        self.assertIn("ARTICLE: 1 sections", hint)
        self.assertIn("json_each", hint)

    def test_raw_hint(self):
        skeleton = ContentSkeleton(
            kind="raw",
            title="test",
            excerpt="Some raw content here",
        )
        hint = get_query_hint(skeleton, "result-789")

        self.assertIn("RAW:", hint)
        self.assertIn("chars", hint)


@tag("content_skeleton")
class CompressionEfficiencyTests(SimpleTestCase):
    """Tests verifying compression ratios."""

    def test_serp_compression_ratio(self):
        """SERP skeleton should be ~90% smaller than raw markdown."""
        # Simulate realistic SERP markdown
        raw_markdown = """
Skip to main content
Sign in

# Google Search

Showing results for: python programming

[Python.org](https://python.org)
The official home of the Python Programming Language.

[Real Python](https://realpython.com)
Python Tutorials – Real Python

[Learn Python](https://learnpython.org)
Free Interactive Python Tutorial

[W3Schools](https://w3schools.com/python)
Python Tutorial - W3Schools

Related searches: python download, python basics, python examples
""" + "x" * 5000  # Simulate navigation/footer noise

        skeleton = extract_serp_skeleton(raw_markdown, "python programming")

        raw_bytes = len(raw_markdown.encode("utf-8"))
        skeleton_bytes = skeleton.byte_size()

        # Skeleton should be much smaller
        compression_ratio = 1 - (skeleton_bytes / raw_bytes)
        self.assertGreater(compression_ratio, 0.85)  # At least 85% smaller

    def test_article_compression_ratio(self):
        """Article skeleton should provide meaningful compression on large docs."""
        # Simulate a realistic large article with lots of navigation/boilerplate
        raw_markdown = """
Skip to content | Accessibility | Sign in

Navigation: Home > Docs > Tutorial > Getting Started

# Getting Started with Python

Python is a versatile programming language that's great for beginners
and experts alike. In this comprehensive guide, we'll cover everything
you need to know to start your Python journey.

## Installation

First, download Python from python.org. The installation process is
straightforward on Windows, macOS, and Linux. Make sure to add Python
to your PATH during installation.

## Your First Program

The classic "Hello, World!" program in Python is simple:
    print("Hello, World!")

## Variables and Data Types

Python supports various data types including strings, integers, floats,
lists, dictionaries, and more. Variables don't need explicit type
declarations - Python infers the type automatically.

---
Footer | Copyright 2024 | Privacy Policy | Terms of Service
Related articles: Advanced Python, Python for Data Science
""" + "\n\nMore padding content to simulate large pages..." * 200

        skeleton = extract_article_skeleton(raw_markdown, "Python Tutorial")

        raw_bytes = len(raw_markdown.encode("utf-8"))
        skeleton_bytes = skeleton.byte_size()

        # Articles retain structure+excerpts, so compression is modest
        # but on large docs with padding, we should still achieve 30%+
        compression_ratio = 1 - (skeleton_bytes / raw_bytes)
        self.assertGreater(compression_ratio, 0.3)  # At least 30% smaller
