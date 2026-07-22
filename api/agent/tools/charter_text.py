import re


LITERAL_NEWLINE = "\\n"
STRUCTURAL_LITERAL_NEWLINE_RE = re.compile(
    r"\\n(?=(?:\\n|#{1,6}(?:\s|$)|[-*+]\s|\d+[.)]\s))"
)


def count_literal_newlines(value: str | None) -> int:
    return (value or "").count(LITERAL_NEWLINE)


def repair_structural_literal_newlines(value: str | None) -> tuple[str, int, int]:
    text = value or ""
    repaired, repaired_count = STRUCTURAL_LITERAL_NEWLINE_RE.subn("\n", text)
    return repaired, repaired_count, count_literal_newlines(repaired)
