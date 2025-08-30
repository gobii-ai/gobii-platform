"""
Web search tool for persistent agents.

This module provides web search functionality for persistent agents using Exa,
including tool definition and execution logic.
"""

import logging
from typing import Dict, Any

from opentelemetry import trace
from django.db import transaction

from tasks.services import TaskCreditService
from ...models import PersistentAgent
from config import settings
from ..core.web_search_formatter import format_search_results, format_search_error

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


def get_search_web_tool() -> Dict[str, Any]:
    """Return the search_web tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web using an search engine. THIS MAY RETURN OUTDATED INFORMATION, SO YOU MAY NEED TO INSTEAD SEARCH FOR SOURCES AND USE THOSE SOURCES FOR UP-TO-DATE INFORMATION. ",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. Be very specific and detailed in your search query. Search cannot return realtime up-to-date info. You can only query pre-indexed information.",
                    }
                },
                "required": ["query"],
            },
        },
    }


@tracer.start_as_current_span("AGENT TOOL Search Web")
def execute_search_web(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Search the web using an LLM-friendly search engine. Currently, this uses Exa. We can integrate others later, and
    may make it a rotation. Consumes a task credit like other web tasks.
    """
    span = trace.get_current_span()
    query = params.get("query")
    span.set_attribute("persistent_agent.id", str(agent.id))
    span.set_attribute("search.query", query)

    if not query:
        span.add_event('Missing query parameter')
        logger.warning("Missing query parameter")
        return {"status": "error", "message": "Missing required parameter: query"}

    # Log search attempt
    logger.info(
        "Agent %s performing web search: %s",
        agent.id, query
    )

    # TEMPORARILY DISABLED: Task credit consumption for web search tool usage
    # Check and consume task credit before performing search
    # TODO: If re-added, make sure we update for organizations, too
    user = agent.user
    if user:
        # with transaction.atomic():
        #     # Use consolidated credit checking and consumption logic
        #     result = TaskCreditService.check_and_consume_credit(user)
            
        #     if not result['success']:
        #         logger.warning(f"User {user.id} attempted web search with insufficient credits")
        #         return {
        #             "status": "error", 
        #             "message": result['error_message']
        #         }
            
        logger.info(f"Performing web search for user {user.id} - credit check temporarily disabled")
    else:
        # For agents without users (e.g., system agents), skip credit check
        logger.info("Performing web search for agent without user - skipping credit check")

    with tracer.start_as_current_span("EXA Search") as exa_span:
        from exa_py import Exa
        exa_span.set_attribute("persistent_agent.id", str(agent.id))
        exa_span.set_attribute("search.query", query)

        exa = Exa(api_key=settings.EXA_SEARCH_API_KEY)
        try:
            search_result = exa.search_and_contents(
                query=query,
                type="auto",
                num_results=10,
                context=True,
                text={
                    "max_characters": 10000
                }
            )
        except Exception as e:
            exa_span.add_event('Exa Search failure')
            logger.error(f"Search failure: {e}", query)
            return {"status": "error", "message": format_search_error(f"Search failure with Exa: {e}", query)}

        if not search_result:
            exa_span.add_event('Exa Search failure')
            logger.error("Search failure", query)
            return {"status": "error", "message": format_search_error("Search failure with Exa", query)}
        else:
            result_count = len(search_result.results)
            exa_span.set_attribute('search.results.count', len(search_result.results))

            if result_count == 0:
                exa_span.add_event('No search results found')
                logger.warning("No search results found for query: %s", query)
                return {"status": "error", "message": format_search_error("No search results found", query)}

    # Format results using shared formatter with XML-like tags
    result_text = format_search_results(search_result.results, query)

    # Log search success
    total_chars = len(result_text)
    logger.info(
        "Agent %s web search returned %d results, %d total chars",
        agent.id, result_count, total_chars
    )

    return {
        "status": "ok",
        "result": result_text
    } 