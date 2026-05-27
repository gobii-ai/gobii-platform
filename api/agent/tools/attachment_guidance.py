"""Shared attachment guidance for agent tool prompts and results."""

SEND_EMAIL_ATTACHMENTS_DESCRIPTION = (
    "Optional filespace paths or $[/path]. Use exact file-tool `attach` value; body text never attaches files."
)


def build_attachment_result_message(attach_value: str) -> str:
    """Return follow-up guidance for sending a generated file as an attachment."""
    return (
        "To send this file as an actual email attachment, pass the exact `attach` value "
        f"({attach_value}) in send_email.attachments. Body text does not attach files. "
        "For inline email images, also use <img src='cid:filename'> with the attached basename."
    )
