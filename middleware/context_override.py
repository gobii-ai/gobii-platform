from console.context_overrides import get_context_override


class ContextOverrideMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        override = get_context_override(request)
        if override and hasattr(request, "session"):
            setattr(request.session, "_context_override", override)
        return self.get_response(request)
