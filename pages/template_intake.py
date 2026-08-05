"""Template intake: pre-signup brief quiz for hireable templates.

A template with an intake schema routes its hire CTA through a short
schema-driven quiz (click-through defaults everywhere) whose answers become
the agent's first input: a structured brief message, a charter override, and
a briefing artifact the timeline renders as a card.

The schemas live in code for now, keyed by template code, as the spec for a
future PersistentAgentTemplate.intake_schema field. Question kinds are chosen
by who can legitimately know the answer:

  text   — ghost sample only; untouched -> assumed, the agent confirms in chat
  tags   — rapid-add capture of the user's own words; empty -> the agent asks
  choice — options the template author legitimately knows; honest defaults
           (delivery always ladders email -> sheet -> integrated -> other)
"""

from __future__ import annotations

SKIP_VALUE = "__skip__"

# Session keys shared between the brief view and agent creation.
BRIEFING_PAYLOAD_SESSION_KEY = "agent_briefing_payload"
INTAKE_ANSWERS_SESSION_KEY = "agent_intake_answers"
PROSPECTIVE_NAME_SESSION_KEY = "prospective_agent_name"

INTAKE_SCHEMAS = {
    "ai-agent-for-candidate-sourcing": {
        "templateName": "Candidate Sourcing",
        "briefNoun": "Hiring brief",
        "accent": "recruiting agent",
        "pasteLabel": "job post",
        # Outcome shown on the plan gate. ESTIMATE by design — figures are
        # template-calibrated, clearly labeled as estimates in the UI, and
        # to be replaced with measured tasks-per-outcome from analytics.
        "outcome": {
            "unit": "qualified candidates",
            "per": "day",
            "startup": 4,
            "scale": 12,
        },
        "questions": [
            {
                "id": "role",
                "eyebrow": "The role",
                "q": "What are you hiring for?",
                "type": "text",
                "sample": "Senior Backend Engineer",
            },
            {
                "id": "must",
                "eyebrow": "What qualifies",
                "q": "What makes a candidate qualified?",
                "type": "tags",
                "ph": "Type a requirement, press Enter — e.g. “Go”, “5+ years”",
            },
            {
                "id": "where",
                "eyebrow": "Location",
                "q": "Where can candidates be?",
                "type": "choice",
                "options": [
                    {"t": "Remote · US timezones", "d": "Widest strong pool for most roles", "rec": True},
                    {"t": "Hybrid · specific city"},
                    {"t": "Anywhere in the world"},
                ],
                "default": 0,
            },
            {
                "id": "volume",
                "eyebrow": "Pace",
                "q": "How many qualified candidates per week?",
                "type": "choice",
                "options": [
                    {"t": "10 · deep-screened", "d": "Highest signal per candidate"},
                    {"t": "20 · the sweet spot", "d": "What most teams pick for one open role", "rec": True},
                    {"t": "40 · aggressive", "d": "For urgent or multiple openings"},
                ],
                "default": 1,
            },
            {
                "id": "delivery",
                "eyebrow": "Delivery",
                "q": "Where should they land?",
                "type": "choice",
                "options": [
                    {"t": "Email digest · Monday 8:00 AM", "d": "Works from day one — nothing to connect", "rec": True},
                    {"t": "Google Sheet", "d": "A living tracker your whole team can see"},
                    {"t": "Greenhouse", "d": "Straight into your pipeline · one-click connect after signup"},
                    {"t": "Other — I'll describe it", "other": True},
                ],
                "default": 0,
            },
        ],
    },
}


def get_intake_schema(template_code: str | None):
    return INTAKE_SCHEMAS.get(template_code or "")


# Templates auto-load their relevant system skills at creation (same pattern
# as default_tools). Code-level map until skills land on the template model.
TEMPLATE_SYSTEM_SKILLS = {
    "ai-agent-for-candidate-sourcing": ("recruitment_sourcing",),
}


def get_template_system_skills(template_code: str | None) -> tuple[str, ...]:
    return TEMPLATE_SYSTEM_SKILLS.get(template_code or "", ())


def get_outcome_estimate(template_code: str | None) -> dict | None:
    schema = get_intake_schema(template_code)
    if not schema:
        return None
    outcome = schema.get("outcome")
    return dict(outcome) if isinstance(outcome, dict) else None


def _answer(answers: dict, qid: str, fallback: str = "") -> str:
    value = (answers or {}).get(qid)
    if value in (None, "", [], SKIP_VALUE):
        return fallback
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def build_briefing_payload(schema: dict, answers: dict) -> dict:
    """Structured briefing artifact for the timeline — what the user SEES.

    The message body remains the model-facing input; the timeline renders this
    instead, because a briefing is an input artifact, not a chat message.
    """
    rows = []
    for q in schema["questions"]:
        value = _answer(answers, q["id"])
        rows.append(
            {
                "label": q["eyebrow"],
                "value": value or "Open — your agent will ask",
                "open": not value,
            }
        )
    return {
        "template": schema.get("templateName") or "Briefing",
        "title": _answer(answers, schema["questions"][0]["id"]) or None,
        "rows": rows,
    }


def build_brief_message(schema: dict, answers: dict) -> str:
    """The initial user->agent message: a structured brief table (renders as a
    styled table in the timeline via GFM), never prompt-paste prose."""
    noun = schema.get("briefNoun") or "Brief"
    title_answer = _answer(answers, schema["questions"][0]["id"])
    title = f"**{noun} — {title_answer}**" if title_answer else f"**{noun}**"
    open_marker = "_you'll ask me — we'll decide together in chat_"
    rows = []
    for q in schema["questions"][1:]:
        value = _answer(answers, q["id"])
        rows.append((q["eyebrow"], value or open_marker))
    table = "\n".join(f"| **{k}** | {v} |" for k, v in rows)
    return f"{title}\n\n| | |\n|---|---|\n{table}"


def build_charter_override(template_charter: str, schema: dict, answers: dict) -> str:
    noun = (schema.get("briefNoun") or "Brief").lower()
    rows = []
    for q in schema["questions"]:
        value = _answer(answers, q["id"])
        rows.append(f"- {q['eyebrow']}: {value or '(not provided — ask the user in chat)'}")
    return (
        (template_charter or "").strip()
        + f"\n\n## {noun.capitalize()} (from intake)\n"
        + "\n".join(rows)
        + "\nTreat this brief as the starting spec. Confirm assumptions before acting on them."
        + "\nIf anything in the brief is unclear, ask the user before going far."
        + "\nWhenever you ask the user questions with options or multiple parts — in"
        + " any round, including after you have started working — always use the"
        + " structured human-input request tool. Never enumerate options inside a"
        + " plain chat message. If the user does not answer within a reasonable"
        + " time, follow up once, then proceed with clearly-stated sensible"
        + " assumptions they can correct."
    )
