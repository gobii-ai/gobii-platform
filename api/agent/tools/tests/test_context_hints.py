"""
Tests for context hint extraction.

Context hints are optimistic - if we can't extract, we return None.
No hint is better than a bad hint.
"""

from django.test import SimpleTestCase, tag

from ..context_hints import (
    extract_context_hint,
    hint_from_serp,
    hint_from_scraped_page,
)


@tag('context_hints_batch')
class ContextHintExtractionTests(SimpleTestCase):
    """Tests for context hint extraction from tool results."""

    def test_hint_from_skeleton_serp(self):
        """Test hint extraction from skeleton format (adapter succeeded)."""
        payload = {
            'kind': 'serp',
            'items': [
                {'t': 'NVIDIA RTX 6000 Pro - B&H Photo', 'u': 'https://www.bhphotovideo.com/rtx-6000', 'p': 1},
                {'t': 'RTX 6000 Specs', 'u': 'https://www.nvidia.com/rtx-6000/', 'p': 2},
                {'t': 'Best GPU Prices', 'u': 'https://www.tomshardware.com/gpus', 'p': 3},
            ],
            'status': 'success',
        }
        hint = hint_from_serp(payload)

        self.assertIsNotNone(hint)
        self.assertIn('🔍', hint)
        self.assertIn('bhphotovideo.com', hint)
        self.assertIn('nvidia.com', hint)
        self.assertIn('tomshardware.com', hint)
        # Should include URLs for agent to scrape
        self.assertIn('https://www.bhphotovideo.com/rtx-6000', hint)

    def test_hint_from_raw_markdown_serp(self):
        """Test hint extraction from raw markdown (adapter failed)."""
        payload = {
            'status': 'success',
            'result': '''
Some Google Search Results

[NVIDIA RTX 6000](https://www.bhphotovideo.com/rtx-6000)
Description here

[](https://www.newegg.com/nvidia-rtx-6000)

[Read more](https://www.tomshardware.com/reviews/rtx-6000)
            ''',
        }
        hint = hint_from_serp(payload)

        self.assertIsNotNone(hint)
        self.assertIn('🔍', hint)
        self.assertIn('bhphotovideo.com', hint)
        # Should derive title from URL for empty bracket link
        self.assertIn('newegg.com', hint)

    def test_hint_skips_google_urls(self):
        """Test that Google internal URLs are filtered out."""
        payload = {
            'status': 'success',
            'result': '''
[Google Search](https://www.google.com/search?q=test)
[Real Result](https://www.example.com/product)
[Google Image](https://www.gstatic.com/image.png)
            ''',
        }
        hint = hint_from_serp(payload)

        self.assertIsNotNone(hint)
        self.assertIn('example.com', hint)
        self.assertNotIn('google.com', hint)
        self.assertNotIn('gstatic.com', hint)

    def test_hint_returns_none_for_no_urls(self):
        """Test that no hint is returned when no URLs found."""
        payload = {
            'status': 'success',
            'result': 'Just plain text with no links at all.',
        }
        hint = hint_from_serp(payload)

        self.assertIsNone(hint)

    def test_hint_from_scraped_page_with_price(self):
        """Test hint extraction from scraped page with prices."""
        payload = {
            'title': 'NVIDIA RTX 6000 Pro',
            'items': [],
            'excerpt': 'Price: $6,200.00. Available now. In stock.',
        }
        hint = hint_from_scraped_page(payload)

        self.assertIsNotNone(hint)
        self.assertIn('📄', hint)
        self.assertIn('NVIDIA RTX 6000 Pro', hint)
        self.assertIn('$6,200.00', hint)

    def test_extract_context_hint_routing(self):
        """Test that extract_context_hint routes to correct extractor."""
        serp_payload = {
            'kind': 'serp',
            'items': [{'t': 'Test', 'u': 'https://example.com', 'p': 1}],
        }

        # Should route to SERP extractor
        hint = extract_context_hint('mcp_brightdata_search_engine', serp_payload)
        self.assertIsNotNone(hint)
        self.assertIn('🔍', hint)

        # Should route to scrape extractor
        scrape_payload = {'title': 'Test Page', 'excerpt': 'Content here'}
        hint = extract_context_hint('mcp_brightdata_scrape_as_markdown', scrape_payload)
        self.assertIsNotNone(hint)
        self.assertIn('📄', hint)

    def test_hint_limits_items(self):
        """Test that hints are kept small with limited items."""
        payload = {
            'kind': 'serp',
            'items': [
                {'t': f'Result {i}', 'u': f'https://site{i}.com/page', 'p': i}
                for i in range(20)
            ],
        }
        hint = hint_from_serp(payload, max_items=3)

        # Should only include 3 URLs
        lines = hint.split('\n')
        url_lines = [l for l in lines if l.startswith('→')]
        self.assertEqual(len(url_lines), 3)

    def test_hint_handles_empty_payload(self):
        """Test graceful handling of empty/invalid payloads."""
        self.assertIsNone(hint_from_serp({}))
        self.assertIsNone(hint_from_serp({'status': 'success'}))
        self.assertIsNone(hint_from_serp({'items': []}))
        self.assertIsNone(extract_context_hint('unknown_tool', {}))
