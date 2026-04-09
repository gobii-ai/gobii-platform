import base64
import logging
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, Optional

import httpx
import litellm
from django.db import DatabaseError
from litellm.types.videos.main import VideoObject
from PIL import Image, ImageOps

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
OPENAI_VIDEO_SUPPORTED_SIZES = {
    "720x1280",
    "1280x720",
    "1024x1792",
    "1792x1024",
}
OPENAI_SORA_2_SUPPORTED_SIZES = {
    "720x1280",
    "1280x720",
}


@dataclass(frozen=True)
class GeneratedVideoResult:
    video_bytes: bytes
    mime_type: str
    response: Any


@dataclass(frozen=True)
class ResolvedSourceImage:
    image_bytes: bytes
    mime_type: str
    path: str | None = None
    width: int | None = None
    height: int | None = None


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


def _build_source_image_data_url(source_image: ResolvedSourceImage) -> str:
    encoded = base64.b64encode(source_image.image_bytes).decode("ascii")
    return f"data:{source_image.mime_type};base64,{encoded}"


def _build_source_image_input_reference(
    *,
    model_name: str,
    source_image: ResolvedSourceImage,
) -> Any:
    provider_hint = provider_hint_from_model(model_name)
    data_url = _build_source_image_data_url(source_image)

    if provider_hint == "runwayml":
        return data_url

    if provider_hint in {"gemini", "vertex_ai"}:
        return BytesIO(source_image.image_bytes)

    return BytesIO(source_image.image_bytes)


def _is_openai_sora_model(model_name: str) -> bool:
    provider_hint = provider_hint_from_model(model_name)
    if provider_hint == "openai":
        return True
    return model_name.startswith("sora-")


def _strip_openai_prefix(model_name: str) -> str:
    if model_name.startswith("openai/"):
        return model_name.split("/", 1)[1]
    return model_name


def _get_openai_video_supported_sizes(model_name: str) -> set[str]:
    normalized = _strip_openai_prefix(model_name)
    if normalized == "sora-2":
        return OPENAI_SORA_2_SUPPORTED_SIZES
    return OPENAI_VIDEO_SUPPORTED_SIZES


