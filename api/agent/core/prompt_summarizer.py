"""Prompt summarization helper with caching and completion logging."""

import hashlib

from django.db import IntegrityError

from ...models import ContentSummaryCache, PersistentAgentCompletion
from .llm_config import get_summarization_llm_config
from .llm_utils import run_completion
from .token_usage import log_agent_completion

PROMPT_SUMMARY_TYPE = "promptree"
SUMMARY_TIMEOUT = 15

def _default_token_estimator(text: str) -> int:
    return len(text.split())


def _hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PromptSummarizer:
    def __init__(
        self,
        *,
        agent,
        routing_profile=None,
        summary_type: str = PROMPT_SUMMARY_TYPE,
        token_estimator=None,
    ):
        self.agent = agent
        self.routing_profile = routing_profile
        self.summary_type = summary_type
        self.token_estimator = token_estimator or _default_token_estimator

    def __call__(self, text: str, target_tokens: int) -> str:
        return self.summarize(text, target_tokens)

    def summarize(self, text: str, target_tokens: int) -> str:
        if not text:
            return ""
        if target_tokens <= 0:
            return ""

        content_hash = _hash_content(text)
        cached = ContentSummaryCache.objects.filter(
            content_hash=content_hash,
            summary_type=self.summary_type,
        ).only("summary").first()
        if cached:
            return cached.summary

        provider, model, params = get_summarization_llm_config(
            agent=self.agent,
            routing_profile=self.routing_profile,
        )
        params = dict(params or {})
        params.setdefault("timeout", SUMMARY_TIMEOUT)
        max_input_tokens = params.pop("max_input_tokens", None)
        if max_input_tokens:
            estimated_tokens = self.token_estimator(text)
            if estimated_tokens > max_input_tokens:
                return ""

        prompt = [
            {
                "role": "system",
                "content": (
                    "You compress text. Return a concise summary that fits within the token budget. "
                    "Preserve key facts, numbers, identifiers, and structure. Do not add new info."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Token budget: {target_tokens}\n"
                    "Summarize the following text:\n\n"
                    f"{text}"
                ),
            },
        ]
        response = run_completion(
            model=model,
            messages=prompt,
            params=params,
            drop_params=True,
        )
        log_agent_completion(
            self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.PROMPT_SUMMARIZATION,
            response=response,
            model=model,
            provider=provider,
        )
        summary_text = response.choices[0].message.content.strip()
        if not summary_text:
            return ""

        try:
            ContentSummaryCache.objects.create(
                content_hash=content_hash,
                summary_type=self.summary_type,
                summary=summary_text,
            )
        except IntegrityError:
            cached = ContentSummaryCache.objects.filter(
                content_hash=content_hash,
                summary_type=self.summary_type,
            ).only("summary").first()
            if cached:
                return cached.summary

        return summary_text
