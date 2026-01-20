import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from api.agent.core.llm_config import get_llm_config_with_failover, get_required_temperature_for_model
from api.agent.core.llm_utils import run_completion
from api.agent.core.schedule_parser import ScheduleParser
from api.agent.core.token_usage import log_agent_completion
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentEnabledTool,
    PersistentAgentTemplate,
    PublicProfile,
)
from api.public_profiles import (
    generate_handle_suggestion,
    validate_public_handle,
    with_handle_suffix,
)
from util.text_sanitizer import normalize_llm_output

logger = logging.getLogger(__name__)

TEMPLATE_TOOL_NAME = "emit_template"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9'.-]+(?:\s+[A-Za-z0-9'.-]+){0,3}\s+"
    r"(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct)\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s]+")
SENSITIVE_URL_HINTS = ("token", "key", "sig", "secret", "auth")

ALLOWED_CHANNELS = {"email", "sms", "slack", "pagerduty"}


class TemplateCloneError(Exception):
    pass


@dataclass(frozen=True)
class TemplateCloneResult:
    template: PersistentAgentTemplate
    created: bool
    public_profile: PublicProfile


class TemplateCloneService:
    @staticmethod
    def clone_agent_to_template(
        *,
        agent: PersistentAgent,
        user,
        requested_handle: str | None = None,
    ) -> TemplateCloneResult:
        if agent.organization_id is not None:
            raise TemplateCloneError("Organization agents cannot be cloned into public templates.")

        existing = (
            PersistentAgentTemplate.objects.filter(source_agent=agent, created_by=user)
            .select_related("public_profile")
            .order_by("-created_at")
            .first()
        )
        if existing and existing.public_profile:
            return TemplateCloneResult(template=existing, created=False, public_profile=existing.public_profile)

        public_profile = TemplateCloneService._resolve_public_profile(user, requested_handle)

        payload = TemplateCloneService._build_template_payload(agent)
        generated = TemplateCloneService._generate_template(agent, payload)
        cleaned = TemplateCloneService._sanitize_template_payload(generated, payload)

        template_slug = TemplateCloneService._generate_template_slug(public_profile, cleaned.get("display_name"))
        template_code = TemplateCloneService._generate_template_code()

        with transaction.atomic():
            template = PersistentAgentTemplate.objects.create(
                code=template_code,
                public_profile=public_profile,
                slug=template_slug,
                source_agent=agent,
                created_by=user,
                display_name=cleaned["display_name"],
                tagline=cleaned["tagline"],
                description=cleaned["description"],
                charter=cleaned["charter"],
                base_schedule=cleaned.get("base_schedule", ""),
                schedule_jitter_minutes=cleaned.get("schedule_jitter_minutes", 0),
                event_triggers=cleaned.get("event_triggers", []),
                default_tools=cleaned.get("default_tools", []),
                recommended_contact_channel=cleaned.get("recommended_contact_channel", "email"),
                category=cleaned.get("category", "Custom"),
            )

        return TemplateCloneResult(template=template, created=True, public_profile=public_profile)

    @staticmethod
    def _resolve_public_profile(user, requested_handle: str | None) -> PublicProfile:
        profile = PublicProfile.objects.filter(user=user).first()
        if profile:
            if requested_handle and requested_handle != profile.handle:
                raise TemplateCloneError("Public profile handle already set and cannot be changed.")
            return profile

        handle = requested_handle or generate_handle_suggestion()
        handle = validate_public_handle(handle)

        candidate = handle
        suffix = 1
        while PublicProfile.objects.filter(handle=candidate).exists():
            suffix += 1
            candidate = with_handle_suffix(handle, suffix)
            candidate = validate_public_handle(candidate)

        profile = PublicProfile(user=user, handle=candidate)
        profile.full_clean()
        profile.save()
        return profile

    @staticmethod
    def _build_template_payload(agent: PersistentAgent) -> dict[str, Any]:
        enabled_tools = list(
            PersistentAgentEnabledTool.objects.filter(agent=agent)
            .values_list("tool_full_name", flat=True)
        )
        schedule_snapshot = agent.schedule_snapshot or agent.schedule or ""
        preferred_channel = "email"
        preferred_endpoint = getattr(agent, "preferred_contact_endpoint", None)
        if preferred_endpoint and preferred_endpoint.channel:
            preferred_channel = str(preferred_endpoint.channel).lower()
            if preferred_endpoint.channel == CommsChannel.SMS:
                preferred_channel = "sms"
        if preferred_channel not in ALLOWED_CHANNELS:
            preferred_channel = "email"

        return {
            "charter": TemplateCloneService._redact_obvious_pii(agent.charter or ""),
            "schedule_snapshot": schedule_snapshot,
            "enabled_tools": enabled_tools,
            "preferred_channel": preferred_channel,
        }

    @staticmethod
    def _generate_template(agent: PersistentAgent, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = TemplateCloneService._build_prompt(payload)
        provider, model, params = TemplateCloneService._get_llm_config(agent)
        tools = [TemplateCloneService._build_emit_tool_def()]

        run_kwargs: dict[str, Any] = {}
        if params.get("supports_tool_choice", True):
            run_kwargs["tool_choice"] = {"type": "function", "function": {"name": TEMPLATE_TOOL_NAME}}

        try:
            response = run_completion(
                model=model,
                messages=prompt,
                params=params,
                tools=tools,
                drop_params=True,
                **run_kwargs,
            )
        except Exception as exc:
            logger.exception("Template clone LLM call failed")
            raise TemplateCloneError("Template generation failed.") from exc

        log_agent_completion(
            agent,
            completion_type=PersistentAgentCompletion.CompletionType.TEMPLATE_CLONE,
            response=response,
            model=model,
            provider=provider,
        )

        tool_payload = TemplateCloneService._extract_tool_payload(response)
        if not tool_payload:
            raise TemplateCloneError("Template generation did not return the required tool output.")
        return tool_payload

    @staticmethod
    def _build_prompt(payload: dict[str, Any]) -> list[dict[str, str]]:
        enabled_tools = payload.get("enabled_tools") or []
        schedule_snapshot = payload.get("schedule_snapshot") or ""
        preferred_channel = payload.get("preferred_channel") or "email"
        charter = payload.get("charter") or ""

        system_prompt = (
            "You create public Gobii templates by generalizing an agent. "
            "Remove private details, PII, secrets, and identifiers. "
            "No spam, harassment, illegal, or harmful content. "
            "Do not include names, emails, phone numbers, addresses, or URLs with tokens. "
            "Keep the output concise and professional. "
            "Always respond by calling the emit_template tool with the template fields."
        )

        user_prompt = (
            "Agent charter (sanitized):\n"
            f"{charter}\n\n"
            "Schedule snapshot:\n"
            f"{schedule_snapshot or 'None'}\n\n"
            "Enabled tools (choose only from this list):\n"
            f"{', '.join(enabled_tools) if enabled_tools else 'None'}\n\n"
            "Preferred contact channel:\n"
            f"{preferred_channel}\n\n"
            "Create a reusable template with a generic charter, schedule, and tooling."
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _build_emit_tool_def() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": TEMPLATE_TOOL_NAME,
                "description": "Emit the sanitized public template definition.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "display_name": {"type": "string", "description": "Short public name."},
                        "tagline": {"type": "string", "description": "One-line benefit statement."},
                        "description": {"type": "string", "description": "Public description of the template."},
                        "charter": {"type": "string", "description": "Generic charter text."},
                        "base_schedule": {"type": "string", "description": "Cron or interval schedule."},
                        "schedule_jitter_minutes": {"type": "integer", "minimum": 0, "maximum": 120},
                        "default_tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Enabled tool names (subset of provided list).",
                        },
                        "recommended_contact_channel": {
                            "type": "string",
                            "enum": sorted(ALLOWED_CHANNELS),
                        },
                        "category": {"type": "string", "description": "Short category label."},
                        "event_triggers": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["type", "name"],
                            },
                        },
                    },
                    "required": [
                        "display_name",
                        "tagline",
                        "description",
                        "charter",
                        "base_schedule",
                        "schedule_jitter_minutes",
                        "default_tools",
                        "recommended_contact_channel",
                        "category",
                        "event_triggers",
                    ],
                },
            },
        }

    @staticmethod
    def _extract_tool_payload(response: Any) -> dict[str, Any] | None:
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        for tool_call in tool_calls:
            function_block = getattr(tool_call, "function", None) or tool_call.get("function")
            if not function_block:
                continue
            function_name = getattr(function_block, "name", None) or function_block.get("name")
            if function_name != TEMPLATE_TOOL_NAME:
                continue
            raw_args = getattr(function_block, "arguments", None) or function_block.get("arguments") or "{}"
            if isinstance(raw_args, dict):
                return raw_args
            try:
                return json.loads(raw_args)
            except Exception:
                logger.warning("Failed to parse emit_template tool arguments")
                return None
        return None

    @staticmethod
    def _sanitize_template_payload(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        display_name = TemplateCloneService._clean_text(payload.get("display_name"), fallback="Custom Template")
        tagline = TemplateCloneService._clean_text(payload.get("tagline"), fallback="A reusable agent template")
        description = TemplateCloneService._clean_text(payload.get("description"), fallback=tagline)
        charter = TemplateCloneService._clean_text(payload.get("charter"), fallback=context.get("charter") or "")
        category = TemplateCloneService._clean_text(payload.get("category"), fallback="Custom")

        base_schedule = (payload.get("base_schedule") or "").strip()
        if base_schedule:
            try:
                ScheduleParser.parse(base_schedule)
            except Exception:
                base_schedule = ""

        jitter = payload.get("schedule_jitter_minutes")
        try:
            jitter_val = int(jitter)
        except (TypeError, ValueError):
            jitter_val = 0
        jitter_val = max(min(jitter_val, 120), 0)

        allowed_tools = set(context.get("enabled_tools") or [])
        default_tools = [
            tool for tool in (payload.get("default_tools") or [])
            if isinstance(tool, str) and tool in allowed_tools
        ]

        recommended_channel = (payload.get("recommended_contact_channel") or "email").lower()
        if recommended_channel not in ALLOWED_CHANNELS:
            recommended_channel = "email"

        event_triggers = []
        for trigger in payload.get("event_triggers") or []:
            if not isinstance(trigger, dict):
                continue
            trigger_type = TemplateCloneService._clean_text(trigger.get("type"), fallback="")
            trigger_name = TemplateCloneService._clean_text(trigger.get("name"), fallback="")
            trigger_desc = TemplateCloneService._clean_text(trigger.get("description"), fallback="")
            if not trigger_type or not trigger_name:
                continue
            event_triggers.append({
                "type": trigger_type,
                "name": trigger_name,
                "description": trigger_desc,
            })

        cleaned = {
            "display_name": display_name,
            "tagline": tagline,
            "description": description,
            "charter": charter,
            "base_schedule": base_schedule,
            "schedule_jitter_minutes": jitter_val,
            "default_tools": default_tools,
            "recommended_contact_channel": recommended_channel,
            "category": category,
            "event_triggers": event_triggers,
        }

        return TemplateCloneService._redact_template_pii(cleaned)

    @staticmethod
    def _clean_text(value: Any, fallback: str = "") -> str:
        if not isinstance(value, str):
            value = fallback
        cleaned = normalize_llm_output(value).strip()
        return cleaned or fallback

    @staticmethod
    def _redact_template_pii(payload: dict[str, Any]) -> dict[str, Any]:
        text_fields = ["display_name", "tagline", "description", "charter", "category"]
        for field in text_fields:
            payload[field] = TemplateCloneService._redact_obvious_pii(payload.get(field, ""))
        return payload

    @staticmethod
    def _redact_obvious_pii(text: str) -> str:
        if not text:
            return ""
        masked = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
        masked = PHONE_RE.sub("[REDACTED_PHONE]", masked)
        masked = ADDRESS_RE.sub("[REDACTED_ADDRESS]", masked)
        masked = TemplateCloneService._mask_sensitive_urls(masked)
        return masked

    @staticmethod
    def _mask_sensitive_urls(text: str) -> str:
        def replace(match: re.Match) -> str:
            url = match.group(0)
            lowered = url.lower()
            if "?" in url or any(hint in lowered for hint in SENSITIVE_URL_HINTS):
                return "[REDACTED_URL]"
            return url

        return URL_RE.sub(replace, text)

    @staticmethod
    def _generate_template_slug(profile: PublicProfile, display_name: str | None) -> str:
        base = slugify(display_name or "template") or "template"
        base = base[:80].strip("-") or "template"

        candidate = base
        suffix = 1
        while PersistentAgentTemplate.objects.filter(public_profile=profile, slug=candidate).exists():
            suffix += 1
            suffix_text = f"-{suffix}"
            max_base_len = 80 - len(suffix_text)
            trimmed = base[:max_base_len].strip("-") or "template"
            candidate = f"{trimmed}{suffix_text}"
        return candidate

    @staticmethod
    def _generate_template_code() -> str:
        for _ in range(5):
            candidate = f"tpl-{uuid.uuid4().hex[:12]}"
            if not PersistentAgentTemplate.objects.filter(code=candidate).exists():
                return candidate
        raise TemplateCloneError("Unable to generate a unique template code.")

    @staticmethod
    def _get_llm_config(agent: PersistentAgent) -> tuple[str, str, dict[str, Any]]:
        configs = get_llm_config_with_failover(agent_id=str(agent.id), token_count=0, agent=agent)
        provider, model, params = configs[0]
        params = dict(params or {})

        supports_temperature = params.get("supports_temperature", True)
        if supports_temperature:
            params.setdefault("temperature", 0)
            required_temp = get_required_temperature_for_model(model)
            if required_temp is not None:
                params["temperature"] = required_temp
        else:
            params.pop("temperature", None)

        return provider, model, params
