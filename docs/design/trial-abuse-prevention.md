# Trial Abuse Prevention Plan

## Goal

Reduce free-trial abuse without blocking legitimate paid conversions.

Primary product behavior:

- suspicious users can still subscribe immediately
- suspicious users do not automatically receive a free trial
- support can permanently allow trial access for a user
- support can later roll that override back if needed


## Current State

The codebase already has some useful pieces:

- `UserAttribution` already stores `ga_client_id`, `last_client_ip`, `last_user_agent`, and `fbp`
- signup attribution is persisted in `pages/signals.py`
- checkout trial eligibility is currently based on Stripe customer subscription history only
- FPJS is currently loaded only on the signup page and only fills the password signup form hidden field

Relevant hooks:

- `api/models.py`
- `pages/signals.py`
- `config/socialaccount_adapter.py`
- `pages/views.py`
- `proprietary/views.py`
- `console/views.py`
- `constants/stripe.py`


## Design Principles

- Keep marketing attribution and anti-abuse identity matching separate.
- Use a dedicated anti-abuse data model now so we do not have to migrate off `UserAttribution` later.
- Only use FingerprintJS at signup, due to cost.
- Capture cheaper signals at signup, login, and checkout start.
- Gate trial at checkout, not at account creation.
- Prefer deterministic rules first, then add scoring later if needed.
- Avoid auto-denial on weak signals like IP alone.


## Proposed Data Model

### 1. `UserIdentitySignal`

Purpose: normalized identity evidence used for matching users across accounts.

Suggested fields:

- `user`
- `signal_type`
- `signal_value`
- `first_seen_at`
- `last_seen_at`
- `first_seen_source`
- `last_seen_source`
- `observation_count`
- `created_at`
- `updated_at`

Suggested `signal_type` values:

- `fpjs_visitor_id`
- `fpjs_request_id`
- `fbp`
- `ga_client_id`
- `ip_exact`
- `ip_prefix`

Notes:

- store raw canonical values in this table
- normalize values before writing them so exact matching stays stable
- unique constraint on `(user, signal_type, signal_value)`
- index on `(signal_type, signal_value)`

Storage policy by signal type:

- `fpjs_visitor_id`: store raw vendor value
- `fpjs_request_id`: store raw vendor value
- `fbp`: store raw browser identifier
- `ga_client_id`: store raw GA client identifier
- `ip_exact`: store raw normalized IP
- `ip_prefix`: store raw normalized prefix

Reason:

- raw FPJS identifiers are useful because they can be pasted into Fingerprint's tooling for deeper investigation
- raw identifiers make support investigation and vendor lookups practical
- exact-match abuse checks do not require hashing, only stable normalization
- staff visibility should still be limited to detail views and review tooling rather than broad list displays


### 2. `UserTrialEligibility`

Purpose: current trial decision, reasons, and support override state.

Suggested fields:

- `user`
- `auto_status`
- `manual_action`
- `reason_codes`
- `evidence_summary`
- `evaluated_at`
- `reviewed_by`
- `reviewed_at`
- `review_note`
- `created_at`
- `updated_at`

Suggested enums:

- `auto_status`: `eligible`, `no_trial`, `review`
- `manual_action`: `inherit`, `allow_trial`, `deny_trial`

Notes:

- support override should be permanent until explicitly rolled back
- final decision logic should be:
  - if `manual_action=allow_trial`, allow trial
  - if `manual_action=deny_trial`, deny trial
  - otherwise use `auto_status`


## Signals To Capture

### Strong signal

- FingerprintJS visitor ID
- FingerprintJS request ID

### Medium signals

- `_fbp`
- GA client ID

### Weak signals

- exact client IP
- IP prefix

### Context-only initially

- user agent

User agent is useful for review context, but should not drive automatic trial denial in v1.


## How To Capture Signals

### Signup

Capture and persist:

- FPJS visitor ID
- `_fbp`
- GA client ID
- exact IP
- IP prefix

Password signup:

- read the hidden FPJS field already being written in `templates/account/signup.html`
- extend this to also capture FPJS request ID

Social signup:

- before redirecting to the provider, stash FPJS visitor ID and GA client ID in signed first-party cookies
- also stash FPJS request ID
- restore them in the OAuth callback path similarly to the existing OAuth stash cookies

