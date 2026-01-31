"""
Custom browser-use actions for uploading files from the agent filespace.

Refactored to move heavy ORM and storage I/O work into a synchronous thread,
similar to file_download.py. This avoids async ORM issues and keeps the async
action lightweight.

Exposes `register_upload_actions(controller, persistent_agent_id)` which adds:
- upload_file_to_element(index: int, path: str, browser_session: BrowserSession)

The action validates the target DOM element asynchronously, then offloads
filespace checks and temp-file creation to a sync thread. Finally it dispatches
an UploadFileEvent with the prepared temp file to the file input element.
"""

import asyncio
import contextlib
import logging
import os
import tempfile
from typing import Optional, Union
from uuid import UUID

from django.core.files.storage import default_storage
from django.core.exceptions import ObjectDoesNotExist
from opentelemetry import trace
from config import settings

from browser_use.agent.views import ActionResult
from browser_use.browser import BrowserSession
from browser_use.browser.events import UploadFileEvent

from api.services.system_settings import get_max_file_size
from ..files.filespace_service import get_or_create_default_filespace
from ...models import AgentFsNode, AgentFileSpaceAccess, PersistentAgent

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)


# Custom exceptions for file upload operations
class FileUploadError(Exception):
    """Base exception for file upload operations."""
    pass


class FileSizeError(FileUploadError):
    """Exception raised when file size exceeds limits."""
    pass


class FileAccessError(FileUploadError):
    """Exception raised when agent lacks access to file."""
    pass


class FileNotFoundError(FileUploadError):
    """Exception raised when file node is not found."""
    pass


class InvalidFileError(FileUploadError):
    """Exception raised when file is invalid (deleted, not a file, etc.)."""
    pass


class AgentValidationError(FileUploadError):
    """Exception raised when agent validation fails."""
    pass


class FilespaceError(FileUploadError):
    """Exception raised when filespace operations fail."""
    pass


# Constants
BUFFER_SIZE = 64 * 1024  # 64KB buffer for file streaming
FILE_UPLOAD_PREFIX = "agent_upload_"


def _agent_has_filespace_access(agent_id: str, filespace_id: str) -> bool:
    """Check if an agent has access to a specific filespace."""
    try:
        return AgentFileSpaceAccess.objects.filter(
            agent_id=agent_id, filespace_id=filespace_id
        ).exists()
    except (ValueError, TypeError) as e:
        logger.warning("Invalid agent or filespace ID format: %s", e)
        return False
    except Exception as e:
        logger.error("Database error checking filespace access: %s", e)
        return False


@contextlib.contextmanager
def _temp_file_context(node_name: str):
    """Context manager for creating and cleaning up temporary files."""
    suffix = os.path.splitext(node_name)[1] if "." in node_name else ""
    fd, temp_path = tempfile.mkstemp(prefix=FILE_UPLOAD_PREFIX, suffix=suffix)
    os.close(fd)
    
    try:
        yield temp_path
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception as e:
            logger.warning("Failed to cleanup temp file %s: %s", temp_path, e)


def _create_temp_file_from_node(node: AgentFsNode, span) -> str:
    """Create a temporary file from a node's content.

    Returns:
        The path to the created temporary file.
        The caller is responsible for cleaning up the temporary file.
        
    Raises:
        FileSizeError: If file exceeds maximum size during reading.
        FileUploadError: If file preparation fails.
    """
    suffix = os.path.splitext(node.name)[1] if "." in node.name else ""
    fd, temp_path = tempfile.mkstemp(prefix=FILE_UPLOAD_PREFIX, suffix=suffix)
    os.close(fd)

    max_file_size = get_max_file_size()
    try:
        total_bytes = 0
        with default_storage.open(node.content.name, "rb") as src, open(temp_path, "wb") as dst:
            for chunk in iter(lambda: src.read(BUFFER_SIZE), b""):
                dst.write(chunk)
                total_bytes += len(chunk)
                if max_file_size and total_bytes > max_file_size:
                    os.remove(temp_path)  # Clean up the oversized file immediately
                    raise FileSizeError(
                        f"File exceeds maximum allowed size while reading "
                        f"({total_bytes} bytes > {max_file_size} bytes)."
                    )

        span.set_attribute("upload.temp_file_size", total_bytes)
        span.set_attribute("upload.node_id", str(node.id))

        return temp_path

    except FileSizeError:
        raise  # Re-raise FileSizeError as-is
    except Exception as e:
        logger.exception("Failed preparing upload temp file for node %s", getattr(node, 'id', 'unknown'))
        # Ensure cleanup on any exception
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise FileUploadError(f"Failed to prepare file for upload: {e}")


def _validate_agent_and_filespace(persistent_agent_id: Union[str, UUID]) -> tuple[PersistentAgent, object]:
    """Validate agent and get or create default filespace.
    
    Returns:
        Tuple of (agent, filespace).
        
    Raises:
        AgentValidationError: If agent validation fails.
        FilespaceError: If filespace operations fail.
    """
    try:
        agent = PersistentAgent.objects.get(id=persistent_agent_id)
    except ObjectDoesNotExist:
        raise AgentValidationError("Invalid agent ID provided for upload.")
    except Exception as e:
        logger.error("Database error fetching agent %s: %s", persistent_agent_id, e)
        raise AgentValidationError("Failed to validate agent.")
    
    try:
        filespace = get_or_create_default_filespace(agent)
        return agent, filespace
    except Exception as e:
        logger.error("Failed to get or create default filespace for agent %s: %s", persistent_agent_id, e)
        raise FilespaceError(f"Failed to get or create default filespace: {e}")


