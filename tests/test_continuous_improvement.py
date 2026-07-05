from __future__ import annotations

import dataclasses
import asyncio
import json
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from symphony.continuous_improvement import (
    BaselineProof,
    CheckResult,
    CheckSpec,
    CommandExecution,
    ImprovementRunResult,
    IssueFinding,
    prove_baseline,
    register_findings,
    render_report,
    run_argv,
    run_continuous_improvement,
    run_predefined_check,
    write_report,
)
from symphony.workflow import build_service_config, load_workflow


class _Stream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def read(self, _size: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _Proc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.pid = None
        self.stdout = _Stream([b"token=sk-secret-value\n"])
        self.stderr = _Stream([b"failure line\n"])
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def _workflow(tmp_path: Path, *, tracker_kind: str = "file"):
    board = tmp_path / "kanban"
    board.mkdir()
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        textwrap.dedent(
            f"""\
            ---
            tracker:
              kind: {tracker_kind}
              board_root: ./kanban
              project_slug: demo
              active_states: [Todo, In Progress]
              terminal_states: [Done, Archive]
            agent:
              kind: codex
            continuous_improvement:
              enabled: true
              interval_ms: 60000
              max_turns: 4
              ticket_prefix: CI
              max_tickets_per_run: 1
              agent_kind: opencode
            ---

            Prompt.
            """
        ),
        encoding="utf-8",
    )
    return build_service_config(load_workflow(workflow))


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_run_argv_uses_exec_args_caps_and_redacts_output(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    async def fake_factory(*argv: str, **kwargs: Any) -> _Proc:
        calls.append((argv, kwargs))
        return _Proc(returncode=1)

    async def fake_wait(proc: _Proc, *, timeout: float | None = None) -> int | None:
        return proc.returncode

    result = await run_argv(
        ("python", "-m", "pytest", "-q"),
        tmp_path,
        timeout_s=1,
        output_limit=24,
        proc_factory=fake_factory,
        proc_wait=fake_wait,
    )

    assert calls[0][0] == ("python", "-m", "pytest", "-q")
    assert "shell" not in calls[0][1]
    assert result.returncode == 1
    assert result.truncated is True
    assert "sk-secret-value" not in result.output
    assert "[REDACTED]" in result.output


@pytest.mark.asyncio
async def test_run_argv_timeout_kills_and_reports_not_proven(tmp_path: Path) -> None:
    proc = _Proc(returncode=None)  # type: ignore[arg-type]
    waits = [None, -9]

    async def fake_factory(*_argv: str, **_kwargs: Any) -> _Proc:
        return proc

    async def fake_wait(_proc: _Proc, *, timeout: float | None = None) -> int | None:
        return waits.pop(0)

    result = await run_argv(
        ("python", "-m", "pytest", "-q"),
        tmp_path,
        timeout_s=0.01,
        proc_factory=fake_factory,
        proc_wait=fake_wait,
    )

    assert result.timed_out is True
    assert proc.killed is True
    assert result.returncode is None


@pytest.mark.asyncio
async def test_run_argv_cancellation_kills_child(tmp_path: Path) -> None:
    proc = _Proc(returncode=None)  # type: ignore[arg-type]
    started = asyncio.Event()
    wait_calls = 0

    async def fake_factory(*_argv: str, **_kwargs: Any) -> _Proc:
        return proc

    async def fake_wait(_proc: _Proc, *, timeout: float | None = None) -> int | None:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            started.set()
            await asyncio.Event().wait()
        return -9

    task = asyncio.create_task(
        run_argv(
            ("python", "-m", "pytest", "-q"),
            tmp_path,
            timeout_s=60,
            proc_factory=fake_factory,
            proc_wait=fake_wait,
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed is True


@pytest.mark.asyncio
async def test_run_predefined_check_normalizes_result_states(tmp_path: Path) -> None:
    async def failed(_argv, _cwd, **_kwargs):
        return CommandExecution(("python", "-m", "pytest", "-q"), 2, "red", False, False)

    async def timed_out(_argv, _cwd, **_kwargs):
        return CommandExecution(("python", "-m", "pytest", "-q"), None, "", True, False)

    failed_result = await run_predefined_check(
        CheckSpec("pytest", ("python", "-m", "pytest", "-q")),
        tmp_path,
        run_argv_func=failed,
    )
    timeout_result = await run_predefined_check(
        CheckSpec("pytest", ("python", "-m", "pytest", "-q")),
        tmp_path,
        run_argv_func=timed_out,
    )

    assert failed_result.status == "failed"
    assert timeout_result.status == "not_proven"
    assert "red" in failed_result.summary


@pytest.mark.asyncio
async def test_failed_check_summary_keeps_distinct_failure_evidence(
    tmp_path: Path,
) -> None:
    async def first_failure(_argv, _cwd, **_kwargs):
        return CommandExecution(
            ("python", "-m", "pytest", "-q"),
            1,
            "FAILED tests/test_a.py::test_one\n",
            False,
            False,
        )

    async def second_failure(_argv, _cwd, **_kwargs):
        return CommandExecution(
            ("python", "-m", "pytest", "-q"),
            1,
            "FAILED tests/test_b.py::test_two\n",
            False,
            False,
        )

    spec = CheckSpec("pytest", ("python", "-m", "pytest", "-q"))
    first = await run_predefined_check(spec, tmp_path, run_argv_func=first_failure)
    second = await run_predefined_check(spec, tmp_path, run_argv_func=second_failure)
    base = BaselineProof("passed", "dev", "abc123", False, "none", "clean")
    first_finding = IssueFinding(
        rubric_item=first.name,
        check_name=first.name,
        command=first.command,
        summary=first.summary,
        evidence=first.output,
        expected="pytest exits 0",
        fix_boundary="tests",
        verification_commands=("python -m pytest -q",),
        baseline_branch=base.branch,
        baseline_sha=base.sha,
    )
    second_finding = dataclasses.replace(
        first_finding, summary=second.summary, evidence=second.output
    )

    assert "tests/test_a.py::test_one" in first.summary
    assert "tests/test_b.py::test_two" in second.summary
    assert first_finding.fingerprint != second_finding.fingerprint


@pytest.mark.asyncio
async def test_baseline_dirty_status_is_not_proven(tmp_path: Path) -> None:
    async def fake_run(argv, _cwd, **_kwargs):
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): "dev\n",
            ("git", "rev-parse", "HEAD"): "abc123\n",
            ("git", "status", "--porcelain"): " M src/app.py\n",
        }
        return CommandExecution(tuple(argv), 0, outputs[tuple(argv)], False, False)

    baseline = await prove_baseline(tmp_path, run_argv_func=fake_run)

    assert baseline.status == "not_proven"
    assert baseline.branch == "dev"
    assert baseline.sha == "abc123"
    assert "dirty" in baseline.summary


@pytest.mark.asyncio
async def test_baseline_target_branch_mismatch_is_not_proven(tmp_path: Path) -> None:
    async def fake_run(argv, _cwd, **_kwargs):
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "feature\n"),
            ("git", "rev-parse", "HEAD"): (0, "abc123\n"),
            ("git", "rev-parse", "--verify", "dev"): (0, "abc123\n"),
        }
        rc, output = outputs[tuple(argv)]
        return CommandExecution(tuple(argv), rc, output, False, False)

    baseline = await prove_baseline(
        tmp_path,
        target_branch="dev",
        run_argv_func=fake_run,
    )

    assert baseline.status == "not_proven"
    assert baseline.branch == "feature"
    assert "target branch 'dev'" in baseline.summary


