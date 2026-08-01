from django.template.loader import render_to_string

from .email_content import convert_body_to_html_and_plaintext
from .email_footer_service import append_footer_for_review


def render_email_transport_content(message, *, emit_logs=True):
    """Build the exact HTML and plain-text alternatives shown and sent."""
    html_snippet, plaintext_body = convert_body_to_html_and_plaintext(
        message.body or "",
        emit_logs=emit_logs,
    )
    html_snippet, plaintext_body, includes_throttle_footer = append_footer_for_review(
        message.owner_agent,
        html_snippet,
        plaintext_body,
    )
    html_body = render_to_string(
        "emails/persistent_agent_email.html",
        {"body": html_snippet},
    )
    return html_body, plaintext_body, html_snippet, includes_throttle_footer
