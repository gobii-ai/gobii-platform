# Eval System Implementation Plan

Every item is a concrete checkbox so we can implement and verify one slice at a
time. Unless noted otherwise, commands should be run from the repo root.

---

## Stage 0 – Repository audit & shared conventions
- [ ] Review `api/agent/comms/message_service.py` (esp. `ingest_inbound_message`,
      `_get_or_create_conversation`, `_ensure_participant`) so new helpers reuse
      the same flows.
- [ ] Review `api/agent/core/llm_utils.py` to confirm `run_completion` is the
      single LLM entry point (needed for the future judge helper).
- [ ] Review `api/agent/core/event_processing.py` + `api/agent/core/budget.py`
      to understand the locking/budget semantics we must respect when invoking
      the orchestrator directly.
- [ ] Confirm no code changes are required for this stage.

_Goal_: Know exactly where to hook in without breaking existing invariants.

---

## Stage 1 – Data model foundation
- [ ] Add `EvalRun` (fields: `id`, `scenario_slug`, `scenario_version`, FK
      `agent`, FK `initiated_by`, `status`, `started_at`, `finished_at`,
      `notes`, `budget_id`, `branch_id`, metrics snapshots for tokens/credits/
      completions/steps) to `api/models.py`.
- [ ] Add `EvalRunTask` (FK `run`, `sequence`, `name`, `status`,
      `assertion_type`, `started_at`, `finished_at`, `expected_summary`,
      `observed_summary`, FK `first_step`, FK `first_message`, FK
      `first_browser_task`, `tool_called`, `charter_before`, `charter_after`,
      `schedule_before`, `schedule_after`, `llm_question`, `llm_answer`,
      `llm_model`) to `api/models.py`.
- [ ] Register both models in `api/admin.py` with list filters and inline task
      view.
- [ ] Run `uv run python manage.py makemigrations` and ensure only eval tables
      appear in the migration.
- [ ] Run `uv run python manage.py migrate`.
- [ ] Log into Django admin (local superuser) and confirm the models show up.

_Goal_: Schema exists with explicit columns and is visible via admin.

---

## Stage 2 – Messaging helper for eval stimuli
- [ ] Implement `inject_internal_web_message` in
      `api/agent/comms/message_service.py` that:
      - Builds sender/agent addresses via `build_web_user_address` /
        `build_web_agent_address`.
      - Reuses `_get_or_create_endpoint`, `_get_or_create_conversation`,
        `_ensure_participant`, `_save_attachments`.
      - Accepts `trigger_processing: bool` (default `True`); when `False`, skip
        calling `process_agent_events_task.delay`.
- [ ] Make the helper return `(message, conversation)` and ensure
      `owner_agent`/participants mirror the real ingest path.
- [ ] Manual test: `uv run python manage.py shell`, call the helper with
      `trigger_processing=False`, inspect the resulting `PersistentAgentMessage`
      row, and verify no Celery task was queued (check worker logs or Redis).

_Goal_: Deterministic way to inject eval messages without touching adapters.

---

## Stage 3 – Scenario registry & helpers
- [ ] Create `api/evals/` package with `__init__.py`, `base.py`, `helpers.py`,
      `registry.py`, and `scenarios/`.
- [ ] In `base.py`, define `EvalScenario` with metadata (`slug`, `version`,
      `description`, task definitions) and a template `run()` method.
- [ ] Add mixins/helpers:
      - Send inbound messages via the Stage 2 helper.
      - Trigger `process_agent_events` synchronously (import and call the
        function to stay on the same lock path).
      - Poll `BrowserUseAgentTask` rows when background tasks are expected.
      - Compute deterministic assertions (charter/schedule diffs, tool enable).
      - Invoke an LLM judge using `run_completion` (YES/NO answers only).
- [ ] Implement `registry.py` with a global `SCENARIOS` dict plus helpers
      `list_scenarios()` and `get_scenario(slug)`.
- [ ] Temporary test: add a dummy scenario, run a short script to ensure
      `list_scenarios()` returns it, then remove the dummy.

_Goal_: Code infrastructure exists before real scenarios land.

---

## Stage 4 – Runner + Celery glue
- [ ] Add `api/evals/runner.py` with `EvalRunner` that:
      - Loads the `EvalRun`, fetches the scenario, and pre-creates
        `EvalRunTask` rows (including snapshots like `charter_before`).
      - Executes tasks sequentially, updating each task row (status, summaries,
        artifact FK fields, LLM metadata).
      - After completion, aggregates metrics by querying
        `PersistentAgentCompletion` and `PersistentAgentStep` between
        `run.started_at` and finish time.
      - Handles exceptions by marking the offending task/run `errored` and
        writing the traceback into `EvalRun.notes`.
- [ ] Add `api/evals/tasks.py` with `@shared_task run_eval_task` that instantiates
      `EvalRunner` and logs errors without crashing the worker.
- [ ] Manual test: in Django shell create a dummy `EvalRun`, call
      `EvalRunner(run_id).run()`, and verify DB state; then run the Celery task
      (`uv run celery -A config worker -l info` must be active) and confirm it
      completes.

_Goal_: Core execution pipeline works before scenarios/API.

---

## Stage 5 – Scenario implementations (initial suite)
Implement under `api/evals/scenarios/`. Each scenario should rely on Stage 2–4
helpers and store artifact references for later inspection.

