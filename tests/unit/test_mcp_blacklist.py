"""Tests for MCP tool blacklist functionality."""

from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
from django.test import TestCase, tag

from api.agent.tools.mcp_manager import MCPToolManager, MCPServerRuntime
from api.models import PersistentAgent, BrowserUseAgent


@tag("batch_mcp_tools")
class TestMCPToolBlacklist(TestCase):
    """Test the MCP tool blacklist functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.manager = MCPToolManager()
        
    def test_is_tool_blacklisted(self):
        """Test the _is_tool_blacklisted method with various patterns."""
        # Test exact match patterns
        self.assertTrue(
            self.manager._is_tool_blacklisted("mcp_brightdata_scraping_browser_navigate")
        )
        self.assertTrue(
            self.manager._is_tool_blacklisted("mcp_brightdata_scraping_browser_click")
        )
        self.assertTrue(
            self.manager._is_tool_blacklisted("mcp_brightdata_scraping_browser_go_back")
        )
        
        self.assertTrue(
            self.manager._is_tool_blacklisted("mcp_brightdata_scrape_as_markdown")
        )
        self.assertTrue(
            self.manager._is_tool_blacklisted("mcp_brightdata_search_engine")
        )
        self.assertTrue(
            self.manager._is_tool_blacklisted("mcp_brightdata_web_data_linkedin_person_profile")
        )
        # Other Bright Data MCP tools remain available.
        self.assertFalse(
            self.manager._is_tool_blacklisted("mcp_brightdata_search_engine_batch")
        )
        self.assertFalse(
            self.manager._is_tool_blacklisted("mcp_other_scraping_browser_tool")
        )
    
    def test_fetch_server_tools_filters_blacklisted(self):
        """Test that _fetch_server_tools filters out blacklisted tools."""
        # Create mock tools - some blacklisted, some not
        class MockTool:
            def __init__(self, name, description, inputSchema):
                self.name = name
                self.description = description
                self.inputSchema = inputSchema
        
        mock_tools = [
            MockTool('scraping_browser_navigate', 'Navigate browser', {}),
            MockTool('scraping_browser_click', 'Click element', {}),
            MockTool('scrape_as_markdown', 'Scrape as markdown', {}),
            MockTool('search_engine', 'Search engine', {}),
            MockTool('web_data_linkedin_person_profile', 'LinkedIn person profile', {}),
            MockTool('search_engine_batch', 'Search engine batch', {}),
        ]
        
        # Create a mock async function that returns tools
        async def mock_fetch():
            # Create a mock client
            class MockClient:
                async def __aenter__(self):
                    return self
                
                async def __aexit__(self, *args):
                    return None
                
                async def list_tools(self):
                    return mock_tools
            
            server = MCPServerRuntime(
                config_id="cfg-bright",
                name="brightdata",
                display_name="Bright Data",
                description="Test server",
                command="test",
                args=[],
                url=None,
                auth_method="none",
                env={},
                headers={},
                prefetch_apps=[],
                scope="platform",
                organization_id=None,
                user_id=None,
                updated_at=None,
            )
            
            # Fetch tools
            return await self.manager._fetch_server_tools(MockClient(), server)
        
        # Run the async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            tools = loop.run_until_complete(mock_fetch())
        finally:
            loop.close()
        
        # Verify only non-blacklisted tools are returned
        self.assertEqual(len(tools), 1)
        tool_names = [tool.full_name for tool in tools]
        self.assertIn("mcp_brightdata_search_engine_batch", tool_names)
        self.assertNotIn("mcp_brightdata_scrape_as_markdown", tool_names)
        self.assertNotIn("mcp_brightdata_search_engine", tool_names)
        self.assertNotIn("mcp_brightdata_web_data_linkedin_person_profile", tool_names)
        self.assertNotIn("mcp_brightdata_scraping_browser_navigate", tool_names)
        self.assertNotIn("mcp_brightdata_scraping_browser_click", tool_names)
    
    def test_execute_mcp_tool_blocks_blacklisted(self):
        """Test that execute_mcp_tool blocks blacklisted tools."""
        # Create a real agent
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(username='blk@example.com')
        browser_agent = BrowserUseAgent.objects.create(user=user, name="blk-browser")
        agent = PersistentAgent.objects.create(user=user, name="blk-agent", charter="T", browser_use_agent=browser_agent)
        
        # Try to execute a blacklisted tool
        result = self.manager.execute_mcp_tool(
            agent, 
            "mcp_brightdata_scraping_browser_navigate",
            {}
        )
        
        # Should return an error
        self.assertEqual(result["status"], "error")
        self.assertIn("blacklisted", result["message"].lower())
    def test_enable_mcp_tool_blocks_blacklisted(self):
        """Test that enable_mcp_tool blocks blacklisted tools."""
        from api.agent.tools.tool_manager import enable_mcp_tool
        
        # Create a real agent
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(username='blk2@example.com')
        browser_agent = BrowserUseAgent.objects.create(user=user, name="blk2-browser")
        agent = PersistentAgent.objects.create(user=user, name="blk2-agent", charter="T", browser_use_agent=browser_agent)
        
        # Mock the manager initialization
        with patch.object(self.manager, '_initialized', True):
            # Try to enable a blacklisted tool
            result = enable_mcp_tool(agent, "mcp_brightdata_scraping_browser_navigate")
        
        # Should return an error
        self.assertEqual(result["status"], "error")
        self.assertIn("blacklisted", result["message"].lower())
