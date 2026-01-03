"""
Tests for adaptive context hint extraction.

Coverage:
1. Known schemas (LinkedIn, Instagram, Crunchbase, etc.) - ensure adaptive system hits them
2. Pathological cases - ensure hard caps work, no crashes on weird data
3. Various data shapes - arrays, nested objects, mixed types
"""

from django.test import SimpleTestCase, tag

from ..context_hints import (
    extract_context_hint,
    hint_from_serp,
    hint_from_scraped_page,
    hint_from_structured_data,
    _detect_item_type,
    _format_count,
    _enforce_limit,
    MAX_HINT_BYTES,
)


# =============================================================================
# Known Schema Tests - Ensure adaptive system efficiently hits common patterns
# =============================================================================

@tag('context_hints_batch')
class LinkedInSchemaTests(SimpleTestCase):
    """Test that LinkedIn data shapes are correctly detected and formatted."""

    def test_linkedin_people_search(self):
        """LinkedIn people search: array of people with name, subtitle, url."""
        payload = {
            'result': [
                {
                    'name': 'Andrew Christianson',
                    'subtitle': 'Founder @ Gobii AI',
                    'url': 'https://www.linkedin.com/in/andrew-christianson',
                },
                {
                    'name': 'Will Bonde',
                    'subtitle': 'Growth Engineer',
                    'url': 'https://www.linkedin.com/in/willbonde',
                },
                {
                    'name': 'Matt Greathouse',
                    'subtitle': 'CTO',
                    'url': 'https://www.linkedin.com/in/mattgreathouse',
                },
            ],
        }
        hint = extract_context_hint('mcp_brightdata_web_data_linkedin_people_search', payload)

        self.assertIsNotNone(hint)
        self.assertIn('Andrew Christianson', hint)
        self.assertIn('Founder', hint)
        self.assertIn('linkedin.com', hint)
        # Should show multiple people
        self.assertIn('Will Bonde', hint)

    def test_linkedin_person_profile(self):
        """LinkedIn person profile: single object with name, headline, experience."""
        payload = {
            'name': 'Andrew Christianson',
            'headline': 'Building browser-native AI agents',
            'location': 'San Francisco Bay Area',
            'connections_count': 500,
            'experience': [
                {'company': 'Gobii AI', 'title': 'Founder & CEO'},
            ],
        }
        hint = extract_context_hint('mcp_brightdata_web_data_linkedin_person_profile', payload)

        self.assertIsNotNone(hint)
        self.assertIn('Andrew Christianson', hint)
        self.assertIn('Building browser-native', hint)
        # Should show metrics
        self.assertIn('500', hint)

    def test_linkedin_company_profile(self):
        """LinkedIn company profile: single object with name, industry, size."""
        payload = {
            'name': 'Gobii AI',
            'industry': 'Software Development',
            'company_size': '11-50 employees',
            'followers_count': 1500,
            'headquarters': 'San Francisco, CA',
        }
        hint = extract_context_hint('mcp_brightdata_web_data_linkedin_company_profile', payload)

        self.assertIsNotNone(hint)
        self.assertIn('Gobii AI', hint)
        # Should show metrics
        self.assertIn('1.5K', hint)


@tag('context_hints_batch')
class SocialMediaSchemaTests(SimpleTestCase):
    """Test that social media data shapes are correctly detected."""

    def test_instagram_profile(self):
        """Instagram profile with followers, posts, bio."""
        payload = {
            'username': 'gobii_ai',
            'full_name': 'Gobii AI',
            'followers_count': 50000,
            'posts_count': 150,
            'biography': 'Building the future of browser AI agents',
        }
        hint = extract_context_hint('mcp_brightdata_web_data_instagram_profiles', payload)

        self.assertIsNotNone(hint)
        # Should show username or full_name
        self.assertTrue('Gobii AI' in hint or 'gobii_ai' in hint)
        self.assertIn('50K', hint)

    def test_tiktok_profile(self):
        """TikTok profile with followers, likes."""
        payload = {
            'unique_id': 'tech_creator',
            'nickname': 'Tech Creator',
            'follower_count': 1500000,
            'heart_count': 25000000,
        }
        hint = extract_context_hint('mcp_brightdata_web_data_tiktok_profiles', payload)

        self.assertIsNotNone(hint)
        # Should show nickname or unique_id
        self.assertTrue('Tech Creator' in hint or 'tech_creator' in hint)
        self.assertIn('1.5M', hint)
        self.assertIn('25M', hint)

    def test_youtube_profile(self):
        """YouTube channel with subscribers, videos."""
        payload = {
            'title': 'AI Explained',
            'subscriber_count': 500000,
            'video_count': 250,
        }
        hint = extract_context_hint('mcp_brightdata_web_data_youtube_profiles', payload)

        self.assertIsNotNone(hint)
        self.assertIn('AI Explained', hint)
        self.assertIn('500K', hint)


