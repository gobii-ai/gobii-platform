# Frontend Endpoint Unused Backend Data Audit

Date: 2026-07-01

## Scope

This audit covers endpoints consumed by browser-facing code in `frontend/src`, `static/js`, and server-rendered templates. It excludes third-party/public API routes, webhooks, Django admin pages, and routes that are registered in `config/urls.py` but have no frontend caller.

Sources checked:

- Frontend data clients in `frontend/src/api/*.ts` and `frontend/src/components/usage/api.ts`.
- React route shell templates under `console/templates/console/*.html`.
- Server-rendered marketing/home/public templates where backend context is rendered directly.
- Backend response builders in `console/api_views.py`, `console/usage_views.py`, `console/system_status.py`, `console/agent_settings/service.py`, `console/*_api_views.py`, `pages/views.py`, and related serializers/types.

Method:

- Enumerated frontend `jsonFetch`, `jsonRequest`, `fetch`, websocket, and template `data-*`/`json_script` consumers.
- Cross-checked backend response fields against actual frontend reads, not just TypeScript type declarations.
- Classified fields as confirmed unused only when the current frontend either never references them or explicitly derives replacement data.

## Executive Summary

Most frontend endpoints return data that is either rendered directly or used for gating, cache hydration, mutations, or follow-up URLs. The clearest unused backend data is concentrated in status/telemetry-style endpoints and a few shell/bootstrap payloads.

High-confidence trim candidates:

1. `/console/api/agents/<agent_id>/timeline/`: top-level `oldest_cursor` and `newest_cursor`.
2. `/console/api/agents/<agent_id>/web-sessions/start|heartbeat/`: `expires_at`, `last_seen_at`, `last_seen_source`, `is_visible`, `ended_at`.
3. `/console/api/usage/summary/`: `metrics.tasks` status breakdown.
4. `/console/api/usage/trends/`: previous-period data (`previous_period`, per-bucket `previous`) and `current_period`.
5. `/console/api/status/`: several summary/row fields are returned but not rendered.
6. `/console/api/agents/<agent_id>/settings/`: several timestamp/helper fields in the agent settings payload are not rendered.
7. Homepage `/`: some authenticated context is built for legacy/base-template compatibility but not used by `pages/templates/home.html`.

## Confirmed Unused Data

### Agent Timeline

Endpoint: `GET /console/api/agents/<agent_id>/timeline/`

Backend:

- `console/api_views.py` `AgentTimelineAPIView.get()` returns `oldest_cursor` and `newest_cursor`.
- `console/agent_chat/timeline.py` computes these from the returned window.

Frontend:

- `frontend/src/api/agentChat.ts` declares `TimelineResponse.oldest_cursor` and `newest_cursor`.
- `frontend/src/hooks/useAgentTimeline.ts` ignores both fields and instead sets:
  - `oldestCursor: events.length ? events[0].cursor : null`
  - `newestCursor: events.length ? events[events.length - 1].cursor : null`

Unused fields:

- `oldest_cursor`
- `newest_cursor`

Recommendation:

- Remove these top-level fields from the API response, or update the frontend to use them and stop deriving cursors from events. The current frontend behavior makes the top-level values redundant.

### Agent Web Sessions

Endpoints:

- `POST /console/api/agents/<agent_id>/web-sessions/start/`
- `POST /console/api/agents/<agent_id>/web-sessions/heartbeat/`

Backend:

- `console/api_views.py` `_session_response()` returns `session_key`, `ttl_seconds`, `expires_at`, `last_seen_at`, `last_seen_source`, `is_visible`, and sometimes `ended_at`.

Frontend:

- `frontend/src/hooks/useAgentWebSession.ts` uses:
  - `session_key` for later heartbeat/end calls.
  - `ttl_seconds` for heartbeat scheduling.
- The hook keeps the entire snapshot in state, but no consumer reads the returned `session` object. `AgentChatPage` destructures only `{ status, error }` from `useAgentWebSession(...)`.

Unused fields:

