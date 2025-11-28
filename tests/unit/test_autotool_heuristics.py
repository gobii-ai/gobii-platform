"""Unit tests for autotool heuristics functionality."""

import uuid
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings, tag
from django.contrib.auth import get_user_model

from api.models import (
    PersistentAgent,
    PersistentAgentEnabledTool,
    BrowserUseAgent,
    MCPServerConfig,
    PromptConfig,
)
from api.agent.tools.autotool_heuristics import (
    find_matching_tools,
    AUTOTOOL_HEURISTICS,
)
from api.agent.tools.tool_manager import (
    auto_enable_heuristic_tools,
    get_enabled_tool_limit,
)
from api.services.prompt_settings import invalidate_prompt_settings_cache
from tests.utils.llm_seed import seed_persistent_basic


User = get_user_model()


def create_test_browser_agent(user):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name="test-browser-agent")


@tag("batch_mcp_tools")
class TestFindMatchingTools(TestCase):
    """Tests for the find_matching_tools function."""

    def test_empty_text_returns_empty_set(self):
        """Empty text should return no matches."""
        result = find_matching_tools("")
        self.assertEqual(result, set())

    def test_none_text_returns_empty_set(self):
        """None text should return no matches."""
        result = find_matching_tools(None)
        self.assertEqual(result, set())

    def test_amazon_keyword_matches(self):
        """Amazon keyword should match amazon tools."""
        result = find_matching_tools("I want to search for products on Amazon")
        self.assertIn("mcp_brightdata_web_data_amazon_product", result)
        self.assertIn("mcp_brightdata_web_data_amazon_product_reviews", result)
        self.assertIn("mcp_brightdata_web_data_amazon_product_search", result)

    def test_amazon_case_insensitive(self):
        """Matching should be case insensitive."""
        result = find_matching_tools("AMAZON products")
        self.assertIn("mcp_brightdata_web_data_amazon_product", result)

        result = find_matching_tools("amazon products")
        self.assertIn("mcp_brightdata_web_data_amazon_product", result)

        result = find_matching_tools("AmAzOn products")
        self.assertIn("mcp_brightdata_web_data_amazon_product", result)

    def test_amzn_variant_matches(self):
        """amzn variant should match amazon tools."""
        result = find_matching_tools("check amzn for deals")
        self.assertIn("mcp_brightdata_web_data_amazon_product", result)

    def test_linkedin_keyword_matches(self):
        """LinkedIn keyword should match linkedin tools."""
        result = find_matching_tools("Find someone on LinkedIn")
        self.assertIn("mcp_brightdata_web_data_linkedin_person_profile", result)
        self.assertIn("mcp_brightdata_web_data_linkedin_company_profile", result)

    def test_instagram_variants_match(self):
        """Instagram and insta should both match."""
        result = find_matching_tools("Check their Instagram profile")
        self.assertIn("mcp_brightdata_web_data_instagram_profiles", result)

        result = find_matching_tools("Look at their insta")
        self.assertIn("mcp_brightdata_web_data_instagram_profiles", result)

    def test_word_boundary_prevents_partial_matches(self):
        """Word boundaries should prevent matching within words."""
        # "pig" should not match "ig"
        result = find_matching_tools("The pig walked across the street")
        self.assertNotIn("mcp_brightdata_web_data_instagram_profiles", result)

        # "big" should not match "ig"
        result = find_matching_tools("This is a big deal")
        self.assertNotIn("mcp_brightdata_web_data_instagram_profiles", result)

        # "figure" should not match "ig"
        result = find_matching_tools("Let me figure this out")
        self.assertNotIn("mcp_brightdata_web_data_instagram_profiles", result)

    def test_tiktok_variants_match(self):
        """TikTok and tik tok should both match."""
        result = find_matching_tools("Watch this TikTok video")
        self.assertIn("mcp_brightdata_web_data_tiktok_posts", result)

        result = find_matching_tools("Check out tik tok")
        self.assertIn("mcp_brightdata_web_data_tiktok_posts", result)

    def test_twitter_x_variants_match(self):
        """Twitter and x.com should match X tools."""
        result = find_matching_tools("See this Twitter post")
        self.assertIn("mcp_brightdata_web_data_x_posts", result)

        result = find_matching_tools("Check x.com for updates")
        self.assertIn("mcp_brightdata_web_data_x_posts", result)

    def test_youtube_matches(self):
        """YouTube variants should match."""
        result = find_matching_tools("Watch this YouTube video")
        self.assertIn("mcp_brightdata_web_data_youtube_videos", result)

        result = find_matching_tools("Check youtu.be link")
        self.assertIn("mcp_brightdata_web_data_youtube_videos", result)

    def test_facebook_variants_match(self):
        """Facebook and fb.com should match."""
        result = find_matching_tools("See this Facebook post")
        self.assertIn("mcp_brightdata_web_data_facebook_posts", result)

        result = find_matching_tools("Check fb.com")
        self.assertIn("mcp_brightdata_web_data_facebook_posts", result)

    def test_multiple_keywords_match_multiple_tool_sets(self):
        """Multiple keywords should match multiple tool sets."""
        result = find_matching_tools("Compare prices on Amazon and Walmart")
        self.assertIn("mcp_brightdata_web_data_amazon_product", result)
        self.assertIn("mcp_brightdata_web_data_walmart_product", result)

    def test_ecommerce_sites_match(self):
        """Various e-commerce sites should match their tools."""
        # eBay
        result = find_matching_tools("Search eBay listings")
        self.assertIn("mcp_brightdata_web_data_ebay_product", result)

        # Etsy
        result = find_matching_tools("Find handmade items on Etsy")
        self.assertIn("mcp_brightdata_web_data_etsy_products", result)

        # Best Buy
        result = find_matching_tools("Check BestBuy for electronics")
        self.assertIn("mcp_brightdata_web_data_bestbuy_products", result)

        result = find_matching_tools("Check Best Buy for electronics")
        self.assertIn("mcp_brightdata_web_data_bestbuy_products", result)

        # Home Depot
        result = find_matching_tools("Find tools at HomeDepot")
        self.assertIn("mcp_brightdata_web_data_homedepot_products", result)

        result = find_matching_tools("Find tools at Home Depot")
        self.assertIn("mcp_brightdata_web_data_homedepot_products", result)

    def test_business_sites_match(self):
        """Business/real estate sites should match."""
        # Zillow
        result = find_matching_tools("Look up properties on Zillow")
        self.assertIn("mcp_brightdata_web_data_zillow_properties_listing", result)

        # Crunchbase
        result = find_matching_tools("Research the company on Crunchbase")
        self.assertIn("mcp_brightdata_web_data_crunchbase_company", result)

        # ZoomInfo
        result = find_matching_tools("Get contact info from ZoomInfo")
        self.assertIn("mcp_brightdata_web_data_zoominfo_company_profile", result)

    def test_google_services_match(self):
        """Google services should match their specific tools."""
        # Google Maps
        result = find_matching_tools("Check Google Maps reviews")
        self.assertIn("mcp_brightdata_web_data_google_maps_reviews", result)

        # Google Play
        result = find_matching_tools("Find app on Google Play")
        self.assertIn("mcp_brightdata_web_data_google_play_store", result)

        result = find_matching_tools("Check the Play Store")
        self.assertIn("mcp_brightdata_web_data_google_play_store", result)

        # Google Shopping
        result = find_matching_tools("Compare on Google Shopping")
        self.assertIn("mcp_brightdata_web_data_google_shopping", result)

    def test_app_store_matches(self):
        """App Store should match."""
        result = find_matching_tools("Find it on the App Store")
        self.assertIn("mcp_brightdata_web_data_apple_app_store", result)

    def test_reddit_matches(self):
        """Reddit should match."""
        result = find_matching_tools("Check Reddit for discussions")
        self.assertIn("mcp_brightdata_web_data_reddit_posts", result)

    def test_github_matches(self):
        """GitHub should match."""
        result = find_matching_tools("Look at the GitHub repository")
        self.assertIn("mcp_brightdata_web_data_github_repository_file", result)

    def test_news_and_finance_match(self):
        """News and finance sites should match."""
        # Reuters
        result = find_matching_tools("Read the Reuters article")
        self.assertIn("mcp_brightdata_web_data_reuter_news", result)

        # Yahoo Finance
        result = find_matching_tools("Check Yahoo Finance for stock info")
        self.assertIn("mcp_brightdata_web_data_yahoo_finance_business", result)

    def test_booking_matches(self):
        """Booking.com should match."""
        result = find_matching_tools("Find hotels on booking.com")
        self.assertIn("mcp_brightdata_web_data_booking_hotel_listings", result)

    def test_no_false_positives_for_common_words(self):
        """Common words should not trigger matches."""
        result = find_matching_tools("Let me help you with that task")
        self.assertEqual(result, set())

        result = find_matching_tools("The weather is nice today")
        self.assertEqual(result, set())


