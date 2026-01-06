"""Shared CSV utilities for robust parsing and detection."""

from dataclasses import dataclass
import csv
import io
import re
from typing import Optional, Sequence, Tuple

CSV_DELIMITERS = (",", "\t", ";", "|", "^")
MAX_CSV_SAMPLE_LINES = 20
MAX_CSV_SAMPLE_BYTES = 20_000


@dataclass(frozen=True)
class CsvDialectInfo:
    delimiter: str
    quotechar: str = '"'
    escapechar: Optional[str] = None
    doublequote: bool = True
    skipinitialspace: bool = False


def normalize_csv_text(text: str) -> Tuple[str, Optional[str]]:
    """Normalize line endings, strip BOM, and handle Excel-style sep=."""
    if not text:
        return "", None

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.lstrip("\ufeff")

    first_line, sep, rest = text.partition("\n")
    sep_match = re.match(r"(?i)^sep=(.)\s*$", first_line)
    if sep_match:
        return rest, sep_match.group(1)
    if first_line.lower().startswith("sep=\\t"):
        return rest, "\t"

    return text, None


def build_csv_sample(text: str) -> Tuple[str, list[str]]:
    """Return a text sample plus non-empty sample lines."""
    if not text:
        return "", []

    sample_text = text[:MAX_CSV_SAMPLE_BYTES]
    sample_lines: list[str] = []
    for line in sample_text.split("\n"):
        if not line.strip():
            continue
        sample_lines.append(line)
        if len(sample_lines) >= MAX_CSV_SAMPLE_LINES:
            break
    return sample_text, sample_lines


def detect_csv_dialect(
    sample_text: str,
    sample_lines: Sequence[str],
    *,
    explicit_delimiter: Optional[str] = None,
) -> Optional[CsvDialectInfo]:
    """Detect CSV dialect (delimiter, quotechar, etc) from a text sample."""
    if explicit_delimiter:
        dialect = _sniff_dialect(sample_text, [explicit_delimiter])
        if dialect:
            return _dialect_info_from_dialect(
                dialect,
                delimiter_override=explicit_delimiter,
                sample_lines=sample_lines,
            )
        return CsvDialectInfo(
            delimiter=explicit_delimiter,
            skipinitialspace=_should_skip_initial_space(sample_lines, explicit_delimiter),
        )

    if not sample_lines:
        return None

    if not explicit_delimiter:
        delimiter_present = False
        for delim in CSV_DELIMITERS:
            lines_with = sum(1 for line in sample_lines if delim in line)
            if lines_with >= 2:
                delimiter_present = True
                break
        if not delimiter_present:
            return None

    dialect = _sniff_dialect(sample_text, CSV_DELIMITERS)
    if dialect:
        return _dialect_info_from_dialect(dialect, sample_lines=sample_lines)

    fallback = _choose_delimiter(sample_lines, CSV_DELIMITERS)
    if not fallback:
        return None

    return CsvDialectInfo(
        delimiter=fallback,
        skipinitialspace=_should_skip_initial_space(sample_lines, fallback),
    )


def dialect_to_reader_kwargs(dialect: Optional[CsvDialectInfo]) -> dict[str, object]:
    if not dialect:
        return {}
    return {
        "delimiter": dialect.delimiter,
        "quotechar": dialect.quotechar,
        "escapechar": dialect.escapechar,
        "doublequote": dialect.doublequote,
        "skipinitialspace": dialect.skipinitialspace,
    }


def read_csv_rows(
    text: str,
    dialect: Optional[CsvDialectInfo],
    *,
    max_rows: Optional[int] = None,
) -> list[list[str]]:
    if text is None:
        return []
    reader_kwargs = dialect_to_reader_kwargs(dialect)
    reader_fns = []
    try:
        import clevercsv  # type: ignore[import-not-found]
    except Exception:
        clevercsv = None
    if clevercsv is not None:
        reader_fns.append(clevercsv.reader)
    reader_fns.append(csv.reader)
    for reader_fn in reader_fns:
        try:
            rows = []
            for row in reader_fn(io.StringIO(text), **reader_kwargs):
                rows.append(row)
                if max_rows is not None and len(rows) >= max_rows:
                    break
            return rows
        except Exception:
            continue
    return []


def _sniff_dialect(sample_text: str, delimiters: Sequence[str]) -> Optional[csv.Dialect]:
    if not sample_text:
        return None

    try:
        import clevercsv  # type: ignore[import-not-found]
    except Exception:
        clevercsv = None

    if clevercsv is not None:
        try:
            return clevercsv.Sniffer().sniff(sample_text, delimiters=list(delimiters))
        except TypeError:
            try:
                return clevercsv.Sniffer().sniff(
                    sample_text,
                    delimiters=list(delimiters),
                    verbose=False,
                )
            except Exception:
                pass
        except Exception:
            pass

    try:
        return csv.Sniffer().sniff(sample_text, delimiters=delimiters)
    except csv.Error:
        return None


def _dialect_info_from_dialect(
    dialect: csv.Dialect,
    *,
    delimiter_override: Optional[str] = None,
    sample_lines: Sequence[str],
) -> CsvDialectInfo:
    delimiter = delimiter_override or getattr(dialect, "delimiter", ",") or ","
    skipinitialspace = getattr(dialect, "skipinitialspace", False)
    if delimiter_override:
        skipinitialspace = _should_skip_initial_space(sample_lines, delimiter)
    return CsvDialectInfo(
        delimiter=delimiter,
        quotechar=getattr(dialect, "quotechar", '"') or '"',
        escapechar=getattr(dialect, "escapechar", None),
        doublequote=bool(getattr(dialect, "doublequote", True)),
        skipinitialspace=bool(skipinitialspace),
    )


def _choose_delimiter(sample_lines: Sequence[str], delimiters: Sequence[str]) -> Optional[str]:
    best_score = 0.0
    best_delim = None
    for delim in delimiters:
        counts = [line.count(delim) for line in sample_lines if line.strip()]
        nonzero = [count for count in counts if count > 0]
        if len(nonzero) < 2:
            continue
        most_common = max(set(nonzero), key=nonzero.count)
        consistency = sum(1 for count in nonzero if count == most_common) / len(nonzero)
        score = consistency * most_common
        if score > best_score:
            best_score = score
            best_delim = delim
    return best_delim


def _should_skip_initial_space(sample_lines: Sequence[str], delimiter: str) -> bool:
    if not delimiter:
        return False
    probe = f"{delimiter} "
    return any(probe in line for line in sample_lines)