def _validate_file_node(path: str, filespace, agent) -> AgentFsNode:
    """Validate and retrieve file node.
    
    Returns:
        The validated file node.
        
    Raises:
        FileNotFoundError: If file node is not found.
        InvalidFileError: If file node is invalid (deleted, not a file, no content).
        FileSizeError: If file size exceeds limits.
        FileAccessError: If agent lacks access to file.
    """
    try:
        node = AgentFsNode.objects.get(path=path, filespace=filespace)
    except ObjectDoesNotExist:
        raise FileNotFoundError(f"File node with path {path} not found.")
    except Exception as e:
        logger.error("Database error fetching node with path %s: %s", path, e)
        raise FileUploadError("Failed to retrieve file node.")
    
    if node.is_deleted:
        raise InvalidFileError("File node is deleted.")
    
    if node.node_type != AgentFsNode.NodeType.FILE:
        raise InvalidFileError("Target node is not a file.")
    
    # Size validation
    max_file_size = get_max_file_size()
    if max_file_size and node.size_bytes and node.size_bytes > max_file_size:
        raise FileSizeError(
            f"File too large ({node.size_bytes} bytes). "
            f"Max allowed is {max_file_size} bytes."
        )
    
    # Authorization check
    if not _agent_has_filespace_access(str(agent.id), str(node.filespace_id)):
        raise FileAccessError("Agent lacks access to the file's filespace.")
    
    # Content validation
    if not node.content or not getattr(node.content, "name", None):
        raise InvalidFileError("File has no stored content.")
    
    return node


def register_upload_actions(controller, persistent_agent_id: Union[str, UUID]) -> None:
    """Register file upload actions on the given controller.

    Args:
        controller: The browser-use Controller to register actions on.
        persistent_agent_id: PersistentAgent ID used to authorize access to filespace nodes.
    """

    @controller.action('Upload file to interactive element with file path')
    async def upload_file_to_element(index: int, path: str, browser_session: BrowserSession, available_file_paths: list[str]) -> ActionResult | None:
        """Upload a file from the agent's filespace to a file input element on the page.
        
        Args:
            index: The DOM index of the target file input element
            path: The path to the file in the agent's filespace
            browser_session: The browser session for interacting with the page
            available_file_paths: List of available file paths (unused but kept for compatibility)
            
        Returns:
            ActionResult with success/error information
        """
        if not settings.ALLOW_FILE_UPLOAD:
            return ActionResult(
                extracted_content="Error: File uploads are disabled in this environment.",
                include_in_memory=False,
            )

        with tracer.start_as_current_span("Browser Agent Upload File") as span:
            temp_path: Optional[str] = None
            try:
                span.set_attribute("upload.dom_index", index)
                span.set_attribute("upload.path", path)

                # Locate the DOM element and ensure it's a file input (async work stays here)
                try:
                    dom_element = await browser_session.get_dom_element_by_index(index)
                except Exception as e:
                    return ActionResult(
                        extracted_content=f"Failed to locate element at index {index}: {e}",
                        include_in_memory=False,
                    )

                if dom_element is None:
                    return ActionResult(
                        extracted_content=f"No element found at index {index}.",
                        include_in_memory=False,
                    )

                tag = (dom_element.tag_name or "").lower()
                input_type = (dom_element.attributes or {}).get("type", "").lower()
                if tag != "input" or input_type != "file":
                    return ActionResult(
                        extracted_content=f"Element at index {index} is not a file input element.",
                        include_in_memory=False,
                    )

                # Offload ORM and file I/O to a sync thread
                def _prepare_upload_sync() -> tuple[str, str]:
                    """Prepare a local temp file for upload and return (temp_path, node_name).
                    
                    Raises:
                        FileUploadError: If any step of the upload preparation fails.
                    """
                    with tracer.start_as_current_span("Browser Agent Upload Prepare File") as inner_span:
                        # Validate agent and get filespace
                        agent, filespace = _validate_agent_and_filespace(persistent_agent_id)

                        # Validate and fetch file node
                        node = _validate_file_node(path, filespace, agent)

                        # Create and populate temporary file
                        temp_file_path = _create_temp_file_from_node(node, inner_span)

                        return temp_file_path, node.name

                try:
                    temp_path, node_name = await asyncio.to_thread(_prepare_upload_sync)
                    # Dispatch upload event to the browser session and cleanup
                    event = browser_session.event_bus.dispatch(
                        UploadFileEvent(node=dom_element, file_path=temp_path)
                    )
                    await event
                    message = f"Uploaded '{node_name}' to element index {index}."
                    result = ActionResult(extracted_content=message, include_in_memory=True)
                except FileUploadError as e:
                    result = ActionResult(extracted_content=f"Error: {e}", include_in_memory=False)
                except Exception as e:
                    logger.exception("Unexpected error in upload preparation")
                    result = ActionResult(extracted_content=f"Unexpected error during upload preparation: {e}", include_in_memory=False)

            except Exception as e:
                result = ActionResult(
                    extracted_content=f"Failed to upload file: {e}",
                    include_in_memory=False,
                )

            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception as e:
                        logger.warning("Failed to cleanup temp file %s: %s", temp_path, e)

            return result
