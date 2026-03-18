#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path


def _collect_paths(node, collected):
    if isinstance(node, dict):
        file_path = node.get("file")
        if isinstance(file_path, str) and file_path:
            collected.add(file_path)

        for key in ("css", "assets"):
            values = node.get(key)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and value:
                        collected.add(value)

        for value in node.values():
            _collect_paths(value, collected)
        return

    if isinstance(node, list):
        for value in node:
            _collect_paths(value, collected)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List and validate concrete asset paths referenced by a Vite manifest."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--check-local", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    paths = set()
    _collect_paths(manifest, paths)
    ordered_paths = sorted(paths)

    if args.check_local:
        if args.root is None:
            parser.error("--root is required with --check-local")

        missing_paths = [path for path in ordered_paths if not (args.root / path).exists()]
        if missing_paths:
            for missing_path in missing_paths:
                print(f"Missing Vite asset referenced by manifest: {missing_path}", file=sys.stderr)
            return 1

    for path in ordered_paths:
        print(path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
