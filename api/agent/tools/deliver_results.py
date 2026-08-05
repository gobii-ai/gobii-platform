"""deliver_results: structured result delivery for templates with a result spec.

Results (candidates, leads, findings) are first-class deliverables, not prose:
the agent hands over structured rows, the timeline renders them as result
cards, and the platform — never the model — decides visibility (the signup
freeze teases the first rows and locks the rest server-side until payment).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

DELIVER_RESULTS_TOOL_NAME = "deliver_results"

RESULTS_PAYLOAD_KEY = "gobii_results"

MAX_RESULT_ROWS = 50
_FIELD_MAX = {"primary": 120, "secondary": 160, "detail": 300, "score": 24, "url": 500}


def get_deliver_results_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": DELIVER_RESULTS_TOOL_NAME,
            "description": (
                "Deliver the session's consolidated result batch (e.g. qualified candidates) to the user as "
                "structured cards in web chat. Use this — not a prose list — to hand over result items, and "
                "deliver ONE consolidated batch per working session: the full rollup of everything that "
                "qualified, best first. Do not drip several small batches; progress along the way goes "
                "through send_chat_message as counts, never identities. Every row must be a real, sourced "
                "item you verified this session; never pad, never invent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short batch title, e.g. '10 qualified candidates — first pass'.",
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "One or two user-facing sentences on how the batch was screened and what to do next. "
                            "Markdown, no raw HTML."
                        ),
                    },
                    "results": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "primary": {"type": "string", "description": "The item itself, e.g. the candidate's name."},
                                "secondary": {"type": "string", "description": "One-line context, e.g. 'Senior Backend Engineer · Plaid'."},
                                "detail": {"type": "string", "description": "Evidence line: why this item qualifies."},
                                "score": {"type": "string", "description": "Optional fit marker: a percentage ('94%') or one-word tier ('strong'). Never a sentence or status phrase; omit when unknown."},
                                "url": {"type": "string", "description": "Optional source URL for the item."},
                            },
                            "required": ["primary"],
                        },
                        "description": f"The result rows, best first, at most {MAX_RESULT_ROWS}.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "true only if work for this active request remains after this delivery.",
                    },
                },
                "required": ["title", "results", "will_continue_work"],
            },
        },
    }


def _normalize_rows(raw_results: Any) -> list[dict[str, str]] | None:
    if not isinstance(raw_results, list) or not raw_results:
        return None
    rows: list[dict[str, str]] = []
    for raw in raw_results[:MAX_RESULT_ROWS]:
        if not isinstance(raw, dict):
            continue
        row: dict[str, str] = {}
        for field, max_len in _FIELD_MAX.items():
            value = str(raw.get(field) or "").strip()
            if value:
                row[field] = value[:max_len]
        if row.get("primary"):
            rows.append(row)
    return rows or None


def execute_deliver_results(agent, params: Dict[str, Any]) -> Dict[str, Any]:
    from api.agent.tools.web_chat_sender import execute_send_chat_message, normalize_llm_output

    rows = _normalize_rows(params.get("results"))
    if rows is None:
        return {
            "status": "error",
            "message": "results must be a non-empty list of rows, each with a non-empty 'primary'.",
            "retryable": False,
        }

    title = normalize_llm_output(str(params.get("title") or "").strip())[:160]
    summary = normalize_llm_output(str(params.get("summary") or "").strip())
    if not title:
        return {"status": "error", "message": "title is required.", "retryable": False}

    body = f"**{title}**"
    if summary:
        body += f"\n\n{summary}"

    result = execute_send_chat_message(
        agent,
        {
            "body": body,
            "will_continue_work": bool(params.get("will_continue_work")),
        },
        extra_raw_payload={
            RESULTS_PAYLOAD_KEY: {
                "title": title,
                "rows": rows,
            }
        },
    )
    if result.get("status") == "ok" and not result.get("skipped"):
        result["message"] = (
            f"Delivered {len(rows)} structured result(s) to web chat as cards. "
            "Do not repeat the rows in a prose message."
        )
    return result
