# BYO Email – Phase 2: Inbound IMAP + Poller

Goal: Receive inbound email from user-provided IMAP mailboxes per agent endpoint. Parse messages into the existing ingestion pipeline and wake agents. Poll frequency is configurable per account.

## Summary

- Implement `ImapEmailAdapter` to normalize RFC822 emails into `ParsedMessage` (sender, subject, body, attachments).
- Add Celery tasks to poll IMAP per account at `poll_interval_sec`, using UID-based incremental fetch.
- Enforce whitelisting and attachment size limits; reuse existing ingestion and event-processing flow.

## Relevant Code References (read first)

- Inbound webhook (Postmark reference): `api/webhooks.py`
  - `email_webhook` flow, address matching, whitelist: ~L215–L370
- Message ingestion service: `api/agent/comms/message_service.py`
  - `ingest_inbound_message(...)`: ~L132–L210 (endpoint/conversation creation, attachment saving, wake agent)
  - Attachment saving + size enforcement: ~L67–L132
- Models: `api/models.py`
  - `PersistentAgentMessage` & `PersistentAgentMessageAttachment`: ~L2228–L2360
  - Whitelist checks: ~L1180–L1376
- Distributed lock pattern (reference for per-account lock style): `api/agent/core/event_processing.py`
  - Redlock acquisition & release: ~L504–L640
  - `get_redis_client`: `config/redis_client.py` ~L135+

## Adapter: `ImapEmailAdapter`

New file: `api/agent/comms/imap_adapter.py`

- Input: raw RFC822 bytes (from IMAP `FETCH BODY[]`), associated self/from endpoint address (the agent‑owned account we polled).
- Output: `ParsedMessage` (reuse `api/agent/comms/adapters.py` dataclass):
  - `sender` from `From:` header (normalize `Name <addr>` → `addr`).
  - `recipient` = endpoint.address (self/from address — we know which mailbox we polled).
  - `subject` from `Subject:` (decoded headers).
  - `body`:
    - Prefer `text/plain` part; else derive text from `text/html` using existing conversion utilities in `email_content.py` (or a simplified HTML→text for body); ensure reply/forward stripping respects `EMAIL_STRIP_REPLIES`.
  - `attachments`: list of decoded file-like objects (respect `MAX_FILE_SIZE`; skip oversize; name, content_type, size).
  - `raw_payload`: include message-id, references, simplified header map for debugging.
  - `msg_channel=CommsChannel.EMAIL`.

Edge cases
- Multipart/alternative and mixed parts: choose best body; collect attachments excluding inline images unless desired (Phase 2: include inline as attachments for now).
- Charset decoding: rely on `email` package; gracefully fallback to `utf-8` with `errors=replace`.

## Poller Tasks

New file: `api/agent/tasks/email_polling.py`

Tasks
- `poll_imap_inboxes()` – runs periodically (e.g., every 60s via RedBeat) and selects due accounts on self/from endpoints: `now >= coalesce(backoff_until, last_polled_at + poll_interval_sec)` and `is_inbound_enabled=1`.
- `poll_imap_inbox(account_id: str)` – acquires a distributed lock, connects, fetches messages, ingests them, updates state.

Locking
- Use `pottery.Redlock` keyed by account id, similar to event processing (`api/agent/core/event_processing.py` ~L504–L640).

Connection & Fetch
- `imaplib.IMAP4_SSL` for `imap_security=ssl` or `IMAP4` + `.starttls()` for `starttls`.
- `LOGIN username password`, `SELECT imap_folder` (default `INBOX`).
- Use UIDs: `UID SEARCH UID {last_seen_uid+1}:*` to get new UIDs; fetch in batches (e.g., 100–200 per run): `UID FETCH <uids> (BODY.PEEK[] FLAGS)`.
- For each message:
  - Pass raw bytes to `ImapEmailAdapter.parse(...)` to get `ParsedMessage`.
  - Call `ingest_inbound_message(CommsChannel.EMAIL, parsed)`.
  - Update `last_seen_uid` to highest successfully processed UID.
- On completion: set `last_polled_at=now`, clear `connection_error`, set `connection_last_ok_at`.

Backoff & Errors
- Catch auth/network exceptions; set `connection_error` and compute `backoff_until` (exponential backoff e.g., 2m→4m→8m up to 1h). Do not update `last_seen_uid` on errors.

Due Selection Query
- Add queryset helper (manager or service) filtering `AgentEmailAccount` by inbound-enabled & due now.
- Add small jitter to avoid synchronized storms (e.g., ±10%).

## Ingestion & Whitelist

- The IMAP path sets `recipient = endpoint.address`, so routing is deterministic (no need to parse To/Cc/Bcc for matching). Whitelist still applies via `ingest_inbound_message` → later wakeup path.
- The ingestion service already:
  - Creates endpoints if needed, creates conversations, messages, and saves attachments (`api/agent/comms/message_service.py` ~L132–L210).
  - Triggers `process_agent_events_task` after commit (L205–L210).

## Admin/Console UI (Phase 2 scope)

- Extend `AgentEmailAccount` admin form with IMAP fields and `is_inbound_enabled` toggle.
- “Test IMAP” action (admin): connect, login, select folder, `UID SEARCH ALL` or `NOOP`; update `connection_last_ok_at`/`connection_error`.
- “Poll Now” action to enqueue `poll_imap_inbox(account_id)`.

## Telemetry & Limits

- Spans for poll start/end and per-message processing; add attributes: `imap.host`, `imap.port`, `folder`, `batch_size` (mask usernames; never log passwords).
- Enforce per-run caps and `poll_interval_sec` minimum (e.g., ≥30s); store `poll_interval_sec` in the DB per account.

## Tests

- Adapter tests: HTML/plain multipart, charsets, inline attachments, oversized attachments dropped.
- Poller tests: UID tracking, batching, due selection, backoff scheduling, lock behavior (single run per account), ingestion call.

## Risks & Notes

- Gmail/Outlook provisioning: document need for app passwords; OAuth2 out-of-scope for v1.
- UID semantics vary slightly across servers; using standard `UID SEARCH` + `UID FETCH` is robust for INBOX.
