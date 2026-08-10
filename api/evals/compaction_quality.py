"""Synthetic, production-shaped cases for compaction prompt evaluation."""

from dataclasses import asdict, dataclass
import json
import re
from typing import Literal
import unicodedata


COMPACTION_QUALITY_SUITE_SLUG = "compaction_quality"
COMPACTION_QUALITY_TASK_NAME = "evaluate_compaction"
DEFAULT_COMPACTION_SUMMARY_CHAR_LIMIT = 2_000


@dataclass(frozen=True)
class CommsEvalEvent:
    direction: Literal["inbound", "outbound"]
    channel: str
    party: str
    body: str
    peer_dm: bool = False


@dataclass(frozen=True)
class StepEvalEvent:
    kind: Literal["generic", "tool", "cron", "system"]
    description: str = ""
    tool_name: str = ""
    tool_params: dict | None = None
    result: str = ""
    cron_expression: str = ""
    schedule_key: str = ""
    schedule_name: str = ""
    schedule_instruction: str = ""
    scheduled_for: str | None = None
    system_code: str = ""
    system_notes: str = ""


CompactionEvalEvent = CommsEvalEvent | StepEvalEvent


@dataclass(frozen=True)
class CompactionQualityCase:
    slug: str
    name: str
    kind: Literal["comms", "steps"]
    previous_summary: str
    batches: tuple[tuple[CompactionEvalEvent, ...], ...]
    required_exact: tuple[str, ...]
    forbidden_exact: tuple[str, ...]
    semantic_requirements: tuple[str, ...]
    required_normalized: tuple[str, ...] = ()
    max_chars: int = DEFAULT_COMPACTION_SUMMARY_CHAR_LIMIT

    def source_context(self) -> str:
        payload = {
            "previous_summary": self.previous_summary,
            "batches": [
                [asdict(event) for event in batch]
                for batch in self.batches
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class CompactionCheckResult:
    passed: bool
    failures: tuple[str, ...]


def _normalized_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(re.findall(r"\w+", normalized, flags=re.UNICODE))


def _contains_normalized_fact(summary: str, required: str) -> bool:
    """Match human-readable facts despite harmless formatting variation."""

    summary_tokens = _normalized_tokens(summary)
    required_tokens = _normalized_tokens(required)
    if not required_tokens:
        return True

    def contains_in_order(tokens: tuple[str, ...]) -> bool:
        candidate_positions = [
            index
            for index, token in enumerate(summary_tokens)
            if token == tokens[0]
        ]
        for start in candidate_positions:
            position = start
            for required_token in tokens[1:]:
                next_positions = range(
                    position + 1,
                    min(position + 4, len(summary_tokens)),
                )
                match = next(
                    (
                        candidate
                        for candidate in next_positions
                        if summary_tokens[candidate] == required_token
                    ),
                    None,
                )
                if match is None:
                    break
                position = match
            else:
                return True
        return False

    if contains_in_order(required_tokens):
        return True

    # Natural summaries commonly place a numeric value before its label
    # ("4 stale records") even when the source uses key=value order ("stale=4").
    if len(required_tokens) == 2 and sum(token.isdecimal() for token in required_tokens) == 1:
        return contains_in_order(tuple(reversed(required_tokens)))

    return False


def check_compaction_summary(case: CompactionQualityCase, summary: str) -> CompactionCheckResult:
    failures: list[str] = []
    if not isinstance(summary, str) or not summary.strip():
        failures.append("Summary is empty.")
        return CompactionCheckResult(False, tuple(failures))

    if len(summary) > case.max_chars:
        failures.append(f"Summary is {len(summary)} characters; limit is {case.max_chars}.")
    if "```" in summary:
        failures.append("Summary contains a code fence.")

    for required in case.required_exact:
        if required not in summary:
            failures.append(f"Missing required exact value: {required}")
    for required in case.required_normalized:
        if not _contains_normalized_fact(summary, required):
            failures.append(f"Missing required normalized fact: {required}")
    for forbidden in case.forbidden_exact:
        if forbidden in summary:
            failures.append(f"Retained forbidden superseded/noise value: {forbidden}")

    return CompactionCheckResult(not failures, tuple(failures))


COMMS_COMPACTION_CASES = (
    CompactionQualityCase(
        slug="compaction_comms_multi_channel_attribution",
        name="Multi-channel attribution",
        kind="comms",
        previous_summary="Project Q4-771 is awaiting a final rollout-risk determination.",
        batches=(
            (
                CommsEvalEvent(
                    "inbound",
                    "email",
                    "Morgan Lee <morgan@example.test>",
                    "For Q4-771, I observed that billing reconciliation is the rollout risk, not traffic volume.",
                ),
                CommsEvalEvent(
                    "inbound",
                    "discord",
                    "Priya Shah in #launch-review",
                    "My dashboard suggests traffic volume may still be the risk for Q4-771. I have not reconciled Morgan's evidence yet.",
                ),
                CommsEvalEvent(
                    "inbound",
                    "peer DM",
                    "Review Agent",
                    "Keep both Q4-771 claims unresolved until the evidence review is complete.",
                    peer_dm=True,
                ),
            ),
        ),
        required_exact=("Q4-771", "Morgan Lee", "Priya Shah"),
        forbidden_exact=(),
        semantic_requirements=(
            "Attribute the billing-reconciliation claim to Morgan Lee via email.",
            "Attribute the competing traffic-volume claim to Priya Shah via Discord.",
            "Keep the competing claims unresolved rather than choosing a winner.",
        ),
        required_normalized=("email", "discord"),
    ),
    CompactionQualityCase(
        slug="compaction_comms_superseded_corrections",
        name="Superseded corrections",
        kind="comms",
        previous_summary=(
            "Launch packet PKT-204 is due 2026-09-12. Draft r1 has decision REVISE. "
            "Owner is Casey Wu."
        ),
        batches=(
            (
                CommsEvalEvent(
                    "inbound",
                    "web",
                    "Casey Wu",
                    "Correction for PKT-204: the deadline is 2026-09-18, replacing 2026-09-12.",
                ),
                CommsEvalEvent(
                    "inbound",
                    "email",
                    "Approver Rowan Bell <rowan@example.test>",
                    "PKT-204 r3 is APPROVED. This supersedes the r1 REVISE decision.",
                ),
            ),
        ),
        required_exact=("PKT-204", "2026-09-18", "r3", "APPROVED"),
        forbidden_exact=("2026-09-12", "r1", "REVISE"),
        semantic_requirements=(
            "Represent only the current deadline and approval revision.",
            "Preserve Casey Wu as owner and Rowan Bell as the approving actor.",
        ),
        required_normalized=("Casey Wu", "Rowan Bell"),
    ),
    CompactionQualityCase(
        slug="compaction_comms_scoped_pause_resume",
        name="Paused and resumed work",
        kind="comms",
        previous_summary="The portfolio-import and billing-reconciliation workstreams are active.",
        batches=(
            (
                CommsEvalEvent(
                    "inbound",
                    "web",
                    "Owner Jordan Kim",
                    "Pause all portfolio-import execution and its polling immediately. Billing reconciliation may continue.",
                ),
            ),
            (
                CommsEvalEvent(
                    "inbound",
                    "sms",
                    "Owner Jordan Kim",
                    "Resume portfolio-import research only. Do not resume imports or polling until I approve them separately.",
                ),
            ),
        ),
        required_exact=(),
        forbidden_exact=("all work resumed", "imports resumed", "polling resumed"),
        semantic_requirements=(
            "Keep portfolio-import execution and polling paused.",
            "Mark only portfolio-import research as resumed.",
            "Keep billing reconciliation active and attribute the scope to Jordan Kim.",
        ),
        required_normalized=("portfolio-import", "billing-reconciliation", "research"),
    ),
    CompactionQualityCase(
        slug="compaction_comms_ownership_permission_handoff",
        name="Ownership and permission handoff",
        kind="comms",
        previous_summary="Dana Ortiz owns assignment ASN-884; outreach has not been authorized.",
        batches=(
            (
                CommsEvalEvent(
                    "inbound",
                    "peer DM",
                    "Dana Ortiz",
                    "Hand ASN-884 research and drafting to Elliot Park. I retain approval authority.",
                    peer_dm=True,
                ),
                CommsEvalEvent(
                    "inbound",
                    "email",
                    "Compliance <compliance@example.test>",
                    "For ASN-884, do not contact candidate Taylor Moss using a work email. Personal email is permitted only after Dana approves the final draft.",
                ),
            ),
        ),
        required_exact=("ASN-884", "Elliot Park", "Dana Ortiz", "Taylor Moss"),
        forbidden_exact=("Elliot Park owns approval", "outreach authorized"),
        semantic_requirements=(
            "Assign research and drafting to Elliot Park while preserving Dana Ortiz as approver.",
            "Preserve the work-email prohibition and conditional personal-email permission for Taylor Moss.",
        ),
        required_normalized=("work email", "personal email"),
    ),
    CompactionQualityCase(
        slug="compaction_comms_campaign_ledger",
        name="Large campaign ledger",
        kind="comms",
        previous_summary=(
            "Campaign CMP-52: 118 provider-accepted sends, 40 approvals, 7 pending drafts. "
            "Conflict BATCH-OLD is unresolved."
        ),
        batches=(
            (
                CommsEvalEvent("inbound", "peer DM", "Writer Agent", "CMP-52 SEND-119 accepted by provider.", peer_dm=True),
                CommsEvalEvent("inbound", "peer DM", "Writer Agent", "CMP-52 SEND-120 accepted by provider.", peer_dm=True),
                CommsEvalEvent("inbound", "peer DM", "Reviewer Agent", "CMP-52 approvals are now 42; pending drafts are now 5.", peer_dm=True),
                CommsEvalEvent("inbound", "peer DM", "Claim Agent", "BATCH-OLD resolved with no continuing action. BATCH-77 remains CONFLICT.", peer_dm=True),
                CommsEvalEvent("inbound", "peer DM", "QA Agent", "Assignment ASN-991 is quarantined_no_send for identity_mismatch.", peer_dm=True),
            ),
        ),
        required_exact=("CMP-52", "120", "42", "5", "BATCH-77", "ASN-991", "quarantined_no_send", "identity_mismatch"),
        forbidden_exact=("118 provider-accepted", "40 approvals", "7 pending", "BATCH-OLD"),
        semantic_requirements=(
            "Use current aggregate counts instead of narrating each completed send.",
            "Retain the unresolved conflict and quarantined assignment with their exact identifiers.",
        ),
        required_normalized=("conflict",),
    ),
    CompactionQualityCase(
        slug="compaction_comms_multilingual_operations",
        name="Multilingual operational status",
        kind="comms",
        previous_summary="DPD-Verarbeitung wartet auf die Erneuerung von Token #EVAL-239.",
        batches=(
            (
                CommsEvalEvent(
                    "inbound",
                    "email",
                    "Joyce Reports <joyce@example.test>",
                    "Token #EVAL-239 ist jetzt aktiv. Die Gmail-Verbindung funktioniert wieder. Unverarbeitete OCR-Einträge: 0; ausstehende Labels/E-Mails: 0.",
                ),
                CommsEvalEvent(
                    "inbound",
                    "web",
                    "Marco",
                    "Nächste Prüfungen bleiben täglich um 00:00 UTC, 09:00 UTC, 16:00 UTC und 18:00 UTC. Es gibt keine offenen Aufgaben.",
                ),
            ),
        ),
        required_exact=("#EVAL-239", "Gmail", "0"),
        forbidden_exact=("wartet auf die Erneuerung", "Token-Problem offen"),
        semantic_requirements=(
            "State that the token and Gmail issues are resolved.",
            "Report zero pending OCR entries, labels, emails, and open tasks.",
            "Preserve all four daily check times without translating away their UTC meaning.",
        ),
        required_normalized=("00:00", "09:00", "16:00", "18:00", "UTC"),
    ),
)


STEP_COMPACTION_CASES = (
    CompactionQualityCase(
        slug="compaction_steps_repetitive_cron_blocker",
        name="Repetitive cron cycles",
        kind="steps",
        previous_summary="PROCESS_EVENTS runs every five minutes.",
        batches=(
            tuple(
                StepEvalEvent(
                    kind="cron",
                    cron_expression="@every 5m",
                    schedule_key=f"poll-{index}",
                    schedule_name="Inbound poll",
                    schedule_instruction="Check for work; no events found; decided to sleep until next trigger.",
                    scheduled_for=f"2026-09-20T10:{index * 5:02d}:00+00:00",
                )
                for index in range(12)
            )
            + (
                StepEvalEvent(
                    kind="system",
                    system_code="SYSTEM_DIRECTIVE",
                    system_notes="Active blocker BLOCK-DELIVERY-7: answer-delivery tool is unavailable, so the final user answer remains undelivered.",
                ),
            ),
        ),
        required_exact=("BLOCK-DELIVERY-7", "answer-delivery", "undelivered"),
        forbidden_exact=(),
        semantic_requirements=(
            "Collapse the empty polling cycles rather than listing them individually.",
            "Retain the active missing-tool blocker and its consequence.",
        ),
    ),
    CompactionQualityCase(
        slug="compaction_steps_failure_then_recovery",
        name="Failure followed by recovery",
        kind="steps",
        previous_summary="Candidate import IMP-73 is in progress.",
        batches=(
            (
                StepEvalEvent(kind="tool", tool_name="http_request", tool_params={"operation": "import", "id": "IMP-73"}, result="HTTP 422: invalid field mapping OLD-MAP-4"),
                StepEvalEvent(kind="tool", tool_name="sqlite_batch", tool_params={"operation": "persist", "id": "IMP-73"}, result="Error: no such column legacy_owner"),
            ),
            (
                StepEvalEvent(kind="tool", tool_name="http_request", tool_params={"operation": "import", "id": "IMP-73"}, result="HTTP 200: imported 25 rows using MAP-9"),
                StepEvalEvent(kind="tool", tool_name="sqlite_batch", tool_params={"operation": "persist", "id": "IMP-73"}, result="Persisted 25 rows; owner column is present"),
                StepEvalEvent(kind="tool", tool_name="read_file", tool_params={"path": "missing-content.json"}, result="FileNotFoundError: missing-content.json"),
            ),
        ),
        required_exact=("IMP-73", "25", "MAP-9", "missing-content.json"),
        forbidden_exact=("HTTP 422", "OLD-MAP-4", "no such column legacy_owner"),
        semantic_requirements=(
            "Retain the successful import and persistence outcome.",
            "Remove the resolved mapping and schema errors.",
            "Keep missing-content.json as the only active failure.",
        ),
    ),
    CompactionQualityCase(
        slug="compaction_steps_artifact_delivery",
        name="Artifacts and delivery effects",
        kind="steps",
        previous_summary="The final candidate export is being prepared for review.",
        batches=(
            (
                StepEvalEvent(kind="tool", tool_name="create_csv", tool_params={"name": "northstar_candidates.csv"}, result="Created /artifacts/eval/northstar_candidates.csv with 199 rows"),
                StepEvalEvent(kind="tool", tool_name="send_email", tool_params={"to": "reviewer@example.test"}, result="delivery_status=sent message_id=msg-eval-72 subject='Northstar interim report'"),
                StepEvalEvent(kind="generic", description="Final CSV attachment still must be sent to owner@example.test after approval."),
            ),
        ),
        required_exact=("/artifacts/eval/northstar_candidates.csv", "199", "reviewer@example.test", "msg-eval-72", "sent", "owner@example.test"),
        forbidden_exact=("final attachment delivered",),
        semantic_requirements=(
            "Distinguish the sent interim report from the still-pending final attachment.",
            "Preserve the exact artifact path, message identifier, delivery status, and recipients.",
        ),
    ),
    CompactionQualityCase(
        slug="compaction_steps_plan_human_schedule",
        name="Plan, human input, and schedule state",
        kind="steps",
        previous_summary="Executive search plan has 7 steps; 1 done, 1 doing, 5 todo. Human input is pending.",
        batches=(
            (
                StepEvalEvent(kind="system", system_code="PROCESS_EVENTS", system_notes="Human request HIR-EVAL-6 resolved: user selected 'AI/ML Engineers & Researchers'."),
                StepEvalEvent(kind="tool", tool_name="update_plan", tool_params={"plan_id": "PLAN-EVAL-7"}, result="Plan now: done=2 doing=1 todo=4; remaining closeout action is finalize shortlist"),
                StepEvalEvent(kind="cron", cron_expression="0 14 * * 1", schedule_key="shortlist-checkin", schedule_name="Shortlist check-in", schedule_instruction="Review shortlist progress", scheduled_for="2026-09-21T14:00:00+00:00"),
            ),
        ),
        required_exact=("HIR-EVAL-6", "AI/ML Engineers & Researchers", "PLAN-EVAL-7", "2026-09-21T14:00:00+00:00"),
        forbidden_exact=("Human input is pending", "1 done", "5 todo"),
        semantic_requirements=(
            "Treat the human selection as resolved.",
            "Preserve the current plan counts and remaining closeout action.",
            "Preserve the next scheduled check-in time and purpose.",
        ),
        required_normalized=("done=2", "doing=1", "todo=4"),
    ),
    CompactionQualityCase(
        slug="compaction_steps_sqlite_ledger_state",
        name="SQLite ledger state",
        kind="steps",
        previous_summary="Ledger LEDGER-31 has 102 rows, 9 stale records, and cursor CUR-18.",
        batches=(
            (
                StepEvalEvent(kind="tool", tool_name="sqlite_batch", tool_params={"ledger": "LEDGER-31"}, result="Inserted 5 rows; ledger rows=107; stale=4; cursor=CUR-22"),
                StepEvalEvent(kind="tool", tool_name="sqlite_query", tool_params={"query": "schema"}, result="Active gap: result_json column missing; owner column present"),
                StepEvalEvent(kind="tool", tool_name="sqlite_query", tool_params={"query": "poll"}, result="Query returned 0 rows"),
                StepEvalEvent(kind="tool", tool_name="sqlite_query", tool_params={"query": "poll"}, result="Query returned 0 rows"),
                StepEvalEvent(kind="tool", tool_name="sqlite_query", tool_params={"query": "poll"}, result="Query returned 0 rows"),
            ),
        ),
        required_exact=("LEDGER-31", "107", "CUR-22", "result_json"),
        forbidden_exact=("102 rows", "9 stale", "CUR-18", "Query returned 0 rows"),
        semantic_requirements=(
            "Preserve current ledger counts, cursor, and meaningful schema state.",
            "Omit repeated empty-query mechanics.",
        ),
        required_normalized=("stale=4", "owner column present"),
    ),
    CompactionQualityCase(
        slug="compaction_steps_credential_scope",
        name="Credential configuration and use restriction",
        kind="steps",
        previous_summary="Sandbox candidate provider setup is pending.",
        batches=(
            (
                StepEvalEvent(
                    kind="tool",
                    tool_name="update_charter",
                    tool_params={"scope": "candidate-sandbox"},
                    result="Configured endpoint https://sandbox.example.test/v1/candidates with credential gobii_eval_key_DO_NOT_USE_7H3K9P2M",
                ),
                StepEvalEvent(
                    kind="system",
                    system_code="CREDENTIALS_PROVIDED",
                    system_notes="Credential gobii_eval_key_DO_NOT_USE_7H3K9P2M may be used only for candidate-sandbox reads. External validation and production writes are forbidden.",
                ),
            ),
        ),
        required_exact=(
            "gobii_eval_key_DO_NOT_USE_7H3K9P2M",
            "https://sandbox.example.test/v1/candidates",
            "candidate-sandbox",
        ),
        forbidden_exact=("production writes are allowed", "external validation is allowed"),
        semantic_requirements=(
            "Preserve the explicitly synthetic credential value exactly.",
            "Explicitly state that the credential permits candidate-sandbox reads only.",
            "Explicitly state that external validation is forbidden and production use or production writes are forbidden; read-only wording alone is insufficient.",
        ),
        required_normalized=("external validation",),
    ),
)


COMPACTION_QUALITY_CASES = COMMS_COMPACTION_CASES + STEP_COMPACTION_CASES
COMPACTION_QUALITY_SCENARIO_SLUGS = tuple(case.slug for case in COMPACTION_QUALITY_CASES)
