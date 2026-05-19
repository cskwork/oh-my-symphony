"""Stage-contract validators for Plan/Review/QA/Done transitions.

The Symphony stage prompts encode contracts in narrative form:

- Plan must produce `## Plan`, `## Acceptance Tests`, `## Done Signals`.
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
    """

    passed: bool
    missing: list[str] = field(default_factory=list)
    note_heading: str = ""
    note_body: str = ""

    @property
    def note(self) -> str:
        if not self.note_heading:
            return ""
        return f"## {self.note_heading}\n{self.note_body}"


# Each producing stage maps to the section headings that MUST be present
# (with non-empty bodies) in the ticket markdown before the next stage is
# allowed to dispatch.
_PLAN_REQUIRED = ("## Plan", "## Acceptance Tests", "## Done Signals")
_REVIEW_REQUIRED_AUDIT = "## Security Audit"
_REVIEW_OUTCOMES = ("## Review", "## Review Findings")
_QA_REQUIRED = ("## QA Evidence", "## AC Scorecard")
_DONE_REQUIRED = ("## As-Is -> To-Be Report", "## Merge Status")


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

    if state == "review":
        missing: list[str] = []
        if not _section_present_nonempty(body, _REVIEW_REQUIRED_AUDIT):
            missing.append(_REVIEW_REQUIRED_AUDIT)
        # Either a clean `## Review` OR a rewind-triggering `## Review
        # Findings` counts as a valid outcome. Missing both means the
        # reviewer produced nothing actionable.
        if not any(_section_present_nonempty(body, name) for name in _REVIEW_OUTCOMES):
            missing.append(_REVIEW_OUTCOMES[0])
        return _build_result(producing_state, missing)

    if state == "qa":
        missing = _missing_sections(body, _QA_REQUIRED)
        return _build_result(producing_state, missing)

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
    # not in the v0.6.7 enforcement bundle.
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


def _build_result(producing_state: str, missing: list[str]) -> ContractResult:
    if not missing:
        return ContractResult(passed=True)
    body = _format_failure_body(producing_state, missing)
    return ContractResult(
        passed=False,
        missing=list(missing),
        note_heading="Contract Failure",
        note_body=body,
    )


def _format_failure_body(producing_state: str, missing: list[str]) -> str:
    """Render the body of the `## Contract Failure` ticket note."""
    bullets = "\n".join(f"- {item}" for item in missing)
    return (
        f"Stage `{producing_state}` did not produce the required outputs.\n"
        f"Missing:\n{bullets}\n"
        "Symphony rewound the ticket so the producing stage can complete the contract."
    )
