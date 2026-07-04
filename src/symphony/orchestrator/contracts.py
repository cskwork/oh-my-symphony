"""Stage-contract validators for the 4-stage Symphony pipeline.

The Symphony stage prompts encode contracts in narrative form:

- In Progress must produce planning, acceptance, done-signal,
  implementation, and self-critique sections plus durable work artefacts.
- Verify must produce review, security, QA, scorecard, and merge evidence.
- Learn must produce the human handoff and wiki write-back record.
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
class ContractFailure:
    """Structured row-level contract failure detail."""

    contract: str
    section: str
    row: int | None
    found: str
    expected: str


@dataclass(frozen=True)
class MarkdownTableRow:
    """Parsed Markdown table data row with a 1-based data-row index."""

    cells: tuple[str, ...]
    row: int


@dataclass(frozen=True)
class ContractResult:
    """Outcome of a stage-contract evaluation.

    `passed` is True when all required sections (and, for Done, artefact
    paths) are present. `missing` lists the section names or artefact
    descriptions that failed. `note_heading` + `note_body` plug straight
    into `_tracker_call_append_note(cfg, issue, heading, body)`; `note`
    is the fully rendered markdown for callers that just want one string.

    `failures` carries structured row-level details for prompt compaction.
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
    failures: tuple[ContractFailure, ...] = field(default_factory=tuple)

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
_IN_PROGRESS_REQUIRED = (
    "## Plan",
    "## Acceptance Tests",
    "## Done Signals",
    "## Implementation",
    "## Self-Critique",
)
_VERIFY_REQUIRED_AUDIT = "## Security Audit"
_VERIFY_REQUIRED = ("## QA Evidence", "## AC Scorecard", "## Merge Status")
_VERIFY_OUTCOMES = ("## Review", "## Review Findings")
_REVIEW_CLEAN = "## Review"
_REVIEW_FINDINGS = "## Review Findings"
_QA_SCORECARD = "## AC Scorecard"
_LEARN_REQUIRED = ("## Human Review", "## Wiki Updates")
_DONE_REQUIRED = ("## As-Is -> To-Be Report", "## Merge Status")

# Result/verdict cells that count as "not a clean pass". Compared
# case-insensitively after stripping whitespace and surrounding backticks.
_SCORECARD_PASS_TOKENS = frozenset({"pass", "passed", "ok", "green", "✅"})
_SECURITY_FAIL_TOKENS = frozenset({"fail", "failed", "critical"})

# Evidence cells that name no real artefact — never treated as a cited path.
_PATH_PLACEHOLDERS = frozenset({"", "n/a", "na", "none", "-", "--", "tbd", "—"})
_EVIDENCE_ARTIFACT_PREFIXES = ("qa/", "work/")


def evaluate_contract(
    producing_state: str,
    ticket_body: str,
    identifier: str,
    *,
    docs_root: Path | None = None,
) -> ContractResult:
    """Evaluate the producing stage's contract against the ticket body.

    Stages outside the 4-stage enforcement set pass through. The
    orchestrator wires a failing result into a rewind by appending
    `result.note` and moving state back to the producing stage.
    """
    state = (producing_state or "").strip().lower()
    body = ticket_body or ""

    if state == "in progress":
        return _evaluate_in_progress_contract(
            producing_state, body, identifier, docs_root
        )

    if state == "verify":
        return _evaluate_verify_contract(producing_state, body, identifier, docs_root)

    if state == "learn":
        missing = _missing_sections(body, _LEARN_REQUIRED)
        return _build_result(producing_state, missing)

    if state == "done":
        return _evaluate_done_contract(producing_state, body, identifier, docs_root)

    return ContractResult(passed=True)


def _evaluate_in_progress_contract(
    producing_state: str,
    body: str,
    identifier: str,
    docs_root: Path | None,
) -> ContractResult:
    missing = _missing_sections(body, _IN_PROGRESS_REQUIRED)
    if docs_root is not None and identifier:
        work_dir = docs_root / identifier / "work"
        if not _directory_has_files(work_dir):
            missing.append(f"artefact directory `{work_dir}` missing or empty")
    return _build_result(producing_state, missing)