def test_write_report_preserves_operator_notes(tmp_path: Path) -> None:
    report = tmp_path / "latest.md"
    report.write_text(
        textwrap.dedent(
            """\
            # Continuous improvement

            operator note

            <!-- ci:auto:summary:start -->
            old
            <!-- ci:auto:summary:end -->
            """
        ),
        encoding="utf-8",
    )
    result = ImprovementRunResult(
        tickets_created=1,
        verified_branch="dev",
        verified_sha="abc123",
        baseline=BaselineProof("passed", "dev", "abc123", False, "none", "clean"),
        checks=(CheckResult("pytest", ("python", "-m", "pytest", "-q"), "passed", "ok"),),
        ticket_ids=("CI-1",),
        started_at="2026-07-05T00:00:00Z",
        finished_at="2026-07-05T00:01:00Z",
        turns_used=1,
        max_turns=4,
    )

    write_report(report, result)
    text = report.read_text(encoding="utf-8")

    assert "operator note" in text
    assert "- Result: passed" in text
    assert "| pytest | passed | ok |" in text
    assert "- CI-1" in text


def test_register_findings_creates_caps_dedupes_and_stamps_agent(tmp_path: Path) -> None:
    cfg = _workflow(tmp_path)
    first = IssueFinding(
        rubric_item="pytest",
        check_name="pytest",
        command=("python", "-m", "pytest", "-q"),
        summary="unit tests failed",
        evidence="FAILED tests/test_demo.py",
        expected="pytest exits 0",
        fix_boundary="tests or source touched by the failure",
        verification_commands=("python -m pytest -q",),
        baseline_branch="dev",
        baseline_sha="abc123",
    )
    second = dataclasses.replace(first, summary="ruff failed")

    result = register_findings(cfg, tmp_path, (first, second))

    assert result.tickets_created == 1
    assert result.ticket_ids == ("CI-1",)
    assert result.skipped_due_to_cap == 1
    text = (tmp_path / "kanban" / "CI-1.md").read_text(encoding="utf-8")
    assert "CI Fingerprint: " in text
    assert "kind: opencode" in text

    duplicate = register_findings(cfg, tmp_path, (first,))
    assert duplicate.tickets_created == 0
    assert duplicate.duplicates == 1
    assert len(list((tmp_path / "kanban").glob("CI-*.md"))) == 1


