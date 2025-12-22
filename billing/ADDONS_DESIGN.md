# Add-on Framework Proposal

This note summarizes a minimal way to support Pro add-ons (prepaid task credit packs and higher per-agent contact caps) using the existing billing structures.

## Current state
- Plan defaults (task credits, contact caps, agent limits) are defined in `PLAN_CONFIG` and refreshed with Stripe product IDs on startup. There is no explicit add-on concept today.【config/plans.py†L13-L107】
- Per-user overrides already exist on `UserBilling` for extra tasks and max contacts per agent; per-organization overrides exist for extra tasks. These act as the effective limits used across helper logic.【api/models.py†L2588-L2636】【api/models.py†L2730-L2771】
- Contact caps are resolved by prioritizing organization plan defaults, then per-user overrides, then legacy quota overrides, and finally falling back to the plan default.【util/subscription_helper.py†L1279-L1354】

## Recommended approach

### 1) Model add-ons as distinct Stripe prices + entitlements table
- Keep base plans unchanged in `PLAN_CONFIG`; introduce Stripe products/prices for each add-on (e.g., `task_pack_1k`, `contact_cap_plus_50`). Store the per-environment product IDs and comma-separated price ID lists on `StripeConfig` (and propagate them through `StripeSettings`) just like existing plan and dedicated IP entries so they can be edited in admin without redeploys.【api/models.py†L2875-L3074】【config/stripe_config.py†L20-L137】
- Add an `AddonEntitlement` model (user- or org-scoped) that records the purchased quantity, price ID, effective period, and applied dimensions (task credits added, contact cap override). This preserves purchase history instead of collapsing state into a single integer.
- On subscription renewal, grant/refresh entitlements that are marked recurring; for one-off purchases tied to the current cycle, set `expires_at` to the billing period end.

### 2) Compute effective limits from entitlements + overrides
- Add helper(s) that layer entitlements on top of plan defaults: `effective_task_credit_limit = plan.monthly_task_credits + sum(active_entitlements.task_credits)`, `effective_contact_cap = max(plan.max_contacts_per_agent, active_entitlements.contact_cap_override, billing.max_contacts_per_agent_override)`.
- Keep the existing `UserBilling.max_extra_tasks` and `max_contacts_per_agent` fields as administrative overrides; treat entitlements as additive and auditable inputs before those overrides.

### 3) Checkout flow (Stripe-hosted) + lifecycle alignment (credit packs expire with the plan)
- Keep the initial plan purchase on Stripe Checkout. For add-ons, also use Stripe-hosted Checkout sessions launched from Billing settings (similar to the dedicated IP button) so quantities, tax, and payment methods are handled by Stripe while the UI remains simple.
- Enable quantity selection on the Checkout session for task packs so users can buy multiples in one flow; persist the chosen quantity on the entitlement and scale the benefit accordingly.
- When an add-on price is purchased mid-cycle, create an entitlement that ends at the purchaser’s current billing cycle anchor (matching plan reset). Grant the credits immediately and mark them with the same `expires_at` so unused credits roll off when the plan resets.
- Renewing subscriptions should re-provision entitlements for recurring add-ons at the start of each cycle and zero out expired ones so the effective limit naturally returns to the plan baseline.

### 4) Scope per user vs per organization
- Store entitlements with a polymorphic `owner_type` (user or organization) so Pro users and org plans share the same pipeline.
- For contact caps, continue to prioritize org defaults when `organization` is provided; then apply any active org entitlements; then fall back to user entitlements/overrides for individual plans, preserving the existing precedence order in `get_user_max_contacts_per_agent`.

### 5) Surfaces and telemetry
- Expose active add-ons in billing settings (line items showing quantity and expiry) and emit analytics events on purchase, renewal, and expiry for upsell insights.
- Add admin toggles to grant test entitlements without Stripe calls, using the same entitlement model for consistency.

## Minimal implementation steps
1) Create `billing.models.AddonEntitlement` (fields: `owner_type`, `owner_id`, `price_id`, `quantity`, `task_credits`, `contact_cap`, `starts_at`, `expires_at`, `is_recurring`, `created_via`). Add simple manager methods to fetch active entitlements.
2) Add new product fields and price ID list fields for each add-on on `StripeConfig` + `StripeSettings` (e.g., `startup_contact_cap_product_id`, `startup_contact_cap_price_ids`, plus Scale and org-team equivalents) and surface them in the admin form so billing settings can render Checkout buttons with the right IDs.【api/admin_forms.py†L365-L458】【api/models.py†L2955-L3097】【config/stripe_config.py†L20-L137】
3) Add a service that, given a user or org, returns effective task credits and contact caps by combining plan defaults, active entitlements (respecting quantity to scale the uplift), and the existing billing overrides. Wire it into existing helper functions.
4) Extend Stripe webhook/checkout handlers to create entitlements when an add-on price is purchased and to refresh entitlements on subscription renewal. Persist the Stripe line item quantity and multiply the unit benefit when creating entitlements.
5) Add UI exposure in billing settings to show active add-ons, expiration, and renewal behavior; reuse existing Pro plan surfaces to avoid new container styles.
