import os
from typing import Any

MCP_REMOTE_NPM_PACKAGE = "@gobii-ai/remote-mcp-remote"
_MCP_REMOTE_DEFAULT_TOKEN = "mcp-remote"
_MCP_REMOTE_ALT_TOKEN = "@modelcontextprotocol/mcp-remote"


def _command_basename(command: str) -> str:
    return os.path.basename((command or "").strip()).lower()


def _is_npx_command(command: str) -> bool:
    return _command_basename(command) in {"npx", "pnpx"}


def _is_mcp_remote_token(token: str) -> bool:
    value = (token or "").strip()
    return (
        value == _MCP_REMOTE_DEFAULT_TOKEN
        or value.startswith(f"{_MCP_REMOTE_DEFAULT_TOKEN}@")
        or value == _MCP_REMOTE_ALT_TOKEN
        or value.startswith(f"{_MCP_REMOTE_ALT_TOKEN}@")
        or value == MCP_REMOTE_NPM_PACKAGE
        or value.startswith(f"{MCP_REMOTE_NPM_PACKAGE}@")
    )


def is_mcp_remote_invocation(command: str, args: list[str]) -> bool:
    if _is_npx_command(command):
        return any(_is_mcp_remote_token(arg) for arg in args)
    return _command_basename(command) == _MCP_REMOTE_DEFAULT_TOKEN


def rewrite_mcp_remote_package_args(command: str, args: list[str]) -> list[str]:
    if not _is_npx_command(command):
        return list(args)

    rewritten: list[str] = []
    replaced = False
    for arg in args:
        value = (arg or "").strip()
        if not replaced and _is_mcp_remote_token(value):
            suffix = ""
            for prefix in (
                f"{_MCP_REMOTE_DEFAULT_TOKEN}@",
                f"{_MCP_REMOTE_ALT_TOKEN}@",
                f"{MCP_REMOTE_NPM_PACKAGE}@",
            ):
                if value.startswith(prefix):
                    suffix = value[len(prefix) :]
                    break
            rewritten.append(f"{MCP_REMOTE_NPM_PACKAGE}{('@' + suffix) if suffix else ''}")
            replaced = True
            continue
        rewritten.append(arg)

    return rewritten


def _has_flag(args: list[str], flag: str) -> bool:
    return any(part == flag for part in args)


def apply_bridge_flags(args: list[str], bridge: dict[str, Any] | None) -> list[str]:
    if not bridge:
        return list(args)

    updated = list(args)

    auth_mode = str(bridge.get("auth_mode") or "bridge").strip()
    if auth_mode and not _has_flag(updated, "--auth-mode"):
        updated.extend(["--auth-mode", auth_mode])

    redirect_url = str(bridge.get("redirect_url") or "").strip()
    if redirect_url and not _has_flag(updated, "--redirect-url"):
        updated.extend(["--redirect-url", redirect_url])

    poll_url = str(bridge.get("poll_url") or "").strip()
    if poll_url and not _has_flag(updated, "--auth-bridge-poll-url"):
        updated.extend(["--auth-bridge-poll-url", poll_url])

    notify_url = str(bridge.get("notify_url") or "").strip()
    if notify_url and not _has_flag(updated, "--auth-bridge-notify-url"):
        updated.extend(["--auth-bridge-notify-url", notify_url])

    poll_interval_seconds = bridge.get("poll_interval_seconds")
    if poll_interval_seconds and not _has_flag(updated, "--auth-bridge-poll-interval"):
        updated.extend(["--auth-bridge-poll-interval", str(poll_interval_seconds)])

    auth_timeout_seconds = bridge.get("auth_timeout_seconds")
    if auth_timeout_seconds and not _has_flag(updated, "--auth-timeout"):
        updated.extend(["--auth-timeout", str(auth_timeout_seconds)])

    auth_session_id = str(bridge.get("auth_session_id") or "").strip()
    if auth_session_id and not _has_flag(updated, "--auth-session-id"):
        updated.extend(["--auth-session-id", auth_session_id])

    return updated


def normalize_mcp_remote_args(
    command: str,
    args: list[str],
    bridge: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    if not is_mcp_remote_invocation(command, args):
        return False, list(args)

    updated = rewrite_mcp_remote_package_args(command, args)
    updated = apply_bridge_flags(updated, bridge)
    return True, updated
