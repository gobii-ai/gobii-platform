"""
Custom browser use agent action for web search using Exa.

This module provides web search functionality for browser use agents,
allowing them to search the web directly without spawning separate tasks.
"""

import logging
from typing import Dict, Any

from opentelemetry import trace
from browser_use import ActionResult
from exa_py import Exa

from config import settings
from ..core.web_search_formatter import format_search_results, format_search_error

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


def register_web_search_action(controller):
    """Register the search_web action with the given controller."""
    
    logger.info("Registering search_web action to controller %s", controller)
    
    @controller.action('Search the web using Exa search engine. Returns relevant web content for the query.')
    def search_web(query: str) -> ActionResult:
        """
        Search the web using Exa search engine.
        
        Args:
            query: Search query string. Be specific and detailed for best results.
            
        Returns:
            ActionResult containing search results with titles, URLs, and content excerpts.
        """
        with tracer.start_as_current_span("Browser Agent Web Search") as span:
            span.set_attribute("search.query", query)
            span.set_attribute("search.engine", "exa")
            
            if not query:
                logger.warning("Empty search query provided")
                return ActionResult(
                    extracted_content="Error: Search query cannot be empty",
                    include_in_memory=False
                )
            
            # Check if Exa API key is configured
            if not settings.EXA_SEARCH_API_KEY:
                logger.error("Exa API key not configured")
                return ActionResult(
                    extracted_content="Error: Web search is not configured on this system",
                    include_in_memory=False
                )
            
            logger.info("Browser agent performing web search: %s", query)
            
            try:
                exa = Exa(api_key=settings.EXA_SEARCH_API_KEY)
                search_result = exa.search_and_contents(
                    query=query,
                    type="auto",
                    num_results=10,
                    context=True,
                    text={
                        "max_characters": 10000
                    }
                )
                
                if not search_result or not search_result.results:
                    span.add_event('No search results found')
                    logger.warning("No search results found for query: %s", query)
                    return ActionResult(
                        extracted_content=format_search_error("No search results found for the given query", query),
                        include_in_memory=True
                    )
                
                # Format results using shared formatter with XML-like tags
                result_text = format_search_results(search_result.results, query)
                result_count = len(search_result.results)
                
                span.set_attribute('search.results.count', result_count)
                span.set_attribute('search.results.total_chars', len(result_text))
                
                logger.info(
                    "Web search returned %d results, %d total chars", 
                    result_count, 
                    len(result_text)
                )
                
                return ActionResult(
                    extracted_content=result_text,
                    include_in_memory=True
                )
                
            except Exception as e:
                span.add_event('Web search failed')
                span.set_attribute('error.message', str(e))
                logger.error("Web search failed: %s", str(e))
                return ActionResult(
                    extracted_content=format_search_error(f"Web search failed: {str(e)}", query),
                    include_in_memory=False
                )