This is required if social signup is to be covered by the same protection model.


### Login

Capture and persist:

- `_fbp`
- GA client ID
- exact IP
- IP prefix

Do not use FPJS here.


### Checkout Start

Capture and persist again right before trial eligibility is evaluated:

- `_fbp`
- GA client ID
- exact IP
- IP prefix

This gives us fresher cheap signals at the actual decision point.


## GA Signal Choice

Preferred signal:

- the actual Google Analytics client ID obtained via `gtag('get', measurement_id, 'client_id', ...)`

Fallback:

- `_ga` cookie value

Reason:

- this is more accurate than relying only on property-specific cookie names
- it still works even if cookie naming changes across GA setups


## Trial Decision Engine

Create a shared service, for example:

- `api/services/trial_eligibility.py`

Suggested API:

- `evaluate_user_trial_eligibility(user, request=None) -> TrialEligibilityResult`

Suggested result shape:

- `eligible: bool`
- `decision: "eligible" | "no_trial" | "review"`
- `reason_codes: list[str]`
- `matched_users: list[int]`
- `evidence_summary: dict`

This service should become the single source of truth used by:

- pricing CTA logic
- checkout trial inclusion logic
- console plan/trial eligibility payloads


## Initial Rules

### Automatic no-trial

- user has prior individual subscription history in Stripe
- FPJS visitor ID matches another user who already started a free trial
- FPJS visitor ID matches another user who already had an individual subscription
- FPJS request history strongly links this signup to a previously trialed identity

### Review

- two medium signals match a prior trial or subscribed user
- many recent signups or trial attempts share the same IP prefix in a short window

### Do not auto-deny on

- IP alone
- user agent alone


## User Experience

Do not block account creation.

Preferred behavior:

- signup succeeds
- suspicious user can still buy immediately
- suspicious user does not get a free trial automatically

Suggested user-facing message:

- "Free trial is unavailable for this account. You can still subscribe now, or contact support if this is a mistake."

If support override is later added:

- user should immediately become trial-eligible again without needing any data cleanup


## Support Workflow

Support/admin needs:

- visibility into the current trial decision
- masked evidence summary
- list of matched signal types
- raw FPJS visitor ID and request ID for direct Fingerprint lookup
- permanent override controls

Suggested controls:

- `Allow trial permanently`
- `Deny trial`
- `Reset to automatic decision`

This can start in Django admin before any custom internal UI exists.


## Stripe / Payment Handling

### Disable Link for personal checkout

Given the concern that Link can obscure card identity, individual self-serve trial/subscription checkout should not allow Link.

Current shared constants still include:

- `CHECKOUT_PAYMENT_METHOD_TYPES = ["card", "link"]`

Recommendation:

- for personal plan checkout flows, explicitly set `payment_method_types=["card"]`
- do not rely only on `excluded_payment_method_types`

This should apply at least to:

- personal Pro checkout
- personal Scale checkout

Organization flows can be evaluated separately if needed.


## Recommended Implementation Order

### Phase 1

- add `UserIdentitySignal`
- add `UserTrialEligibility`
- capture FPJS on password signup
- capture FPJS across social signup via signed cookies
- capture `_fbp`, GA client ID, and IP at signup, login, and checkout start
- implement shared trial eligibility service
- replace current trial checks in pricing, checkout, and console with the service
- disable Link for personal checkout
- add Django admin visibility and permanent override controls

### Phase 2

- add stronger payment-side signals, especially Stripe card fingerprint where available
- add velocity rules
- add staff review tooling beyond Django admin
- add analytics/telemetry around false positives and override frequency


## Why This Structure

This keeps concerns separated cleanly:

- `UserAttribution` remains marketing-oriented
- `UserIdentitySignal` becomes the evolving identity-evidence store
- `UserTrialEligibility` becomes the evolving decision and override record

That gives room to expand rules later without needing a future table migration away from attribution.


## Non-Goals For V1

- full fraud scoring framework
- automatic signup rejection
- account suspension based on weak evidence
- heavy real-time evaluation on every request


## Concrete Next Step

Implement the data model and shared service first, then cut over the existing trial checks to that service before adding more advanced rules.
