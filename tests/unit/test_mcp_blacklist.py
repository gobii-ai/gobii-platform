"""Tests for MCP tool blacklist functionality."""

from unittest.mock import Mock, patch, MagicMock, AsyncMock
import asyncio
from django.test import TestCase

from api.agent.tools.mcp_manager import MCPToolManager, MCPToolInfo, MCPServer
from api.models import PersistentAgent


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
        
        # Test non-matching patterns
        self.assertFalse(
            self.manager._is_tool_blacklisted("mcp_brightdata_scrape_as_markdown")
        )
        self.assertFalse(
            self.manager._is_tool_blacklisted("mcp_brightdata_search_engine")
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
            
            # Create a mock server
            server = MCPServer(
                name="brightdata",
                display_name="Bright Data",
                description="Test server",
                command="test"
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
        self.assertEqual(len(tools), 2)
        tool_names = [tool.full_name for tool in tools]
        self.assertIn("mcp_brightdata_scrape_as_markdown", tool_names)
        self.assertIn("mcp_brightdata_search_engine", tool_names)
        self.assertNotIn("mcp_brightdata_scraping_browser_navigate", tool_names)
        self.assertNotIn("mcp_brightdata_scraping_browser_click", tool_names)
    
    def test_execute_mcp_tool_blocks_blacklisted(self):
        """Test that execute_mcp_tool blocks blacklisted tools."""
        # Create a mock agent
        agent = Mock(spec=PersistentAgent)
        agent.enabled_mcp_tools = ["mcp_brightdata_scraping_browser_navigate"]
        
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
        from api.agent.tools.mcp_manager import enable_mcp_tool
        
        # Create a mock agent
        agent = Mock(spec=PersistentAgent)
        agent.enabled_mcp_tools = []
        agent.mcp_tool_usage = {}
        
        # Mock the manager initialization
        with patch.object(self.manager, '_initialized', True):
            # Try to enable a blacklisted tool
            result = enable_mcp_tool(agent, "mcp_brightdata_scraping_browser_navigate")
        
        # Should return an error
        self.assertEqual(result["status"], "error")
        self.assertIn("blacklisted", result["message"].lower())
    
    def test_ensure_default_tools_skips_blacklisted(self):
        """Test that ensure_default_tools_enabled skips blacklisted default tools."""
        from api.agent.tools.mcp_manager import ensure_default_tools_enabled, MCPToolManager, _mcp_manager
        
        # Temporarily add a blacklisted tool to defaults for testing
        original_defaults = MCPToolManager.DEFAULT_ENABLED_TOOLS.copy()
        MCPToolManager.DEFAULT_ENABLED_TOOLS.append("mcp_brightdata_scraping_browser_navigate")
        
        try:
            # Create a mock agent
            agent = Mock(spec=PersistentAgent)
            agent.enabled_mcp_tools = []
            agent.id = 1
            
            # Mock the global manager instance and available tools
            with patch.object(_mcp_manager, '_initialized', True):
                with patch.object(_mcp_manager, 'get_all_available_tools') as mock_get_tools:
                    mock_get_tools.return_value = [
                        MCPToolInfo(
                            full_name="mcp_brightdata_scrape_as_markdown",
                            server_name="brightdata",
                            tool_name="scrape_as_markdown",
                            description="Scrape",
                            parameters={}
                        )
                    ]
                    
                    with patch('api.agent.tools.mcp_manager.enable_mcp_tool') as mock_enable:
                        ensure_default_tools_enabled(agent)
                        
                        # Should only enable non-blacklisted tools
                        mock_enable.assert_called_once_with(agent, "mcp_brightdata_scrape_as_markdown")
        
        finally:
            # Restore original defaults
            MCPToolManager.DEFAULT_ENABLED_TOOLS = original_defaults