- `expires_at`
- `last_seen_at`
- `last_seen_source`
- `is_visible`
- `ended_at`

Recommendation:

- Return only `session_key` and `ttl_seconds` for start/heartbeat unless future UI will show live web-session diagnostics.
- Keep the current richer shape only if another planned frontend view needs it.

### Usage Summary

Endpoint: `GET /console/api/usage/summary/`

Backend:

- `console/usage_views.py` `UsageSummaryAPIView.get()` returns a `metrics.tasks` object with `count`, `completed`, `in_progress`, `pending`, `failed`, and `cancelled`.

Frontend:

- `frontend/src/components/usage/UsageMetricsGrid.tsx` reads `metrics.quota` and `metrics.credits`.
- `frontend/src/screens/AgentChatPage.tsx` reads `metrics.todayCredits`, `period.resetOn`, `billing.purchasedSeats`, `extra_tasks.enabled`, and quota fields.
- No current frontend code reads `metrics.tasks`.

Unused fields:

- `metrics.tasks.count`
- `metrics.tasks.completed`
- `metrics.tasks.in_progress`
- `metrics.tasks.pending`
- `metrics.tasks.failed`
- `metrics.tasks.cancelled`

Recommendation:

- Remove `metrics.tasks` from this summary endpoint, or move it behind a query flag if a future detailed usage card will need it.

### Usage Trends

Endpoint: `GET /console/api/usage/trends/`

Backend:

- `console/usage_views.py` `UsageTrendAPIView.get()` computes a previous range and returns:
  - `current_period`
  - `previous_period`
  - `buckets[].previous`

Frontend:

- `frontend/src/components/usage/UsageTrendSection.tsx` renders only `buckets[].timestamp`, `buckets[].current`, `buckets[].agents`, `resolution`, and `timezone`.
- No current chart line/tooltip renders previous-period comparison.

Unused fields:

- `current_period.start`
- `current_period.end`
- `previous_period.start`
- `previous_period.end`
- `buckets[].previous`

Recommendation:

- Remove the previous-period calculations from the default response. If comparison is planned, add `?include_previous=1` and compute those fields only when requested.

### System Status

Endpoint: `GET /console/api/status/`

Backend:

- `console/system_status.py` `build_system_status_payload()` and section collectors return a broad operational snapshot.

Frontend:

- `frontend/src/screens/SystemStatusScreen.tsx` uses `meta.environment`, `meta.refreshedAt`, `meta.pollIntervalSeconds`, `overview`, and section availability.
- `frontend/src/components/systemStatus/StatusSections.tsx` renders a subset of section summaries/rows.

Unused fields by section:

- `sections.agents.summary.heartbeatCount`
- `sections.agents.summary.queuedOrPendingCount`
- `sections.compute.summary.stoppedCount`
- `sections.compute.rows[].namespace`
- `sections.compute.rows[].proxyName`
- `sections.compute.rows[].lastActivityAt`
- `sections.proxies.summary.inactiveCount`
- `sections.proxies.rows[].isActive`
- `sections.proxies.rows[].responseTimeMs`
- `sections.proxies.rows[].consecutiveHealthFailures`
- `sections.proxies.rows[].deactivationReason`

Recommendation:

- Trim these from the response if the page is meant to stay high-level.
- If staff need this detail, render it in the table or a row expansion instead of shipping it invisibly.

### Agent Settings

Endpoint: `GET /console/api/agents/<agent_id>/settings/`

Backend:

- `console/agent_settings/service.py` `_build_agent_detail_props()` returns the agent settings payload.

Frontend:

- `frontend/src/screens/AgentDetailScreen.tsx` and related settings tables render most of the payload, but not every helper/timestamp field.

Unused fields:

- `dedicatedIps.options[].assignedNames`
- `agent.pendingTransfer.createdAtIso` (`createdAtDisplay` is rendered instead)
- `collaborators.pendingInvites[].invitedAtIso`
- `collaborators.pendingInvites[].expiresAtIso`
- `reassignment.canReassign` (duplicates `reassignment.enabled` in the current frontend model)
- `urls.mcpServersManage`

