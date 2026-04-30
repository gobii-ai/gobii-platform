#!/usr/bin/env python
"""Ad hoc runner for mini-description LLM generation.

Examples:
  uv run python scripts/test_mini_description_llm.py --agent-id <uuid> --charter "Finds qualified sales leads"
  uv run python scripts/test_mini_description_llm.py --agent-id <uuid> --charter-file /tmp/charters.txt --split-file
  cat charter.txt | uv run python scripts/test_mini_description_llm.py --agent-id <uuid>
"""

import argparse
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _setup_django() -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


def _read_charters(args: argparse.Namespace, default_charter: str) -> list[str]:
    charters: list[str] = []
    charters.extend(args.charter or [])

    if args.charter_file:
        with open(args.charter_file, encoding="utf-8") as handle:
            text = handle.read()
        if args.split_file:
            charters.extend(part.strip() for part in text.split("\n---\n") if part.strip())
        else:
            charters.append(text.strip())

    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()
        if stdin_text:
            charters.append(stdin_text)

    if not charters and default_charter.strip():
        charters.append(default_charter.strip())

    return [charter.strip() for charter in charters if charter.strip()]


def _nested_get(obj: Any, *path: Any) -> Any:
    current = obj
    for key in path:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(key, int):
            try:
                current = current[key]
            except Exception:
                return None
        else:
            current = getattr(current, key, None)
    return current


def _response_chat_content(response: Any) -> str:
    content = _nested_get(response, "choices", 0, "message", "content")
    if content is None:
        content = _nested_get(response, "choices", 0, "message", "reasoning")
    return str(content or "").strip()


def _response_reasoning(response: Any) -> str:
    try:
        from api.agent.core.token_usage import extract_reasoning_content

        reasoning = extract_reasoning_content(response)
    except Exception:
        reasoning = None
    if reasoning is None:
        reasoning = _nested_get(response, "choices", 0, "message", "reasoning_content")
    if reasoning is None:
        reasoning = _nested_get(response, "choices", 0, "message", "reasoning")
    return str(reasoning or "").strip()


def _load_routing_profile(profile_id: str | None) -> Any:
    if not profile_id:
        return None
    from api.models import LLMRoutingProfile

    return LLMRoutingProfile.objects.get(id=profile_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run mini-description _generate_via_llm against arbitrary charters.")
    parser.add_argument("--agent-id", required=True, help="PersistentAgent ID used for routing config and completion logging.")
    parser.add_argument("--routing-profile-id", help="Optional LLMRoutingProfile ID to use for summarization routing.")
    parser.add_argument("--charter", action="append", help="Charter text to test. Repeat for multiple runs.")
    parser.add_argument("--charter-file", help="Read charter text from a file.")
    parser.add_argument("--split-file", action="store_true", help="Split --charter-file on lines containing only ---.")
    parser.add_argument("--show-prompt", action="store_true", help="Print the exact messages sent to the model.")
    args = parser.parse_args()

    _setup_django()

    from api.agent.short_description import prepare_mini_description
    from api.agent.tasks import mini_description as mini_task
    from api.models import PersistentAgent

    try:
        agent = PersistentAgent.objects.get(id=args.agent_id)
    except PersistentAgent.DoesNotExist:
        print(f"PersistentAgent not found: {args.agent_id}", file=sys.stderr)
        return 2
    routing_profile = _load_routing_profile(args.routing_profile_id)
    charters = _read_charters(args, agent.charter or "")
    if not charters:
        print("No charter provided and selected agent has no charter.", file=sys.stderr)
        return 2

    real_run_completion = mini_task.run_completion

    for index, charter in enumerate(charters, start=1):
        captured: dict[str, Any] = {}

        def capture_run_completion(*run_args: Any, **run_kwargs: Any) -> Any:
            captured["args"] = run_args
            captured["kwargs"] = run_kwargs
            response = real_run_completion(*run_args, **run_kwargs)
            captured["response"] = response
            return response

        mini_task.run_completion = capture_run_completion
        try:
            generated = mini_task._generate_via_llm(agent, charter, routing_profile)
        finally:
            mini_task.run_completion = real_run_completion

        response = captured.get("response")
        run_kwargs = captured.get("kwargs") or {}

        print(f"\n=== Charter {index} ===")
        print(charter)
        print("\n--- Model ---")
        print(run_kwargs.get("model", "(not called)"))
        if run_kwargs.get("params"):
            print(f"params={run_kwargs['params']}")

        if args.show_prompt:
            print("\n--- Prompt ---")
            for message in run_kwargs.get("messages") or []:
                print(f"[{message.get('role')}] {message.get('content')}")

        print("\n--- Reasoning ---")
        print(_response_reasoning(response) or "(no reasoning returned)")
        print("\n--- Chat response ---")
        print(_response_chat_content(response) or "(no chat content returned)")
        print("\n--- _generate_via_llm return ---")
        print(generated or "(empty)")
        print("\n--- prepared mini description ---")
        print(prepare_mini_description(generated) or "(empty)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
