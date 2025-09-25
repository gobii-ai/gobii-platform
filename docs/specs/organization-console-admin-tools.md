# MSU Spec: Organization Console Billing & Admin Tools

## Overview
- Build a first-class organization billing experience in the console, covering subscription overview, credit usage, and payment actions while keeping parity with the existing personal billing UX (see `console/templates/billing.html:16` onward).
- Deliver staff-facing admin tooling to adjust organization credits and surface contact usage insights so support can intervene quickly.
- Lean on the owner-aware helper APIs that already exist in `tasks.services.TaskCreditService` (`tasks/services.py:574-640`) and `util.subscription_helper` (`util/subscription_helper.py:657-759`) instead of duplicating billing math.

## Current State
- Organization visitors land on a “not yet implemented” warning ( `console/templates/billing.html:4-15` ); no path to review plan, credits, seats, or payment methods while in org context.
- Seat counts render only on the organization detail page (`console/templates/console/organization_detail.html:9-25`) and require context switching to manage billing via Stripe portal (`console/views.py:3960-4061`).
- Manual credit grants are hard-coded to users and fixed amounts (`console/views.py:2701-2748`), with no UI for staff to apply org adjustments or record reasons.
- Contact quotas are enforced per agent, yet there is no consolidated visibility for support (allowlist logic depends on `CommsAllowlistEntry` and `AgentAllowlistInvite`, see `api/models.py:2500-2790`).

## Goals
- Show organization plan, seat allocation, next billing period, invoices/payment methods, and task credit status in a single console surface.
- Provide self-serve controls for billing admins: adjust extra-task limits, launch Stripe billing portal, download invoices, manage payment methods.
- Surface credit balances and consumption trends at the org level, including additional-task usage, using helper APIs.
- Expose contact usage by agent for support teams and flag limits or pending requests that need attention.
- Provide staff with visibility into credit usage, deferring manual credit adjustments to a future follow-up.

## Non-Goals
- Replacing Stripe Checkout flows for seat purchases (existing flows remain).
- Reworking personal billing UI/logic beyond keeping shared components reusable.
- Changing the underlying task credit consumption pipeline or metering jobs.
- Automating contact limit enforcement changes; scope is visibility and manual interventions.

## Personas & Access
- **Org Owner/Admin/Billing**: may view billing overview, credits, invoices, and adjust auto-purchase settings for their org.
- **Org Member**: read-only visibility into plan summary and credit usage if policy allows; no payment actions.
- **Gobii Support Staff (is_staff)**: access to admin tools for manual credits and contact usage dashboards across orgs.

## UX Outline
- **Hero summary**: plan name, seat counts, current period end, Stripe subscription status.
- **Credit balance card**: available vs granted vs used credits, entitlement per seat, extra-task usage this period.
- **Auto-purchase controls**: toggle + limit slider mirroring personal UI but backed by org helper APIs.
- **Payment actions**: buttons to update payment method (Stripe billing portal), download latest invoices, view invoice history (modal or table).
- **Activity log**: highlight plan/usage events sourced from helper APIs (manual adjustments optional for future iteration).

### Admin Tools (staff-only console section)
- **Usage monitoring**: dashboards for org credit/contact usage; manual adjustment tooling can be layered on later if required.
- **Contact usage dashboard**: searchable table of orgs → agents showing active contacts, pending requests, limit, percentage used, and ability to drill to agent detail.

## Functional Requirements
### Data Retrieval
- Use `TaskCreditService.get_current_task_credit_for_owner` to fetch active credit blocks per org (`tasks/services.py:582-596`).
- Derive entitlement and remaining totals via `TaskCreditService.get_tasks_entitled_for_owner` and `TaskCreditService.calculate_available_tasks` (owner-aware) or extend with an org-aware wrapper reusing helper math.
- Pull plan metadata, included credits, extra-task caps, and billing period via:
  - `get_organization_plan` (`util/subscription_helper.py:640-654`)
  - `get_organization_task_credit_limit` (`util/subscription_helper.py:657-678`)
  - `get_organization_extra_task_limit` and `allow_organization_extra_tasks` (`util/subscription_helper.py:681-706`)
  - `calculate_org_extra_tasks_used_during_subscription_period` (`util/subscription_helper.py:735-759`).