- [ ] `echo_response`: send “Reply with ORANGE”, run the agent, assert the most
      recent outbound message contains the keyword, store message FK.
- [ ] `charter_update_basic`: capture charter, instruct agent to update and
      confirm, assert charter diff + outbound acknowledgement.
- [ ] `schedule_update_basic`: same for schedule; store reference to the system
      step documenting the change.
- [ ] `enable_tool_search`: ensure a specific MCP tool is not enabled, instruct
      the agent to enable it via `search_tools`, assert `PersistentAgentEnabledTool`
      row exists afterward.
- [ ] `sleep_control`: request a reply followed by sleep, assert a
      `sleep_until_next_trigger` tool call was recorded.
- [ ] `monitor_ai_news`: multi-assertion scenario (charter mentions monitoring
      AI via LLM judge, schedule set to non-empty cadence, at least one research
      tool executed, outbound message references the seeded headline).
- [ ] Manual test for each: create an `EvalRun` via shell, run the scenario, and
      inspect `EvalRunTask` rows plus linked `PersistentAgentStep`/
      `PersistentAgentMessage` entries in admin.

_Goal_: Minimal scenario suite exercising core behaviors end to end.

---

## Stage 6 – REST API for staff usage
- [ ] Add serializers in `api/serializers.py`:
      - `EvalScenarioSerializer` (registry metadata).
      - `EvalRunTaskSerializer`.
      - `EvalRunSerializer` (includes computed task counts and nested tasks).
      - `EvalRunCreateSerializer` validating `scenario_slug`/`agent_id`.
- [ ] Add views in `api/views.py`:
      - `GET /api/evals/scenarios/` (staff-only list).
      - `POST /api/evals/runs/` (create run + enqueue Celery task).
      - `GET /api/evals/runs/` and `GET /api/evals/runs/{id}` (history/detail).
- [ ] Wire URLs in `api/urls.py` under a staff-only namespace.
- [ ] Manual test via curl/Postman:
      - `GET /api/evals/scenarios/`.
      - `POST /api/evals/runs/` with a valid scenario slug + agent.
      - Poll the runs endpoint and verify nested task info.

_Goal_: Staff can trigger/inspect evals via API before any UI.

---

## Stage 7 – Console (staff-only) MVP
- [ ] Add a staff-only console page (e.g., `console/views.py` +
      `templates/console/evals/index.html`) listing scenarios with trigger
      buttons backed by the API.
- [ ] Add a run history table with filters (scenario, agent, status) and link to
      a detail page.
- [ ] Add a run detail template showing each task, status badge, expected vs
      observed summary, and links to referenced steps/messages (link to existing
      agent detail views with anchors).
- [ ] Manual test: log in as staff, trigger a scenario, watch it appear in the
      history table, and drill into the detail page.

_Goal_: Staff can operate evals without leaving the console.

---

## Stage 8 – LLM judge robustness (optional but recommended)
- [ ] Finalize the judge prompt in `api/evals/helpers.py` (strict YES/NO plus
      rationale, log model name).
- [ ] Add unit tests mocking `litellm.completion` to ensure YES/NO parsing is
      resilient to lowercase or trailing text.
- [ ] Manual test: run `monitor_ai_news` twice and confirm judge outputs are
      stable and recorded on `EvalRunTask`.

_Goal_: Confidence that LLM-based assertions are reliable.

---

## Stage 9 – Simulated environments (future extension)
- [ ] Build a simple news sim app under `/eval/sim/news` using the existing web
      stack (Django view + static data) along with reset helpers callable from
      scenarios.
- [ ] Update `monitor_ai_news` to pull the headline from the sim rather than a
      hardcoded string.
- [ ] Manual test: `curl http://localhost:8000/eval/sim/news/` to verify the
      fixture, run the scenario, and confirm the agent’s outbound message
      matches the sim-provided fact.

_Goal_: Foundation for richer browser/web-task evals later.

---

## Stage 10 – Testing & deployment checklist
- [ ] Write automated tests:
      - Model tests for `EvalRun`/`EvalRunTask`.
      - Runner unit tests covering success/failure branches.
      - Scenario smoke tests (tagged per `AGENTS.md`, e.g., `@tag('evals_batch')`).
- [ ] Manual smoke runs: trigger every scenario on a sample agent, verify DB
      artifacts, and ensure sequential runs don’t interfere via locks/budgets.
- [ ] Update documentation (`AGENTS.md` or README) with a link to this file and
      a short usage blurb.
- [ ] Deployment steps: apply migrations, restart Celery + web, sanity-check the
      eval console in staging/prod.

_Goal_: Production-ready eval system with tests + docs.

---

## Scope management notes
- After Stage 2 we already have reliable message injection.
- After Stage 5 we can run evals manually (without API/UI).
- Later stages (API/UI/LLM judge/sims) can be postponed if time is tight.
- If we must cut scope further, finish Stages 1–6 for just the
  `echo_response` + `charter_update_basic` scenarios before expanding.
- Minimum critical path for a single working eval:
  1. Stage 1 (models/migrations)
  2. Stage 2 (message injection helper)
  3. Stage 3 (registry + base helpers limited to the chosen scenario’s needs)
  4. Stage 4 (runner + Celery)
  5. Stage 5 (implement one scenario, e.g., `echo_response`)

Complete each checkbox—including manual verification steps—before moving to the
next stage to avoid chasing intertwined bugs under time pressure.
