"""Allow-by-default approval guardrails for unattended agent workers."""

from __future__ import annotations

import re

_COMMAND_START = r"(?:^|[;&|()\n]\s*)"


def dangerous_command_reason(command: str) -> str | None:
    """Return a denial reason for commands too destructive to auto-approve.

    The classifier is intentionally not shell-quoting-aware. Approval prompts
    block unattended workers, while false negatives here can destroy data; a
    quoted string like ``echo "rm -rf"`` is therefore allowed to deny.
    """
    if _has_recursive_force_rm(command):
        return "rm with recursive and force flags is blocked"
    if _starts_command(command, "sudo"):
        return "sudo invocation is blocked"
    if re.search(_COMMAND_START + r"mkfs(?:\.[^\s;&|()]+)?(?:\s|$)", command):
        return "mkfs invocation is blocked"
    if re.search(_COMMAND_START + r"dd\b[^\n;&|]*\bof=/dev/", command):
        return "dd writing to /dev is blocked"
    if _starts_command(command, "shred"):
        return "shred invocation is blocked"
    if re.search(_COMMAND_START + r"find\b[^\n;&|]*\s-delete(?:\s|$)", command):
        return "find -delete is blocked"
    if _has_force_ignored_git_clean(command):
        return "git clean with -f and -x is blocked"
    return None


def _starts_command(command: str, name: str) -> bool:
    return re.search(_COMMAND_START + re.escape(name) + r"(?:\s|$)", command) is not None


def _has_recursive_force_rm(command: str) -> bool:
    for match in re.finditer(r"\brm\b(?P<tail>[^\n;&|]*)", command):
        flags = re.findall(r"--[A-Za-z-]+|-[A-Za-z]+", match.group("tail"))
        has_recursive = any(
            flag == "--recursive" or (flag.startswith("-") and "r" in flag.lower())
            for flag in flags
        )
        has_force = any(
            flag == "--force" or (flag.startswith("-") and "f" in flag.lower())
            for flag in flags
        )
        if has_recursive and has_force:
            return True
    return False


def _has_force_ignored_git_clean(command: str) -> bool:
    for match in re.finditer(
        _COMMAND_START + r"git\s+clean\b(?P<tail>[^\n;&|]*)",
        command,
    ):
        flags = re.findall(r"--[A-Za-z-]+|-[A-Za-z]+", match.group("tail"))
        has_force = any(
            flag == "--force" or (flag.startswith("-") and "f" in flag)
            for flag in flags
        )
        has_ignored = any(flag.startswith("-") and "x" in flag for flag in flags)
        if has_force and has_ignored:
            return True
    return False
