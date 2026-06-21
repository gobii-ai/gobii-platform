from django.http import HttpResponse
from django.template.loader import render_to_string


def _render_error_response(template_name, status):
    response = HttpResponse(render_to_string(template_name), status=status)
    response["X-Robots-Tag"] = "noindex, follow"
    return response


def permission_denied(request, exception, template_name="403.html"):
    return _render_error_response(template_name, 403)


def page_not_found(request, exception, template_name="404.html"):
    return _render_error_response(template_name, 404)


def server_error(request, template_name="500.html"):
    return _render_error_response(template_name, 500)


def csrf_failure(request, reason=""):
    return _render_error_response("403.html", 403)
