"""Markdown section parsers for ticket bodies.

The orchestrator scans tickets for two operator-facing sections:

  * `## Touched Files` — repo-relative paths the agent claims it edited,
    used by the conflict pre-check before re-dispatching a ticket.
  * `## Review Findings` / `## QA Failure` — severity + file:line + fix
    rows surfaced into the prompt env vars so the next attempt has
    structured guidance instead of free-text scrollback.

Both helpers are deliberately permissive — real agent output routinely
trails annotations, comments, and stray whitespace that a strict parser
would drop. See the inline notes in each function for the rationale.
"""

from __future__ import annotations

import re
from typing import Any

from .constants import (
    _BULLET_PATH_BACKTICK_RE,
    _BULLET_PATH_PLAIN_RE,
    _NEXT_HEADING_RE,
    _QA_FAILURE_HEADING_RE,
    _REVIEW_FINDINGS_HEADING_RE,
    _TOUCHED_FILES_HEADING_RE,
)


def _section_body(text: str, heading_re: re.Pattern[str]) -> str | None:
    """Return the body between `heading_re` and the next `## ` heading.

    Returns None when the heading is absent. Returns "" when the heading is
    present but the body is empty.
    """
    if not text:
        return None
    matches = list(heading_re.finditer(text))
    if not matches:
        return None
    # Use the LAST occurrence so re-issued findings (e.g. Review→IP→Review)
    # win over older sections in the same ticket body.
    match = matches[-1]
    after = text[match.end() :]
    next_heading = _NEXT_HEADING_RE.search(after)
    body = after if next_heading is None else after[: next_heading.start()]
    return body.strip("\n")


def _parse_touched_files(text: str | None) -> set[str]:
    """Extract repo-relative paths from the `## Touched Files` bullet list.

    Returns an empty set when the section is missing or contains no
    bullet rows. Tolerant of trailing comments after the path (anything
    after the first whitespace following a backticked path is ignored)."""
    if not text:
        return set()
    body = _section_body(text, _TOUCHED_FILES_HEADING_RE)
    if body is None:
        return set()
    out: set[str] = set()
    for line in body.splitlines():
        m = _BULLET_PATH_BACKTICK_RE.match(line) or _BULLET_PATH_PLAIN_RE.match(line)
        if not m:
            continue
        path = m.group("path").strip()
        if path:
            out.add(path)
    return out


def _parse_findings_rows(text: str | None) -> list[dict[str, Any]]:
    """Best-effort parse of `## Review Findings` / `## QA Failure` bullets.

    Returns a list of `{severity, file, line, fix}` dicts. Unrecognised
    bullets are skipped silently — the env var is informational, not
    contractual, so the agent prompt must already tolerate empty rows.

    Heuristics (bullet variants we have seen in WORKFLOW prompts):
      - ``- HIGH: src/foo.py:42 — refactor to use shared helper``
      - ``- [CRITICAL] src/foo.py:42 fix XSS``
      - ``- src/foo.py:42 fix XSS`` (severity defaults to empty string)
    """
    if not text:
        return []
    # Prefer Review Findings if both sections are present; QA Failure is a
    # fallback because QA-stage tickets emit it instead.
    body = _section_body(text, _REVIEW_FINDINGS_HEADING_RE)
    if body is None:
        body = _section_body(text, _QA_FAILURE_HEADING_RE)
    if not body:
        return []

    severity_re = re.compile(
        r"^\s*[-*]\s+"
        r"(?:\[(?P<sev_b>[A-Za-z]+)\]\s*|"
        r"(?P<sev_a>CRITICAL|HIGH|MEDIUM|LOW|INFO)\s*[:\-—]?\s*)?"
        r"(?P<rest>.+)$",
        re.IGNORECASE,
    )
    path_line_re = re.compile(
        r"`?(?P<file>[A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)`?"
        r"(?::(?P<line>\d+))?"
    )

    rows: list[dict[str, Any]] = []
    for raw in body.splitlines():
        m = severity_re.match(raw)
        if not m:
            continue
        rest = (m.group("rest") or "").strip()
        if not rest:
            continue
        severity = (m.group("sev_a") or m.group("sev_b") or "").upper()
        pm = path_line_re.search(rest)
        file_path = pm.group("file") if pm else ""
        try:
            line_no = int(pm.group("line")) if pm and pm.group("line") else 0
        except ValueError:
            line_no = 0
        # `fix` = the trailing free-text after the path (or the whole rest
        # when no path was found). Strip common dash separators so the
        # downstream prompt isn't fed `— foo`.
        fix_text = rest
        if pm:
            fix_text = (rest[pm.end() :] or "").strip(" -—:\t")
        rows.append(
            {
                "severity": severity,
                "file": file_path,
                "line": line_no,
                "fix": fix_text,
            }
        )
    return rows
