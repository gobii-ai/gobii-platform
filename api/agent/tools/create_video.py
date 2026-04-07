import base64
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import litellm
from django.db import DatabaseError

from api.models import AgentFsNode, PersistentAgent, PersistentAgentCompletion
from api.agent.core.video_generation_config import (
    get_create_video_generation_llm_configs,
    is_create_video_generation_configured,
)
from api.agent.core.provider_hints import provider_hint_from_model
from api.agent.core.token_usage import log_agent_completion
from api.agent.files.attachment_helpers import build_signed_filespace_download_url
from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.agent.tools.agent_variables import set_agent_variable
from api.agent.tools.file_export_helpers import resolve_export_target

logger = logging.getLogger(__name__)

MAX_POLL_SECONDS = 600
POLL_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class GeneratedVideoResult:
    video_bytes: bytes
    mime_type: str
    response: Any


class VideoGenerationResponseError(ValueError):
    def __init__(self, message: str, *, response: Any = None) -> None:
        super().__init__(message)
        self.response = response


def _log_video_generation_completion(
    *,
    agent: PersistentAgent,
    model_name: str,
    response: Any,
) -> None:
    if response is None:
        return
    log_agent_completion(
        agent,
        completion_type=PersistentAgentCompletion.CompletionType.VIDEO_GENERATION,
        response=response,
        model=model_name,
        provider=provider_hint_from_model(model_name),
    )


def is_video_generation_available_for_agent(agent: Optional[PersistentAgent]) -> bool:
    if agent is None:
        return False
    try:
        return is_create_video_generation_configured()
    except Exception:
        logger.exception("Failed checking video generation availability")
        return False


def _resolve_source_image_bytes(
    *,
    agent: PersistentAgent,
    raw_source: Any,
) -> tuple[bytes | None, str | None]:
    """Resolve a single filespace image path to raw bytes for image-to-video."""
    if raw_source is None:
        return None, None

    if not isinstance(raw_source, str) or not raw_source.strip():
        return None, "source_image must be a non-empty string."

    source = raw_source.strip()
    # Strip $[...] wrapper if present
    if source.startswith("$[") and source.endswith("]"):
        source = source[2:-1]

    try:
        filespace = get_or_create_default_filespace(agent)
    except DatabaseError:
        logger.exception("Failed resolving filespace for source image")
        return None, "Unable to resolve the agent filespace for source_image."

    node = (
        AgentFsNode.objects.alive()
        .filter(
            filespace=filespace,
            path=source,
            node_type=AgentFsNode.NodeType.FILE,
        )
        .only("id", "path", "mime_type", "content")
        .first()
    )
    if node is None:
        return None, f"Source image not found in filespace: {source}"

    mime_type = (node.mime_type or "").split(";", 1)[0].strip().lower()
    if not mime_type.startswith("image/"):
        return None, f"Source file must be an image: {source}"

    content_field = getattr(node, "content", None)
    if not content_field or not getattr(content_field, "name", None):
        return None, f"Source image has no stored content: {source}"

    try:
        with content_field.open("rb") as handle:
            image_bytes = handle.read()
    except OSError:
        logger.exception("Failed reading source image %s", source)
        return None, f"Failed reading source image: {source}"

    return image_bytes, None


