import logging
import uuid
from typing import Any

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def json_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """
    Ensure API errors always return JSON.

    Delegates to DRF's default handler first so that expected APIException
    subclasses keep their normal structure. For any other unexpected exception,
    we log the failure and return a generic JSON payload with a unique error id.
    """
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    error_id = uuid.uuid4()
    view = context.get("view")
    view_name = view.__class__.__name__ if view is not None else "unknown"

    logger.exception(
        "Unhandled exception in API view %s (error_id=%s)",
        view_name,
        error_id,
        exc_info=exc,
    )

    data = {
        "detail": "Internal server error.",
        "error_id": str(error_id),
    }
    response = Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    response.exception = True
    return response


__all__ = ["json_exception_handler"]
