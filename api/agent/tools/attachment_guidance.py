"""Shared attachment guidance for agent tool prompts and results."""

SEND_EMAIL_ATTACHMENTS_DESCRIPTION = (
    "Optional filespace paths or $[/path] variables. To attach generated files, pass the exact file-tool `attach` value; body text never attaches files. "
    "For an inline email image, also include the file here and reference it in mobile_first_html with <img src='cid:exact filename'>, where exact filename is the attached file's basename such as report.png."
)


def build_attachment_result_message(attach_value: str) -> str:
    """Return follow-up guidance for sending a generated file as an attachment."""
    return (
        "To send this file as an actual email attachment, pass the exact value from `attach` "
        f"({attach_value}) in send_email.attachments. Mentioning it in the email body does "
        "not attach anything. To embed an attached image inline in email HTML, also include "
        "an <img src='cid:exact filename'> tag that uses the attached file's exact basename."
    )
