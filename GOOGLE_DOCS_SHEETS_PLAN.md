# Google Docs & Sheets First-Party Tooling Plan

## Goals
- Remove reliance on the Pipedream MCP server for Google Docs/Sheets while preserving the existing tool surface (all functions listed in the request).
- Use official Google Python clients with durable OAuth + refresh handling so agents operate with end-user identity.
- Keep agent UX predictable: same tool names, similar parameter/return shapes, clear connect links when auth is missing.
- Make the integration testable and observable (retries, rate-limit handling, audit logging).

## Non-Goals
- Do not stand up a new MCP server; tools should be first-party built-ins under `api/agent/tools/`.
- No generic Google Drive explorer beyond what is needed for Docs/Sheets parity.
- Do not depend on users signing into Gobii via Google; OAuth for API access is a separate consent flow.

## Current State (repo)
- Google Docs/Sheets today are remote MCP tools discovered through Pipedream (`api/agent/tools/mcp_manager.py` with `PIPEDREAM_PREFETCH_APPS` etc.).
- Connect links are generated via `create_connect_session` in `api/integrations/pipedream_connect.py` and surfaced as `action_required` in `execute_mcp_tool`.
- Tool names are already referenced in prompts/config (e.g., `agents/pretrained_worker_definitions.py`, migrations seeding tool defaults).
- Credential storage primitives exist: encrypted secrets (`PersistentAgentSecret`), MCP OAuth creds (`MCPServerOAuthCredential`), and credential request tooling (`secure_credentials_request`).
- Site supports Google login via allauth (`config/socialaccount_adapter.py`), but those tokens/scopes are not wired to agents.

