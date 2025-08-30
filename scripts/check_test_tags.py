#!/usr/bin/env python3
"""
Fail if any test function lacks a Django @tag annotation, and ensure all tags
used in tests are present in the CI matrix defined in .github/workflows/ci.yml.

This is a static AST-based check, no Django import/initialization required.
"""

from __future__ import annotations

import ast
import glob
import os
import re
import sys
from typing import Iterable, Set, Tuple


def find_test_files() -> list[str]:
    # Match common patterns: tests/**/test*.py
    files = glob.glob("tests/**/*.py", recursive=True)
    return [f for f in files if os.path.basename(f).startswith("test")]


def decorator_is_tag(node: ast.expr) -> bool:
    # Matches @tag or @tag("...")
    if isinstance(node, ast.Call):
        func = node.func
    else:
        func = node
    return isinstance(func, ast.Name) and func.id == "tag"


def class_or_func_tags(decorators: Iterable[ast.expr]) -> Set[str]:
    tags: Set[str] = set()
    for d in decorators:
        if isinstance(d, ast.Call):
            if decorator_is_tag(d):
                for arg in d.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        tags.add(arg.value)
    return tags


def has_tag(decorators: Iterable[ast.expr]) -> bool:
    return any(decorator_is_tag(d) for d in decorators)


def collect_tests_and_tags(pyfile: str) -> Tuple[int, int, list[str], Set[str]]:
    """Return (total_tests, untagged_tests, untagged_names, used_tags)."""
    with open(pyfile, "r", encoding="utf-8") as fh:
        try:
            tree = ast.parse(fh.read(), filename=pyfile)
        except SyntaxError as e:
            print(f"SyntaxError parsing {pyfile}: {e}", file=sys.stderr)
            return (0, 0, [], set())

    total = 0
    untagged = 0
    untagged_list: list[str] = []
    used_tags: Set[str] = set()

    # Track class-level tagging status and tags
    class_tagged: dict[str, bool] = {}
    class_tags: dict[str, Set[str]] = {}

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_tagged[node.name] = has_tag(node.decorator_list)
            class_tags[node.name] = class_or_func_tags(node.decorator_list)
            used_tags |= class_tags[node.name]

    for node in tree.body:
        # Top-level test function
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            total += 1
            used_tags |= class_or_func_tags(node.decorator_list)
            if not has_tag(node.decorator_list):
                untagged += 1
                untagged_list.append(f"{pyfile}::{node.name}")
        # Test class with methods
        elif isinstance(node, ast.ClassDef):
            cls_tagged = class_tagged.get(node.name, False)
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"):
                    total += 1
                    meth_tags = class_or_func_tags(n.decorator_list)
                    used_tags |= meth_tags
                    if not (cls_tagged or has_tag(n.decorator_list)):
                        untagged += 1
                        untagged_list.append(f"{pyfile}::{node.name}.{n.name}")

    return total, untagged, untagged_list, used_tags


def load_ci_tags(ci_yml_path: str = ".github/workflows/ci.yml") -> Set[str]:
    # Only accept YAML mapping lines that begin with optional spaces then 'tag:'
    # to avoid matching shell strings like: echo "... tag: $TAG".
    tag_line = re.compile(r"^\s*tag:\s*([A-Za-z0-9_.-]+)\s*$")
    tags: Set[str] = set()
    try:
        with open(ci_yml_path, "r", encoding="utf-8") as f:
            for line in f:
                m = tag_line.match(line)
                if m:
                    tags.add(m.group(1))
    except FileNotFoundError:
        pass
    return tags


def main() -> int:
    files = find_test_files()
    if not files:
        print("No test files found.")
        return 0

    total = 0
    total_untagged = 0
    untagged_names: list[str] = []
    used_tags: Set[str] = set()

    for f in sorted(files):
        t, u, names, tags = collect_tests_and_tags(f)
        total += t
        total_untagged += u
        untagged_names.extend(names)
        used_tags |= tags

    ci_tags = load_ci_tags()

    ok = True
    if total_untagged > 0:
        ok = False
        print(f"Untagged tests: {total_untagged} of {total}")
        for name in untagged_names[:100]:
            print(f" - {name}")
        if len(untagged_names) > 100:
            print(f" ... and {len(untagged_names) - 100} more")
    else:
        print(f"All tests are tagged: {total} tests, 0 untagged")

    # Ensure every used tag is represented in CI matrix
    missing_in_ci = used_tags - ci_tags
    if missing_in_ci:
        ok = False
        print("Tags used in tests but missing from CI matrix:")
        for tag in sorted(missing_in_ci):
            print(f" - {tag}")
    else:
        print("All used tags are present in CI matrix.")

    # Optionally warn for CI tags not used
    unused_ci_tags = ci_tags - used_tags
    if unused_ci_tags:
        print("Note: CI matrix contains tags not used in tests:")
        for tag in sorted(unused_ci_tags):
            print(f" - {tag}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

