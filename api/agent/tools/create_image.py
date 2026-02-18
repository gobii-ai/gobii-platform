import base64
import logging
import mimetypes
from typing import Any, Dict, Optional
from urllib.parse import unquote_to_bytes
from urllib.parse import urlparse

import httpx
from django.db import DatabaseError

from api.models import AgentFsNode, PersistentAgent
from api.agent.core.image_generation_config import (
    get_image_generation_llm_configs,
    is_image_generation_configured,
)
from api.agent.core.llm_utils import run_completion
from api.agent.files.attachment_helpers import (
    build_signed_filespace_download_url,
    load_signed_filespace_download_payload,
)
from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.agent.tools.agent_variables import set_agent_variable
from api.agent.tools.file_export_helpers import resolve_export_target

logger = logging.getLogger(__name__)

DEFAULT_ASPECT_RATIO = "1:1"
MAX_SOURCE_IMAGES = 4


def is_image_generation_available_for_agent(agent: Optional[PersistentAgent]) -> bool:
    if agent is None:
        return False
    try:
        return is_image_generation_configured()
    except Exception:
        logger.exception("Failed checking image generation availability")
        return False


def _extract_image_url(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return None

    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    if message is None:
        return None

    images = getattr(message, "images", None)
    if images is None and isinstance(message, dict):
        images = message.get("images")
    if isinstance(images, list):
        for image_entry in images:
            image_url = getattr(image_entry, "image_url", None)
            if image_url is None and isinstance(image_entry, dict):
                image_url = image_entry.get("image_url")

            candidate = None
            if isinstance(image_url, str):
                candidate = image_url.strip()
            elif isinstance(image_url, dict):
                candidate = str(image_url.get("url") or "").strip()
            elif image_url is not None:
                candidate = str(getattr(image_url, "url", "")).strip()

            if candidate:
                return candidate

    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type not in {"image_url", "image", "output_image"}:
                continue

            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                candidate = str(image_url.get("url") or "").strip()
                if candidate:
                    return candidate
            candidate = str(part.get("url") or "").strip()
            if candidate:
                return candidate

    return None


def _decode_data_uri(url: str) -> tuple[bytes, str] | None:
    if not url.startswith("data:") or "," not in url:
        return None

    header, payload = url.split(",", 1)
    mime_part = header[5:]
    if ";" in mime_part:
        mime_type = mime_part.split(";", 1)[0].strip() or "image/png"
    else:
        mime_type = mime_part.strip() or "image/png"
    if not mime_type.startswith("image/"):
        return None
    is_base64 = ";base64" in header.lower()

    if is_base64:
        try:
            return base64.b64decode(payload, validate=True), mime_type
        except (ValueError, TypeError):
            return None
    return unquote_to_bytes(payload), mime_type


def _download_image(url: str) -> tuple[bytes, str] | None:
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
    try:
        response = httpx.get(url, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Failed downloading generated image URL: %s", url, exc_info=True)
        return None

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or "image/png"
    if not content_type.startswith("image/"):
        return None
    return response.content, content_type


def _normalize_aspect_ratio(value: Any) -> str:
    if not isinstance(value, str):
        return DEFAULT_ASPECT_RATIO
    cleaned = value.strip()
    if not cleaned:
        return DEFAULT_ASPECT_RATIO
    if ":" not in cleaned:
        return DEFAULT_ASPECT_RATIO
    left, right = cleaned.split(":", 1)
    if not left.isdigit() or not right.isdigit():
        return DEFAULT_ASPECT_RATIO
    if int(left) <= 0 or int(right) <= 0:
        return DEFAULT_ASPECT_RATIO
    return f"{int(left)}:{int(right)}"


def _extension_for_mime(mime_type: str) -> str:
    guessed = mimetypes.guess_extension(mime_type) or ""
    if guessed == ".jpe":
        return ".jpg"
    return guessed


def _normalize_source_image_reference(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("$[") and value.endswith("]"):
        value = value[2:-1].strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    return value or None


def _extract_signed_token_from_url(url: str) -> str | None:
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "d":
        return parts[1]
    return None


def _normalize_filespace_path(value: str) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    for delimiter in ("?", "#"):
        if delimiter in cleaned:
            cleaned = cleaned.split(delimiter, 1)[0]
    if not cleaned:
        return None
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def _resolve_source_image_path(
    *,
    agent: PersistentAgent,
    filespace,
    source: str,
) -> str | None:
    token = _extract_signed_token_from_url(source)
    if token:
        payload = load_signed_filespace_download_payload(token)
        if not isinstance(payload, dict):
            return None
        if str(payload.get("agent_id") or "") != str(agent.id):
            return None
        node_id = payload.get("node_id")
        if not node_id:
            return None
        node = (
            AgentFsNode.objects.alive()
            .filter(
                id=node_id,
                filespace=filespace,
                node_type=AgentFsNode.NodeType.FILE,
            )
            .only("path")
            .first()
        )
        return node.path if node else None

    if source.startswith(("http://", "https://", "data:", "mailto:", "tel:", "#")):
        return None

    return _normalize_filespace_path(source)


def _resolve_source_image_data_uris(
    *,
    agent: PersistentAgent,
    raw_sources: Any,
) -> tuple[list[str], str | None]:
    if raw_sources is None:
        return [], None

    if isinstance(raw_sources, str):
        requested_sources = [raw_sources]
    elif isinstance(raw_sources, list):
        requested_sources = raw_sources
    else:
        return [], "source_images must be a string or an array of strings."

    if not requested_sources:
        return [], None
    if len(requested_sources) > MAX_SOURCE_IMAGES:
        return [], f"source_images supports up to {MAX_SOURCE_IMAGES} items."

    try:
        filespace = get_or_create_default_filespace(agent)
    except DatabaseError:
        logger.exception("Failed resolving filespace for source images")
        return [], "Unable to resolve the agent filespace for source_images."

    normalized_paths: list[str] = []
    seen_paths: set[str] = set()
    for idx, raw_source in enumerate(requested_sources, start=1):
        if not isinstance(raw_source, str):
            return [], "Each source_images entry must be a string."
        source = _normalize_source_image_reference(raw_source)
        if not source:
            return [], f"source_images[{idx}] is empty."
        path = _resolve_source_image_path(agent=agent, filespace=filespace, source=source)
        if not path:
            return [], (
                f"source_images[{idx}] must reference a filespace image path like "
                "$[/inbox/photo.png] or /inbox/photo.png."
            )
        if path not in seen_paths:
            normalized_paths.append(path)
            seen_paths.add(path)

    nodes = (
        AgentFsNode.objects.alive()
        .filter(
            filespace=filespace,
            path__in=normalized_paths,
            node_type=AgentFsNode.NodeType.FILE,
        )
        .only("id", "path", "mime_type", "content")
    )
    node_by_path = {node.path: node for node in nodes}

    missing = [path for path in normalized_paths if path not in node_by_path]
    if missing:
        return [], f"Source image not found in filespace: {missing[0]}"

    data_uris: list[str] = []
    for path in normalized_paths:
        node = node_by_path[path]
        mime_type = (node.mime_type or "").split(";", 1)[0].strip().lower()
        if not mime_type.startswith("image/"):
            return [], f"Source file must be an image: {path}"
        content_field = getattr(node, "content", None)
        if not content_field or not getattr(content_field, "name", None):
            return [], f"Source image has no stored content: {path}"
        try:
            with content_field.open("rb") as handle:
                content_bytes = handle.read()
        except OSError:
            logger.exception("Failed reading source image %s", path)
            return [], f"Failed reading source image: {path}"
        encoded = base64.b64encode(content_bytes).decode("ascii")
        data_uris.append(f"data:{mime_type};base64,{encoded}")

    return data_uris, None


def _generate_image_bytes(
    config,
    *,
    prompt: str,
    aspect_ratio: str,
    source_image_data_uris: list[str] | None = None,
) -> tuple[bytes, str]:
    if source_image_data_uris:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_url in source_image_data_uris:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                }
            )
        messages = [{"role": "user", "content": content}]
    else:
        messages = [{"role": "user", "content": prompt}]
    params = dict(config.params or {})
    completion_kwargs: Dict[str, Any] = {"modalities": ["image", "text"]}
    if config.supports_image_config:
        completion_kwargs["image_config"] = {"aspect_ratio": aspect_ratio}

    response = run_completion(
        model=config.model,
        messages=messages,
        params=params,
        drop_params=True,
        **completion_kwargs,
    )

    image_url = _extract_image_url(response)
    if not image_url:
        raise ValueError("endpoint returned no image payload")

    decoded = _decode_data_uri(image_url)
    if decoded:
        return decoded

    downloaded = _download_image(image_url)
    if downloaded:
        return downloaded

    raise ValueError("endpoint returned an unsupported image URL format")


def get_create_image_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_image",
            "description": (
                "Generate an image from a text prompt using configured image-generation tiers, "
                "then save it to the agent filespace. "
                "Use for logos, illustrations, banners, concept art, and visual assets. "
                "For transformations of existing images, pass source_images to preserve subject, layout, or brand elements. "
                "Returns `file`, `inline`, `inline_html`, and `attach` placeholders for reuse in messages and documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Natural-language image prompt describing the desired output.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Required filespace path for the generated image "
                            "(recommended: /exports/your-image.png)."
                        ),
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Optional aspect ratio like 1:1, 16:9, 9:16, 4:3 (default: 1:1).",
                    },
                    "source_images": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional filespace image paths to use as references/edit inputs "
                            "(e.g. $[/Inbox/photo.png], /exports/logo.png). "
                            "Use this for image-to-image edits and style transfer."
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


