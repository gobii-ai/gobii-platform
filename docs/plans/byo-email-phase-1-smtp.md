# BYO Email – Phase 1: Models + Outbound SMTP + Selection

Goal: Add per-agent SMTP accounts and route outbound email through the user’s SMTP server when configured on that agent’s email endpoint. Keep Postmark default in SaaS when no account is configured; use simulation in OSS when nothing is configured.

## Summary

- Add `AgentEmailAccount` (1:1 with `PersistentAgentCommsEndpoint` for channel=email) storing SMTP and IMAP creds (IMAP read-only in Phase 1).
- Implement `SmtpTransport` using `smtplib` (per-send connection), not Django’s global mail backend.
- Selection logic in `deliver_agent_email`: per-endpoint SMTP if enabled; else Postmark in SaaS; else simulation.
- Admin/console: add forms to store SMTP creds; add “Test SMTP” action; require success before enabling outbound.

## Relevant Code References (read first)

- Outbound delivery flow: `api/agent/comms/outbound_delivery.py`
  - `deliver_agent_email(...)` entry point and content wrapping
    - Content conversion call around: L408–L418
    - Template wrapping and Anymail/attempt handling: L420–L536
- Email content prep (centralized): `api/agent/comms/email_content.py`
  - `convert_body_to_html_and_plaintext(body)` – HTML/Markdown/plaintext detection
- Message model for delivery tracking: `api/models.py`
  - `PersistentAgentMessage` (id, endpoints, cc, status): ~L2228–L2360
  - `OutboundMessageAttempt` usage (status updates) referenced in delivery code
- Webhooks (context: inbound email in Phase 2): `api/webhooks.py`
  - `email_webhook` endpoint: ~L215–L370
- Whitelist enforcement (inbound/outbound): `api/models.py`
  - `PersistentAgent.is_sender_whitelisted` and `is_recipient_whitelisted`: ~L1180–L1376
- Admin patterns for per-agent actions (for Test SMTP UI): `api/admin.py`
  - Simulate inbound email view for reference: ~L1188–L1260

## Data Model

Add new model `AgentEmailAccount` (in `api/models.py`) with a 1:1 FK to a self/from `PersistentAgentCommsEndpoint` (agent‑owned) where `channel=email`.

Fields (smtp-only for Phase 1; IMAP stored now but used in Phase 2):

- `endpoint` (OneToOneField → `PersistentAgentCommsEndpoint`, unique)
- SMTP outbound
  - `smtp_host` (str)
  - `smtp_port` (int)
  - `smtp_security` (choice: `ssl`, `starttls`, `none`)
  - `smtp_auth` (choice: `plain`, `login`, `none`)
  - `smtp_username` (str)
  - `smtp_password_encrypted` (BinaryField via `SecretsEncryption`)
  - `is_outbound_enabled` (bool)
- IMAP inbound (Phase 2)
  - `imap_host`, `imap_port`, `imap_security`, `imap_username`, `imap_password_encrypted`, `imap_folder` (default `INBOX`), `is_inbound_enabled`
  - `poll_interval_sec` (default 120), `last_polled_at`, `last_seen_uid`, `backoff_until`
- Health
  - `connection_last_ok_at` (datetime), `connection_error` (text)
  - `created_at`, `updated_at`

Validation
- Enforce 1:1 relation with a self/from email endpoint only: `channel=email` AND `owner_agent IS NOT NULL` (agent‑owned). External `to`/`cc` endpoints (typically `owner_agent=NULL`) must not have accounts.
- Require SMTP fields if `is_outbound_enabled` is true.
- Use `SecretsEncryption` for passwords (see `api/models.py` ~L1488–L1660 for patterns).

Migrations
- Create migration `00xx_agent_email_account.py` with model and indexes:
  - Index on `(endpoint)` and `(is_outbound_enabled)`.
  - Protective unique constraint endpoint→account.

## Outbound SMTP Transport

Add `SmtpTransport` (new file: `api/agent/comms/smtp_transport.py`):

