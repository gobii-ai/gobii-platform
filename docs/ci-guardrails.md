# CI Guardrails

CI enforces two hard budgets before the expensive test shards run:

- Source LoC: nonblank lines in tracked core product source files.
- Prompt size: rendered prompt messages plus JSON tool definitions for normal explicit-send, web-chat implied-send, and planning first-run paths.

Run locally:

```bash
uv run python scripts/check_complexity_budgets.py
```

The LoC guardrail intentionally excludes generated assets, migrations, vendor/build outputs, media/cache, docs, CI metadata, and the guardrail tooling itself. It also excludes unit/integration tests, dedicated eval assets such as `api/evals/`, eval-only settings, eval UI files, and synthetic eval tool definitions.

The core-product scope is intentionally narrower than the whole repository. Marketing/event pages, setup/deployment helpers, proprietary overlays, standalone static assets, and server-rendered templates are excluded so the budget tracks the interactive agent runtime, app code, and shared services that most affect product complexity. Product files that merely reference eval/test concepts still count unless they are in one of the explicit excluded test or eval paths.

The May 2026 limits were lowered from baseline `02ca37078fa554eb11efaede6f2c5b087bb0aa4d` after trimming prompt/tool guidance and narrowing the core LoC scope. Budgets include only small headroom and remain at least 10% below the prior measured source and prompt/tool fixtures.

Intentional budget increases require approval. After approval, update the committed limits from the approved branch:

```bash
uv run python scripts/check_complexity_budgets.py --update-baselines --baseline-sha "$(git rev-parse HEAD)"
```
