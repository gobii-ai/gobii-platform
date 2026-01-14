CONTEXT_TYPE_HEADER = "X-Gobii-Context-Type"
CONTEXT_ID_HEADER = "X-Gobii-Context-Id"


def get_context_override(request):
    if request is None:
        return None
    context_type = request.headers.get(CONTEXT_TYPE_HEADER) or request.GET.get("context_type")
    context_id = request.headers.get(CONTEXT_ID_HEADER) or request.GET.get("context_id")
    if not context_type or not context_id:
        return None
    return {"type": context_type, "id": context_id}
