#!/usr/bin/env python3
"""
Utility script to compare multiple text-similarity ratios between two strings.

This mirrors and extends the duplicate detection logic used for outbound messages
so engineers can experiment with arbitrary inputs from the command line.
"""

import argparse
import difflib
import math
import re
import sys
import os
import contextlib
import io
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

os.environ.setdefault("LITELLM_LOG", "ERROR")
os.environ.setdefault("FIREWORKS_AI_API_KEY", "fw_3ZUSZZhFUEwDZve6UV3jrR5U")

try:  # pragma: no cover - optional dependency for embeddings
    import litellm
except ImportError:  # pragma: no cover - litellm should be available but guard just in case
    litellm = None

try:  # pragma: no cover - optional dependency for higher fidelity fuzzy matching
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - rapidfuzz may not be installed in all envs
    fuzz = None


FIREWORKS_EMBEDDING_MODEL = "fireworks_ai/accounts/fireworks/models/qwen3-embedding-8b"
PROMPT_SENTINEL = "EOF"


def _read_source(value: str) -> str:
    """Return the string content for either literal input or a file path."""
    if value == "-":
        return sys.stdin.read()

    path = Path(value)
    if path.exists():
        return path.read_text()

    return value


def compute_similarity(left: str, right: str, *, strip: bool = True) -> float:
    """Return the SequenceMatcher ratio using the same configuration as production."""
    if strip:
        left = left.strip()
        right = right.strip()
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=True)
    return matcher.ratio()


def compute_levenshtein_ratio(left: str, right: str, *, strip: bool = True) -> float:
    """Return the classic Levenshtein ratio based on edit distance."""
    if strip:
        left = left.strip()
        right = right.strip()

    if left == right:
        return 1.0
    if not left or not right:
        return 0.0

    rows = len(left) + 1
    cols = len(right) + 1
    previous_row = list(range(cols))
    for i in range(1, rows):
        current_row = [i]
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            insertions = previous_row[j] + 1
            deletions = current_row[j - 1] + 1
            substitutions = previous_row[j - 1] + cost
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    distance = previous_row[-1]
    total_length = len(left) + len(right)
    if total_length == 0:
        return 1.0
    return (total_length - distance) / total_length


def compute_rapidfuzz_ratio(left: str, right: str, *, strip: bool = True) -> float | None:
    """Return the RapidFuzz ratio if the library is installed."""
    if fuzz is None:
        return None
    if strip:
        left = left.strip()
        right = right.strip()
    return fuzz.ratio(left, right) / 100.0


_TOKEN_PATTERN = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.lower())


