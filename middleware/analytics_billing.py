from typing import Callable

from django.http import HttpRequest, HttpResponse

from util.analytics_billing import bind_request_billing_context_cache, reset_request_billing_context_cache


class AnalyticsBillingContextCacheMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        token = bind_request_billing_context_cache()
        try:
            return self.get_response(request)
        finally:
            reset_request_billing_context_cache(token)
