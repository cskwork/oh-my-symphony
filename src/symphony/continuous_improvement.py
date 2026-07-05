"""Continuous-improvement heartbeat: runner, registrar, and durable lease.

This module owns the read-only product-readiness inspection that the
orchestrator scheduler delegates to:

* prove the current baseline without changing the host worktree;
* run fixed argv checks with timeouts, caps, and redaction;
* write machine-owned report sections;
* register failed findings as normal Kanban tickets through the tracker API;
* coordinate concurrent orchestrators through a fakeable advisory lease.

Keep this module dependency-light: it must not import the orchestrator.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from symphony.workflow import ServiceConfig

from ._shell import safe_proc_wait
from .trackers.file import FileBoardTracker

# Lockfile name under `<workflow_dir>/.symphony/`.
LEASE_FILENAME = "continuous_improvement.lock"
# A lease older than this (seconds) is considered abandoned and may be stolen
# — covers an orchestrator that crashed mid-run without releasing.
DEFAULT_LEASE_TTL_SECONDS = 1800.0
DEFAULT_CHECK_TIMEOUT_S = 600.0
DEFAULT_OUTPUT_LIMIT = 12_000
DEFAULT_REPORT_PATH = Path("docs/continuous-improvement/latest.md")

SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{8,}|"
    r"(token|api[_-]?key|password|secret)\s*=\s*[^\s]+)"
)


@dataclass(frozen=True)
class CommandExecution:
    argv: tuple[str, ...]
    returncode: int | None
    output: str
    timed_out: bool
    missing: bool
    truncated: bool = False


@dataclass(frozen=True)
class CheckSpec:
    name: str
    argv: tuple[str, ...]
    timeout_s: float = DEFAULT_CHECK_TIMEOUT_S
    optional: bool = False
    not_available_detail: str = ""


@dataclass(frozen=True)
class BaselineProof:
    status: str
    branch: str | None
    sha: str | None
    dirty: bool
    upstream: str | None
    summary: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: tuple[str, ...]
    status: str
    summary: str
    output: str = ""
    returncode: int | None = None


@dataclass(frozen=True)
class IssueFinding:
    rubric_item: str
    check_name: str
    command: tuple[str, ...]
    summary: str
    evidence: str
    expected: str
    fix_boundary: str
    verification_commands: tuple[str, ...]
    baseline_branch: str | None
    baseline_sha: str | None

    @property
    def fingerprint(self) -> str:
        normalized = _normalize_summary_for_fingerprint(self.summary)
        raw = "\n".join((self.rubric_item, " ".join(self.command), normalized))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TicketRegistrationResult:
    tickets_created: int = 0
    ticket_ids: tuple[str, ...] = ()
    duplicates: int = 0
    skipped_due_to_cap: int = 0
    unsupported_tracker: bool = False
    skipped_reason: str | None = None


@dataclass(frozen=True)
class _PreparedBaseline:
    proof: BaselineProof
    check_dir: Path
    cleanup_worktree: Path | None = None


@dataclass(frozen=True)
class ImprovementRunResult:
    """Outcome of one heartbeat run, surfaced to the web-API status."""

    tickets_created: int = 0
    verified_branch: str | None = None
    verified_sha: str | None = None
    status: str = "passed"
    skipped_reason: str | None = None
    baseline: BaselineProof | None = None
    checks: tuple[CheckResult, ...] = ()
    ticket_ids: tuple[str, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    turns_used: int = 0
    max_turns: int = 0


# The scheduler passes the live config, the resolved workflow dir, and a
# `report_phase` callback the runner uses to publish coarse progress
# (e.g. "checking", "verifying") into the status dict. Injectable so tests
# swap in a fake that records the call and returns a canned result.
ImprovementRunner = Callable[
    ["ServiceConfig", Path, Callable[[str], None]], Awaitable[ImprovementRunResult]
]


async def default_improvement_runner(
    cfg: "ServiceConfig",
    workflow_dir: Path,
    report_phase: Callable[[str], None],
) -> ImprovementRunResult:
    return await run_continuous_improvement(cfg, workflow_dir, report_phase)


def _utc_iso_z() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def redact_output(output: str) -> str:
    return SECRET_RE.sub("[REDACTED]", output)


async def _read_stream(stream: Any, limit: int) -> tuple[str, bool]:
    if stream is None:
        return "", False
    chunks: list[bytes] = []
    total_read = 0
    total_stored = 0
    truncated = False
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        total_read += len(chunk)
        if total_stored < limit:
            remaining = limit - total_stored
            chunks.append(chunk[:remaining])
            total_stored += min(len(chunk), remaining)
        if total_read > limit:
            truncated = True
    text = b"".join(chunks).decode("utf-8", errors="replace")
    return text, truncated


async def run_argv(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    timeout_s: float,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    proc_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    proc_wait: Callable[..., Awaitable[int | None]] = safe_proc_wait,
) -> CommandExecution:
    try:
        proc = await proc_factory(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return CommandExecution(
            argv, None, f"command not found: {argv[0]}", False, True
        )
    stdout_task = asyncio.create_task(_read_stream(proc.stdout, output_limit))
    stderr_task = asyncio.create_task(_read_stream(proc.stderr, output_limit))
    try:
        returncode = await proc_wait(proc, timeout=timeout_s)
        timed_out = returncode is None
        if timed_out:
            proc.kill()
            await proc_wait(proc, timeout=5)
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
    except asyncio.CancelledError:
        proc.kill()
        try:
            await proc_wait(proc, timeout=5)
        finally:
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    raw_output = stdout + stderr
    truncated = stdout_truncated or stderr_truncated or len(raw_output) > output_limit
    output = redact_output(raw_output[:output_limit])
    return CommandExecution(argv, returncode, output, timed_out, False, truncated)


def _first_output_line(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return ""


def _failed_check_summary(command: str, execution: CommandExecution) -> str:
    detail = _first_output_line(execution.output)
    if detail:
        return f"{command} exited {execution.returncode}: {detail}"
    return f"{command} exited {execution.returncode}"


async def run_predefined_check(
    spec: CheckSpec,
    cwd: Path,
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]] = run_argv,
) -> CheckResult:
    execution = await run_argv_func(spec.argv, cwd, timeout_s=spec.timeout_s)
    command = " ".join(spec.argv)
    if execution.missing:
        status = "not_available" if spec.optional else "not_proven"
        return CheckResult(spec.name, spec.argv, status, execution.output)
    if execution.timed_out:
        status = "not_available" if spec.optional else "not_proven"
        return CheckResult(spec.name, spec.argv, status, f"{command} timed out")
    if execution.returncode == 0:
        return CheckResult(spec.name, spec.argv, "passed", "ok", execution.output, 0)
    return CheckResult(
        spec.name,
        spec.argv,
        "failed",
        _failed_check_summary(command, execution),
        execution.output,
        execution.returncode,
    )


async def _git_branch_and_sha(
    cwd: Path,
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]],
) -> tuple[CommandExecution, CommandExecution]:
    branch = await run_argv_func(
        ("git", "rev-parse", "--abbrev-ref", "HEAD"),
        cwd,
        timeout_s=30,
    )
    if branch.returncode != 0 or branch.missing or branch.timed_out:
        return branch, CommandExecution(("git", "rev-parse", "HEAD"), None, "", False, True)
    sha = await run_argv_func(("git", "rev-parse", "HEAD"), cwd, timeout_s=30)
    return branch, sha


async def prove_baseline(
    workflow_dir: Path,
    *,
    target_branch: str = "",
    run_argv_func: Callable[..., Awaitable[CommandExecution]] = run_argv,
) -> BaselineProof:
    branch, sha = await _git_branch_and_sha(
        workflow_dir, run_argv_func=run_argv_func
    )
    if branch.returncode != 0 or branch.missing or branch.timed_out:
        return BaselineProof("not_proven", None, None, False, None, branch.output)
    if sha.returncode != 0 or sha.missing or sha.timed_out:
        return BaselineProof(
            "not_proven", branch.output.strip(), None, False, None, sha.output
        )
    current_branch = branch.output.strip()
    current_sha = sha.output.strip()
    target = target_branch.strip()
    if target:
        resolved_target = await run_argv_func(
            ("git", "rev-parse", "--verify", target),
            workflow_dir,
            timeout_s=30,
        )
        if (
            resolved_target.returncode != 0
            or resolved_target.missing
            or resolved_target.timed_out
        ):
            return BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                None,
                f"configured target branch {target!r} cannot be resolved",
            )
        if current_branch != target:
            return BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                None,
                "current branch "
                f"{current_branch!r} is not configured target branch {target!r}",
            )
    status = await run_argv_func(
        ("git", "status", "--porcelain"), workflow_dir, timeout_s=30
    )
    if status.returncode != 0 or status.missing or status.timed_out:
        return BaselineProof(
            "not_proven", current_branch, current_sha, False, None, status.output
        )
    dirty = bool(status.output.strip())
    if dirty:
        return BaselineProof(
            "not_proven",
            current_branch,
            current_sha,
            True,
            None,
            "dirty worktree blocks baseline proof",
        )
    upstream = await run_argv_func(
        ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
        workflow_dir,
        timeout_s=30,
    )
    upstream_text = "none"
    if upstream.returncode == 0:
        upstream_text = upstream.output.strip()
        ahead = await run_argv_func(
            ("git", "rev-list", "--left-right", "--count", "HEAD...@{u}"),
            workflow_dir,
            timeout_s=30,
        )
        if ahead.returncode != 0 or ahead.timed_out or ahead.missing:
            return BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                upstream_text,
                "upstream configured but not reachable",
            )
        upstream_text = f"{upstream_text} ({ahead.output.strip()})"
    return BaselineProof(
        "passed",
        current_branch,
        current_sha,
        False,
        upstream_text,
        "clean",
    )


def _worktree_path(workflow_dir: Path, target_branch: str) -> Path:
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "-", target_branch).strip("-")
    return (
        workflow_dir
        / ".symphony"
        / "continuous-improvement"
        / "worktrees"
        / f"{safe_target or 'target'}-{os.getpid()}-{time.time_ns()}"
    )


async def _prepare_baseline(
    workflow_dir: Path,
    target_branch: str,
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]],
) -> _PreparedBaseline:
    target = target_branch.strip()
    branch, sha = await _git_branch_and_sha(
        workflow_dir, run_argv_func=run_argv_func
    )
    if branch.returncode != 0 or branch.missing or branch.timed_out:
        return _PreparedBaseline(
            BaselineProof("not_proven", None, None, False, None, branch.output),
            workflow_dir,
        )
    current_branch = branch.output.strip()
    current_sha = sha.output.strip() if sha.returncode == 0 else None
    if sha.returncode != 0 or sha.missing or sha.timed_out:
        return _PreparedBaseline(
            BaselineProof(
                "not_proven", current_branch, None, False, None, sha.output
            ),
            workflow_dir,
        )
    if not target or current_branch == target:
        return _PreparedBaseline(
            await prove_baseline(workflow_dir, run_argv_func=run_argv_func),
            workflow_dir,
        )

    resolved_target = await run_argv_func(
        ("git", "rev-parse", "--verify", target),
        workflow_dir,
        timeout_s=30,
    )
    if resolved_target.returncode != 0 or resolved_target.missing or resolved_target.timed_out:
        return _PreparedBaseline(
            BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                None,
                f"configured target branch {target!r} cannot be resolved",
            ),
            workflow_dir,
        )

    check_dir = _worktree_path(workflow_dir, target)
    check_dir.parent.mkdir(parents=True, exist_ok=True)
    added = await run_argv_func(
        ("git", "worktree", "add", "--detach", str(check_dir), target),
        workflow_dir,
        timeout_s=120,
    )
    if added.returncode != 0 or added.missing or added.timed_out:
        return _PreparedBaseline(
            BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                None,
                f"could not create temporary worktree for {target!r}: {added.output}",
            ),
            workflow_dir,
        )

    proof = await prove_baseline(check_dir, run_argv_func=run_argv_func)
    if proof.status == "passed":
        proof = BaselineProof(
            proof.status,
            target,
            proof.sha,
            proof.dirty,
            proof.upstream,
            f"clean temporary worktree for {target}",
        )
    return _PreparedBaseline(proof, check_dir, check_dir)


async def _cleanup_baseline(
    prepared: _PreparedBaseline,
    workflow_dir: Path,
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]],
) -> None:
    if prepared.cleanup_worktree is None:
        return
    await run_argv_func(
        ("git", "worktree", "remove", "--force", str(prepared.cleanup_worktree)),
        workflow_dir,
        timeout_s=120,
    )


def _normalize_summary_for_fingerprint(summary: str) -> str:
    summary = re.sub(r"/(?:private/)?tmp/[^\s]+", "<tmp>", summary)
    summary = re.sub(r"\b\d{4}-\d{2}-\d{2}T[^\s]+", "<timestamp>", summary)
    summary = re.sub(r"\bpid=\d+\b", "pid=<pid>", summary)
    return summary.strip().lower()


def _finding_from_check(check: CheckResult, baseline: BaselineProof) -> IssueFinding:
    return IssueFinding(
        rubric_item=check.name,
        check_name=check.name,
        command=check.command,
        summary=check.summary,
        evidence=check.output,
        expected=f"{' '.join(check.command)} exits 0",
        fix_boundary=f"Fix the product-readiness failure reported by {check.name}.",
        verification_commands=(" ".join(check.command),),
        baseline_branch=baseline.branch,
        baseline_sha=baseline.sha,
    )


def _ticket_body(finding: IssueFinding) -> str:
    command = " ".join(finding.command)
    verification = "\n".join(finding.verification_commands)
    return textwrap.dedent(
        f"""\
        ## Continuous improvement finding

        - Rubric item: {finding.rubric_item}
        - Failing check: `{command}`
        - Baseline: branch `{finding.baseline_branch or 'unknown'}` @ `{finding.baseline_sha or 'unknown'}`

        ### Failure summary

        {finding.summary}

        ### Evidence

        ```
        {finding.evidence}
        ```

        ### Expected behavior

        {finding.expected}

        ### Proposed fix boundary

        {finding.fix_boundary}

        ### Verification

        Re-run before closing:

        ```
        {verification}
        ```

        CI Fingerprint: {finding.fingerprint}
        """
    ).strip() + "\n"


def register_findings(
    cfg: "ServiceConfig",
    workflow_dir: Path,
    findings: tuple[IssueFinding, ...],
) -> TicketRegistrationResult:
    ci = cfg.continuous_improvement
    if not findings:
        return TicketRegistrationResult()
    if cfg.tracker.kind != "file" or cfg.tracker.board_root is None:
        return TicketRegistrationResult(
            unsupported_tracker=True, skipped_reason="unsupported_tracker"
        )
    tracker = FileBoardTracker(cfg.tracker)
    active = tracker.fetch_candidate_issues()
    existing = {
        match.group(1)
        for issue in active
        for match in re.finditer(
            r"CI Fingerprint:\s*([a-f0-9]{16})", issue.description or ""
        )
    }
    created: list[str] = []
    duplicates = 0
    for finding in findings:
        if finding.fingerprint in existing:
            duplicates += 1
            continue
        if len(created) >= ci.max_tickets_per_run:
            continue
        title = f"CI: {finding.summary}"[:120]
        identifier, _ = tracker.create_with_next_identifier(
            ci.ticket_prefix,
            title=title,
            state=cfg.tracker.active_states[0] if cfg.tracker.active_states else "Todo",
            priority=1,
            labels=["continuous-improvement", "bug"],
            description=_ticket_body(finding),
            agent_kind=ci.agent_kind or None,
        )
        created.append(identifier)
        existing.add(finding.fingerprint)
    skipped_due_to_cap = max(0, len(findings) - duplicates - len(created))
    return TicketRegistrationResult(
        tickets_created=len(created),
        ticket_ids=tuple(created),
        duplicates=duplicates,
        skipped_due_to_cap=skipped_due_to_cap,
    )


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _evidence_blocks(checks: tuple[CheckResult, ...]) -> str:
    blocks: list[str] = []
    for check in checks:
        output = check.output.strip()
        if not output:
            continue
        command = " ".join(check.command) if check.command else check.name
        blocks.append(
            textwrap.dedent(
                f"""\
                ### {check.name}

                Command: `{command}`

                ```text
                {output}
                ```
                """
            ).strip()
        )
    return "\n\n".join(blocks) or "(none)"


def _report_sections(result: ImprovementRunResult) -> dict[str, str]:
    baseline = result.baseline or BaselineProof(
        "not_proven", None, None, False, None, "missing"
    )
    checks = result.checks or ()
    check_rows = ["| Check | Result | Detail |", "| --- | --- | --- |"]
    if checks:
        for check in checks:
            check_rows.append(
                f"| {_table_cell(check.name)} | {check.status} | "
                f"{_table_cell(check.summary)} |"
            )
    else:
        check_rows.append("| (none) | - | - |")
    tickets = "\n".join(f"- {ticket}" for ticket in result.ticket_ids) or "(none)"
    return {
        "summary": (
            f"- Result: {result.status}\n"
            f"- Tickets created: {result.tickets_created}\n"
            f"- Skipped reason: {result.skipped_reason or 'none'}"
        ),
        "baseline": (
            f"- Branch: {baseline.branch or '(unknown)'}\n"
            f"- SHA: {baseline.sha or '(unknown)'}\n"
            f"- Dirty: {baseline.dirty}\n"
            f"- Upstream: {baseline.upstream or '(none)'}\n"
            f"- Result: {baseline.status}\n"
            f"- Summary: {baseline.summary}"
        ),
        "checks": "\n".join(check_rows),
        "evidence": _evidence_blocks(checks),
        "tickets": tickets,
        "meta": (
            f"- Started at: {result.started_at or '(unknown)'}\n"
            f"- Finished at: {result.finished_at or '(unknown)'}\n"
            f"- Turns used / max turns: {result.turns_used} / {result.max_turns}\n"
            f"- Skipped reason: {result.skipped_reason or 'none'}"
        ),
    }


def _replace_section(text: str, name: str, body: str) -> str:
    start = f"<!-- ci:auto:{name}:start -->"
    end = f"<!-- ci:auto:{name}:end -->"
    replacement = f"{start}\n{body}\n{end}"
    if start in text and end in text:
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        return before + replacement + after
    return text.rstrip() + "\n\n" + replacement + "\n"


def render_report(result: ImprovementRunResult) -> str:
    text = "# Continuous improvement - latest run\n"
    for name, body in _report_sections(result).items():
        text = _replace_section(text, name, body)
    return text


def write_report(path: Path, result: ImprovementRunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else render_report(result)
    for name, body in _report_sections(result).items():
        text = _replace_section(text, name, body)
    path.write_text(text, encoding="utf-8")


DEFAULT_CHECKS = (
    CheckSpec("pytest", ("python", "-m", "pytest", "-q")),
    CheckSpec("ruff", ("python", "-m", "ruff", "check", "src", "tests")),
    CheckSpec("pyright", ("python", "-m", "pyright")),
)


async def run_continuous_improvement(
    cfg: "ServiceConfig",
    workflow_dir: Path,
    report_phase: Callable[[str], None],
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]] = run_argv,
) -> ImprovementRunResult:
    started_at = _utc_iso_z()
    report_phase("baseline")
    prepared = await _prepare_baseline(
        workflow_dir,
        cfg.agent.auto_merge_target_branch,
        run_argv_func=run_argv_func,
    )
    baseline = prepared.proof
    checks: list[CheckResult] = []
    registration = TicketRegistrationResult()
    try:
        if baseline.status == "passed":
            report_phase("checks")
            for spec in DEFAULT_CHECKS:
                checks.append(
                    await run_predefined_check(
                        spec, prepared.check_dir, run_argv_func=run_argv_func
                    )
                )
            checks.extend(
                [
                    CheckResult("browser_qa", (), "not_available", "not configured"),
                    CheckResult("db_probe", (), "not_available", "not configured"),
                ]
            )
            findings = tuple(
                _finding_from_check(c, baseline)
                for c in checks
                if c.status == "failed"
            )
            report_phase("report")
            registration = register_findings(cfg, workflow_dir, findings)
            report_phase("registrar")
    finally:
        await asyncio.shield(
            _cleanup_baseline(
                prepared, workflow_dir, run_argv_func=run_argv_func
            )
        )
    status = "passed"
    if baseline.status == "not_proven":
        status = "not_proven"
    elif any(c.status == "failed" for c in checks):
        status = "failed"
    elif any(c.status == "not_proven" for c in checks):
        status = "not_proven"
    result = ImprovementRunResult(
        tickets_created=registration.tickets_created,
        verified_branch=baseline.branch,
        verified_sha=baseline.sha,
        status=status,
        skipped_reason=registration.skipped_reason,
        baseline=baseline,
        checks=tuple(checks),
        ticket_ids=registration.ticket_ids,
        started_at=started_at,
        finished_at=_utc_iso_z(),
        max_turns=cfg.continuous_improvement.max_turns,
    )
    write_report(workflow_dir / DEFAULT_REPORT_PATH, result)
    return result


@runtime_checkable
class Lease(Protocol):
    """Cross-process advisory lock. All methods must be non-blocking."""

    def acquire(self) -> bool:
        """Try to take the lease. Return True on success, False if held."""
        ...

    def refresh(self) -> None:
        """Renew the lease timestamp during a long-running hold."""
        ...

    def release(self) -> None:
        """Release the lease. Idempotent; safe to call if never acquired."""
        ...


def lease_path_for(workflow_dir: Path) -> Path:
    return workflow_dir / ".symphony" / LEASE_FILENAME


class FileLease:
    """Lockfile-backed :class:`Lease` under the workflow dir.

    The file holds ``{"pid": ..., "acquired_at": <epoch>}``. Acquisition uses
    an exclusive create so two processes racing the empty state cannot both
    win; a lease older than ``ttl_seconds`` is treated as abandoned and
    stolen. This is advisory (best-effort), which is all the heartbeat needs
    — the durable turn counter and idle-board check are the real guards.
    """

    def __init__(
        self,
        path: Path,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._now = now
        self._held = False
        self._token = f"{os.getpid()}:{id(self)}:{self._now()}"

    def _payload(self) -> dict[str, object]:
        return {
            "pid": os.getpid(),
            "acquired_at": self._now(),
            "token": self._token,
        }

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(self._payload()),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    def _owns_current_file(self) -> bool:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        return data.get("token") == self._token

    def _is_stale(self) -> bool:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            acquired_at = float(data.get("acquired_at", 0.0))
        except (OSError, ValueError, TypeError):
            # Unreadable/corrupt lockfile — treat as abandoned.
            return True
        return (self._now() - acquired_at) >= self._ttl

    def acquire(self) -> bool:
        if self._held:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(
                    self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
            except FileExistsError:
                if not self._is_stale():
                    return False
                try:
                    self._path.unlink()
                except FileNotFoundError:
                    pass
                continue
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._payload(), fh)
                self._held = True
                return True
        return False

    def refresh(self) -> None:
        if not self._held:
            return
        if not self._owns_current_file():
            self._held = False
            return
        self._write()

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        if not self._owns_current_file():
            return
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
