# Local Eval Runs

Canonical evals run through `run_evals`; Meta Gobii does not have a standalone
runner.

Simulated Meta Gobii check:

```bash
uv run python manage.py run_evals --suite meta_gobii --sync --n-runs 1 --simulated --settings=config.eval_local_settings
```

Live Meta Gobii check with OpenRouter:

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
SQLite schema, and seeds the `openrouter-deepseek-v4-flash` routing profile. The
profile stores the `OPENROUTER_API_KEY` environment variable name only; it does
not store the key value. Simulated runs are deterministic local checks and are
not live model evals.
