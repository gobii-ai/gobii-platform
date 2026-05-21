# CI Guardrails

CI enforces two hard budgets before the expensive test shards run:

- Source LoC: nonblank lines in tracked core product source files.
- Prompt size: rendered prompt messages plus JSON tool definitions for normal explicit-send, web-chat implied-send, and planning first-run paths.

Run locally:

```bash
uv run python scripts/check_complexity_budgets.py
```

The LoC guardrail intentionally excludes generated assets, migrations, vendor/build outputs, media/cache, docs, CI metadata, and the guardrail tooling itself. It also excludes unit/integration tests and dedicated eval assets such as `api/evals/`, eval-only settings, eval UI files, and synthetic eval tool definitions. The budget targets core product source size; product files that merely reference eval/test concepts still count unless they are in one of the explicit excluded test or eval paths.

Intentional budget increases require approval. After approval, update the committed limits from the approved branch:

```bash
uv run python scripts/check_complexity_budgets.py --update-baselines --baseline-sha "$(git rev-parse HEAD)"
```
