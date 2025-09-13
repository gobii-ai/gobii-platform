"""
Minimal Pipedream MCP proof‑of‑concept script.

Subcommands
- list (default): list tools for the target app
- create: create a Google Sheet and add a row (end‑to‑end)

What it does
- Validates required env vars
- Obtains a client‑credentials access token from Pipedream
- Connects to the remote MCP endpoint with fastmcp using headers
- Sets x-pd-app-slug=google_sheets (overridable)

Usage (list tools)
  PIPEDREAM_CLIENT_ID=... \
  PIPEDREAM_CLIENT_SECRET=... \
  PIPEDREAM_PROJECT_ID=... \
  PIPEDREAM_ENVIRONMENT=development \
  .venv/bin/python scripts/pd_mcp_list_tools.py list

Usage (create sheet then add a row)
  PIPEDREAM_CLIENT_ID=... PIPEDREAM_CLIENT_SECRET=... \
  PIPEDREAM_PROJECT_ID=... PIPEDREAM_ENVIRONMENT=development \
  .venv/bin/python scripts/pd_mcp_list_tools.py create

Optional envs
- PIPEDREAM_REMOTE_URL (default: https://remote.mcp.pipedream.net)
- PIPEDREAM_APP_SLUG (default: google_sheets)
- PIPEDREAM_EXTERNAL_USER_ID (default: auto)
- PIPEDREAM_CONVERSATION_ID (default: same as EXTERNAL_USER_ID)
- POC_TITLE (default: Gobii MCP PoC <timestamp>)
- POC_ROW_JSON (default: {'Name':'Gobii','Email':'hello@gobii.ai','Note':'Hello from MCP!'})
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Dict, Any, Optional, Tuple

import requests
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from datetime import datetime, UTC
import argparse
import json
import re


REQUIRED_ENVS = [
    "PIPEDREAM_CLIENT_ID",
    "PIPEDREAM_CLIENT_SECRET",
    "PIPEDREAM_PROJECT_ID",
    "PIPEDREAM_ENVIRONMENT",
]


def _fail(msg: str, code: int = 2) -> None:
    print(f"[pipedream-mcp-poc] {msg}", file=sys.stderr)
    sys.exit(code)


def _validate_env() -> Dict[str, str]:
    missing = [k for k in REQUIRED_ENVS if not os.getenv(k)]
    if missing:
        _fail(
            "Missing required env vars: " + ", ".join(missing) +
            "\nSee header of this script for usage.")

    env = {
        "CLIENT_ID": os.environ["PIPEDREAM_CLIENT_ID"],
        "CLIENT_SECRET": os.environ["PIPEDREAM_CLIENT_SECRET"],
        "PROJECT_ID": os.environ["PIPEDREAM_PROJECT_ID"],
        "ENVIRONMENT": os.environ["PIPEDREAM_ENVIRONMENT"],
        "REMOTE_URL": os.getenv("PIPEDREAM_REMOTE_URL", "https://remote.mcp.pipedream.net"),
        "APP_SLUG": os.getenv("PIPEDREAM_APP_SLUG", "google_sheets"),
        "EXTERNAL_USER_ID": os.getenv("PIPEDREAM_EXTERNAL_USER_ID", _default_user_id()),
        "CONVERSATION_ID": os.getenv("PIPEDREAM_CONVERSATION_ID"),
    }
    if not env["CONVERSATION_ID"]:
        env["CONVERSATION_ID"] = env["EXTERNAL_USER_ID"]
    return env


def _default_user_id() -> str:
    import getpass, socket
    user = getpass.getuser()
    host = socket.gethostname().split(".")[0]
    return f"poc-{user}@{host}"


def _get_access_token(client_id: str, client_secret: str) -> str:
    try:
        resp = requests.post(
            "https://api.pipedream.com/v1/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        _fail(f"Failed to obtain Pipedream token: {e}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        _fail("Token response missing access_token")
    return token


def _build_headers(env: Dict[str, str], tool_mode: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_access_token(env['CLIENT_ID'], env['CLIENT_SECRET'])}",
        "x-pd-project-id": env["PROJECT_ID"],
        "x-pd-environment": env["ENVIRONMENT"],
        "x-pd-external-user-id": env["EXTERNAL_USER_ID"],
        "x-pd-conversation-id": env["CONVERSATION_ID"],
        "x-pd-tool-mode": tool_mode,
        "x-pd-app-discovery": "true",
        "x-pd-app-slug": env["APP_SLUG"],
    }


def _connect_client(env: Dict[str, str], tool_mode: str) -> Client:
    headers = _build_headers(env, tool_mode)
    transport = StreamableHttpTransport(url=env["REMOTE_URL"], headers=headers)
    return Client(transport)


def _extract_text_and_data(result: Any) -> Tuple[Optional[str], Optional[Any]]:
    data = getattr(result, "data", None)
    text = None
    content = getattr(result, "content", None)
    if content:
        for block in content:
            if hasattr(block, "text") and isinstance(block.text, str):
                text = block.text
                break
    return text, data


def _find_connect_url(text: Optional[str], data: Optional[Any]) -> Optional[str]:
    def find_in_obj(obj: Any) -> Optional[str]:
        if isinstance(obj, str) and "pipedream.com/_static/connect.html" in obj:
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                url = find_in_obj(v)
                if url:
                    return url
        if isinstance(obj, list):
            for v in obj:
                url = find_in_obj(v)
                if url:
                    return url
        return None

    if text:
        m = re.search(r"https://[^\s]*pipedream\.com/_static/connect\.html[^\s]*", text)
        if m:
            return m.group(0)
    return find_in_obj(data)


async def _list_tools(env: Dict[str, str]) -> int:
    client = _connect_client(env, tool_mode="full-config")
    async with client:
        tools = await client.list_tools()
    if not tools:
        print("No tools returned. Check credentials/project/environment and app slug.")
        return 1
    print(f"Found {len(tools)} tool(s) for app '{env['APP_SLUG']}'\n")
    for t in tools:
        name = getattr(t, "name", "<unnamed>")
        desc = (getattr(t, "description", None) or "").strip()
        print(f"- {name}" + (f": {desc}" if desc else ""))
    return 0


async def _create_and_add_row(env: Dict[str, str]) -> int:
    # Use a single sub-agent client for the whole flow to keep context
    client_sa = _connect_client(env, tool_mode="sub-agent")

    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    title = os.getenv("POC_TITLE", f"Gobii MCP PoC {ts}")

    async with client_sa:
        # Step 1: Create spreadsheet (handle Connect Link if needed)
        print("[1/5] Creating spreadsheet…")
        create_instr = (
            "Create a blank Google spreadsheet titled '" + title + "'. "
            "Return a compact JSON object with keys spreadsheetId and spreadsheetUrl only."
        )
        r = await client_sa.call_tool("google_sheets-create-spreadsheet", {"instruction": create_instr})
        text, data = _extract_text_and_data(r)
        url = _find_connect_url(text, data)
        if url:
            print("Authorization required. Open this URL, complete the connection, then press Enter here:")
            print(url)
            input("")
            r = await client_sa.call_tool("google_sheets-create-spreadsheet", {"instruction": create_instr})
            text, data = _extract_text_and_data(r)

        spreadsheet_id = None
        spreadsheet_url = None
        if isinstance(data, dict):
            spreadsheet_id = data.get("spreadsheetId")
            spreadsheet_url = data.get("spreadsheetUrl") or data.get("url")
        if not spreadsheet_id and text:
            try:
                maybe = json.loads(text)
                if isinstance(maybe, dict):
                    spreadsheet_id = spreadsheet_id or maybe.get("spreadsheetId")
                    spreadsheet_url = spreadsheet_url or maybe.get("spreadsheetUrl") or maybe.get("url")
            except Exception:
                pass
        if not spreadsheet_id and text:
            m = re.search(r"[0-9A-Za-z-_]{40,}", text)
            if m:
                spreadsheet_id = m.group(0)
        if not spreadsheet_id:
            print("Could not parse spreadsheet ID from response. Raw response:")
            print(text or data)
            return 1
        print(f"Created spreadsheet: {spreadsheet_url or spreadsheet_id}")

        # Prepare row payload
        default_row = {"Name": "Gobii", "Email": "hello@gobii.ai", "Note": "Hello from MCP!"}
        try:
            row = json.loads(os.getenv("POC_ROW_JSON", ""))
            if not isinstance(row, dict):
                row = default_row
        except Exception:
            row = default_row

        headers_csv = ", ".join(row.keys())
        values_csv = ", ".join(str(v) for v in row.values())

        # Step 2: Ensure header row exists (idempotent)
        print("[2/5] Ensuring header row exists (Name, Email, Note)…")
        ensure_header_instr = (
            f"For spreadsheet with ID {spreadsheet_id}, in the first worksheet, ensure the first row is a header row "
            f"with columns: {headers_csv}. If headers already exist, do not duplicate or reorder them."
        )
        _ = await client_sa.call_tool("google_sheets-update-row", {"instruction": ensure_header_instr})

        # Step 3: Add data row
        print("[3/5] Adding data row…")
        add_instr = (
            f"For spreadsheet with ID {spreadsheet_id}, append a new row to the first worksheet immediately below the header row. "
            f"Use these columns and values: {headers_csv} -> {values_csv}. "
            f"Return a short confirmation including the A1 range of the inserted row."
        )
        r = await client_sa.call_tool("google_sheets-add-single-row", {"instruction": add_instr})
        text, data = _extract_text_and_data(r)

        # Step 4: Verify by reading first 3 rows
        print("[4/5] Verifying inserted row…")
        verify_instr = (
            f"For spreadsheet with ID {spreadsheet_id}, read the first worksheet range A1:C3. "
            f"Return pure JSON with a 'values' array of arrays, no prose."
        )
        values = None
        try:
            vr = await client_sa.call_tool("google_sheets-get-values-in-range", {"instruction": verify_instr})
            vtext, vdata = _extract_text_and_data(vr)

            if isinstance(vdata, dict) and isinstance(vdata.get("values"), list):
                values = vdata["values"]
            else:
                try:
                    if vtext:
                        maybe = json.loads(vtext)
                        if isinstance(maybe, dict) and isinstance(maybe.get("values"), list):
                            values = maybe["values"]
                except Exception:
                    pass
        except Exception:
            # Verification is best-effort; continue without failing the flow
            values = None

        ok = False
        if isinstance(values, list) and len(values) >= 2:
            # Basic check: header present and at least one data row
            ok = True

        # If still not ok, try one more explicit append
        if not ok:
            print("Row verification inconclusive; attempting a second append…")
            add_instr2 = (
                f"Append one row under the header in the first worksheet of spreadsheet {spreadsheet_id}. "
                f"Columns: {headers_csv}. Values: {values_csv}."
            )
            _ = await client_sa.call_tool("google_sheets-add-single-row", {"instruction": add_instr2})

        # Step 5: Final output
        print("[5/5] Done.")
        if spreadsheet_url:
            print(f"Spreadsheet URL: {spreadsheet_url}")
        print(text or data or "Row append attempted.")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipedream MCP PoC")
    parser.add_argument("command", nargs="?", default="list", choices=["list", "create"],
                        help="What to do: list tools or create sheet and add row")
    args = parser.parse_args()

    env = _validate_env()
    try:
        if args.command == "list":
            rc = asyncio.run(_list_tools(env))
        else:
            rc = asyncio.run(_create_and_add_row(env))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