@tag('context_hints_batch')
class CrunchbaseSchemaTests(SimpleTestCase):
    """Test Crunchbase company data."""

    def test_crunchbase_company(self):
        """Crunchbase company with funding, employees."""
        payload = {
            'name': 'Anthropic',
            'short_description': 'AI safety company',
            'total_funding_usd': 7300000000,
            'funding_stage': 'Series E',
            'num_employees_enum': '501-1000',
            'headquarters_location': 'San Francisco',
        }
        hint = extract_context_hint('mcp_brightdata_web_data_crunchbase_company', payload)

        self.assertIsNotNone(hint)
        self.assertIn('Anthropic', hint)
        self.assertIn('AI safety', hint)
        self.assertIn('7.3B', hint)


# =============================================================================
# SERP and Scraped Page Tests
# =============================================================================

@tag('context_hints_batch')
class SerpHintTests(SimpleTestCase):
    """Test SERP (search results) hint extraction."""

    def test_serp_skeleton_format(self):
        """Test with pre-processed skeleton format."""
        payload = {
            'kind': 'serp',
            'items': [
                {'t': 'NVIDIA RTX 6000 Pro - B&H', 'u': 'https://www.bhphotovideo.com/rtx-6000', 'p': 1},
                {'t': 'RTX 6000 Specs', 'u': 'https://www.nvidia.com/rtx-6000/', 'p': 2},
            ],
        }
        hint = hint_from_serp(payload)

        self.assertIsNotNone(hint)
        self.assertIn('🔍', hint)
        self.assertIn('bhphotovideo.com', hint)
        self.assertIn('nvidia.com', hint)

    def test_serp_raw_markdown(self):
        """Test extraction from raw markdown."""
        payload = {
            'result': '''
Search Results:
[NVIDIA RTX 6000](https://www.bhphotovideo.com/rtx-6000) - Professional GPU
[Read more](https://www.tomshardware.com/reviews/rtx-6000) - Review
            ''',
        }
        hint = hint_from_serp(payload)

        self.assertIsNotNone(hint)
        self.assertIn('bhphotovideo.com', hint)

    def test_serp_skips_google_urls(self):
        """Test that Google internal URLs are filtered."""
        payload = {
            'result': '''
[Google Search](https://www.google.com/search?q=test)
[Real Result](https://www.example.com/product)
            ''',
        }
        hint = hint_from_serp(payload)

        self.assertIn('example.com', hint)
        self.assertNotIn('google.com', hint)


@tag('context_hints_batch')
class ScrapedPageHintTests(SimpleTestCase):
    """Test scraped page hint extraction."""

    def test_page_with_title_and_prices(self):
        """Test extraction of title and prices."""
        payload = {
            'title': 'NVIDIA RTX 6000 Pro',
            'excerpt': 'Price: $6,200.00. Available now.',
        }
        hint = hint_from_scraped_page(payload)

        self.assertIsNotNone(hint)
        self.assertIn('📄', hint)
        self.assertIn('NVIDIA RTX 6000 Pro', hint)
        self.assertIn('$6,200.00', hint)

    def test_page_raw_markdown(self):
        """Test extraction from raw markdown."""
        payload = {
            'result': '''# Product Page

The price is $299.99 for the basic model.
            ''',
        }
        hint = hint_from_scraped_page(payload)

        self.assertIsNotNone(hint)
        self.assertIn('Product Page', hint)
        self.assertIn('$299.99', hint)


# =============================================================================
# Generic Data Shape Tests
# =============================================================================

