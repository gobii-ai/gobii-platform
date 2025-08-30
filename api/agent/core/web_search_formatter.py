"""
Shared formatting utilities for web search results.

This module provides consistent formatting for web search results
across different agent types (persistent agents and browser agents).
"""

import logging
from typing import List, Any

logger = logging.getLogger(__name__)


def format_search_results(results: List[Any], query: str = "") -> str:
    """
    Format web search results with XML-like tags for consistent output.
    
    Args:
        results: List of search result objects with title, url, text, and published_date attributes
        query: Optional search query to include in the formatted output
        
    Returns:
        Formatted string with XML-like tags containing all search results
    """
    if not results:
        disclaimer_text = ("This information represents the latest available content at the time of search index creation. "
                         "If more recent or real-time information is needed, please look up the information directly "
                         "via web browser navigation or API calls to the original sources.")
        return "<search_results>\n<query>{}</query>\n<message>No search results found</message>\n<disclaimer>\n{}\n</disclaimer>\n</search_results>".format(query, disclaimer_text)
    
    formatted_output = "<search_results>\n"
    
    if query:
        formatted_output += f"<query>{query}</query>\n"
    
    formatted_output += f"<result_count>{len(results)}</result_count>\n\n"
    
    for i, result in enumerate(results, 1):
        formatted_output += f"<result number=\"{i}\">\n"
        formatted_output += f"  <title>{result.title or 'No title'}</title>\n"
        formatted_output += f"  <url>{result.url or 'No URL'}</url>\n"
        
        if hasattr(result, 'published_date') and result.published_date:
            formatted_output += f"  <published_date>{result.published_date}</published_date>\n"
        
        formatted_output += f"  <content>\n{result.text or 'No content available'}\n  </content>\n"
        formatted_output += "</result>\n\n"
    
    formatted_output += "<disclaimer>\n"
    formatted_output += "This information represents the latest available content at the time of search index creation. "
    formatted_output += "If more recent or real-time information is needed, please look up the information directly "
    formatted_output += "via web browser navigation or API calls to the original sources.\n"
    formatted_output += "</disclaimer>\n\n"
    formatted_output += "</search_results>"
    
    return formatted_output


def format_search_error(error_message: str, query: str = "") -> str:
    """
    Format a search error with XML-like tags.
    
    Args:
        error_message: The error message to format
        query: Optional search query that caused the error
        
    Returns:
        Formatted error string with XML-like tags
    """
    formatted_output = "<search_results>\n"
    
    if query:
        formatted_output += f"<query>{query}</query>\n"
    
    formatted_output += f"<error>{error_message}</error>\n"
    formatted_output += "<disclaimer>\n"
    formatted_output += "This information represents the latest available content at the time of search index creation. "
    formatted_output += "If more recent or real-time information is needed, please look up the information directly "
    formatted_output += "via web browser navigation or API calls to the original sources.\n"
    formatted_output += "</disclaimer>\n"
    formatted_output += "</search_results>"
    
    return formatted_output