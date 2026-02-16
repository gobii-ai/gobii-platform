# Marketing Events (CAPI fan-out)
`marketing_events` provides a single helper, `capi(user, event_name, properties=None, request=None, context=None)`, that normalizes marketing signals and pushes them through an async Celery task to the configured providers (Meta/Facebook, Reddit, TikTok, and optional GA4 Measurement Protocol). Calls are non-blocking; hashing, consent checks, retries, and tracing happen in the background worker.

## Required settings

Set these environment variables (usually via Django settings) to enable each provider:

- `META_PIXEL_ID`
- `META_CAPI_TOKEN`
- `REDDIT_PIXEL_ID`
- `REDDIT_CONVERSIONS_TOKEN`
- `TIKTOK_PIXEL_ID`
- `TIKTOK_ACCESS_TOKEN`
- `GA_MEASUREMENT_ID` (already used by frontend gtag)
- `GA_MEASUREMENT_API_SECRET` (enables server-side GA4 events)

If a provider’s credentials are missing the task will skip it automatically.

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

The helper will merge this context with any derived request metadata, hash PII, and send the normalized payload to all enabled providers. OpenTelemetry spans (`marketing_event`) are emitted automatically for observability.***