def execute_create_image(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"status": "error", "message": "Missing required parameter: prompt"}

    path, overwrite, error = resolve_export_target(params)
    if error:
        return error

    aspect_ratio = _normalize_aspect_ratio(params.get("aspect_ratio"))
    source_image_data_uris, source_error = _resolve_source_image_data_uris(
        agent=agent,
        raw_sources=params.get("source_images"),
    )
    if source_error:
        return {"status": "error", "message": source_error}
    configs = get_image_generation_llm_configs()
    if not configs:
        return {
            "status": "error",
            "message": "No image generation model is configured. Add an image-generation endpoint and tier first.",
        }

    image_bytes: bytes | None = None
    mime_type: str | None = None
    selected_config = None
    errors: list[str] = []
    for config in configs:
        selected_config = config
        if source_image_data_uris and not config.supports_image_to_image:
            errors.append(f"{config.endpoint_key or config.model}: endpoint does not support image-to-image")
            continue
        try:
            image_bytes, mime_type = _generate_image_bytes(
                config,
                prompt=prompt.strip(),
                aspect_ratio=aspect_ratio,
                source_image_data_uris=source_image_data_uris,
            )
            break
        except ValueError as exc:
            errors.append(f"{config.endpoint_key or config.model}: {exc}")
            logger.info("Image generation attempt failed: %s", errors[-1])
        except Exception as exc:
            errors.append(f"{config.endpoint_key or config.model}: {type(exc).__name__}: {exc}")
            logger.warning("Image generation attempt failed", exc_info=True)

    if image_bytes is None or mime_type is None or selected_config is None:
        detail = errors[-1] if errors else "unknown error"
        return {
            "status": "error",
            "message": f"Image generation failed for all configured endpoints ({detail}).",
        }

    extension = _extension_for_mime(mime_type)
    result = write_bytes_to_dir(
        agent=agent,
        content_bytes=image_bytes,
        extension=extension,
        mime_type=mime_type,
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
        "inline": f"![Generated image]({signed_url})",
        "inline_html": f"<img src='{signed_url}' alt='Generated image' />",
        "attach": var_ref,
        "endpoint_key": selected_config.endpoint_key,
        "model": selected_config.model,
        "source_image_count": len(source_image_data_uris),
    }
