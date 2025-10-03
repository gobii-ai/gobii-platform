# OSS Readiness Plan

## Priority 1 – Neutral Community Defaults, Proprietary Opt-In
- [x] Replace hard-coded Gobii domains/URLs with environment-driven values and neutral community defaults, preserving proprietary overrides (`config/settings.py:73`, `config/settings.py:724`, `templates/includes/_footer.html:19`, `misc/ts-example-client/src/index.ts:275`).
- [x] Externalise analytics/support contact defaults so community deployments don’t emit Gobii-specific IDs or addresses (`config/settings.py:600`, `config/settings.py:613`).

## Priority 2 – Optional Integrations by Default
- [x] Guard Stripe, Mailgun/Postmark, and Twilio integrations so absent keys fall back to safe no-op behaviour while proprietary mode can re-enable them (`config/settings.py:562`, `billing/services.py:1`).
- [x] Register Bright Data and Pipedream MCP servers only when credentials exist; surface clear availability in logs/UI (`api/agent/tools/mcp_manager.py:82`).
- [x] Disable Segment/telemetry features unless keys are supplied, with proprietary mode able to opt in (`config/settings.py:600`).

## Priority 3 – LLM Bootstrap Safety Net
- [x] Remove the `precheck-llm-keys` barrier so compose boots into an "unconfigured" state while blocking agent execution until setup completes (`compose.yaml:42`).
- [x] Teach LLM selection paths to detect missing credentials, surface a clear maintenance banner, and short-circuit scheduling/API usage until configuration is saved (`api/agent/core/llm_config.py:168`, `api/agent/core/event_processing.py:942`).

## Priority 4 – Self-Host by Default, Dev Overlay, First-Run Wizard
- [x] Rework `compose.yaml` into the hardened self-host stack (persistent volumes, `DEBUG=0`, optional worker/beat toggles) so `docker compose up` yields a production-style deployment (`compose.yaml`, `docker-compose.dev.yaml`).
- [x] Add an automated secrets bootstrap step so the first `docker compose up` generates credentials without manual edits (`compose.yaml:1`, `docker/bootstrap/runtime_env.py`, `.env.oss.example`).
- [x] Add `docker-compose.dev.yaml` (or equivalent) with developer conveniences for `docker compose -f docker-compose.dev.yaml up`.
- [ ] Implement a first-run setup flow that captures admin credentials, primary LLM keys, and optional integrations before unlocking the app; allow proprietary mode to extend the wizard.

## Priority 5 – Documentation Refresh
- [ ] Publish a new README/quickstart describing the self-host default workflow, first-run wizard, config storage, and proprietary opt-ins (replace outdated guidance referencing `infra/local` assets).
- [ ] Author a developer TOC covering the dev compose overlay, Vite dev server usage, and local testing expectations.
- [ ] Document storage expectations (local filesystem vs. MinIO/S3) and backup guidance (`compose.yaml:30`, `config/settings.py:251`).
- [ ] Provide OSS community docs (CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md) aligned with the new workflows.
- [ ] Update legal/marketing content (Terms, Privacy, templates, sample clients) to remove Gobii-specific contact info or gate it behind proprietary mode.

## Priority 6 – Copy Terminology Alignment
- [ ] After structural work, adjust UI copy so community builds refer to “agents” and proprietary mode can use “AI employee” branding.
