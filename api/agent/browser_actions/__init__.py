"""
Custom actions for browser use agents.

This package contains custom action implementations that extend 
the default browser use agent capabilities.
"""

from .web_search import register_web_search_action

__all__ = ['register_web_search_action']