@tag("batch_mcp_tools")
class TestAutoEnableHeuristicTools(TestCase):
    """Tests for the auto_enable_heuristic_tools function."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        seed_persistent_basic()
        invalidate_prompt_settings_cache()

    def setUp(self):
        """Create test user and agent."""
        unique_id = uuid.uuid4()
        self.user = User.objects.create_user(
            username=f"testuser-{unique_id}",
            email=f"test-{unique_id}@example.com",
            password="testpass123",
        )
        self.browser_agent = create_test_browser_agent(self.user)
        self.agent = PersistentAgent.objects.create(
            name="Test Agent",
            user=self.user,
            charter="Test charter",
            browser_use_agent=self.browser_agent,
        )

    def tearDown(self):
        """Clean up test data."""
        PersistentAgentEnabledTool.objects.filter(agent=self.agent).delete()
        self.agent.delete()
        self.user.delete()

    def test_empty_text_returns_empty_list(self):
        """Empty text should not enable any tools."""
        result = auto_enable_heuristic_tools(self.agent, "")
        self.assertEqual(result, [])

    def test_none_agent_returns_empty_list(self):
        """None agent should not enable any tools."""
        result = auto_enable_heuristic_tools(None, "Check Amazon")
        self.assertEqual(result, [])

    def test_no_matching_keywords_returns_empty_list(self):
        """Text without matching keywords should not enable any tools."""
        result = auto_enable_heuristic_tools(self.agent, "Just a regular message")
        self.assertEqual(result, [])

    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_enables_matching_tools_when_capacity_available(self, mock_get_manager, mock_build_index):
        """Should enable tools when keywords match and capacity is available."""
        # Mock the catalog to include amazon tools
        mock_catalog = {
            "mcp_brightdata_web_data_amazon_product": MagicMock(
                provider="mcp",
                full_name="mcp_brightdata_web_data_amazon_product",
                tool_server="brightdata",
                tool_name="web_data_amazon_product",
                server_config_id=None,
            ),
        }
        mock_build_index.return_value = mock_catalog

        mock_manager = MagicMock()
        mock_manager.is_tool_blacklisted.return_value = False
        mock_get_manager.return_value = mock_manager

        result = auto_enable_heuristic_tools(self.agent, "Search Amazon for products")

        # Should have enabled the tool
        self.assertIn("mcp_brightdata_web_data_amazon_product", result)

        # Verify it was persisted
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name="mcp_brightdata_web_data_amazon_product",
            ).exists()
        )

    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_does_not_enable_when_at_capacity(self, mock_get_manager, mock_build_index):
        """Should not enable tools when at capacity (no eviction)."""
        # Fill up the agent's tool slots
        limit = get_enabled_tool_limit(self.agent)
        for i in range(limit):
            PersistentAgentEnabledTool.objects.create(
                agent=self.agent,
                tool_full_name=f"dummy_tool_{i}",
            )

        result = auto_enable_heuristic_tools(self.agent, "Search Amazon for products")

        # Should not have enabled any tools
        self.assertEqual(result, [])

        # Verify amazon tool was NOT created
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name="mcp_brightdata_web_data_amazon_product",
            ).exists()
        )

    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_does_not_re_enable_already_enabled_tools(self, mock_get_manager, mock_build_index):
        """Should not re-enable tools that are already enabled."""
        # Pre-enable the amazon product tool
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="mcp_brightdata_web_data_amazon_product",
        )

        mock_catalog = {
            "mcp_brightdata_web_data_amazon_product": MagicMock(
                provider="mcp",
                full_name="mcp_brightdata_web_data_amazon_product",
            ),
            "mcp_brightdata_web_data_amazon_product_reviews": MagicMock(
                provider="mcp",
                full_name="mcp_brightdata_web_data_amazon_product_reviews",
                tool_server="brightdata",
                tool_name="web_data_amazon_product_reviews",
                server_config_id=None,
            ),
        }
        mock_build_index.return_value = mock_catalog

        mock_manager = MagicMock()
        mock_manager.is_tool_blacklisted.return_value = False
        mock_get_manager.return_value = mock_manager

        result = auto_enable_heuristic_tools(self.agent, "Search Amazon for products")

        # Should not include the already-enabled tool in the result
        self.assertNotIn("mcp_brightdata_web_data_amazon_product", result)

        # But should enable other matching tools
        self.assertIn("mcp_brightdata_web_data_amazon_product_reviews", result)

    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_respects_max_auto_enable_limit(self, mock_get_manager, mock_build_index):
        """Should respect the max_auto_enable parameter."""
        # Mock catalog with many tools
        mock_catalog = {}
        for tool_name in [
            "mcp_brightdata_web_data_amazon_product",
            "mcp_brightdata_web_data_amazon_product_reviews",
            "mcp_brightdata_web_data_amazon_product_search",
            "mcp_brightdata_web_data_walmart_product",
            "mcp_brightdata_web_data_walmart_seller",
            "mcp_brightdata_web_data_ebay_product",
        ]:
            mock_catalog[tool_name] = MagicMock(
                provider="mcp",
                full_name=tool_name,
                tool_server="brightdata",
                tool_name=tool_name.replace("mcp_brightdata_", ""),
                server_config_id=None,
            )
        mock_build_index.return_value = mock_catalog

        mock_manager = MagicMock()
        mock_manager.is_tool_blacklisted.return_value = False
        mock_get_manager.return_value = mock_manager

        # Request with max_auto_enable=2
        result = auto_enable_heuristic_tools(
            self.agent,
            "Compare Amazon, Walmart, and eBay prices",
            max_auto_enable=2,
        )

        # Should only enable 2 tools
        self.assertEqual(len(result), 2)

    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_skips_blacklisted_tools(self, mock_get_manager, mock_build_index):
        """Should not enable blacklisted tools."""
        mock_catalog = {
            "mcp_brightdata_web_data_amazon_product": MagicMock(
                provider="mcp",
                full_name="mcp_brightdata_web_data_amazon_product",
            ),
        }
        mock_build_index.return_value = mock_catalog

        mock_manager = MagicMock()
        mock_manager.is_tool_blacklisted.return_value = True  # Tool is blacklisted
        mock_get_manager.return_value = mock_manager

        result = auto_enable_heuristic_tools(self.agent, "Search Amazon for products")

        # Should not enable the blacklisted tool
        self.assertEqual(result, [])

    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_skips_tools_not_in_catalog(self, mock_get_manager, mock_build_index):
        """Should skip tools that aren't in the agent's catalog."""
        # Empty catalog - tools don't exist for this agent
        mock_build_index.return_value = {}

        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager

        result = auto_enable_heuristic_tools(self.agent, "Search Amazon for products")

        # Should not enable any tools
        self.assertEqual(result, [])

    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_respects_available_capacity(self, mock_get_manager, mock_build_index):
        """Should only enable up to available capacity."""
        # Fill up most of the capacity
        limit = get_enabled_tool_limit(self.agent)
        for i in range(limit - 1):  # Leave 1 slot
            PersistentAgentEnabledTool.objects.create(
                agent=self.agent,
                tool_full_name=f"dummy_tool_{i}",
            )

        # Mock catalog with multiple tools
        mock_catalog = {}
        for tool_name in [
            "mcp_brightdata_web_data_amazon_product",
            "mcp_brightdata_web_data_amazon_product_reviews",
            "mcp_brightdata_web_data_amazon_product_search",
        ]:
            mock_catalog[tool_name] = MagicMock(
                provider="mcp",
                full_name=tool_name,
                tool_server="brightdata",
                tool_name=tool_name.replace("mcp_brightdata_", ""),
                server_config_id=None,
            )
        mock_build_index.return_value = mock_catalog

        mock_manager = MagicMock()
        mock_manager.is_tool_blacklisted.return_value = False
        mock_get_manager.return_value = mock_manager

        result = auto_enable_heuristic_tools(self.agent, "Search Amazon for products")

        # Should only enable 1 tool (the available capacity)
        self.assertEqual(len(result), 1)


@tag("batch_mcp_tools")
class TestHeuristicsRegistryCompleteness(TestCase):
    """Tests to ensure the heuristics registry is well-formed."""

    def test_all_entries_have_keywords(self):
        """All entries should have at least one keyword."""
        for entry in AUTOTOOL_HEURISTICS:
            self.assertIn("keywords", entry)
            self.assertIsInstance(entry["keywords"], list)
            self.assertGreater(len(entry["keywords"]), 0, f"Entry has no keywords: {entry}")

    def test_all_entries_have_tools(self):
        """All entries should have at least one tool."""
        for entry in AUTOTOOL_HEURISTICS:
            self.assertIn("tools", entry)
            self.assertIsInstance(entry["tools"], list)
            self.assertGreater(len(entry["tools"]), 0, f"Entry has no tools: {entry}")

    def test_all_tool_names_follow_convention(self):
        """All tool names should follow the mcp_brightdata_ prefix convention."""
        for entry in AUTOTOOL_HEURISTICS:
            for tool in entry["tools"]:
                self.assertTrue(
                    tool.startswith("mcp_brightdata_"),
                    f"Tool name doesn't follow convention: {tool}",
                )

    def test_no_duplicate_keywords_across_entries(self):
        """Each keyword should only appear in one entry."""
        seen_keywords = {}
        for entry in AUTOTOOL_HEURISTICS:
            for keyword in entry["keywords"]:
                keyword_lower = keyword.lower()
                if keyword_lower in seen_keywords:
                    self.fail(
                        f"Duplicate keyword '{keyword}' found in entries for "
                        f"tools: {seen_keywords[keyword_lower]} and {entry['tools']}"
                    )
                seen_keywords[keyword_lower] = entry["tools"]