Recommendation:

- Remove the duplicated/timestamp-only fields unless there is a planned tooltip/detail display.
- Prefer keeping display-ready timestamps only, or switch the UI to ISO timestamps and format client-side consistently.

### Homepage Page Load

Endpoint: `GET /`

Backend:

- `pages/views.py` `HomePage.get_context_data()` builds context for SEO, agent creation, integrations, recent agents, and authenticated console context.

Frontend/template:

- `pages/templates/home.html` consumes most homepage-specific context.
- The template does not directly consume some authenticated context fields built by `HomePage`.

Unused or not directly consumed by `home.html`:

- `can_manage_org_agents`
- `current_membership`
- `user_organizations`
- `simple_examples`
- `rich_examples`
- `recent_agents[*].mini_description`
- `recent_agents[*].mini_description_source`

Notes:

- `current_context` is used by `templates/base.html` analytics/Segment bootstrap, so it is not unused at page-load level.
- The `simple_examples`/`rich_examples` constants may be legacy homepage copy that can be removed from the context if no included partial uses them.

Recommendation:

- Remove the unused homepage-only context, or move authenticated console context assembly into the base-context layer if it exists only for global analytics/header behavior.

## Endpoint Inventory

### React Page Shells

| Page load endpoint | Backend/template | Frontend data passed on load | Finding |
| --- | --- | --- | --- |
| `/staff/status/` | `ConsoleStatusView`, `console/system_status.html` | `data-app="system-status"` | No unused shell data; JSON endpoint has unused fields above. |
| `/console/diagnostics/` | `ConsoleDiagnosticsView`, `console/diagnostics.html` | `data-app="diagnostics"` | No backend data payload beyond mount marker. |
| `/staff/users/`, `/staff/users/<id>/`, `/staff/orgs/<id>/` | `StaffUsersView`, `console/staff_users.html` | selected user/org ids | Used to select initial detail. |
| `/system-settings/` | `SystemSettingsView`, `templates/system_settings.html` | `data-app="system-settings"` | No unused shell data; JSON endpoint checked below. |
| `/llm-config/` | `ConsoleLLMConfigView`, `console/llm_config.html` | `data-app="llm-config"` | No unused shell data; JSON endpoint checked below. |
| `/evals/` | `ConsoleEvalsView`, `console/evals.html` | `data-app="evals"` | No unused shell data. |
| `/evals/<suite_run_id>/` | `ConsoleEvalsDetailView`, `console/evals_detail.html` | suite run id, staff flag | Used by `EvalsDetailScreen`. |
| `/console/staff/agents/<agent_id>/audit/` | `StaffAgentAuditView`, `console/staff_agent_audit.html` | agent id/name/admin URL | Used by `AgentAuditScreen`. |
| `/console/advanced/mcp-servers/` | `MCPServerManagementView`, `console/mcp_servers.html` | owner scope, list/detail/assignment/test URL templates | Used by MCP screens/modals. |
| `/staff/mcp/` | `PlatformMCPServerManagementView`, `console/staff_platform_mcp.html` | platform MCP URL templates | Used by MCP screens/modals. |
| `/console/system-skills/<skill_key>/profiles/` | `SystemSkillProfilesView`, `console/system_skill_profiles.html` | owner scope, skill key, list URL | Used by `SystemSkillProfilesScreen`. |

### Agent Chat And Settings APIs

