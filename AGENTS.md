Running unit tests:

- Use the uv '.venv' already existing in the dir.
- Prefer to run using `uv run python manage.py test --settings=config.test_settings --parallel auto`,
  Or fall back to `python manage.py test --settings=config.test_settings`.
- Typically, run only the test(s) you need, then at the end run the full suite.
- Use `uv run` for almost everything python related

Writing unit tests:
- Ensure the test is tagged with a batch tag, e.g. `@tag('my_feature_batch')`
- Ensure the tag is registered in ci.yml
- Do not feel the need to write tests just for tertiary features like analytics. Focus on core functionality.
- Do not write tests for removed/legacy/dead code
- Do not add front-end tests unless specifically requested.

Writing evals:
- Use the canonical eval system under `api/evals` and launch with `uv run python manage.py run_evals`; do not add feature-specific eval commands.
- Keep eval evidence categories separate: unit tests prove eval code/scoring, simulated evals prove deterministic runner paths, live evals prove model behavior, and official runs are durable comparison data.
- Prefer micro evals for agent behavior regressions. A good micro eval is one short, realistic user request with one behavioral claim: expected tool choice, forbidden tool absence, planning policy, stop condition, approval/permission behavior, or final response shape.
- Make micro eval prompts concrete and bounded. Use fake but realistic domains, IDs, rows, URLs, people, and values so the correct behavior is mechanically checkable. Avoid broad tasks where many tool sequences would be equally defensible.
- For common tool-choice cases, declare `expected_tools`, `forbidden_tools`, `expected_params`, `plan_expected`, `accepted_tool_alternatives`, `allowed_preamble_tools`, and `eval_synthetic_tools` deliberately. Do not put `update_plan` in expected/forbidden tool lists; use `plan_expected`.
- Keep accepted alternatives narrow. Add an alternative only when it is genuinely product-correct, not to make a flaky eval pass.
- Use eval synthetic tools and `mock_config` instead of live external systems where possible. Never hardcode provider calls or secrets in an eval; route live models through `LLMRoutingProfile` and refer to secret names/status only.
- Give every scenario stable metadata: `tier`, `category`, `expected_runtime`, `cost_class`, `owner`, `area`, `tags`, plus `required_fixtures`/`required_secrets` when applicable. Generated scenario classes must set metadata too so catalog filters work.
- Define small, named `ScenarioTask`s with clear pass/fail meaning. Prefer several specific checks over one broad pass/fail task so failures identify discovery, tool choice, approval policy, output safety, stop condition, or response quality.
- Use `ScenarioExecutionTools` for message injection, processing, task recording, artifacts, and LLM judges. Record useful `expected_summary`/`observed_summary` values and sanitized artifacts like message/step/browser task references; do not store raw secrets or giant transcripts.
- For agent-processing evals, pass a narrow `eval_stop_policy` to stop after the relevant terminal tool, all expected tools, a tracked human-input request, or an unexpected relevant tool. Filter known bookkeeping noise explicitly rather than letting unrelated tool calls hide regressions.
- Prefer deterministic Python scoring helpers for assertions. Use `llm_judge` only when the behavior cannot be scored reliably with structured state, and keep the judge question/options tight.
- If a scenario supports offline execution, set `supports_simulation = True` and make the simulated path deterministic. Do not present simulated results as live model quality.
- Add new scenarios to `api/evals/scenarios/`, register/import them via the canonical loader/registry, and add them to a focused suite when developers should run that group repeatedly. The dynamic `all` suite already covers every registered scenario.
- The point of evals is to help us optimize general agent prompts, tool definitions, etc. Do not make eval prompts too strong or give guidance that should be handled by general prompting.

Python:
- Do NOT do annotations imports like `from __future__ import annotations`

For now, only run specific tests, not the full suite, as that will crash.

Design/UX:

- Use existing components where possible
  - Extract out common/new components as needed
- Reference preline components in vendor/
- Do not use light gray backgrounds for anything
- Do not use horizontal rules
- Keep components "integrated" --avoid container-in-container looks
- Avoid stacked shadows

Code quality:
- Pay careful attention to code organization and structure, where files go, etc. Fit the existing structure.
- Break things into multiple files, smaller testable components.
- Do things the right way.
- Leave the codebase in a better state than when you started. If you can do light refactoring, cleanup, have a net negative lines of code, that is encouraged.
- Avoid catching broad `Exception`; prefer specific exception classes so unrelated bugs are not masked.
- Prefer direct settings access (e.g., `settings.MY_SETTING`) instead of `getattr`, since defaults are centralized in `settings.py`.
- Comments should explain *why* (intent/constraints), not *what* (obvious code behavior).

Task lists:
- Only create task lists when the work genuinely benefits from tracking multiple independent pieces. For straightforward work, just do it directly without ceremony.

Debugging:
- Never guess what the issue is. Aim to *prove it* via running unit tests, adding new tests when needed (if it's something that should be covered by unit tests), running one off code with uv run django shell, etc.

Agent trajectories are sometimes in ./mediafiles/ 😉
