# Marketing Events (CAPI fan-out)
`marketing_events` provides a single helper, `capi(user, event_name, properties=None, request=None, context=None)`, that normalizes marketing signals and pushes them through an async Celery task to the configured providers (Meta/Facebook, Reddit, TikTok, LinkedIn, and optional GA4 Measurement Protocol). Calls are non-blocking; hashing, consent checks, retries, and tracing happen in the background worker.

## Required settings

Set these environment variables (usually via Django settings) to enable each provider:

- `META_PIXEL_ID`
- `FACEBOOK_ACCESS_TOKEN`
- `REDDIT_ADVERTISER_ID` (defaults to `REDDIT_PIXEL_ID`)
- `REDDIT_ACCESS_TOKEN`
- `TIKTOK_PIXEL_ID`
- `TIKTOK_ACCESS_TOKEN`
- `LINKEDIN_CAPI_ACCESS_TOKEN`
- `LINKEDIN_CAPI_CONVERSION_IDS_JSON` or the per-event `LINKEDIN_CAPI_*_CONVERSION_ID` settings
- `GA_MEASUREMENT_ID` (already used by frontend gtag)
- `GA_MEASUREMENT_API_SECRET` (enables server-side GA4 events)
- `CAPI_START_TRIAL_CONV_RATE` (optional, defaults to `0.322`; scales non-scale `StartTrial` conversion value from predicted LTV)
- `CAPI_START_TRIAL_SCALE_CONV_RATE` (optional, defaults to `0.22`; scales `Scale` `StartTrial` conversion value from predicted LTV)
- `CAPI_START_TRIAL_DELAY_MINUTES` (optional, defaults to `60`; delays `StartTrial` dispatch)

If a provider’s credentials are missing the task will skip it automatically.

LinkedIn conversion IDs can be configured as plain IDs or full `urn:lla:llaPartnerConversion:<id>` URNs. The built-in per-event settings are:

- `LINKEDIN_CAPI_COMPLETE_REGISTRATION_CONVERSION_ID`
- `LINKEDIN_CAPI_START_TRIAL_CONVERSION_ID`
- `LINKEDIN_CAPI_SUBSCRIBE_CONVERSION_ID`
- `LINKEDIN_CAPI_ACTIVATED_CONVERSION_ID`
- `LINKEDIN_CAPI_INITIATE_CHECKOUT_CONVERSION_ID`
- `LINKEDIN_CAPI_ADD_PAYMENT_INFO_CONVERSION_ID`

## What `capi` does

1. Exits immediately unless `GOBII_PROPRIETARY_MODE` is truthy (matching legacy behavior).
2. Builds a payload from the supplied `user`, `properties`, and optional request/context.
2. Hashes identifiers (`id`, `email`, `phone`) with SHA-256 and normalizes click metadata/UTMs.
3. Generates `event_id` (UUID4) and `event_time` (epoch seconds) when not provided.
4. Enqueues the `enqueue_marketing_event` Celery task which fans out to the active providers with retries on transient failures.

### Request vs. context

- Pass `request` when called inside a Django view to auto-capture IP, user agent, page URL, and cookies (`_fbp`, `_fbc`), plus UTM/click params.
- Use `context` to supply manual overrides or extra details (e.g., `{"consent": False}`, `{"click_ids": {"rdt_cid": "..."} }`).
- When both are provided, `context` wins for overlapping keys.

### Properties

`properties` can include any custom event metadata. Reserved keys:

- `event_id`, `event_time` (to preserve upstream ids/timestamps)
- `test_mode` for Reddit to flag sandbox sends
- Value/currency/item/products for conversion recording

## Example usage

```python
from marketing_events.api import capi


def signup_complete_view(request):
    user = request.user
    capi(
        user=user,
        event_name="CompleteRegistration",
        properties={
            "plan": "free",
            "value": 0,
        },
        request=request,  # auto-extracts click IDs, IP, UA, page URL
    )
```

### Manual context example

```python
capi(
    user=user,
    event_name="UpgradePlan",
    properties={"value": 99.99, "currency": "USD"},
    context={
        "consent": True,
        "click_ids": {"rdt_cid": "rdt_click_123"},
        "utm": {"utm_source": "newsletter"},
    },
)
```

## Billing failure events

`invoice.payment_failed` can emit ad-only marketing events for user-owned subscriptions.
These are currently routed to `meta`, `reddit`, `tiktok`, and `linkedin`, but
not GA. LinkedIn sends only when a matching conversion ID/URN is configured.

- `TrialConversionPaymentFailed`: retryable failed trial-conversion charge.
- `TrialConversionPaymentFailedFinal`: terminal failed trial-conversion charge.
- `SubscriptionPaymentFailed`: retryable non-trial subscription payment failure.

Notes:

- `TrialConversionPaymentFailedFinal` is the current "we've given up" event.
- These events use the Stripe webhook event id as `event_id` when available so repeated failures for the same invoice do not dedupe together downstream.
- Failure payloads carry `value` and `currency` for provider-friendly reporting, plus `attempt_number`, `final_attempt`, `subscription_id`, and `stripe.invoice_id`.

The helper will merge this context with any derived request metadata, hash PII, and send the normalized payload to all enabled providers. OpenTelemetry spans (`marketing_event`) are emitted automatically for observability.***