## Architecture Plan
- **Dependencies & Config**
  - Add `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, and `google-api-python-client` (pin via `pyproject.toml` + `uv.lock`).
  - Introduce settings for client ID/secret, allowed redirect URI(s), and default scopes. Default to the narrowest set (`documents`, `spreadsheets`) and gate search-only functionality behind optional scopes (`drive.metadata.readonly` preferred; allow opt-in to `drive.file` only if we truly need read/write on non-app files).
- **Scope Tiers & UX**
  - Offer tiered consent at connect time: Minimal (`documents`, `spreadsheets`), Search-enabled (`documents`, `spreadsheets`, `drive.metadata.readonly`), Full (`documents`, `spreadsheets`, `drive.file`).
  - Store granted scopes with the credential; surface the chosen tier to the user and in audit logs.
  - When a tool needs a higher tier (e.g., `find_document` requiring Drive metadata), return `action_required` with an “upgrade scope” link pre-populated to request only the missing scopes.
  - Allow per-agent choice: keep the existing bound credential (and its scopes) or re-consent with elevated scopes; no silent upsell.
- **Credential Model & Storage**
  - New encrypted model (e.g., `GoogleWorkspaceCredential`) keyed by Google account email and scope set; hold refresh/access tokens, expiry, token type, metadata.
  - `AgentGoogleWorkspaceBinding` required per agent (no implicit user/org fallback for now); future-proof the model so we can allow sharing later, but default to “explicitly bound per agent.”
  - Use existing encryption helper (`SecretsEncryption`) for token fields; add admin visibility for debugging without exposing secrets.
- **OAuth Flow**
  - Django views to start consent and handle callback; use hosted domain from `Site` to build redirect URLs.
  - Request offline access with incremental scopes; persist refresh token; force re-consent when scopes are insufficient.
  - Store Google account email + profile to display to users and in audit logs.
- **Credential Resolution & Refresh**
  - Helper in `api/integrations/google/auth.py` to load credentials for an agent (binding required); surface `action_required` with connect URL when absent.
  - Auto-refresh expired access tokens using google-auth; persist refreshed tokens; handle 401/invalid_grant by clearing binding and returning `action_required`.
  - Provide a “test connection” helper to validate scopes before binding.
- **Service Layer**
  - Create `api/integrations/google/docs.py` and `.../sheets.py` wrappers using `googleapiclient.discovery.build` with shared HTTP session + retry/backoff for 429/5xx.
  - Normalize IDs/ranges (e.g., A1 notation helpers, worksheet lookup) and encapsulate schema mapping from Pipedream-style params to Google API calls.
  - Common error adapter that returns user-friendly messages and structured error data for tool responses.
- **Tool Definitions (OpenAI format)**
  - Add builtin tool modules under `api/agent/tools/` (`google_docs_tools.py`, `google_sheets_tools.py`) returning definitions + executors.
  - Register them in `BUILTIN_TOOL_REGISTRY` with gating so execution checks for an attached credential before proceeding.
  - Keep tool names stable (`google_docs_create_document`, `google_sheets_add_single_row`, etc.) and mirror Pipedream parameter schema as closely as feasible to avoid prompt changes.
  - Execution shape: `{"status": "ok"/"error"/"action_required", "result"/"data"/"message", ...}` matching existing conventions. Include links or previews where helpful (e.g., document URL, spreadsheet URL).
- **Tool Coverage Notes**
  - Docs: create document (title + optional initial content), find document (Drive search by name/owner/folder), get current user (People API or OAuth token info).
  - Sheets core ops: create spreadsheet/worksheet, list worksheets, get sheet metadata, append/add rows (single/multi/upsert), update rows/cells, add/delete rows/columns/dimensions, move/insert dimensions, add/update formatting + conditional rules, protected ranges, notes/comments, data validation, copy worksheet, clear ranges, retrieve values.
  - Ensure helpers for range parsing, sheet ID resolution, value input option (RAW/USER_ENTERED), and handling numeric/string coercion match Pipedream behavior.
- **UX & Console**
  - Console entry point to connect a Google account and choose scopes; show which agents are bound and allow re-auth/revoke.
  - When a tool lacks credentials, return `action_required` with a generated connect URL; align messaging with `secure_credentials_request` expectations.
  - Update docs/help text so users know Google login to the site is separate from granting Docs/Sheets access.
- **Rollout & Compatibility**
  - Feature flag to prefer first-party tools while keeping Pipedream as fallback during transition; per-agent toggle for staged rollout.
  - Update `agents/pretrained_worker_definitions.py`, any migrations/seeded defaults, and `config/settings.py` defaults so new agents auto-enable the builtin tools instead of Pipedream ones.
  - Migration/cleanup to remove unused `PIPEDREAM_PREFETCH_APPS` entries once rollout completes.
- **Observability & Limits**
  - Structured logging for OAuth events, tool invocation, rate-limit retries, and Google API errors.
  - Counters for usage per tool and per credential (basic analytics + abuse detection).
  - Respect Google quota headers; implement exponential backoff and partial failure handling for batch updates.

## Implementation Phases
1. **Foundations**: Add dependencies, settings, and empty integration modules; define feature flags and scopes.
2. **OAuth & Models**: Create credential/binding models + migrations; implement consent/callback views and connect URL generation; admin wiring.
3. **Service Layer**: Build auth helper + client factory + Drive/Docs/Sheets wrappers with retries/error adapters.
4. **Tools**: Implement Docs tools, then Sheets tools covering the required function list; register in `BUILTIN_TOOL_REGISTRY` with gating; add heuristics/prompts updates.
5. **UI/Docs**: Console connect UI, agent binding selector, user-facing guidance; deprecate Pipedream messaging.
6. **Testing**: Unit tests for auth refresh + tool executors using stubbed Google responses; tags registered in `ci.yml`; targeted test runs via `uv run python manage.py test --settings=config.test_settings`.
7. **Rollout**: Enable flag for internal/staging agents, monitor metrics/logs, then default new agents to first-party tools and prune Pipedream fallback.

## Testing Strategy
- Mock Google APIs with recorded/stub responses for each tool path; assert request payloads, retries, and error surfaces.
- Credential refresh tests (expired token, invalid_grant recovery, scope mismatch).
- Tool-level contract tests to ensure outputs mirror Pipedream expectations (field names, status codes, action_required when missing auth).
- UI flow test for OAuth start→callback→binding persistence.

## Open Questions
- Scope tradeoff: resolve by keeping `find_document` callable; if scopes are too narrow, return `action_required` with an upgrade link (no silent failure, no omission).
- Multi-account: v1 will use a single bound account per agent; defer per-call multi-account selection unless a multi-tenant need appears.
- Any existing Pipedream-dependent prompts/workflows we must preserve verbatim (e.g., exact return shapes) beyond matching tool names?
- Service accounts: target later phase; confirm requirements/tenants before adding.
