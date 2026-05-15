# Local eval runs

Canonical evals run through `run_evals`; feature evals should not have
standalone management commands. The developer-facing source of truth is
`docs/content/developers/evals.mdx`.

List registered suites and scenarios:

```bash
uv run python manage.py run_evals --list
```

List local routing profiles:

```bash
uv run python manage.py run_evals --list-routing-profiles --settings=config.eval_local_settings
```

Run one simulated Meta Gobii scenario:

```bash
uv run python manage.py run_evals --scenario meta_gobii_negative_content_task --sync --n-runs 1 --simulated --settings=config.eval_local_settings
```

Run the simulated Meta Gobii suite:

```bash
uv run python manage.py run_evals --suite meta_gobii --sync --n-runs 1 --simulated --settings=config.eval_local_settings
```

Run the live Meta Gobii suite with OpenRouter:

```bash
set -a; source /Users/andrew/.env-openrouter >/dev/null; set +a
uv run python manage.py run_evals --suite meta_gobii --sync --n-runs 1 --routing-profile openrouter-deepseek-v4-flash --settings=config.eval_local_settings
```

If OpenRouter is rate-limiting or returning transient provider overloads, keep
the same canonical suite and add a delay:

```bash
uv run python manage.py run_evals --suite meta_gobii --sync --n-runs 1 --delay-between-runs-seconds 8 --routing-profile openrouter-deepseek-v4-flash --settings=config.eval_local_settings
```

`config.eval_local_settings` uses `.local/eval-local.sqlite3`, syncs the local
SQLite schema, and seeds local profiles for OpenRouter DeepSeek V4 Flash,
OpenRouter Qwen, OpenAI GPT-4.1 Mini, and an optional custom LiteLLM model when
`EVAL_LOCAL_CUSTOM_MODEL` is set. Profiles store environment variable names
only; they do not store key values. Simulated runs are deterministic local
checks and are not live model evals. Run one eval-local command at a time; the
local SQLite database is intentionally simple and can lock under concurrent
eval-local commands.
