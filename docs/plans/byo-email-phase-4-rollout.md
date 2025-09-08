# BYO Email – Phase 4: Rollout & Optional Enhancements

Goal: Safely ship the feature behind flags, migrate progressively, and outline optional enhancements (e.g., DSN/bounce parsing).

## Rollout Plan

- Feature flag: `BYO_EMAIL_ENABLED` (default true for OSS, configurable in SaaS).
- Migrations: introduce `AgentEmailAccount` in an additive migration; enforce linking only to self/from endpoints (`owner_agent IS NOT NULL`); no schema changes to messages.
- Deploy steps:
  1. Ship Phase 1 (models + SMTP + selection) with Admin “Test SMTP”.
  2. Enable in staging; configure a few test accounts; exercise send.
  3. Ship Phase 2 (IMAP adapter + poller); enable limited set of accounts; verify UID tracking and ingestion.
  4. Phase 3 UI polish and docs; enable to all.

## Monitoring

- Error budgets: percent of failed SMTP sends and IMAP connection failures below threshold.
- Logs/spans sampling: ensure spans include server host/port but never secrets.
- Backoff metrics: count accounts in backoff to spot systemic auth issues.

## Optional Enhancements

- DSN/bounce parsing via IMAP:
  - Filter for “delivery status notification” content-type or subject patterns.
  - Parse `Action: failed` and `Original-Recipient` to update `OutboundMessageAttempt` to FAILED.
  - Use `X-Gobii-Message-ID` header correlation (set in Phase 1) to map to attempt.
- Outbound connection pooling (per account):
  - Simple short-lived pool with TTL; consider only if sends per account are frequent.
- OAuth2 for Gmail/Outlook:
  - Add identity flow and token storage; out-of-scope for v1; document roadmap.
- Per-account rate limits:
  - Respect provider quotas; throttle sends per minute/hour per account via Redis counters.

## References

- Transport selection: `api/agent/comms/outbound_delivery.py` (deliver_agent_email around L373+)
- Ingestion: `api/agent/comms/message_service.py` (L132–L210)
- Distributed lock pattern: `api/agent/core/event_processing.py` (L504–L640)
- Redis client: `config/redis_client.py` (L135+)
