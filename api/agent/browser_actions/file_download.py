"""
Event-driven browser-use actions for capturing downloads into the agent filespace.

Refactored to follow the pattern in file_download_example.py by listening for
`FileDownloadedEvent` on the `BrowserSession` and then saving the resulting file
to the agent's default filespace under a top-level directory named "downloads".

Provides `register_download_listener(browser_session, persistent_agent_id)` which
attaches a listener to persist downloaded files automatically.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
from typing import Optional
import logging

from django.core.files import File as DjangoFile
from django.utils.text import get_valid_filename
from opentelemetry import trace
from config import settings

from browser_use import BrowserSession
from browser_use.browser.events import FileDownloadedEvent

from api.services.system_settings import get_max_file_size
from ...models import AgentFsNode, PersistentAgent
from ..files.filespace_service import (
    get_or_create_default_filespace,
    get_or_create_dir,
    dedupe_name,
)

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)


def _compute_sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def register_download_listener(browser_session: BrowserSession, persistent_agent_id: str) -> None:
    """Attach a FileDownloadedEvent listener that persists files to the agent filespace."""
    if not settings.ALLOW_FILE_DOWNLOAD:
        return

    def _handle_download_sync(evt: FileDownloadedEvent) -> None:
        """Synchronous handler that performs ORM and file I/O work.

        Runs inside a thread via asyncio.to_thread to avoid async ORM issues.
        """
        with tracer.start_as_current_span("Browser Agent Download Listener") as span:
            # Validate agent (ORM access must be sync-only)
            try:
                agent = PersistentAgent.objects.get(id=persistent_agent_id)
            except PersistentAgent.DoesNotExist:
                return  # Cannot save without agent

            local_path: Optional[str] = getattr(evt, "path", None)
            file_name: Optional[str] = getattr(evt, "file_name", None)
            mime_type: Optional[str] = getattr(evt, "mime_type", None)
            reported_size: Optional[int] = getattr(evt, "file_size", None)
            span.set_attribute("download.event.file_name", file_name or "")
            span.set_attribute("download.event.path", local_path or "")
            span.set_attribute("download.event.mime_type", mime_type or "")
            if reported_size is not None:
                span.set_attribute("download.event.size", reported_size)

            if not local_path or not os.path.exists(local_path):
                return

            try:
                actual_size = os.path.getsize(local_path)
                max_file_size = get_max_file_size()
                if max_file_size and actual_size > max_file_size:
                    span.set_attribute("download.rejected_too_large", True)
                    return

                target_name = get_valid_filename(file_name or os.path.basename(local_path) or "download")
                if not mime_type:
                    mime_type = mimetypes.guess_type(target_name)[0] or "application/octet-stream"

                # Filespace/FS node operations involve ORM; keep in sync context
                fs = get_or_create_default_filespace(agent)
                downloads_dir = get_or_create_dir(fs, None, "downloads")
                name = dedupe_name(fs, downloads_dir, target_name)

                checksum = _compute_sha256_file(local_path)

                node = AgentFsNode(
                    filespace=fs,
                    parent=downloads_dir,
                    node_type=AgentFsNode.NodeType.FILE,
                    name=name,
                    created_by_agent=agent,
                    mime_type=mime_type,
                    checksum_sha256=checksum,
                )
                node.save()

                try:
                    with open(local_path, "rb") as f_in:
                        node.content.save(name, DjangoFile(f_in), save=True)
                    node.refresh_from_db()
                    span.set_attribute("download.size_bytes", node.size_bytes or actual_size)
                    span.set_attribute("download.filespace_node_id", str(node.id))
                    span.set_attribute("download.filespace_path", node.path)
                except Exception as e:
                    span.set_attribute("error.message", str(e))
                    # Also log with stack trace for visibility in standard logs
                    logger.exception(
                        "Failed to persist downloaded file to filespace for agent %s (name=%s, path=%s)",
                        agent.id,
                        name,
                        local_path,
                    )
                    return
            finally:
                # Always attempt to remove the temporary downloaded file
                try:
                    if local_path and os.path.exists(local_path):
                        os.remove(local_path)
                        span.set_attribute("download.temp_file_removed", True)
                except Exception as cleanup_err:
                    # Do not raise; just record cleanup failure
                    span.set_attribute("download.temp_file_remove_failed", str(cleanup_err))

    async def on_download(evt: FileDownloadedEvent):
        # Offload all sync ORM and file work to a thread
        await asyncio.to_thread(_handle_download_sync, evt)

    browser_session.event_bus.on(FileDownloadedEvent, on_download)