def _evaluate_verify_contract(
    producing_state: str,
    body: str,
    identifier: str,
    docs_root: Path | None,
) -> ContractResult:
    missing = _missing_sections(body, _VERIFY_REQUIRED)
    if not _section_present_nonempty(body, _VERIFY_REQUIRED_AUDIT):
        missing.append(_VERIFY_REQUIRED_AUDIT)
    if not any(_section_present_nonempty(body, name) for name in _VERIFY_OUTCOMES):
        missing.append(_VERIFY_OUTCOMES[0])

    if _VERIFY_REQUIRED_AUDIT not in missing and _security_has_fail_verdict(body):
        has_clean = _section_present_nonempty(body, _REVIEW_CLEAN)
        has_findings = _section_present_nonempty(body, _REVIEW_FINDINGS)
        if has_clean and not has_findings:
            missing.append(
                "`## Security Audit` has a `fail` verdict but the body "
                "is a clean `## Review` (expected `## Review Findings`)"
            )

    evidence_failures = _cited_path_failures(body, docs_root, identifier)
    missing.extend(_failure_missing_message(failure) for failure in evidence_failures)
    missing.extend(_bug_repro_closed(docs_root, identifier))
    scorecard_problems = _scorecard_all_pass(body)[1]
    return _build_result(
        producing_state,
        missing,
        warnings=scorecard_problems,
        failures=evidence_failures,
    )


def _evaluate_done_contract(
    producing_state: str,
    body: str,
    identifier: str,
    docs_root: Path | None,
) -> ContractResult:
    missing = _missing_sections(body, _DONE_REQUIRED)
    if docs_root is not None and identifier:
        for required_dir in ("qa", "work"):
            target = docs_root / identifier / required_dir
            if not _directory_has_files(target):
                missing.append(f"artefact directory `{target}` missing or empty")
    return _build_result(producing_state, missing)


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


def _bug_repro_closed(docs_root: Path | None, identifier: str) -> list[str]:
    """Return a message when a bug's reproduction was never re-run at QA.

    Todo populates `docs_root / identifier / "reproduce"` for bug tickets.
    When that directory holds files, QA must close the loop by saving
    `docs_root / identifier / "qa" / "repro-after.log"`; a missing log is a
    hard failure naming the absent file. No reproduce dir (non-bug ticket)
    or no docs_root -> no-op. Mirrors the Done-branch path resolution.
    """
    if docs_root is None or not identifier:
        return []
    reproduce_dir = docs_root / identifier / "reproduce"
    if not _directory_has_files(reproduce_dir):
        return []
    repro_after = docs_root / identifier / "qa" / "repro-after.log"
    if not repro_after.exists():
        return [
            f"bug reproduction not closed: `{repro_after}` missing "
            f"(reproduce dir `{reproduce_dir}` is populated)"
        ]
    return []


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
    return [list(row.cells) for row in _parse_markdown_table_rows(body, heading)]