def _cosine_similarity(vec_a: Mapping[str, float], vec_b: Mapping[str, float]) -> float:
    dot = sum(value * vec_b.get(term, 0.0) for term, value in vec_a.items())
    norm_a = math.sqrt(sum(value * value for value in vec_a.values()))
    norm_b = math.sqrt(sum(value * value for value in vec_b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _cosine_from_dense(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if not vec_a and not vec_b:
        return 0.0
    if len(vec_a) != len(vec_b):
        raise ValueError(f"Embedding length mismatch ({len(vec_a)} vs {len(vec_b)})")
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_tfidf_cosine_ratio(left: str, right: str, *, strip: bool = True) -> float:
    """Return cosine similarity over simple TF-IDF vectors."""
    if strip:
        left = left.strip()
        right = right.strip()

    tokens_left = _tokenize(left)
    tokens_right = _tokenize(right)

    if not tokens_left and not tokens_right:
        return 1.0

    documents: tuple[list[str], ...] = (tokens_left, tokens_right)
    document_frequency: Counter[str] = Counter()
    for tokens in documents:
        document_frequency.update(set(tokens))

    total_docs = len(documents)

    def build_vector(tokens: Iterable[str]) -> dict[str, float]:
        counts = Counter(tokens)
        total_terms = sum(counts.values())
        if total_terms == 0:
            return {}
        vector: dict[str, float] = {}
        for term, occurrences in counts.items():
            tf = occurrences / total_terms
            idf = math.log((total_docs + 1) / (document_frequency[term] + 1)) + 1.0
            vector[term] = tf * idf
        return vector

    vector_left = build_vector(tokens_left)
    vector_right = build_vector(tokens_right)

    if not vector_left and not vector_right:
        return 1.0

    return _cosine_similarity(vector_left, vector_right)


def _format_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    if len(message) > 160:
        message = f"{message[:157]}..."
    return message


def _fetch_embeddings(texts: Sequence[str]) -> list[list[float]]:
    if litellm is None:
        raise RuntimeError("litellm not installed")
    with contextlib.redirect_stdout(io.StringIO()):
        response = litellm.embedding(model=FIREWORKS_EMBEDDING_MODEL, input=list(texts))
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if not data:
        raise ValueError("embedding response missing data")
    embeddings: list[list[float]] = []
    for entry in data:
        embedding = getattr(entry, "embedding", None)
        if embedding is None and isinstance(entry, dict):
            embedding = entry.get("embedding")
        if embedding is None:
            raise ValueError("embedding response missing embedding vector")
        embeddings.append([float(value) for value in embedding])
    if len(embeddings) != len(texts):
        raise ValueError(f"expected {len(texts)} embeddings, received {len(embeddings)}")
    return embeddings


def compute_embedding_similarity(left: str, right: str, *, strip: bool = True) -> tuple[float | None, str | None]:
    """Return similarity based on Fireworks embeddings, or an error message."""
    if litellm is None:
        return None, "litellm not installed"

    if strip:
        left = left.strip()
        right = right.strip()

    if not left and not right:
        return 1.0, None
    if not left or not right:
        return 0.0, None

    try:
        left_vector, right_vector = _fetch_embeddings([left, right])
    except Exception as exc:  # pragma: no cover - depends on external API and env
        return None, _format_exception(exc)

    cosine = _cosine_from_dense(left_vector, right_vector)
    ratio = (cosine + 1.0) / 2.0
    ratio = min(max(ratio, 0.0), 1.0)
    return ratio, None


def _prompt_for_string(label: str) -> str:
    print(f"{label}:")
    print(f"  Paste text and finish with a line containing only '{PROMPT_SENTINEL}'.")
    print("  Or begin with '@file <path>' to load content from disk.")
    sys.stdout.flush()

    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break

        if not lines and line.startswith("@file "):
            path = line[len("@file ") :].strip()
            return _read_source(path)

        if line == PROMPT_SENTINEL:
            break

        lines.append(line)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare multiple similarity ratios for two strings.",
    )
    parser.add_argument(
        "left",
        nargs="?",
        default=None,
        help="Left string literal or file path (use '-' to read from stdin).",
    )
    parser.add_argument(
        "right",
        nargs="?",
        default=None,
        help="Right string literal or file path (use '-' to read from stdin).",
    )
    parser.add_argument(
        "--no-strip",
        action="store_false",
        dest="strip",
        help="Disable leading/trailing whitespace stripping before comparison.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Optional threshold to compare against (e.g. 0.97).",
    )

    args = parser.parse_args(argv)

    if args.left is None:
        left = _prompt_for_string("String 1")
    else:
        left = _read_source(args.left)

    if args.right is None:
        right = _prompt_for_string("String 2")
    else:
        right = _read_source(args.right)

    metrics: list[dict[str, object]] = [
        {
            "name": "SequenceMatcher ratio",
            "value": compute_similarity(left, right, strip=args.strip),
            "message": None,
        },
        {
            "name": "Levenshtein ratio",
            "value": compute_levenshtein_ratio(left, right, strip=args.strip),
            "message": None,
        },
        {
            "name": "TF-IDF cosine ratio",
            "value": compute_tfidf_cosine_ratio(left, right, strip=args.strip),
            "message": None,
        },
    ]

    rapidfuzz_ratio = compute_rapidfuzz_ratio(left, right, strip=args.strip)
    metrics.append(
        {
            "name": "RapidFuzz ratio",
            "value": rapidfuzz_ratio,
            "message": None if rapidfuzz_ratio is not None else "rapidfuzz not installed",
        }
    )

    embedding_ratio, embedding_error = compute_embedding_similarity(left, right, strip=args.strip)
    metrics.append(
        {
            "name": "Fireworks embedding ratio",
            "value": embedding_ratio,
            "message": embedding_error,
        }
    )

    width = max(len(entry["name"]) for entry in metrics)

    print("Similarity ratios:")
    for entry in metrics:
        name = entry["name"]
        value = entry["value"]
        message = entry["message"]
        if message:
            print(f"  {name:>{width}}: unavailable ({message})")
        elif value is None:
            print(f"  {name:>{width}}: unavailable")
        else:
            print(f"  {name:>{width}}: {float(value):.6f}")

    if args.threshold is not None:
        primary_value = metrics[0]["value"]
        primary_ratio = float(primary_value) if isinstance(primary_value, (int, float)) else 0.0
        meets = primary_ratio >= args.threshold
        indicator = "≥" if meets else "<"
        print(f"\nThreshold check (SequenceMatcher): {primary_ratio:.6f} {indicator} {args.threshold:.6f}")
        return 0 if meets else 1

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
