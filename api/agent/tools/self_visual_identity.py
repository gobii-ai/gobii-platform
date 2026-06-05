"""Just-in-time self visual identity retrieval for agent media generation."""

import re
from typing import Any, Dict

from api.agent.avatar import prepare_visual_description
from api.models import PersistentAgent


GET_SELF_VISUAL_IDENTITY_TOOL_NAME = "get_self_visual_identity"
SELF_IMAGE_GENERATION_SYSTEM_SKILL_KEY = "self_image_generation"
SELF_VIDEO_GENERATION_SYSTEM_SKILL_KEY = "self_video_generation"

_DIRECT_SELF_IMAGE_TERMS = (
    "selfie",
    "self portrait",
    "self-portrait",
    "self video",
)
_IMAGE_TERMS = (
    "animation",
    "avatar",
    "clip",
    "headshot",
    "image",
    "illustration",
    "picture",
    "photo",
    "portrait",
    "profile photo",
    "profile picture",
    "render",
    "short video",
    "video",
    "visual",
)
_SELF_REFERENCE_TERMS = (
    "of you",
    "the agent",
    "the gobii",
    "this agent",
    "this gobii",
    "you look",
    "your avatar",
    "your face",
    "your own",
    "your visual identity",
    "yourself",
)


def _normalize_prompt_text(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", (text or "").strip().lower())
    return f" {collapsed} "


def prompt_requests_self_visual_identity(prompt: str) -> bool:
    """Return true when a media prompt asks to depict this agent itself."""

    normalized = _normalize_prompt_text(prompt)
    if any(term in normalized for term in _DIRECT_SELF_IMAGE_TERMS):
        return True
    return any(term in normalized for term in _IMAGE_TERMS) and any(
        term in normalized for term in _SELF_REFERENCE_TERMS
    )


def build_self_visual_identity_prompt_fragment(agent: PersistentAgent) -> str:
    """Build a bounded prompt fragment containing only self-image visual identity."""

    visual_description = prepare_visual_description(agent.visual_description or "")
    if not visual_description:
        return ""

    agent_name = (agent.name or "Gobii").strip() or "Gobii"
    return (
        "Stable Gobii visual identity for this self visual media request only:\n"
        f"- Name: {agent_name}\n"
        f"- Visual description: {visual_description}\n"
        "Use this to depict the Gobii itself. Do not add unrelated private or internal details."
    )


def augment_prompt_with_self_visual_identity(
    agent: PersistentAgent,
    prompt: str,
) -> tuple[str, bool]:
    """Append visual identity only for self visual media prompts."""

    cleaned_prompt = (prompt or "").strip()
    if not prompt_requests_self_visual_identity(cleaned_prompt):
        return cleaned_prompt, False

    visual_description = prepare_visual_description(agent.visual_description or "")
    if visual_description and visual_description.lower() in cleaned_prompt.lower():
        return cleaned_prompt, True

    fragment = build_self_visual_identity_prompt_fragment(agent)
    if not fragment:
        return cleaned_prompt, False

    return f"{cleaned_prompt}\n\n{fragment}", True


def get_self_visual_identity_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": GET_SELF_VISUAL_IDENTITY_TOOL_NAME,
            "description": (
                "Retrieve this Gobii's own stable visual description for selfie, avatar, portrait, "
                "profile-photo, self-video, or other self visual media generation. Use only when an image "
                "or video should depict this Gobii itself; do not use for ordinary text tasks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "purpose": {
                        "type": "string",
                        "description": (
                            "Brief reason for retrieval, for example 'generate and send a selfie'."
                        ),
                    },
                },
            },
        },
    }


def execute_get_self_visual_identity(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    visual_description = prepare_visual_description(agent.visual_description or "")
    if not visual_description:
        return {
            "status": "error",
            "message": "No visual description is available for this Gobii yet.",
        }

    agent_name = (agent.name or "Gobii").strip() or "Gobii"
    return {
        "status": "ok",
        "agent_name": agent_name,
        "visual_description": visual_description,
        "image_prompt_fragment": build_self_visual_identity_prompt_fragment(agent),
        "usage": (
            "Use this visual_description only in the image/video prompt for this Gobii's own selfie/avatar/"
            "portrait/self-video. Do not expose unrelated private or internal details."
        ),
    }