| Endpoint | Main frontend consumer | Finding |
| --- | --- | --- |
| `GET /console/api/agents/roster/` | `fetchAgentRoster`, `AgentChatPage`, sidebar/components | No confirmed unused response data. Many fields are permission/gating/cache metadata. |
| `POST /console/api/agents/create/` | `createAgent`, `AgentChatPage` | No confirmed unused data. |
| `GET /console/api/agents/spawn-intent/` | `fetchAgentSpawnIntent`, `AgentChatPage` | No confirmed unused data. |
| `GET /console/api/agents/<id>/timeline/` | `useAgentTimeline`, `AgentChatPage` | `oldest_cursor`, `newest_cursor` unused. |
| `POST /console/api/agents/<id>/messages/` | `sendAgentMessage` | Returned `event` is used. |
| `POST /console/api/agents/<id>/messages/<message_id>/copy/` | `trackAgentMessageCopy` | Returned `ok` is not meaningfully used, but it is a conventional mutation ack. |
| `POST /console/api/agents/<id>/messages/<message_id>/report-issue/` | `reportAgentMessageIssue` | Response is used for issue/judge status. |
| `POST /console/api/agents/<id>/messages/latest/read/` | `markLatestAgentMessageRead` | Response is normalized and used for unread state. |
| `GET /console/api/agents/<id>/processing/` | `fetchProcessingStatus` | No confirmed unused data. |
| `POST /console/api/agents/<id>/stop/` | `stopAgentProcessing` | `cancelledWebTaskCount` is currently not rendered, but it may be useful mutation feedback; treat as low-priority. |
| `POST /console/api/agents/<id>/web-sessions/start/` | `useAgentWebSession` | Unused snapshot fields listed above. |
| `POST /console/api/agents/<id>/web-sessions/heartbeat/` | `useAgentWebSession` | Unused snapshot fields listed above. |
| `POST /console/api/agents/<id>/web-sessions/end/` | `useAgentWebSession`, `sendBeacon` | End response is not read for UI. Conventional ack only. |
| `GET /console/api/agents/<id>/settings/` | `AgentDetailScreen` | Unused fields listed above. |
| `GET/PATCH /console/api/agents/<id>/quick-settings/` | `useAgentQuickSettings`, chat/settings panels | No confirmed unused data. |
| `GET/POST /console/api/agents/<id>/addons/` | `useAgentAddons`, chat/settings panels | No confirmed unused data. |
| `GET /console/api/agents/<id>/insights/` | agent insight panels | No confirmed unused data; `refreshAfterSeconds` is typed but not currently used for query refetch. Low-priority candidate. |
| `GET /console/api/agents/<id>/suggestions/` | starter prompt suggestions | No confirmed unused data. |
| Pending action endpoints under `/human-input-requests/`, `/requested-secrets/`, `/contact-requests/`, `/spawn-requests/`, `/planning/skip/` | `agentChat.ts` mutation helpers | Returned pending action arrays are used to refresh chat state. |

### Files, Secrets, Email, Discord, Integrations

| Endpoint group | Frontend consumer | Finding |
| --- | --- | --- |
| `/console/api/agents/<id>/files/*` | `AgentFilesScreen`, embedded files panel | No confirmed unused data. |
| `/console/api/secrets/` and `/console/api/agents/<id>/secrets/` | global/agent secrets screens | No confirmed unused data. |
| `/console/api/agents/<id>/email-settings/` and related ensure/test OAuth endpoints | `AgentEmailSettingsScreen`, `static/js/agent_email_oauth*.js` | `backUrl`, `isInboundAliasActive`, `connectionLastOkAt`, `connectionError` appear unused in the current React screen. Verify before trimming because static legacy email JS may still expect differently shaped payloads. |
| `/console/api/agents/<id>/discord/*` and `/console/api/discord/disconnect/` | Discord modal/insight panels | DTO-only fields such as `active_subscription_count`, `guild_count`, and `last_message_at` are not currently rendered. Low-priority candidates. |
| `/console/api/native-integrations/` and provider connect/callback/picker/files/revoke/agent-events | MCP/native integration panels and OAuth callback JS | Core URLs and picker token fields are used. Provider `auth_type` and `api_hosts` are not rendered outside tests/shared defaults. |
| `/console/api/mcp/servers/*`, `/console/api/staff/mcp/servers/*` | MCP server screens/modals | Most fields are used. Assignment `assigned_count` is not used; the UI recomputes selected size. Low-priority candidate. |
| `/console/api/mcp/pipedream/*`, `/console/api/agents/<id>/pipedream/*` | Pipedream modals/panels | No confirmed unused data in active flows. Disconnect `deleted_count` is typed but not displayed. |

