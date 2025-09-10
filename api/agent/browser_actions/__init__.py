"""
Custom actions for browser use agents.

This package contains custom action implementations that extend 
the default browser use agent capabilities.
"""

from .web_search import register_web_search_action
from .file_download import register_download_listener
from .file_upload import register_upload_actions

__all__ = ['register_web_search_action', 'register_download_listener', 'register_upload_actions']