def test_register_findings_reports_unsupported_tracker(tmp_path: Path) -> None:
    cfg = _workflow(tmp_path)
    cfg = dataclasses.replace(
        cfg, tracker=dataclasses.replace(cfg.tracker, kind="jira", board_root=None)
    )
    finding = IssueFinding(
        rubric_item="pytest",
        check_name="pytest",
        command=("python", "-m", "pytest", "-q"),
        summary="unit tests failed",
        evidence="FAILED",
        expected="pytest exits 0",
        fix_boundary="tests",
        verification_commands=("python -m pytest -q",),
        baseline_branch="dev",
        baseline_sha="abc123",
    )

    result = register_findings(cfg, tmp_path, (finding,))

    assert result.unsupported_tracker is True
    assert result.skipped_reason == "unsupported_tracker"


@pytest.mark.asyncio
async def test_run_continuous_improvement_writes_report_and_registers_failed_check(
    tmp_path: Path,
) -> None:
    cfg = _workflow(tmp_path)
    phases: list[str] = []

    async def fake_run(argv, _cwd, **_kwargs):
        key = tuple(argv)
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "dev\n"),
            ("git", "rev-parse", "HEAD"): (0, "abc123\n"),
            ("git", "status", "--porcelain"): (0, ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                128,
                "no upstream",
            ),
            ("python", "-m", "pytest", "-q"): (1, "FAILED tests/test_demo.py\n"),
            ("python", "-m", "ruff", "check", "src", "tests"): (0, "ok\n"),
            ("python", "-m", "pyright"): (0, "0 errors\n"),
        }
        rc, output = outputs[key]
        return CommandExecution(key, rc, output, False, False)

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        phases.append,
        run_argv_func=fake_run,
    )

    assert phases == ["baseline", "checks", "report", "registrar"]
    assert result.tickets_created == 1
    assert result.verified_branch == "dev"
    assert result.verified_sha == "abc123"
    assert (tmp_path / "docs" / "continuous-improvement" / "latest.md").exists()
    assert (tmp_path / "kanban" / "CI-1.md").exists()
    assert "FAILED tests/test_demo.py" in render_report(result)


@pytest.mark.asyncio
async def test_run_continuous_improvement_required_check_not_proven_marks_run(
    tmp_path: Path,
) -> None:
    cfg = _workflow(tmp_path)

    async def fake_run(argv, _cwd, **_kwargs):
        key = tuple(argv)
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "dev\n", False),
            ("git", "rev-parse", "HEAD"): (0, "abc123\n", False),
            ("git", "status", "--porcelain"): (0, "", False),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                128,
                "no upstream",
                False,
            ),
            ("python", "-m", "pytest", "-q"): (None, "", True),
            ("python", "-m", "ruff", "check", "src", "tests"): (0, "ok\n", False),
            ("python", "-m", "pyright"): (0, "0 errors\n", False),
        }
        rc, output, timed_out = outputs[key]
        return CommandExecution(key, rc, output, timed_out, False)

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        lambda _phase: None,
        run_argv_func=fake_run,
    )

    assert result.status == "not_proven"
    assert result.tickets_created == 0