### Usage And Billing

| Endpoint | Frontend consumer | Finding |
| --- | --- | --- |
| `GET /console/api/usage/summary/` | `UsageMetricsGrid`, `AgentChatPage` | `metrics.tasks` unused. |
| `GET /console/api/usage/burn-rate/` | `AgentChatPage` intelligence gate | No confirmed unused data. |
| `GET /console/api/usage/trends/` | `UsageTrendSection` | previous-period data unused. |
| `GET /console/api/usage/agents/` | usage agent selector | No confirmed unused data. |
| `GET /console/api/usage/agents/leaderboard/` | leaderboard panel | No confirmed unused data. |
| `GET /console/api/billing/initial/` and legacy billing mutation endpoints | billing screens/components | Not deeply audited beyond frontend access patterns; no high-confidence unused fields found in this pass. |

### Staff, Evals, System Admin

| Endpoint group | Frontend consumer | Finding |
| --- | --- | --- |
| `/console/api/status/` | `SystemStatusScreen` | Unused section fields listed above. |
| `/system-settings/api/` | `SystemSettingsScreen` | No confirmed unused data; system setting metadata is rendered or used for validation/display. |
| `/console/api/llm/overview/` and LLM config mutation endpoints | LLM config admin | No confirmed unused data in active LLM config UI. |
| `/console/api/evals/*` | eval list/detail/compare screens | Many detail fields are typed but only some are rendered depending on view state. Candidate unused fields include task `expected_summary`, run `comparison.comparable_runs_count`, `has_comparable_runs`, suite `shared_agent_id`, scenario `required_fixtures`, and compare response target ids. Treat as lower priority because eval detail screens often expose data through debug/detail views. |
| `/console/api/staff/users/search/` | staff users search | No confirmed unused data. |
| `/console/api/staff/users/<id>/` and `/console/api/staff/orgs/<id>/` | staff detail panels | Most data is rendered. `billing.addons[].isRecurring` is not rendered; other billing/agent/task-credit fields are used. |
| staff email verify/trigger/task-credit/system-message/process endpoints | staff action modals | Responses are used for acknowledgements/counts. |
| staff agent audit endpoints | `AgentAuditScreen`, audit store/components | A few audit event fields are typed but not rendered (`filespace_node_id`, `peer_link_id`), but audit data is intentionally diagnostic. Avoid trimming without staff UX review. |

### Public And Marketing Page Loads

| Endpoint/page | Backend/template | Finding |
| --- | --- | --- |
| `/` homepage | `HomePage`, `pages/templates/home.html` | Unused page context listed above. |
| `/plans/<plan>/` | `PaidPlanLanding`, `plan_landing.html` | Context appears template-driven; no confirmed unused fields from this pass. |
| proprietary pricing/support/prequal/contact/blog pages | `proprietary/views.py` templates | Context appears consumed by templates/forms. |
| public template/pretrained worker detail pages | `pages/views.py` detail templates | SEO/structured-data and page sections are consumed by templates. |
| static legal/about/team/careers/comparison/solution pages | `pages/views.py` templates | Mostly static context; no confirmed unused fields found. |
| account modal/login/signup endpoints | `config/account_views.py`, allauth templates, static auth JS | Auth modal payloads are consumed by templates/static JS; no high-confidence unused fields found. |

## Recommended Cleanup Order

1. Remove top-level timeline cursors or switch the frontend to use them.
2. Slim web-session start/heartbeat responses to `session_key` and `ttl_seconds`.
3. Remove unused usage summary/trend fields, especially previous-period calculations.
4. Trim or render unused system status details.
5. Trim small agent-settings helper fields after confirming no planned UI detail/tooltips need them.

## Verification Notes

This was a static audit. I did not run Django tests because no code behavior changed and the project instruction says not to run the full suite for now. Before removing fields, add focused API/React tests for any endpoint where external consumers or non-React static scripts may depend on the current response shape.