- Use `smtplib.SMTP_SSL` for `smtp_security=ssl` (port typically 465) or `smtplib.SMTP` + `starttls()` for `starttls` (port typically 587). Default timeouts (e.g., 30s).
- Build the message via `email.message.EmailMessage`:
  - Set `From` to endpoint address (`message.from_endpoint.address`).
  - `To` primary, `Cc` list (from `message.cc_endpoints`).
  - `Subject` from `message.raw_payload['subject']`.
  - `Message-ID` – let `email.utils.make_msgid` generate one.
  - Add custom header `X-Gobii-Message-ID: <attempt_id>` for future DSN/bounce attribution.
  - Alternate parts: `text/plain` and `text/html` from `convert_body_to_html_and_plaintext` and HTML template render (`emails/persistent_agent_email.html`).
- Auth using account’s `smtp_auth`:
  - `none`: skip `.login()`
  - `plain`/`login`: call `.login(username, password)`
- Send with `.send_message(msg, from_addr=endpoint.address, to_addrs=[to + cc])`, then `.quit()`.

Error handling
- Wrap send in try/except; map errors to `OutboundMessageAttempt` with `FAILED`, set `latest_error_message`.
- On success, set `SENT`, store minimal provider id if available (some SMTP servers return no id; keep empty string).

## Selection Logic

Modify `deliver_agent_email` in `api/agent/comms/outbound_delivery.py` (~L373+):

1) Before creating attempt, select transport based on the message’s self/from endpoint:
   - If `from_endpoint.agentemailaccount.is_outbound_enabled` → `SmtpTransport`.
   - Else if `settings.GOBII_PROPRIETARY_MODE` and Postmark token present → Postmark (existing flow).
   - Else → simulation (`SIMULATE_EMAIL_DELIVERY`).

2) If SMTP chosen (self/from endpoint override):
   - Create attempt with provider=`smtp` (not `postmark`).
   - Build message bodies with `convert_body_to_html_and_plaintext`, render HTML template (`templates/emails/persistent_agent_email.html`).
   - Call `SmtpTransport.send(...)` with per-account creds and addresses.
   - Update attempt/message status accordingly.

3) Keep Postmark path unchanged for SaaS default.

Note: content conversion is centralized at `api/agent/comms/email_content.py` and is already used in delivery (L408–L418), so reuse its outputs.

## Admin/Console UI (Phase 1 scope)

- Add admin inline for `AgentEmailAccount` under `PersistentAgentCommsEndpoint` (see `api/admin.py` patterns around inline admin classes, ~L1354+). Restrict to self/from endpoints (`owner_agent IS NOT NULL`). Fields: SMTP settings, `is_outbound_enabled` toggle.
- “Test SMTP” admin action:
  - Attempts connect + optional STARTTLS + optional login using provided fields, sends a `NOOP` or EHLO, closes.
  - On success, set `connection_last_ok_at`; on failure, set `connection_error`.
  - Disable `is_outbound_enabled` until a successful test (UX/validation).

Console UI (if available in scope now or Phase 3): mirror the admin behavior with nicer UX and error messages.

## Telemetry

- Add spans around SMTP send with attributes: `smtp.host`, `smtp.port`, `smtp.security`, `from`, `to_count`, `cc_count` (no usernames/passwords).
- On failure, log exception and span event.

## Tests

- Unit tests for SMTP transport:
  - Successful send (mock `smtplib`), correct headers/body, CC handling.
  - Auth variants (none/plain/login), SSL vs STARTTLS.
  - Failure paths update attempts and message status.
- Selection tests:
  - With per-endpoint SMTP enabled, `deliver_agent_email` uses SMTP provider.
  - In SaaS w/out account, still uses Postmark path.
  - In OSS w/out account, simulation path remains.

## Risks & Mitigations

- Provider policies requiring `MAIL FROM == From == username`: we set `From` and envelope sender to the endpoint’s address to conform; document any provider-specific caveats.
- Throughput: per-send connection (no pooling) for simplicity; can add pooling later if needed.
- Secrets: encrypted at rest, never logged.
