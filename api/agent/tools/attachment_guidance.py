"""Shared attachment guidance for agent tool prompts and results."""

SEND_EMAIL_ATTACHMENTS_DESCRIPTION = (
    "Optional list of filespace paths or $[/path] variables from the default filespace. "
    "This is the only way to create an actual email attachment. Pass the exact $[/path] "
    "value returned by a file tool's `attach` field here. Mentioning a filename or path in "
    "the email body does not attach anything."
)


def build_attachment_result_message(attach_value: str) -> str:
    """Return follow-up guidance for sending a generated file as an attachment."""
    return (
        "To send this file as an actual email attachment, pass the exact value from `attach` "
        f"({attach_value}) in send_email.attachments. Mentioning it in the email body does "
        "not attach anything."
    )
