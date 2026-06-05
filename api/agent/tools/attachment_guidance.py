"""Shared attachment guidance for agent tool prompts and results."""

SEND_TOOL_ATTACHMENTS_DESCRIPTION = (
    "Optional filespace paths or $[/path] variables. Prefer the exact square-bracketed file-tool "
    "`attach` value, like $[/exports/file.png]; do not rewrite it as $/path. Body text never attaches files."
)

SEND_EMAIL_ATTACHMENTS_DESCRIPTION = (
    "Optional filespace paths or $[/path]. Use exact file-tool `attach` value, preserving the square brackets; "
    "body text never attaches files."
)

AGENT_VARIABLES_ATTACHMENT_NOTE = (
    "Available file variables (use $[name] in messages; pass exact $[name] to send-tool attachments for files):"
)

SYSTEM_ATTACHMENT_PREFLIGHT_GUIDANCE = (
    "# Attachment pre-flight: pass file-tool `result.attach` values to send-tool attachments; "
    "keep the full square-bracketed $[/path] syntax and body text never attaches files. "
    "RIGHT: send_email(..., attachments=[result.attach]). "
    "For resend/reply/duplicate risk: verify prior sends via __messages.attachment_count and "
    "__messages.rejected_attachments_json before claiming or resending files."
)


def build_attachment_result_message(attach_value: str) -> str:
    """Return follow-up guidance for sending a generated file as an attachment."""
    return (
        "To send this file as an actual email attachment, pass the exact `attach` value "
        f"({attach_value}) in send_email.attachments. Body text does not attach files. "
        "For inline email images, also use <img src='cid:filename'> with the attached basename."
    )