- Resolve billing cycle anchors with `BillingService.get_current_billing_period_for_owner` (`billing/services.py:96-129`).
- Seat data comes from `Organization.billing` fields (`api/models.py:1164-1250`).
- Contact usage uses `CommsAllowlistEntry` and `AgentAllowlistInvite` counts grouped by agent, enriched with plan limits from `get_user_max_contacts_per_agent` for the owning user (`util/subscription_helper.py:938-960`).

### API & View Layer
- Introduce a dedicated helper module (e.g. `console/org_billing_helpers.py`) that composes the above helpers into serializable payloads for templates or JSON responses.
- Add JSON endpoints under `/api/v1/org/{org_id}/billing-overview/`, guarded by membership checks mirroring `build_console_context` (`console/context_helpers.py:39-86`). Responses should include plan summary, credit aggregates, extra-task usage, billing period, and invoice links (from Stripe customer if present).
- Extend existing billing settings endpoints to support organizations:
  - `/billing/settings/update/` to accept `context_type` and route to org vs user path, updating `OrganizationBilling.max_extra_tasks` when in org context.
  - `/api/v1/user/billing-settings/` equivalent for org context returning org extra-task configuration.
- Implement contact usage API `/admin/api/org-contact-usage/` delivering aggregated counts for dashboards (manual adjustment endpoints optional future work).

### Persistence & Auditability
- Keep schema changes minimal initially; manual grant logging can be addressed when adjustment tooling ships.
- Store invoice metadata (Stripe invoice IDs, hosted URL) if not already captured; expose via spec without altering Stripe syncing pipeline.

### Frontend
- Replace the org warning block in `console/templates/billing.html` with the full layout when `current_context.type == 'organization'` (line 4). Use Alpine component to fetch the new org billing endpoint and hydrate the UI, similar to `billingSettings()` (`console/templates/billing.html:235-320`).
- Factor shared components (e.g., credit progress, auto-purchase toggle) so they accept owner-agnostic data.
- Build staff admin views under `console/templates/admin/` or reuse existing console layout with feature flag gating (could leverage Waffle `ORGANIZATIONS` flag already used in seat views).

## Permissions & Security
- Membership enforcement: reuse checks from `OrganizationSeatPortalView` (`console/views.py:3968-4043`) so only OWNER/ADMIN/BILLING roles can modify settings or open Stripe portal.
- Staff endpoints require `is_staff` and should respect organization ownership to avoid cross-tenant exposure.
- Ensure manual adjustments validate that exactly one of user or organization is targeted, aligning with `TaskCredit` check constraint (`api/models.py:210-232`).

## Observability & Analytics
- Wrap new helper calls with existing `@tracer` decorators to keep traces consistent.
- Emit analytics events for billing overview load, extra-task toggle changes, manual adjustment actions (extend `AnalyticsEvent` as needed).
- Log contact usage fetches and flag over-limit conditions for alerting.

## Testing Strategy
- Unit tests covering helper aggregation, billing endpoints, and contact usage calculations; tag with `@tag('org_billing_batch')` per repo convention.
- Integration tests for manual adjustment flow can follow when that feature is prioritised.
- Template tests verifying org context renders the new billing sections and hides controls for non-admin roles.

## Rollout Plan
1. Ship backend helper + API scaffolding behind Waffle flag.
2. Integrate front-end UI while feature-flagged; dogfood with internal orgs.
3. Enable staff admin tools in staging, backfill adjustment history by parsing historic TaskCredit records where possible.
4. Flip flag after verifying end-to-end billing and adjustment flows; monitor traces and logs for anomalies.

## Risks & Open Questions
- Stripe invoice retrieval: do we need additional sync jobs to surface historical invoices or can we rely on Stripe billing portal only?
- Manual adjustments for negative credits may require refund handling with Stripe; clarify policy with finance before enabling debit adjustments.
- Contact limits currently derive from user plan; if org plans diverge, we may need an org-level contact quota helper.
- Performance for contact usage dashboards on large orgs—consider pagination or async updates if counts prove expensive.
