# BYO Email – Phase 3: UI, Telemetry, Docs

Goal: Provide a solid UX in admin/console to configure SMTP/IMAP per endpoint, test connectivity, and observe health. Add docs and analytics where appropriate.

## Admin UI

- Inline or detail editing for `AgentEmailAccount` under `PersistentAgentCommsEndpoint` (email, self/from only):
  - Group sections: SMTP (outbound), IMAP (inbound), Health.
  - Fields: match model. Password fields are write-only (show as **** when set; update only if provided).
  - Validation: enforce required fields only when toggles enabled.
- Actions:
  - “Test SMTP”: run a safe connection attempt (EHLO, optional STARTTLS, optional LOGIN). Update `connection_last_ok_at` or `connection_error`.
  - “Test IMAP”: connect, LOGIN, SELECT folder, `NOOP`. Update health fields.
  - “Poll Now”: enqueue `poll_imap_inbox(account_id)`.
- Status badges: last OK timestamp, last error summary, inbound/outbound enabled flags, poll interval.

Code references for patterns to copy:
- Admin view examples and HTMX style: `api/admin.py` around simulate email/SMS views (~L1188–L1360).
- Templates for admin modals/forms: `templates/admin/api/persistentagent/simulate_email.html`.

## Console UI (if applicable)

- Page under agent → communications → email:
  - Forms mirroring Admin fields/toggles with help text.
  - Only show/create accounts for self/from endpoints (owned by the agent).
  - Test buttons show toast with status.
  - Require successful test before allowing “Enable Outbound/Inbound.”

## Telemetry

- Tracing (OpenTelemetry):
  - SMTP send span: `email.smtp.send` with `smtp.host`, `smtp.port`, `smtp.security`, `to_count`, `cc_count`.
  - IMAP poll span: `email.imap.poll` with `imap.host`, `imap.port`, `folder`, `batch_size`, `new_uid_count`.
  - Mask usernames; never include passwords or full addresses where not necessary.
- Analytics events (Segment – see `util/analytics.py`):
  - Track “Email Account Created/Updated”, “SMTP Test Passed/Failed”, “IMAP Test Passed/Failed”.
  - For delivery metrics, reuse existing `PERSISTENT_AGENT_EMAIL_SENT` when SMTP used (already fired in `deliver_agent_email`).

## Docs

- New guide: “Bring Your Own Email” covering:
  - How to create an agent email endpoint (no custom domain needed for BYO).
  - How to add SMTP/IMAP settings (screenshots), recommend app passwords.
  - Poll interval guidance and rate considerations (suggest 60–300s).
  - Feature differences vs SaaS Postmark (no opens/clicks/bounces for BYO).
- Troubleshooting section:
  - Common auth errors, STARTTLS vs SSL, non-ASCII mailbox names, provider restrictions (MAIL FROM must match auth user), 2FA.

## QA Checklist

- SMTP test connects and fails gracefully with wrong creds.
- IMAP test connects and properly selects folder; failure messages are actionable.
- Enabling outbound/inbound requires passing test (unless overridden by staff/admin flag in dev).
- Health badges update on successful send/poll.
