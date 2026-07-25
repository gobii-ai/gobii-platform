import re


LITERAL_NEWLINE = "\\n"
# Two or more in a row is a paragraph break. Nothing writes that on purpose, and matching the whole
# run matters: a lookahead that only inspects the next token repairs the first of a pair and leaves
# the second stranded, which is how "\n\n## Heading" used to come out as a real break followed by a
# visible \n.
LITERAL_NEWLINE_RUN_RE = re.compile(r"(?:\\n){2,}")
# A single one is only unambiguous when what follows can only begin a line: another break, a
# heading, a bullet, or a numbered item. A lone \n in prose -- including one inside backticks being
# discussed as text -- is left exactly as written.
STRUCTURAL_LITERAL_NEWLINE_RE = re.compile(
    r"\\n(?=(?:\\n|#{1,6}(?:\s|$)|[-*+]\s|\d+[.)]\s))"
)


def count_literal_newlines(value: str | None) -> int:
    return (value or "").count(LITERAL_NEWLINE)


def repair_structural_literal_newlines(value: str | None) -> tuple[str, int, int]:
    text = value or ""
    before = count_literal_newlines(text)
    repaired = LITERAL_NEWLINE_RUN_RE.sub(
        lambda match: "\n" * (len(match.group(0)) // len(LITERAL_NEWLINE)),
        text,
    )
    repaired = STRUCTURAL_LITERAL_NEWLINE_RE.sub("\n", repaired)
    remaining = count_literal_newlines(repaired)
    return repaired, before - remaining, remaining


def literal_newline_failure(value: str | None) -> bool:
    """True when a body uses literal backslash-n where a line break was meant.

    Only the unambiguous positions count: a run of them, or one before a heading, bullet or
    numbered item. A lone occurrence in prose -- someone writing about the escape sequence -- is
    not a formatting failure.
    """
    text = value or ""
    return bool(LITERAL_NEWLINE_RUN_RE.search(text) or STRUCTURAL_LITERAL_NEWLINE_RE.search(text))