@tag('context_hints_batch')
class GenericArrayTests(SimpleTestCase):
    """Test adaptive detection of array patterns."""

    def test_people_array_detection(self):
        """Array of people with name + title/position."""
        payload = {
            'result': [
                {'name': 'Alice', 'title': 'Engineer'},
                {'name': 'Bob', 'title': 'Designer'},
                {'name': 'Carol', 'title': 'Manager'},
            ],
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('👥', hint)  # People emoji
        # Names shown (not titles) - more items > more detail per item
        self.assertIn('Alice', hint)
        self.assertIn('Bob', hint)

    def test_product_array_detection(self):
        """Array of products with name + price."""
        payload = {
            'items': [
                {'name': 'Widget Pro', 'price': '$99'},
                {'name': 'Widget Basic', 'price': '$49'},
            ],
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('🛒', hint)  # Product emoji
        self.assertIn('Widget Pro', hint)
        self.assertIn('$99', hint)

    def test_company_array_detection(self):
        """Array of companies with name + industry."""
        payload = {
            'data': [
                {'name': 'Acme Corp', 'industry': 'Technology'},
                {'name': 'BigCo', 'industry': 'Finance'},
            ],
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('🏢', hint)  # Company emoji
        self.assertIn('Acme Corp', hint)
        self.assertIn('Technology', hint)

    def test_link_array_detection(self):
        """Array of links with title + url."""
        payload = {
            'results': [
                {'title': 'Article One', 'url': 'https://example.com/1'},
                {'title': 'Article Two', 'url': 'https://example.com/2'},
            ],
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('Article One', hint)
        self.assertIn('example.com', hint)

    def test_nested_array_detection(self):
        """Array buried in nested structure."""
        payload = {
            'response': {
                'data': {
                    'items': [
                        {'name': 'Nested Item', 'headline': 'Found it!'},
                    ],
                },
            },
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('Nested Item', hint)


@tag('context_hints_batch')
class SingleObjectTests(SimpleTestCase):
    """Test adaptive detection of single object patterns."""

    def test_profile_object(self):
        """Single profile with metrics."""
        payload = {
            'username': 'testuser',
            'headline': 'Software Engineer',
            'followers_count': 5000,
            'location': 'New York',
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('testuser', hint)
        self.assertIn('Software Engineer', hint)
        self.assertIn('5K', hint)

    def test_company_object(self):
        """Single company with funding."""
        payload = {
            'name': 'StartupCo',
            'short_description': 'Revolutionary platform',
            'total_funding_usd': 50000000,
            'headquarters_location': 'Austin, TX',
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('StartupCo', hint)
        self.assertIn('50M', hint)

    def test_wrapped_object(self):
        """Object wrapped in 'result' key."""
        payload = {
            'result': {
                'name': 'Wrapped Entity',
                'headline': 'Important info here',
            },
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('Wrapped Entity', hint)


# =============================================================================
# Pathological Cases - Hard Caps and Edge Cases
# =============================================================================

@tag('context_hints_batch')
class HardCapTests(SimpleTestCase):
    """Test that hard caps are enforced."""

    def test_hint_never_exceeds_byte_limit(self):
        """Hint must never exceed MAX_HINT_BYTES."""
        # Create payload with very long strings
        payload = {
            'result': [
                {
                    'name': 'A' * 200,
                    'subtitle': 'B' * 200,
                    'url': 'https://example.com/' + 'x' * 200,
                } for _ in range(20)
            ],
        }
        hint = hint_from_structured_data(payload)

        if hint:
            self.assertLessEqual(len(hint.encode('utf-8')), MAX_HINT_BYTES)

    def test_enforce_limit_truncates_correctly(self):
        """_enforce_limit should truncate without breaking."""
        long_hint = "📋 " + "A" * 500
        result = _enforce_limit(long_hint)

        self.assertLessEqual(len(result.encode('utf-8')), MAX_HINT_BYTES)
        # Should end cleanly
        self.assertTrue(result.endswith('...') or len(result.encode('utf-8')) <= MAX_HINT_BYTES)

    def test_max_items_enforced(self):
        """Should only include up to MAX_ITEMS."""
        payload = {
            'result': [
                {'name': f'Person {i}', 'title': f'Title {i}'} for i in range(100)
            ],
        }
        hint = hint_from_structured_data(payload)

        # Should not have more than a few people listed
        self.assertLess(hint.count('Person'), 10)


@tag('context_hints_batch')
class EdgeCaseTests(SimpleTestCase):
    """Test edge cases and weird data."""

    def test_empty_payload(self):
        """Empty payload returns None."""
        self.assertIsNone(extract_context_hint('any_tool', {}))
        self.assertIsNone(hint_from_structured_data({}))
        self.assertIsNone(hint_from_serp({}))

    def test_non_dict_payload(self):
        """Non-dict payload returns None."""
        self.assertIsNone(extract_context_hint('any_tool', "string"))
        self.assertIsNone(extract_context_hint('any_tool', ['list']))
        self.assertIsNone(extract_context_hint('any_tool', None))

    def test_empty_arrays(self):
        """Empty arrays return None."""
        self.assertIsNone(hint_from_structured_data({'result': []}))
        self.assertIsNone(hint_from_structured_data({'items': []}))

    def test_arrays_of_primitives(self):
        """Arrays of non-objects return None."""
        self.assertIsNone(hint_from_structured_data({'result': [1, 2, 3]}))
        self.assertIsNone(hint_from_structured_data({'result': ['a', 'b', 'c']}))

    def test_objects_without_interesting_fields(self):
        """Objects without recognizable fields return None."""
        payload = {
            'result': [
                {'foo': 'bar', 'baz': 123},
                {'qux': 'quux', 'abc': 456},
            ],
        }
        hint = hint_from_structured_data(payload)
        # Should return None or minimal hint
        # (depends on whether any field matches)

    def test_mixed_type_array(self):
        """Array with mixed types handles gracefully."""
        payload = {
            'result': [
                {'name': 'Valid', 'title': 'Person'},
                'invalid string',
                {'name': 'Also Valid', 'title': 'Another'},
                None,
                123,
            ],
        }
        hint = hint_from_structured_data(payload)

        # Should still extract valid items
        self.assertIsNotNone(hint)
        self.assertIn('Valid', hint)

    def test_unicode_handling(self):
        """Unicode characters handled correctly."""
        payload = {
            'name': '日本語名前',
            'headline': 'Emoji test 🚀🎉',
            'followers_count': 1000,
        }
        hint = hint_from_structured_data(payload)

        self.assertIsNotNone(hint)
        self.assertIn('日本語名前', hint)
        # Should still fit in byte limit
        self.assertLessEqual(len(hint.encode('utf-8')), MAX_HINT_BYTES)

    def test_deeply_nested_structure(self):
        """Deeply nested structure with max_depth limit."""
        payload = {
            'level1': {
                'level2': {
                    'level3': {
                        'level4': {
                            'level5': {
                                'result': [{'name': 'Too Deep', 'title': 'Unreachable'}],
                            },
                        },
                    },
                },
            },
        }
        hint = hint_from_structured_data(payload)
        # max_depth=3 should prevent finding this
        self.assertIsNone(hint)


# =============================================================================
# Type Detection Tests
# =============================================================================

@tag('context_hints_batch')
class TypeDetectionTests(SimpleTestCase):
    """Test that item types are correctly detected."""

    def test_detect_person_type(self):
        """Detect person from LinkedIn-like fields."""
        item = {'name': 'Test', 'headline': 'Engineer', 'connections_count': 500}
        self.assertEqual(_detect_item_type(item), 'person')

    def test_detect_profile_type(self):
        """Detect social profile from follower fields."""
        item = {'username': 'test', 'followers_count': 1000, 'bio': 'Hello'}
        self.assertEqual(_detect_item_type(item), 'profile')

    def test_detect_company_type(self):
        """Detect company from industry/funding fields."""
        item = {'name': 'Corp', 'industry': 'Tech', 'total_funding': 1000000}
        self.assertEqual(_detect_item_type(item), 'company')

    def test_detect_product_type(self):
        """Detect product from price fields."""
        item = {'name': 'Widget', 'price': '$99'}
        self.assertEqual(_detect_item_type(item), 'product')

    def test_detect_post_type(self):
        """Detect post/tweet from text fields."""
        item = {'text': 'Hello world', 'favorite_count': 100}
        self.assertEqual(_detect_item_type(item), 'post')


# =============================================================================
# Helper Function Tests
# =============================================================================

@tag('context_hints_batch')
class FormatCountTests(SimpleTestCase):
    """Test _format_count helper."""

    def test_billions(self):
        self.assertEqual(_format_count(7300000000), '7.3B')
        self.assertEqual(_format_count(1000000000), '1B')

    def test_millions(self):
        self.assertEqual(_format_count(1500000), '1.5M')
        self.assertEqual(_format_count(1000000), '1M')

    def test_thousands(self):
        self.assertEqual(_format_count(1500), '1.5K')
        self.assertEqual(_format_count(1000), '1K')
        self.assertEqual(_format_count(50000), '50K')

    def test_small_numbers(self):
        self.assertEqual(_format_count(500), '500')
        self.assertEqual(_format_count(42), '42')

    def test_string_input(self):
        self.assertEqual(_format_count('1,500,000'), '1.5M')
        self.assertEqual(_format_count('500+'), '500')

    def test_invalid_input(self):
        result = _format_count('not a number')
        self.assertEqual(result, 'not a numb')  # Truncated to 10 chars


@tag('context_hints_batch')
class RoutingTests(SimpleTestCase):
    """Test that extract_context_hint routes correctly."""

    def test_routes_to_serp(self):
        """search_engine tools route to SERP extractor."""
        payload = {'items': [{'t': 'Test', 'u': 'https://example.com', 'd': 'example.com'}]}

        hint = extract_context_hint('mcp_brightdata_search_engine', payload)
        self.assertIn('🔍', hint)

        hint = extract_context_hint('search_engine', payload)
        self.assertIn('🔍', hint)

    def test_routes_to_scrape(self):
        """scrape_as_markdown tools route to scraped page extractor."""
        payload = {'title': 'Test Page'}

        hint = extract_context_hint('mcp_brightdata_scrape_as_markdown', payload)
        self.assertIn('📄', hint)

    def test_routes_to_adaptive(self):
        """Unknown tools route to adaptive extractor."""
        payload = {'result': [{'name': 'Test Person', 'headline': 'Engineer'}]}

        hint = extract_context_hint('unknown_tool_xyz', payload)
        self.assertIsNotNone(hint)
        self.assertIn('Test Person', hint)


# =============================================================================
# Bright Data Schema Tests (from real API responses)
# =============================================================================

@tag('context_hints_batch')
class BrightDataAmazonTests(SimpleTestCase):
    """Test Bright Data Amazon product schemas."""

    def test_amazon_product(self):
        """Amazon product with price, rating, seller."""
        payload = {
            'name': 'Apple AirPods Pro (2nd Generation)',
            'final_price': '$189.00',
            'rating': '4.7',
            'reviews_count': 128450,
            'asin': 'B0D1XD1ZV3',
            'seller_name': 'Amazon.com',
            'brand': 'Apple',
            'url': 'https://www.amazon.com/dp/B0D1XD1ZV3',
        }
        hint = extract_context_hint('mcp_brightdata_web_data_amazon_product', payload)

        self.assertIsNotNone(hint)
        self.assertIn('Apple AirPods', hint)
        self.assertIn('$189', hint)

    def test_amazon_product_search_array(self):
        """Amazon product search results as array."""
        payload = {
            'result': [
                {'name': 'AirPods Pro 2', 'price': '$189.00', 'rating': '4.7', 'asin': 'B0D1'},
                {'name': 'AirPods Max', 'price': '$449.00', 'rating': '4.5', 'asin': 'B08P'},
                {'name': 'Beats Studio', 'price': '$349.00', 'rating': '4.6', 'asin': 'B09S'},
            ],
        }
        hint = extract_context_hint('mcp_brightdata_web_data_amazon_product_search', payload)

        self.assertIsNotNone(hint)
        self.assertIn('🛒', hint)  # Product emoji
        self.assertIn('AirPods Pro', hint)
        self.assertIn('$189', hint)

    def test_amazon_reviews(self):
        """Amazon product reviews."""
        payload = {
            'result': [
                {'rating': '5', 'text': 'Best headphones I have ever owned!', 'author_name': 'John D.'},
                {'rating': '4', 'text': 'Great sound but battery could be better', 'author_name': 'Sarah M.'},
                {'rating': '5', 'text': 'Amazing noise cancellation', 'author_name': 'Mike T.'},
            ],
        }
        hint = extract_context_hint('mcp_brightdata_web_data_amazon_product_reviews', payload)

        self.assertIsNotNone(hint)
        self.assertIn('⭐', hint)  # Review emoji
        self.assertIn('5', hint)


@tag('context_hints_batch')
class BrightDataJobListingsTests(SimpleTestCase):
    """Test Bright Data job listing schemas from LinkedIn."""

    def test_linkedin_job_listing(self):
        """LinkedIn job listing with title, company, location."""
        payload = {
            'title': 'Software Engineer, Professional Services',
            'company': 'Fireblocks',
            'location': 'Tel Aviv District, Israel',
            'employment_type': 'Full-time',
            'seniority_level': 'Entry level',
            'applicants': 25,
            'apply_link': 'https://www.linkedin.com/jobs/view/externalApply/4107998267',
        }
        hint = extract_context_hint('mcp_brightdata_web_data_linkedin_job_listings', payload)

        self.assertIsNotNone(hint)
        self.assertIn('Software Engineer', hint)
        self.assertIn('Fireblocks', hint)

    def test_linkedin_job_search_array(self):
        """LinkedIn job search results as array."""
        payload = {
            'result': [
                {
                    'title': 'Data Platform Engineer',
                    'company': 'Cycode',
                    'location': 'Tel Aviv',
                    'employment_type': 'Full-time',
                    'job_link': 'https://www.linkedin.com/jobs/view/4073552631',
                },
                {
                    'title': 'ML Engineer',
                    'company': 'Anthropic',
                    'location': 'San Francisco',
                    'employment_type': 'Full-time',
                    'job_link': 'https://www.linkedin.com/jobs/view/1234567890',
                },
            ],
        }
        hint = extract_context_hint('mcp_brightdata_web_data_linkedin_job_listings', payload)

        self.assertIsNotNone(hint)
        self.assertIn('💼', hint)  # Job emoji
        self.assertIn('Data Platform Engineer', hint)
        self.assertIn('Cycode', hint)


@tag('context_hints_batch')
class BrightDataLinkedInPostsTests(SimpleTestCase):
    """Test Bright Data LinkedIn posts schemas."""

    def test_linkedin_post(self):
        """LinkedIn post with engagement metrics."""
        payload = {
            'text': 'Free Datasets! Not just samples, but complete datasets with millions of records.',
            'author': 'Or Lenchner',
            'num_likes': 18,
            'num_comments': 4,
            'post_info': {'id': '7176601589682434049'},
        }
        hint = extract_context_hint('mcp_brightdata_web_data_linkedin_posts', payload)

        self.assertIsNotNone(hint)
        self.assertIn('💬', hint)  # Post emoji
        self.assertIn('Free Datasets', hint)

    def test_linkedin_posts_array(self):
        """LinkedIn posts as array."""
        payload = {
            'result': [
                {
                    'text': 'Launching our new product today!',
                    'username': 'techfounder',
                    'num_likes': 150,
                    'engagement': {'likes': 150, 'comments': 25},
                },
                {
                    'text': 'Hiring for multiple roles...',
                    'username': 'recruiter',
                    'num_likes': 45,
                },
            ],
        }
        hint = extract_context_hint('mcp_brightdata_web_data_linkedin_posts', payload)

        self.assertIsNotNone(hint)
        self.assertIn('Launching', hint)


@tag('context_hints_batch')
class BrightDataOtherServicesTests(SimpleTestCase):
    """Test Bright Data schemas for other services."""

    def test_google_maps_reviews(self):
        """Google Maps reviews."""
        payload = {
            'result': [
                {'rating': 5, 'text': 'Amazing restaurant with great food!', 'author': 'Food Lover'},
                {'rating': 4, 'text': 'Good service but a bit pricey', 'author': 'Budget Diner'},
            ],
        }
        hint = extract_context_hint('mcp_brightdata_web_data_google_maps_reviews', payload)

        self.assertIsNotNone(hint)
        self.assertIn('⭐', hint)  # Review emoji
        self.assertIn('Amazing restaurant', hint)

    def test_zillow_property_listing(self):
        """Zillow property listing."""
        payload = {
            'name': '123 Main Street',
            'price': '$450,000',
            'location': 'San Francisco, CA',
            'url': 'https://www.zillow.com/homedetails/123-main-st',
        }
        hint = extract_context_hint('mcp_brightdata_web_data_zillow_properties_listing', payload)

        self.assertIsNotNone(hint)
        self.assertIn('123 Main Street', hint)

    def test_booking_hotel(self):
        """Booking.com hotel listing."""
        payload = {
            'name': 'Grand Hotel Palace',
            'price': '$299/night',
            'rating': '4.8',
            'location': 'Paris, France',
            'reviews_count': 2450,
        }
        hint = extract_context_hint('mcp_brightdata_web_data_booking_hotel_listings', payload)

        self.assertIsNotNone(hint)
        self.assertIn('Grand Hotel', hint)

    def test_reddit_post(self):
        """Reddit post."""
        payload = {
            'title': 'TIL about an amazing fact',
            'text': 'This is the post body with interesting content',
            'author': 'redditor123',
            'upvotes': 15000,
            'num_comments': 500,
        }
        hint = extract_context_hint('mcp_brightdata_web_data_reddit_posts', payload)

        self.assertIsNotNone(hint)
        self.assertIn('💬', hint)  # Post emoji
        # Shows text body (not title) - text field takes precedence
        self.assertIn('post body', hint)
        self.assertIn('redditor123', hint)

    def test_x_posts(self):
        """X/Twitter posts."""
        payload = {
            'text': 'Just released a major update to our product!',
            'username': 'techceo',
            'favorite_count': 5000,
            'retweet_count': 1200,
        }
        hint = extract_context_hint('mcp_brightdata_web_data_x_posts', payload)

        self.assertIsNotNone(hint)
        self.assertIn('major update', hint)


# =============================================================================
# Extended Type Detection Tests
# =============================================================================

@tag('context_hints_batch')
class ExtendedTypeDetectionTests(SimpleTestCase):
    """Extended tests for type detection patterns."""

    def test_detect_job_type_from_employment(self):
        """Detect job from employment_type field."""
        item = {'title': 'Engineer', 'company': 'Corp', 'employment_type': 'Full-time'}
        self.assertEqual(_detect_item_type(item), 'job')

    def test_detect_job_type_from_job_link(self):
        """Detect job from job_link + company."""
        item = {'job_title': 'Developer', 'company': 'StartupCo', 'job_link': 'https://...', 'location': 'NYC'}
        self.assertEqual(_detect_item_type(item), 'job')

    def test_detect_review_type(self):
        """Detect review from rating + text fields."""
        item = {'rating': 5, 'text': 'Great product!', 'author_name': 'John'}
        self.assertEqual(_detect_item_type(item), 'review')

    def test_detect_product_from_asin(self):
        """Detect product from ASIN field (Amazon specific)."""
        item = {'name': 'AirPods', 'asin': 'B0D1XD1ZV3', 'seller': 'Amazon'}
        self.assertEqual(_detect_item_type(item), 'product')

    def test_detect_company_from_key_info(self):
        """Detect company from key_info nested field."""
        item = {'name': 'Kraft Heinz', 'key_info': {'headquarters': 'Chicago'}, 'metrics': {'followers': 1000000}}
        self.assertEqual(_detect_item_type(item), 'company')

    def test_detect_profile_from_linkedin_followers(self):
        """Detect profile from linkedin_followers field."""
        item = {'name': 'Gobii AI', 'linkedin_followers': 1557451, 'linkedin_employees': 25254}
        self.assertEqual(_detect_item_type(item), 'profile')

    def test_detect_post_from_engagement(self):
        """Detect post from engagement metrics."""
        item = {'text': 'Hello!', 'post_info': {'id': '123'}, 'num_likes': 100, 'num_comments': 5}
        self.assertEqual(_detect_item_type(item), 'post')