def _read_image_dimensions(image_bytes: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
    except OSError:
        return None, None
    return int(width), int(height)


def _parse_video_size(size: str) -> tuple[int, int]:
    width_str, height_str = str(size).strip().split("x", 1)
    width = int(width_str)
    height = int(height_str)
    if width <= 0 or height <= 0:
        raise ValueError
    return width, height


def _choose_openai_video_target_size(width: int, height: int, *, supported_sizes: set[str]) -> str:
    source_ratio = width / height
    landscape = width >= height
    candidates: list[str] = []
    for candidate in supported_sizes:
        candidate_width, candidate_height = _parse_video_size(candidate)
        if landscape == (candidate_width >= candidate_height):
            candidates.append(candidate)
    if not candidates:
        candidates = list(OPENAI_VIDEO_SUPPORTED_SIZES)
    return min(
        candidates,
        key=lambda candidate: (
            abs((_parse_video_size(candidate)[0] / _parse_video_size(candidate)[1]) - source_ratio),
            _parse_video_size(candidate)[0] * _parse_video_size(candidate)[1],
            candidate,
        ),
    )


def _normalize_source_image_to_size(
    *,
    source_image: ResolvedSourceImage,
    target_size: str,
) -> ResolvedSourceImage:
    target_width, target_height = _parse_video_size(target_size)
    current_width = source_image.width
    current_height = source_image.height
    if current_width == target_width and current_height == target_height:
        return source_image

    try:
        with Image.open(BytesIO(source_image.image_bytes)) as image:
            image_rgba = image.convert("RGBA")
            contained = ImageOps.contain(image_rgba, (target_width, target_height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 255))
            offset = (
                (target_width - contained.width) // 2,
                (target_height - contained.height) // 2,
            )
            canvas.alpha_composite(contained, dest=offset)
            output = BytesIO()
            canvas.convert("RGB").save(output, format="PNG")
    except OSError as exc:
        raise ValueError("Failed to normalize source_image for Sora image-to-video.") from exc

    return ResolvedSourceImage(
        image_bytes=output.getvalue(),
        mime_type="image/png",
        path=source_image.path,
        width=target_width,
        height=target_height,
    )


def _resolve_openai_video_source_image(
    *,
    model_name: str,
    requested_size: str | None,
    source_image: ResolvedSourceImage | None,
) -> tuple[str | None, ResolvedSourceImage | None]:
    supported_sizes = _get_openai_video_supported_sizes(model_name)
    if source_image is None:
        return requested_size, None

    inferred_size = None
    if source_image.width and source_image.height:
        inferred_size = f"{source_image.width}x{source_image.height}"

    if requested_size:
        normalized_size = str(requested_size).strip()
        if normalized_size not in supported_sizes:
            raise ValueError(
                f"Unsupported size for {_strip_openai_prefix(model_name)} image-to-video: {normalized_size}. "
                f"Supported sizes are {', '.join(sorted(supported_sizes))}."
            )
        return normalized_size, _normalize_source_image_to_size(
            source_image=source_image,
            target_size=normalized_size,
        )

    if inferred_size:
        if inferred_size not in supported_sizes:
            inferred_size = _choose_openai_video_target_size(
                source_image.width,
                source_image.height,
                supported_sizes=supported_sizes,
            )
            return inferred_size, _normalize_source_image_to_size(
                source_image=source_image,
                target_size=inferred_size,
            )
        return inferred_size, source_image

    if requested_size:
        return requested_size, _normalize_source_image_to_size(
            source_image=source_image,
            target_size=requested_size,
        )

    raise ValueError("Unable to determine source_image dimensions for Sora image-to-video.")


def _create_openai_video_job(
    *,
    config,
    prompt: str,
    duration: str | None,
    size: str | None,
    source_image: ResolvedSourceImage | None,
) -> VideoObject:
    params = dict(config.params or {})
    api_key = params.get("api_key")
    if not api_key:
        raise ValueError("OpenAI video generation requires an api_key.")

    api_base = str(params.get("api_base") or "https://api.openai.com/v1").rstrip("/")
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    extra_headers = params.get("extra_headers")
    if isinstance(extra_headers, dict):
        for key, value in extra_headers.items():
            if value is not None:
                headers[str(key)] = str(value)

    payload: Dict[str, Any] = {
        "prompt": prompt,
        "model": _strip_openai_prefix(config.model),
    }
    resolved_size, resolved_source_image = _resolve_openai_video_source_image(
        model_name=config.model,
        requested_size=size,
        source_image=source_image,
    )

    if duration:
        payload["seconds"] = str(duration)
    if resolved_size:
        payload["size"] = resolved_size
    if resolved_source_image is not None:
        payload["input_reference"] = {
            "image_url": _build_source_image_data_url(resolved_source_image),
        }

    timeout = params.get("timeout", MAX_POLL_SECONDS)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{api_base}/videos",
            headers=headers,
            json=payload,
        )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text
        try:
            response_json = response.json()
        except ValueError:
            response_json = None
        if isinstance(response_json, dict):
            error_payload = response_json.get("error")
            if isinstance(error_payload, dict):
                detail = str(error_payload.get("message") or detail)
        raise VideoGenerationResponseError(
            f"Video generation failed: {detail}",
            response=response_json,
        ) from exc

    try:
        response_json = response.json()
    except ValueError as exc:
        raise VideoGenerationResponseError(
            "Video generation returned a non-JSON response",
            response=response.text,
        ) from exc

    try:
        return VideoObject(**response_json)
    except Exception as exc:
        raise VideoGenerationResponseError(
            "Video generation returned an invalid response payload",
            response=response_json,
        ) from exc


def _resolve_source_image(
    *,
    agent: PersistentAgent,
    raw_source: Any,
) -> tuple[ResolvedSourceImage | None, str | None]:
    """Resolve a single filespace image path for image-to-video."""
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
    width, height = _read_image_dimensions(image_bytes)

    return ResolvedSourceImage(
        image_bytes=image_bytes,
        mime_type=mime_type,
        path=node.path,
        width=width,
        height=height,
    ), None


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
        video_obj = litellm.video_status(video_id, **params)
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
    source_image: ResolvedSourceImage | None = None,
) -> GeneratedVideoResult:
    params = dict(config.params or {})
    is_openai_source_image_request = source_image is not None and _is_openai_sora_model(config.model)

    gen_kwargs: Dict[str, Any] = {
        "prompt": prompt,
        "model": config.model,
    }
    if duration:
        gen_kwargs["seconds"] = str(duration)
    if size:
        gen_kwargs["size"] = size
    if source_image is not None and not is_openai_source_image_request:
        gen_kwargs["input_reference"] = _build_source_image_input_reference(
            model_name=config.model,
            source_image=source_image,
        )

    if is_openai_source_image_request:
        video_obj = _create_openai_video_job(
            config=config,
            prompt=prompt,
            duration=duration,
            size=size,
            source_image=source_image,
        )
    else:
        gen_kwargs.update(params)
        video_obj = litellm.video_generation(**gen_kwargs)

    # Poll until completed
    if video_obj.status != "completed":
        video_obj = _wait_for_video_completion(video_obj, params=params)

    video_bytes = litellm.video_content(video_obj.id, **params)
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
    source_image: ResolvedSourceImage | None = None
    raw_source = params.get("source_image")
    if raw_source:
        source_image, source_error = _resolve_source_image(
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
        if source_image is not None and not config.supports_image_to_video:
            errors.append(f"{config.endpoint_key or config.model}: endpoint does not support image-to-video")
            continue
        try:
            generated = _generate_video(
                config,
                prompt=prompt.strip(),
                duration=duration,
                size=size,
                source_image=source_image,
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
        "has_source_image": source_image is not None,
    }
