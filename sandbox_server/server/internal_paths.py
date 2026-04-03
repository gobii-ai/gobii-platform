"""Sandbox-internal path constants.

Keep this module free of Django app imports so the standalone sandbox image can
boot without the main application package on sys.path.
"""

CUSTOM_TOOL_SQLITE_FILESPACE_PATH = "/.gobii/internal/custom_tool_agent_state.sqlite3"
