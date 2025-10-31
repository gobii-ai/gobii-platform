# Marketing Events (CAPI fan-out)
Use `capi(user, event_name, properties=None, request=None, context=None)` to enqueue marketing events asynchronously. The Celery task normalizes and sends to Meta CAPI and Reddit CAPI.

## Settings (env vars)
- META_PIXEL_ID
- META_CAPI_TOKEN
- REDDIT_AD_ACCOUNT
- REDDIT_CONVERSIONS_TOKEN

## Example usage
```python
from marketing_events.api import capi


def signup_complete_view(request):
    user = request.user
    capi(
        user=user,
        event_name="CompleteRegistration",
        properties={"plan": "free"},
        request=request,   # captures IP, UA, fbp/fbc, fbclid, utm, page_url
    )
```