def _parse_markdown_table_rows(body: str, heading: str) -> list[MarkdownTableRow]:
    """Parse table data rows under `heading`, preserving data-row numbers."""
    section = _section_body_text(body, heading)
    if not section:
        return []
    rows: list[tuple[str, ...]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = tuple(c.strip() for c in stripped.strip("|").split("|"))
        # Drop the header/body separator row (cells made only of - : spaces).
        if cells and all(set(c) <= set("-: ") and c for c in cells):
            continue
        rows.append(cells)
    # First remaining row is the header; data rows follow.
    data_rows = rows[1:] if len(rows) > 1 else []
    return [
        MarkdownTableRow(cells=row, row=index)
        for index, row in enumerate(data_rows, start=1)
    ]


def _normalize_cell(cell: str) -> str:
    """Lower-case a table cell, stripping surrounding backticks/whitespace."""
    return cell.strip().strip("`").strip().lower()


def _security_has_fail_verdict(body: str) -> bool:
    """True when any `## Security Audit` row carries a `fail` verdict.

    The audit table is `check | verdict | evidence`; the verdict is the
    second column. Rows with fewer columns are skipped defensively.
    """
    for row in _parse_markdown_table(body, _VERIFY_REQUIRED_AUDIT):
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


def _cited_path_failures(
    body: str, docs_root: Path | None, identifier: str
) -> list[ContractFailure]:
    """Return structured failures for invalid or absent evidence paths.

    Collects the evidence column of `## AC Scorecard` and `## Security Audit`.
    Evidence paths must point under `docs/<identifier>/qa/` or
    `docs/<identifier>/work/`; source anchors belong inside those artefacts.
    """
    if docs_root is None or not identifier:
        return []
    failures: list[ContractFailure] = []
    expected_shape = _expected_evidence_shape(identifier)
    for heading in (_QA_SCORECARD, _VERIFY_REQUIRED_AUDIT):
        for row in _parse_markdown_table_rows(body, heading):
            if not row.cells:
                continue
            cell = row.cells[-1]
            cited = _extract_cited_path(cell)
            if cited is None:
                failures.append(
                    ContractFailure(
                        contract="Verify",
                        section=heading,
                        row=row.row,
                        found=cell,
                        expected=expected_shape,
                    )
                )
                continue
            artifact_path = _normalise_ticket_artifact_path(cited, identifier)
            if artifact_path is None:
                failures.append(
                    ContractFailure(
                        contract="Verify",
                        section=heading,
                        row=row.row,
                        found=cell,
                        expected=expected_shape,
                    )
                )
                continue
            candidate = docs_root / identifier / artifact_path
            if not candidate.exists():
                failures.append(
                    ContractFailure(
                        contract="Verify",
                        section=heading,
                        row=row.row,
                        found=cited,
                        expected=(
                            f"existing durable artifact under docs/{identifier} "
                            "as `qa/...` or `work/...`"
                        ),
                    )
                )
    return failures


def _normalise_ticket_artifact_path(cited: str, identifier: str) -> str | None:
    path = cited.strip().lstrip("./")
    for prefix in (f"docs/{identifier}/", f"{identifier}/"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    if path.startswith(_EVIDENCE_ARTIFACT_PREFIXES):
        return path
    return None


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

def _expected_evidence_shape(identifier: str) -> str:
    return (
        "evidence must cite a durable artifact such as "
        f"`docs/{identifier}/qa/evidence.md`, `qa/evidence.md`, "
        f"`docs/{identifier}/work/verify.log`, or `work/verify.log`; "
        "put source anchors/prose inside that artifact"
    )


def _failure_missing_message(failure: ContractFailure) -> str:
    row = f" row {failure.row}" if failure.row is not None else ""
    return (
        f"{failure.section}{row} evidence {_markdown_code_span(failure.found)} is invalid: "
        f"{failure.expected}"
    )


def _markdown_code_span(value: str) -> str:
    """Render ``value`` as a code span without colliding with its backticks."""
    text = value or ""
    runs = [len(match.group(0)) for match in re.finditer(r"`+", text)]
    fence = "`" * (max(runs, default=0) + 1)
    if text.startswith("`") or text.endswith("`"):
        return f"{fence} {text} {fence}"
    return f"{fence}{text}{fence}"


def _build_result(
    producing_state: str,
    missing: list[str],
    *,
    warnings: list[str] | None = None,
    failures: list[ContractFailure] | tuple[ContractFailure, ...] | None = None,
) -> ContractResult:
    warn_list = list(warnings or [])
    if not missing:
        return ContractResult(passed=True, warnings=warn_list)
    failure_tuple = tuple(failures or ())
    body = _format_failure_body(producing_state, missing, failure_tuple)
    return ContractResult(
        passed=False,
        missing=list(missing),
        note_heading="Contract Failure",
        note_body=body,
        warnings=warn_list,
        failures=failure_tuple,
    )


def _format_failure_body(
    producing_state: str,
    missing: list[str],
    failures: tuple[ContractFailure, ...] = (),
) -> str:
    """Render the body of the `## Contract Failure` ticket note."""
    chunks = [f"Stage `{producing_state}` did not produce the required outputs."]
    if failures:
        rows = "\n".join(
            f"- {failure.section} row {failure.row}: "
            f"found {_markdown_code_span(failure.found)}; "
            f"expected {failure.expected}"
            for failure in failures
        )
        chunks.append(f"Failing rows:\n{rows}")
    other_missing = [
        item
        for item in missing
        if not any(item == _failure_missing_message(failure) for failure in failures)
    ]
    if other_missing:
        bullets = "\n".join(f"- {item}" for item in other_missing)
        chunks.append(f"Missing:\n{bullets}")
    chunks.append(
        "Symphony rewound the ticket so the producing stage can complete the contract."
    )
    return "\n".join(chunks)