@pytest.mark.asyncio
async def test_run_continuous_improvement_uses_temp_worktree_for_target_branch(
    tmp_path: Path,
) -> None:
    cfg = _workflow(tmp_path)
    cfg = dataclasses.replace(
        cfg, agent=dataclasses.replace(cfg.agent, auto_merge_target_branch="dev")
    )
    check_cwds: list[Path] = []
    worktree_paths: list[Path] = []

    async def fake_run(argv, cwd, **_kwargs):
        key = tuple(argv)
        cwd_path = Path(cwd)
        if key[:4] == ("git", "worktree", "add", "--detach"):
            worktree_paths.append(Path(key[4]))
            return CommandExecution(key, 0, "", False, False)
        if key[:4] == ("git", "worktree", "remove", "--force"):
            assert worktree_paths and Path(key[4]) == worktree_paths[0]
            return CommandExecution(key, 0, "", False, False)
        if cwd_path == tmp_path:
            outputs = {
                ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "feature\n"),
                ("git", "rev-parse", "HEAD"): (0, "featureabc\n"),
                ("git", "rev-parse", "--verify", "dev"): (0, "devabc\n"),
            }
            rc, output = outputs[key]
            return CommandExecution(key, rc, output, False, False)
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "HEAD\n"),
            ("git", "rev-parse", "HEAD"): (0, "devabc\n"),
            ("git", "status", "--porcelain"): (0, ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                128,
                "no upstream",
            ),
            ("python", "-m", "pytest", "-q"): (0, "ok\n"),
            ("python", "-m", "ruff", "check", "src", "tests"): (0, "ok\n"),
            ("python", "-m", "pyright"): (0, "0 errors\n"),
        }
        rc, output = outputs[key]
        if key[0] == "python":
            check_cwds.append(cwd_path)
        return CommandExecution(key, rc, output, False, False)

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        lambda _phase: None,
        run_argv_func=fake_run,
    )

    assert result.status == "passed"
    assert result.verified_branch == "dev"
    assert result.verified_sha == "devabc"
    assert worktree_paths
    assert check_cwds and all(path == worktree_paths[0] for path in check_cwds)


@pytest.mark.asyncio
async def test_run_continuous_improvement_real_git_target_worktree_e2e(
    tmp_path: Path,
) -> None:
    (tmp_path / "kanban").mkdir()
    (tmp_path / "src" / "demo_pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "demo_pkg" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_failure.py").write_text(
        "def test_failure():\n    assert False, 'e2e heartbeat failure'\n",
        encoding="utf-8",
    )
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps({"include": ["src"], "typeCheckingMode": "basic"}),
        encoding="utf-8",
    )
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
              project_slug: e2e
              active_states: [Todo, In Progress]
              terminal_states: [Done, Archive]
            agent:
              kind: codex
              auto_merge_target_branch: dev
            continuous_improvement:
              enabled: true
              interval_ms: 60000
              max_turns: 2
              ticket_prefix: CI
              max_tickets_per_run: 1
              agent_kind: opencode
            ---

            E2E prompt.
            """
        ),
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "ci-e2e@example.test")
    _git(tmp_path, "config", "user.name", "CI E2E")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "initial dev baseline")
    _git(tmp_path, "branch", "-M", "dev")
    _git(tmp_path, "switch", "-q", "-c", "feature")
    cfg = build_service_config(load_workflow(workflow))
    phases: list[str] = []

    result = await run_continuous_improvement(cfg, tmp_path, phases.append)

    ticket_text = (tmp_path / "kanban" / "CI-1.md").read_text(encoding="utf-8")
    report = (tmp_path / "docs" / "continuous-improvement" / "latest.md").read_text(
        encoding="utf-8"
    )
    worktree_root = tmp_path / ".symphony" / "continuous-improvement" / "worktrees"
    remaining_worktrees = list(worktree_root.iterdir()) if worktree_root.exists() else []
    assert result.status == "failed"
    assert result.tickets_created == 1
    assert result.verified_branch == "dev"
    assert phases == ["baseline", "checks", "report", "registrar"]
    assert "e2e heartbeat failure" in ticket_text
    assert "e2e heartbeat failure" in report
    assert "kind: opencode" in ticket_text
    assert "CI Fingerprint:" in ticket_text
    assert "clean temporary worktree for dev" in report
    assert _git(tmp_path, "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    assert remaining_worktrees == []
