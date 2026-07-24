import re


_TABLE_DIVIDER_CELL = re.compile(r":?-{3,}:?")
_CODE_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_DISCORD_CONTENT_LIMIT = 2_000


def _split_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None
    stripped = stripped.removeprefix("|")
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        stripped = stripped[:-1]
    return [
        cell.strip().replace(r"\|", "|")
        for cell in re.split(r"(?<!\\)\|", stripped)
    ]


def _is_table_divider(cells: list[str] | None, expected_columns: int) -> bool:
    return bool(
        cells
        and len(cells) == expected_columns
        and expected_columns >= 2
        and all(_TABLE_DIVIDER_CELL.fullmatch(cell) for cell in cells)
    )


def _bold(value: str) -> str:
    value = value.strip()
    return value if value.startswith("**") and value.endswith("**") else f"**{value}**"


def _format_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    entities = (
        ((row[0], zip(headers[1:], row[1:])) for row in rows)
        if headers[0]
        else (
            (
                headers[column],
                ((row[0], row[column]) for row in rows),
            )
            for column in range(1, len(headers))
        )
    )
    blocks = []
    for title, fields in entities:
        if not title:
            continue
        lines = [_bold(title)]
        lines.extend(
            f"- {_bold(label + ':')} {value}"
            for label, value in fields
            if label and value
        )
        blocks.append("\n".join(lines))
    return blocks


def _format_compact_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    compact_headers = list(headers)
    if not compact_headers[0]:
        compact_headers[0] = "Metric"
    return [
        "\n".join(
            " · ".join(row)
            for row in [compact_headers, *rows]
        )
    ]


def _normalize_discord_tables(body: str, *, compact: bool) -> str:
    lines = body.splitlines()
    normalized: list[str] = []
    active_fence = ""
    index = 0

    while index < len(lines):
        fence_match = _CODE_FENCE.match(lines[index])
        if fence_match:
            marker = fence_match.group(1)[0]
            if not active_fence:
                active_fence = marker
            elif active_fence == marker:
                active_fence = ""
            normalized.append(lines[index])
            index += 1
            continue

        headers = None if active_fence else _split_table_row(lines[index])
        divider = _split_table_row(lines[index + 1]) if headers and index + 1 < len(lines) else None
        if not headers or not _is_table_divider(divider, len(headers)):
            normalized.append(lines[index])
            index += 1
            continue

        rows: list[list[str]] = []
        next_index = index + 2
        while next_index < len(lines):
            row = _split_table_row(lines[next_index])
            if row is None:
                break
            rows.append((row + [""] * len(headers))[:len(headers)])
            next_index += 1

        formatted = (
            _format_compact_table(headers, rows)
            if compact
            else _format_table(headers, rows)
        )
        if not formatted:
            normalized.append(lines[index])
            index += 1
            continue

        normalized.extend("\n\n".join(formatted).splitlines())
        index = next_index

    return "\n".join(normalized)


def normalize_discord_markdown(body: str) -> str:
    """Replace Markdown tables that Discord exposes as raw text."""

    normalized = _normalize_discord_tables(body, compact=False)
    if len(body) <= _DISCORD_CONTENT_LIMIT < len(normalized):
        return _normalize_discord_tables(body, compact=True)
    return normalized
