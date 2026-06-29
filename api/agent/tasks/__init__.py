"""
Celery tasks for the *persistent-agent* subsystem.

Importing this module ensures that the contained task definitions are picked up
by Celery autodiscovery when Django starts.
"""

# Re-export task symbols so `celery -A proj inspect registered` shows them.
from .process_events import (  # noqa: F401
    enqueue_interactive_process_agent_events,
    process_agent_cron_trigger_task,
    process_discord_inbound_debounce_task,
    process_agent_events_task,
    queue_agent_process_events_batch_task,
    process_planning_timeout_task,
    process_unseen_web_chat_followup_task,
)
from .filespace_imports import import_message_attachments_to_filespace_task  # noqa: F401
from .email_polling import poll_imap_inboxes, poll_imap_inbox  # noqa: F401
from .short_description import generate_agent_short_description_task  # noqa: F401
from .mini_description import generate_agent_mini_description_task  # noqa: F401
from .agent_tags import generate_agent_tags_task  # noqa: F401
from .agent_avatar import (  # noqa: F401
    generate_agent_avatar_task,
    generate_agent_visual_description_task,
)
from .reported_message_judge import run_reported_agent_judge_task  # noqa: F401
