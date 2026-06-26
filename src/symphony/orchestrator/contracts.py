"""Stage-contract validators for Plan/Review/QA/Done transitions.

The Symphony stage prompts encode contracts in narrative form:

- Plan must produce `## Plan`, `## Acceptance Tests`, `## Done Signals`.
- Critic must produce either a clean `## Critic` (no gaps) or the rewind
  pair `## Surfaced Requirements` + `## Critic Tests` (rewind to In Progress).
- Review must produce a 7-row `## Security Audit` table and either
  `## Review` (clean pass) or `## Review Findings` (rewind to In Progress).
- QA must produce `## QA Evidence` and `## AC Scorecard`.
- Done must produce `## As-Is -> To-Be Report` and `## Merge Status`, and
  the artefact directories named in `## Evidence` must actually contain
  files on disk.

Strong models (Sonnet/Opus) obey those rules from prose. Weak models
(Haiku, GPT-4o-mini, open-weight) skip steps silently. The validator
parses the body for the required sections, surfaces the missing ones,
and returns a `## Contract Failure` note the caller appends before
rewinding the ticket back to the producing stage.

The validator is intentionally permissive on body *shape* (any non-empty
section body counts) but strict on *presence* — the prompts already
encode what each section must contain, and re-implementing that here in
regex would duplicate the contract in two places. Presence is the
machine-checkable signal; weak models that produce the section name will
also produce some content under it.

On top of presence, a second band of checks (S2) verifies *external
facts* the prompt cannot self-certify — file existence and verdict
consistency — NOT a regex re-implementation of what a section "should
say" (the :19-24 rationale still holds: prose shape is not re-encoded
here). Concretely:

- `_cited_paths_exist` confirms every evidence path the model cited
  (AC Scorecard `evidence path` cells, Security Audit `path:line` cells)
  actually exists under `docs_root`. A cited-but-absent file is a
  fabricated citation — a hard rewind, not a matter of prose.
- A `fail` verdict row in `## Security Audit` paired with a clean
  `## Review` (instead of `## Review Findings`) is self-contradictory:
  the reviewer flagged a failure yet signalled "clean". Hard rewind.
- `_scorecard_all_pass` reads the AC Scorecard `result` column. A
  fail/error/empty result cell is surfaced as a soft `[contract-warn]`
  note this release (passed stays True) rather than a rewind, so a
  hollow-but-honest scorecard is visible without blocking the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ContractResult:
    """Outcome of a stage-contract evaluation.

    `passed` is True when all required sections (and, for Done, artefact
    paths) are present. `missing` lists the section names or artefact
    descriptions that failed. `note_heading` + `note_body` plug straight
    into `_tracker_call_append_note(cfg, issue, heading, body)`; `note`
    is the fully rendered markdown for callers that just want one string.

    `warnings` carries soft advisories (e.g. a non-passing AC Scorecard
    row this release) that do NOT flip `passed` to False. Callers that
    only read `passed`/`missing` keep working; callers that want to
    surface advisories append `warning_note` when `warnings` is set.
    """

    passed: bool
    missing: list[str] = field(default_factory=list)
    note_heading: str = ""
    note_body: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        if not self.note_heading:
            return ""
        return f"## {self.note_heading}\n{self.note_body}"

    @property
    def warning_note(self) -> str:
        """Rendered `## Contract Warning` markdown, or "" when no warnings."""
        if not self.warnings:
            return ""
        bullets = "\n".join(f"- [contract-warn] {item}" for item in self.warnings)
        return f"## Contract Warning\n{bullets}"


# Each producing stage maps to the section headings that MUST be present
# (with non-empty bodies) in the ticket markdown before the next stage is
# allowed to dispatch.
_PLAN_REQUIRED = ("## Plan", "## Acceptance Tests", "## Done Signals")
_REVIEW_REQUIRED_AUDIT = "## Security Audit"
_REVIEW_OUTCOMES = ("## Review", "## Review Findings")
_REVIEW_CLEAN = "## Review"
_REVIEW_FINDINGS = "## Review Findings"
_QA_REQUIRED = ("## QA Evidence", "## AC Scorecard")
_QA_SCORECARD = "## AC Scorecard"
_DONE_REQUIRED = ("## As-Is -> To-Be Report", "## Merge Status")

# Critic produces EITHER a rewind pair (`## Surfaced Requirements` +
# `## Critic Tests`, when it found gaps and bounced the ticket back to
# In Progress) OR a clean `## Critic` ("no surfaced requirements"). Same
# either/or shape as `_REVIEW_OUTCOMES`: a clean pass needs no rewind
# sections; a rewind needs both.
_CRITIC_REQUIRED = ("## Surfaced Requirements", "## Critic Tests")
_CRITIC_CLEAN = "## Critic"

# Result/verdict cells that count as "not a clean pass". Compared
# case-insensitively after stripping whitespace and surrounding backticks.
_SCORECARD_PASS_TOKENS = frozenset({"pass", "passed", "ok", "green", "✅"})
_SECURITY_FAIL_TOKENS = frozenset({"fail", "failed", "critical"})

# Evidence cells that name no real artefact — never treated as a cited path.
_PATH_PLACEHOLDERS = frozenset({"", "n/a", "na", "none", "-", "--", "tbd", "—"})


def evaluate_contract(
    producing_state: str,
    ticket_body: str,
    identifier: str,
    *,
    docs_root: Path | None = None,
) -> ContractResult:
    """Evaluate the producing stage's contract against the ticket body.

    Returns a passing result for stages outside the v0.6.7 enforcement
    set (Explore, In Progress, Learn). The orchestrator wires the
    failing result into a rewind: the caller appends `result.note` and
    moves state back to the producing stage.
    """
    state = (producing_state or "").strip().lower()
    body = ticket_body or ""

    if state == "plan":
        missing = _missing_sections(body, _PLAN_REQUIRED)
        return _build_result(producing_state, missing)

    if state == "critic":
        # A clean `## Critic` (no gaps) needs no rewind sections. Otherwise
        # the rewind pair (`## Surfaced Requirements` + `## Critic Tests`)
        # must both be present — list whichever is absent.
        if _section_present_nonempty(body, _CRITIC_CLEAN):
            return _build_result(producing_state, [])
        missing = _missing_sections(body, _CRITIC_REQUIRED)
        return _build_result(producing_state, missing)

    if state == "review":
        missing: list[str] = []
        if not _section_present_nonempty(body, _REVIEW_REQUIRED_AUDIT):
            missing.append(_REVIEW_REQUIRED_AUDIT)
        # Either a clean `## Review` OR a rewind-triggering `## Review
        # Findings` counts as a valid outcome. Missing both means the
        # reviewer produced nothing actionable.
        if not any(_section_present_nonempty(body, name) for name in _REVIEW_OUTCOMES):
            missing.append(_REVIEW_OUTCOMES[0])
        # S2 security-fail consistency (hard): a `fail` Security Audit row
        # paired with a clean `## Review` (not `## Review Findings`) is
        # self-contradictory. Only meaningful once the audit table exists.
        if _REVIEW_REQUIRED_AUDIT not in missing and _security_has_fail_verdict(body):
            has_clean = _section_present_nonempty(body, _REVIEW_CLEAN)
            has_findings = _section_present_nonempty(body, _REVIEW_FINDINGS)
            if has_clean and not has_findings:
                missing.append(
                    "`## Security Audit` has a `fail` verdict but the body "
                    "is a clean `## Review` (expected `## Review Findings`)"
                )
        # S2 evidence-path realness (hard): every cited path must exist.
        missing.extend(_cited_paths_exist(body, docs_root, identifier))
        return _build_result(producing_state, missing)

    if state == "qa":
        missing = _missing_sections(body, _QA_REQUIRED)
        # S2 evidence-path realness (hard): cited AC Scorecard paths must
        # exist under docs_root.
        missing.extend(_cited_paths_exist(body, docs_root, identifier))
        # S2 scorecard consistency (soft this release): a non-passing
        # result cell is a warning, not a rewind — passed stays True.
        scorecard_problems = _scorecard_all_pass(body)[1]
        return _build_result(producing_state, missing, warnings=scorecard_problems)

    if state == "done":
        missing = _missing_sections(body, _DONE_REQUIRED)
        # Done additionally requires the artefact directories named in
        # the prompt's `## Evidence` block to contain at least one file
        # apiece. Missing files surface as a single descriptive entry —
        # the operator only needs one notification per Done attempt.
        if docs_root is not None and identifier:
            for required_dir in ("qa", "work"):
                target = docs_root / identifier / required_dir
                if not _directory_has_files(target):
                    missing.append(
                        f"artefact directory `{target}` missing or empty"
                    )
        return _build_result(producing_state, missing)

    # Explore, In Progress, Learn, and any future state pass through —
    # not in the enforcement bundle.
    return ContractResult(passed=True)


def _missing_sections(body: str, required: tuple[str, ...]) -> list[str]:
    """Return required headings that are absent or have an empty body."""
    return [name for name in required if not _section_present_nonempty(body, name)]


def _section_present_nonempty(body: str, heading: str) -> bool:
    """True when `heading` exists in `body` with a non-whitespace body.

    Matches Markdown `## heading` (and uses the same end-of-section
    delimiter — next `## ` heading or end-of-text — as the existing
    `_section_body` helper in `parsing.py`). Case-insensitive on the
    heading itself; tolerant of trailing colons and whitespace.
    """
    if not body:
        return False
    pattern = re.compile(
        r"^##\s+" + re.escape(heading[3:].strip()) + r"\s*:?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(body)
    if not match:
        return False
    after = body[match.end() :]
    next_heading = re.search(r"^##\s+\S", after, re.MULTILINE)
    section_body = after if next_heading is None else after[: next_heading.start()]
    return bool(section_body.strip())


def _directory_has_files(path: Path) -> bool:
    """True when `path` is a directory containing at least one regular file."""
    if not path.is_dir():
        return False
    for entry in path.rglob("*"):
        if entry.is_file():
            return True
    return False


def _section_body_text(body: str, heading: str) -> str:
    """Return the raw text under `heading` (up to the next `## ` heading)."""
    if not body:
        return ""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading[3:].strip()) + r"\s*:?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(body)
    if not match:
        return ""
    after = body[match.end() :]
    next_heading = re.search(r"^##\s+\S", after, re.MULTILINE)
    return after if next_heading is None else after[: next_heading.start()]


def _parse_markdown_table(body: str, heading: str) -> list[list[str]]:
    """Parse the GitHub-flavoured Markdown table under `heading`.

    Returns the data rows (header row and `---` separator dropped) as
    lists of trimmed cell strings. Non-table lines are ignored, so prose
    around the table does not corrupt the parse. Returns `[]` when the
    section or table is absent.
    """
    section = _section_body_text(body, heading)
    if not section:
        return []
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        # Drop the header/body separator row (cells made only of - : spaces).
        if cells and all(set(c) <= set("-: ") and c for c in cells):
            continue
        rows.append(cells)
    # First remaining row is the header; data rows follow.
    return rows[1:] if len(rows) > 1 else []


def _normalize_cell(cell: str) -> str:
    """Lower-case a table cell, stripping surrounding backticks/whitespace."""
    return cell.strip().strip("`").strip().lower()


def _security_has_fail_verdict(body: str) -> bool:
    """True when any `## Security Audit` row carries a `fail` verdict.

    The audit table is `check | verdict | evidence`; the verdict is the
    second column. Rows with fewer columns are skipped defensively.
    """
    for row in _parse_markdown_table(body, _REVIEW_REQUIRED_AUDIT):
        if len(row) < 2:
            continue
        if _normalize_cell(row[1]) in _SECURITY_FAIL_TOKENS:
            return True
    return False


def _scorecard_all_pass(body: str) -> tuple[bool, list[str]]:
    """Inspect the `## AC Scorecard` `result` column.

    The scorecard is `signal | source | result | evidence path`; the
    result is the third column. A cell that is not a pass token (fail,
    error, empty, anything else) is collected as a problem. Returns
    `(all_pass, problems)` where `problems` are human-readable advisories.
    """
    problems: list[str] = []
    for row in _parse_markdown_table(body, _QA_SCORECARD):
        if len(row) < 3:
            continue
        signal = row[0].strip() or "(unnamed signal)"
        result = _normalize_cell(row[2])
        if result in _SCORECARD_PASS_TOKENS:
            continue
        shown = result or "empty"
        problems.append(f"AC Scorecard signal `{signal}` result is `{shown}`")
    return (not problems, problems)


def _cited_paths_exist(
    body: str, docs_root: Path | None, identifier: str
) -> list[str]:
    """Return messages for cited evidence paths that do not exist on disk.

    Collects the evidence column of `## AC Scorecard` (last cell) and the
    `path:line` evidence column of `## Security Audit` (last cell), strips
    a trailing `:line`, and checks each resolves under `docs_root`. Bare
    placeholders (`n/a`, `-`, empty, …) and non-path tokens are skipped.
    A no-op when `docs_root` is unavailable — mirrors the Done branch,
    which only checks the filesystem when given a root.
    """
    if docs_root is None or not identifier:
        return []
    missing: list[str] = []
    for heading in (_QA_SCORECARD, _REVIEW_REQUIRED_AUDIT):
        for row in _parse_markdown_table(body, heading):
            if not row:
                continue
            cited = _extract_cited_path(row[-1])
            if cited is None:
                continue
            candidate = docs_root / cited
            if not candidate.exists():
                missing.append(
                    f"cited evidence path `{cited}` does not exist under docs root"
                )
    return missing


def _extract_cited_path(cell: str) -> str | None:
    """Normalise an evidence cell to a repo-relative path, or None.

    Strips backticks and Markdown link wrappers, drops a trailing
    `:line`/`:line:col`, and rejects placeholders and bare words that
    name no file (no `/` and no `name.ext` shape).
    """
    raw = cell.strip().strip("`").strip()
    # Unwrap a Markdown link `[text](path)` -> path.
    link = re.match(r"\[[^\]]*\]\(([^)]+)\)", raw)
    if link:
        raw = link.group(1).strip()
    if raw.lower() in _PATH_PLACEHOLDERS:
        return None
    # Strip a trailing :line or :line:col citation.
    path_part = re.sub(r":\d+(?::\d+)?$", "", raw).strip()
    if not path_part or path_part.lower() in _PATH_PLACEHOLDERS:
        return None
    looks_like_path = "/" in path_part or re.search(r"\.[A-Za-z0-9]+$", path_part)
    if not looks_like_path:
        return None
    return path_part


def _build_result(
    producing_state: str,
    missing: list[str],
    *,
    warnings: list[str] | None = None,
) -> ContractResult:
    warn_list = list(warnings or [])
    if not missing:
        return ContractResult(passed=True, warnings=warn_list)
    body = _format_failure_body(producing_state, missing)
    return ContractResult(
        passed=False,
        missing=list(missing),
        note_heading="Contract Failure",
        note_body=body,
        warnings=warn_list,
    )


def _format_failure_body(producing_state: str, missing: list[str]) -> str:
    """Render the body of the `## Contract Failure` ticket note."""
    bullets = "\n".join(f"- {item}" for item in missing)
    return (
        f"Stage `{producing_state}` did not produce the required outputs.\n"
        f"Missing:\n{bullets}\n"
        "Symphony rewound the ticket so the producing stage can complete the contract."
    )