def _wait_for_video_completion(video_obj, *, params: Dict[str, Any]) -> Any:
    """Poll video_status until the video is completed, failed, or times out."""
    video_id = video_obj.id
    elapsed = 0.0
    while video_obj.status not in ("completed", "failed", "expired"):
        if elapsed >= MAX_POLL_SECONDS:
            raise VideoGenerationResponseError(
                f"Video generation timed out after {MAX_POLL_SECONDS}s",
                response=video_obj,
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
        video_obj = litellm.video_status(
            video_id,
            **{k: v for k, v in params.items() if k in ("api_key", "api_base", "extra_headers")},
        )
    if video_obj.status != "completed":
        error_detail = "unknown error"
        if video_obj.error and isinstance(video_obj.error, dict):
            error_detail = video_obj.error.get("message", error_detail)
        raise VideoGenerationResponseError(
            f"Video generation failed: {error_detail}",
            response=video_obj,
        )
    return video_obj


def _generate_video(
    config,
    *,
    prompt: str,
    duration: str | None = None,
    size: str | None = None,
    source_image_bytes: bytes | None = None,
) -> GeneratedVideoResult:
    params = dict(config.params or {})

    gen_kwargs: Dict[str, Any] = {
        "prompt": prompt,
        "model": config.model,
    }
    if duration:
        gen_kwargs["seconds"] = str(duration)
    if size:
        gen_kwargs["size"] = size
    if source_image_bytes is not None:
        gen_kwargs["input_reference"] = source_image_bytes

    # Pass through auth/base params
    for key in ("api_key", "api_base", "extra_headers"):
        if key in params:
            gen_kwargs[key] = params[key]

    video_obj = litellm.video_generation(**gen_kwargs)

    # Poll until completed
    if video_obj.status != "completed":
        video_obj = _wait_for_video_completion(video_obj, params=params)

    # Fetch the video content bytes
    content_kwargs: Dict[str, Any] = {}
    for key in ("api_key", "api_base", "extra_headers"):
        if key in params:
            content_kwargs[key] = params[key]

    video_bytes = litellm.video_content(video_obj.id, **content_kwargs)
    if not video_bytes:
        raise VideoGenerationResponseError(
            "Video generation returned empty content",
            response=video_obj,
        )

    return GeneratedVideoResult(
        video_bytes=video_bytes,
        mime_type="video/mp4",
        response=video_obj,
    )


def get_create_video_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_video",
            "description": (
                "Generate a video from a text prompt using configured video-generation endpoints, "
                "then save it to the agent filespace. "
                "Use for short video clips, animations, and visual content. "
                "For image-to-video, pass source_image to animate an existing image. "
                "Returns `file` and `attach` placeholders for reuse in messages and documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Natural-language prompt describing the desired video content.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Required filespace path for the generated video "
                            "(recommended: /exports/your-video.mp4)."
                        ),
                    },
                    "duration": {
                        "type": "string",
                        "description": "Optional video duration in seconds (e.g., '5', '10').",
                    },
                    "size": {
                        "type": "string",
                        "description": "Optional resolution like '1920x1080', '1080x1920', '1280x720'.",
                    },
                    "source_image": {
                        "type": "string",
                        "description": (
                            "Optional filespace image path to use as the starting frame "
                            "(e.g. $[/Inbox/photo.png], /exports/logo.png). "
                            "Use this for image-to-video generation."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "When true, overwrites an existing file at file_path.",
                    },
                },
                "required": ["prompt", "file_path"],
            },
        },
    }


def execute_create_video(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"status": "error", "message": "Missing required parameter: prompt"}

    path, overwrite, error = resolve_export_target(params)
    if error:
        return error

    duration = params.get("duration")
    size = params.get("size")

    # Resolve source image for image-to-video
    source_image_bytes: bytes | None = None
    raw_source = params.get("source_image")
    if raw_source:
        source_image_bytes, source_error = _resolve_source_image_bytes(
            agent=agent,
            raw_source=raw_source,
        )
        if source_error:
            return {"status": "error", "message": source_error}

    configs = get_create_video_generation_llm_configs()
    if not configs:
        return {
            "status": "error",
            "message": "No video generation model is configured. Add a video-generation endpoint and tier first.",
        }

    video_bytes: bytes | None = None
    selected_config = None
    errors: list[str] = []
    for config in configs:
        selected_config = config
        if source_image_bytes is not None and not config.supports_image_to_video:
            errors.append(f"{config.endpoint_key or config.model}: endpoint does not support image-to-video")
            continue
        try:
            generated = _generate_video(
                config,
                prompt=prompt.strip(),
                duration=duration,
                size=size,
                source_image_bytes=source_image_bytes,
            )
            _log_video_generation_completion(agent=agent, model_name=config.model, response=generated.response)
            video_bytes = generated.video_bytes
            break
        except VideoGenerationResponseError as exc:
            _log_video_generation_completion(agent=agent, model_name=config.model, response=exc.response)
            errors.append(f"{config.endpoint_key or config.model}: {exc}")
            logger.info("Video generation attempt failed: %s", errors[-1])
        except ValueError as exc:
            errors.append(f"{config.endpoint_key or config.model}: {exc}")
            logger.info("Video generation attempt failed: %s", errors[-1])
        except Exception as exc:
            errors.append(f"{config.endpoint_key or config.model}: {type(exc).__name__}: {exc}")
            logger.warning("Video generation attempt failed", exc_info=True)

    if video_bytes is None or selected_config is None:
        detail = errors[-1] if errors else "unknown error"
        return {
            "status": "error",
            "message": f"Video generation failed for all configured endpoints ({detail}).",
        }

    result = write_bytes_to_dir(
        agent=agent,
        content_bytes=video_bytes,
        extension=".mp4",
        mime_type="video/mp4",
        path=path,
        overwrite=overwrite,
    )
    if result.get("status") != "ok":
        return result

    file_path = result.get("path")
    node_id = result.get("node_id")
    signed_url = build_signed_filespace_download_url(
        agent_id=str(agent.id),
        node_id=node_id,
    )
    set_agent_variable(file_path, signed_url)

    var_ref = f"$[{file_path}]"
    return {
        "status": "ok",
        "file": var_ref,
        "attach": var_ref,
        "endpoint_key": selected_config.endpoint_key,
        "model": selected_config.model,
        "has_source_image": source_image_bytes is not None,
    }
