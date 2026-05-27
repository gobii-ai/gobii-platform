"""Shared attachment guidance for agent tool prompts and results."""

SEND_EMAIL_ATTACHMENTS_DESCRIPTION = (
    "Optional filespace paths or $[/path]. Use exact file-tool `attach` value; body text never attaches files. "
    "Inline images: also use <img src='cid:filename'>."
)


def build_attachment_result_message(attach_value: str) -> str:
    """Return follow-up guidance for sending a generated file as an attachment."""
    return (
        "To send this file as an actual email attachment, pass the exact value from `attach` "
        f"({attach_value}) in send_email.attachments. Mentioning it in the email body does "
        "not attach anything. To embed an attached image inline in email HTML, also include "
        "an <img src='cid:exact filename'> tag that uses the attached file's exact basename."
    )
