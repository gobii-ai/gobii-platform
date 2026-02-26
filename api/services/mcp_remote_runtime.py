import os
from dataclasses import dataclass
from typing import Dict, List, Tuple


REMOTE_MCP_REMOTE_PACKAGE = "@mattgreathouse/remote-mcp-remote"
DEFAULT_REMOTE_MCP_CONFIG_DIR = "/workspace/.mcp-auth"

_MCP_REMOTE_PACKAGE_TOKENS = {
    "mcp-remote",
    "mcp-remote@latest",
    REMOTE_MCP_REMOTE_PACKAGE,
}
_PACKAGE_RUNNERS = {"npx", "pnpx", "bunx", "npm", "pnpm", "yarn"}
_RUNNER_SUBCOMMANDS = {
    "npm": {"exec"},
    "pnpm": {"dlx", "exec"},
    "yarn": {"dlx"},
}


def _normalize_binary(value: str) -> str:
    return os.path.basename((value or "").strip()).lower()


def _is_mcp_remote_package_token(value: str) -> bool:
    token = (value or "").strip().lower()
    if not token:
        return False
    if token in _MCP_REMOTE_PACKAGE_TOKENS:
        return True
    if token.startswith("mcp-remote@"):
        return True
    if token.startswith(REMOTE_MCP_REMOTE_PACKAGE + "@"):
        return True
    return False


def _strip_runner_subcommand(command: str, args: List[str]) -> List[str]:
    subcommands = _RUNNER_SUBCOMMANDS.get(_normalize_binary(command), set())
    if args and args[0] in subcommands:
        return args[1:]
    return args


def is_mcp_remote_invocation(command: str, args: List[str]) -> bool:
    binary = _normalize_binary(command)
    if binary == "mcp-remote":
        return True

    if binary not in _PACKAGE_RUNNERS:
        return False

    normalized_args = _strip_runner_subcommand(command, list(args or []))
    idx = 0
    while idx < len(normalized_args):
        token = normalized_args[idx]
        if token in {"-p", "--package"} and idx + 1 < len(normalized_args):
            if _is_mcp_remote_package_token(normalized_args[idx + 1]):
                return True
            idx += 2
            continue
        if _is_mcp_remote_package_token(token):
            return True
        idx += 1
    return False


@dataclass(frozen=True)
class RewrittenMCPRemoteCommand:
    command: str
    args: List[str]
    is_remote_mcp_remote: bool


def rewrite_mcp_remote_invocation(command: str, args: List[str]) -> RewrittenMCPRemoteCommand:
    binary = _normalize_binary(command)
    source_args = list(args or [])
    if binary == "mcp-remote":
        return RewrittenMCPRemoteCommand(
            command="npx",
            args=["-y", REMOTE_MCP_REMOTE_PACKAGE, *source_args],
            is_remote_mcp_remote=True,
        )

    if binary not in _PACKAGE_RUNNERS:
        return RewrittenMCPRemoteCommand(
            command=command,
            args=source_args,
            is_remote_mcp_remote=False,
        )

    normalized_args = _strip_runner_subcommand(command, source_args)
    rewritten_tail: List[str] = []
    matched = False
    idx = 0
    while idx < len(normalized_args):
        token = normalized_args[idx]
        if token in {"-y", "--yes"}:
            idx += 1
            continue
        if token in {"-p", "--package"} and idx + 1 < len(normalized_args):
            package_name = normalized_args[idx + 1]
            if _is_mcp_remote_package_token(package_name):
                matched = True
                idx += 2
                continue
            rewritten_tail.extend([token, package_name])
            idx += 2
            continue
        if _is_mcp_remote_package_token(token):
            matched = True
            idx += 1
            continue
        rewritten_tail.append(token)
        idx += 1

    if not matched:
        return RewrittenMCPRemoteCommand(
            command=command,
            args=source_args,
            is_remote_mcp_remote=False,
        )

    return RewrittenMCPRemoteCommand(
        command="npx",
        args=["-y", REMOTE_MCP_REMOTE_PACKAGE, *rewritten_tail],
        is_remote_mcp_remote=True,
    )


def ensure_remote_mcp_env(env: Dict[str, str], *, is_remote_mcp_remote: bool) -> Dict[str, str]:
    normalized_env = dict(env or {})
    if is_remote_mcp_remote:
        normalized_env.setdefault("MCP_REMOTE_CONFIG_DIR", DEFAULT_REMOTE_MCP_CONFIG_DIR)
    return normalized_env
