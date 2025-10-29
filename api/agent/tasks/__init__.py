"""
Celery tasks for the *persistent-agent* subsystem.

Importing this module ensures that the contained task definitions are picked up
by Celery autodiscovery when Django starts.
"""

# Re-export task symbols so `celery -A proj inspect registered` shows them.
from .process_events import process_agent_events_task, process_agent_cron_trigger_task  # noqa: F401 
from .filespace_imports import import_message_attachments_to_filespace_task  # noqa: F401
from .email_polling import poll_imap_inboxes, poll_imap_inbox  # noqa: F401
from .short_description import generate_agent_short_description_task  # noqa: F401
from .mini_description import generate_agent_mini_description_task  # noqa: F401
