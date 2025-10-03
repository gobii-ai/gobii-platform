# Agent-to-Agent Messaging Plan

## Goals
- Enable direct communication between persistent agents owned by the same user or belonging to the same organization.
- Prevent infinite or runaway message loops via configurable rate limits, short-term debouncing, and clear agent instructions.
- Keep the initial implementation small, reliable, and extensible for future channels and features.

## Scope Summary
1. **Agent Links**
   - Introduce an `AgentPeerLink` model representing a symmetric relationship between two agents sharing an owner or organization.
   - Store per-link communication settings (quota size/window, optional preferred endpoints, feature flag state).
   - Reuse `PersistentAgentConversation` rows for DM history with a boolean `is_peer_dm` flag; no new channel enum for v1.

2. **Rate Limiting & Debounce**
   - Add `AgentCommPeerState` to hold a rolling credit bucket per `(link, channel)` with fields for `messages_per_window`, `window_hours`, `credits_remaining`, `window_reset_at`, plus a `last_message_at` timestamp for ~5s duplicate suppression.
   - Default quotas to 30 messages per 6 hours; allow user overrides via UI when creating/editing links.
   - Before sending:
     - Run debounce check (reject if last message < debounce window).
     - Draw down credits; if empty, enqueue Celery task with ETA `window_reset_at` and surface a throttle message to the agent/tool.
     - On reschedule execution, credits are automatically refreshed when the window rolls.
   - Maintain the existing Redis-based agent-level debounce for follow-up scheduling; keep the new peer-level limiter separate but align logging/terminology for consistency.

3. **Messaging Flow Changes**
   - Implement `send_agent_message` tool leveraging the shared DM conversation.
   - On inbound peer messages, record the link, apply quota checks, and queue `process_agent_events_task` immediately or with ETA as needed.
   - Persist `peer_agent_id` on `PersistentAgentMessage` for auditing and prompt context.

4. **Prompt & UX Updates**
   - Inject into system prompt when the active event is a peer DM:
     > "This is an agent-to-agent exchange. Minimize chatter, batch information, and avoid loops."
   - Append quota context (e.g., "Limit: 30 messages / 6 hours. Remaining credits: 12.") so the LLM manages usage.
   - Extend console Agent Detail page with an "Agent Contacts" card:
     - List existing links, quota stats, and provide unlink action.
     - Modal/picker to add agents (filtered to same owner/org) and to configure quota values.
     - Display current credit status and next reset time.

5. **Observability & Admin**
   - Emit structured logs and traces for quota consumption, throttle events, and DM sends.
   - Register `AgentPeerLink` and `AgentCommPeerState` in Django admin for inspection.

6. **Testing**
   - Unit tests for quota arithmetic, debounce logic, `send_agent_message`, and inbound scheduling.
   - Manual staging checklist: create link, exercise burst to quota, confirm deferred delivery, and validate prompt guidance.

## Open Decisions & Defaults
- Allow links only when agents share an owner or an organization (no cross-org federation yet).
- Quota UI lets owners adjust messages/window; default remains 30 per 6 hours.
- DM delivery marked "delivered" on save (no read receipts in v1).
- Feature rollout behind a flag; enable per customer as readiness allows.

## Implementation Phases
1. **Data Layer**: models, migrations, admin registration.
2. **Messaging Core**: shared rate-limit helper, DM tool, inbound/outbound hooks.
3. **Prompt & Analytics**: system prompt injection, logging tweaks.
4. **Console UI**: link management card, picker, quota inputs.
5. **Testing & Rollout**: unit tests, staging validation, feature flag enablement.

This plan keeps the first iteration lean while delivering reliable agent-to-agent collaboration with clear guardrails.
