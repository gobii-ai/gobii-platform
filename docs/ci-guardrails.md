# CI Guardrails

CI enforces two hard budgets before the expensive test shards run:

- Source LoC: nonblank lines in tracked application, test, and frontend source files.
- Prompt size: rendered prompt messages plus JSON tool definitions for normal explicit-send, web-chat implied-send, and planning first-run paths.

Run locally:

```bash
uv run python scripts/check_complexity_budgets.py
```

The LoC guardrail intentionally excludes generated assets, migrations, vendor/build outputs, media/cache, docs, CI metadata, and the guardrail tooling itself. The budget targets product source size; CI and budget metadata are excluded so this bootstrap PR can freeze the current application baseline without raising it.

Intentional budget increases require approval. After approval, update the committed limits from the approved branch:

```bash
uv run python scripts/check_complexity_budgets.py --update-baselines --baseline-sha "$(git rev-parse HEAD)"
```
