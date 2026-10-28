"""Done-count, wiki/artifact/archive sweeps, and continuous-improvement scheduling for ``Orchestrator``.

Extracted from ``core.Orchestrator`` (LR-3 lean-core split). Each method is a
module-level function whose first parameter is ``self: Orchestrator``;
``core.Orchestrator`` binds it as a class attribute, so ``self.<name>(...)``
calls, ``Orchestrator.<name>`` patches, and behaviour are unchanged.
Collaborators that tests monkeypatch on ``symphony.orchestrator.core`` are read
through the late-bound ``_core()`` handle at call time, never bound here.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..artifacts import ArtifactRecord, format_bytes
from ..backends import BackendInit
from ..agent_modes import cfg_for_mode
from ..issue import normalize_state
from ..needs import needs_path, needs_states, refresh_needs
from ..utils import git_inspect
from ..utils.archive import select_archivable
from ..utils.atomic_json import state_file_name, write_json_atomic
from ..errors import SymphonyError
from ..continuous_improvement import AgentTask, FileLease, any_mode_due, lease_path_for
from ..utils.wiki_sweep import sweep as _wiki_sweep_run
from ..workflow import ServiceConfig
from .helpers import _to_iso, _utc_iso_z
from ..logging import get_logger

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()


def _core():
    """Late-bound handle to ``symphony.orchestrator.core`` (imports this module)."""
    from . import core

    return core


# The one path a continuous-improvement agent turn may write in the host
# worktree. Mirrors `continuous_improvement.AGENT_OUTPUT_DIR`.
CI_AGENT_OUTPUT_PREFIX = ".symphony/continuous-improvement/proposals/"


def _done_count_path(self: Orchestrator, cfg: ServiceConfig) -> Path:
    """On-disk location for the persisted Done counter."""
    return (
        cfg.workflow_path.parent
        / ".symphony"
        / state_file_name(cfg.workflow_path, "done_count")
    )


def _load_done_count(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Restore the Done counter across orchestrator restarts.

        Without persistence, every restart resets `_done_count` to 0 and
        the C5 wiki-sweep cadence skips indefinitely on a frequently
        restarted backend. Malformed payloads degrade to 0 rather than
        crash startup.
        """
    path = self._done_count_path(cfg)
    try:
        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("done_count_load_failed", path=str(path), error=str(exc))
        return
    if isinstance(raw, dict):
        value = raw.get("done_count")
        if isinstance(value, int) and value >= 0:
            self._done_count = value


def _persist_done_count(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Best-effort flush; mirrors `_persist_token_ema`."""
    path = self._done_count_path(cfg)
    try:
        write_json_atomic(path, {"done_count": self._done_count})
    except OSError as exc:
        log.warning("done_count_persist_failed", path=str(path), error=str(exc))


async def _maybe_run_wiki_sweep(self: Orchestrator, cfg: ServiceConfig, *, identifier: str) -> None:
    """C5 — bump the Done counter and run wiki-sweep every Nth time.

        Called (awaited) from the two Done-transition sites
        (`_on_worker_exit` and the reconcile-driven path). `sweep_every_n:
        0` disables the auto-sweep entirely. The sweep is best-effort and
        runs in-process for simplicity (the typical wiki is small), but
        off the event loop via `asyncio.to_thread` so a slow filesystem
        scan cannot stall ticks or delay shutdown. Failures only log a
        warning and never block the Done transition. The counter is
        persisted after every Done so sweep cadence survives
        orchestrator restarts.
        """
    every = cfg.wiki.sweep_every_n
    if every <= 0:
        return
    self._done_count += 1
    self._persist_done_count(cfg)
    if self._done_count % every != 0:
        return
    root = cfg.wiki.root
    if root is None:
        return
    try:
        report = await asyncio.to_thread(_wiki_sweep_run, root, dry_run=False)
    except Exception as exc:
        log.warning(
            "wiki_sweep_failed",
            identifier=identifier,
            root=str(root),
            error=str(exc),
        )
        return
    log.info(
        "wiki_sweep_run",
        identifier=identifier,
        done_count=self._done_count,
        sweep_every_n=every,
        root=str(report.root) if report.root is not None else "",
        duplicates=len(report.duplicates),
        orphans=len(report.orphans),
        missing_files=len(report.missing_files),
        stale=len(report.stale_entries),
        mutations=len(report.mutations),
        clean=report.is_clean(),
    )


def _artifact_commit_excludes(cfg: "ServiceConfig | None") -> tuple[str, ...]:
    """Pathspecs keeping collected deliverables out of the Done commit.

        Belt to `_ensure_artifact_dir_git_excluded`'s braces: that writes
        `.git/info/exclude` in the *workflow* repo, but a custom
        `after_create` hook can build the worktree in a different repo (the
        shipped monorepo template does), where the rule would not apply.
        This exclusion travels with the commit call, so it holds either way.
        """
    if cfg is None or not cfg.artifacts.enabled:
        return ()
    return (cfg.artifacts.dir,)


def _prompt_artifacts_dir(self: Orchestrator, cfg: ServiceConfig) -> str:
    """`artifacts.dir` for templates, or "" when collection is off.

        The empty string is what lets `{% if artifacts_dir %}` drop the
        instruction entirely — a disabled board must not tell workers to
        write into a directory nothing will ever read.
        """
    if not cfg.artifacts.enabled or self._artifact_store is None:
        return ""
    return cfg.artifacts.dir


async def _collect_ticket_artifacts(
    self: Orchestrator,
    cfg: ServiceConfig,
    *,
    identifier: str,
    workspace_path: Path,
    run_id: str,
    turn: int | None,
) -> None:
    """Copy new workspace deliverables into the host-owned store.

        Runs after every completed turn so a deliverable outlives the
        workspace (removed at Done) and is visible on the board while the
        run is still going. Idempotent by content hash, so re-scanning an
        unchanged directory writes nothing. Best-effort throughout: a
        failed collection must never fail the turn.
        """
    store = self._artifact_store
    if store is None:
        return
    try:
        result = await asyncio.to_thread(
            store.collect_from_workspace,
            workspace_path,
            identifier=identifier,
            magic_dir_name=cfg.artifacts.dir,
            run_id=run_id or None,
            turn=turn,
        )
    except Exception as exc:
        log.warning(
            "artifact_collect_failed", identifier=identifier, error=str(exc)
        )
        return
    for name, reason in result.skipped:
        if reason in ("duplicate", "hidden"):
            continue
        log.warning(
            "artifact_skipped", identifier=identifier, name=name, reason=reason
        )
    if not result.collected:
        return
    try:
        records = await asyncio.to_thread(store.list_for, identifier)
        await asyncio.to_thread(
            self._tracker_call_upsert_artifacts_section,
            cfg,
            identifier,
            self._artifact_section_body(cfg, identifier, records),
        )
    except Exception as exc:
        log.warning(
            "artifact_section_write_failed",
            identifier=identifier,
            error=str(exc),
        )


def _artifact_section_body(
    self: Orchestrator, cfg: ServiceConfig, identifier: str, records: list[ArtifactRecord]
) -> str:
    """Render the ticket's `## Artifacts` list.

        File-board tickets get a relative link that resolves in any local
        Markdown viewer; remote trackers (Jira, Linear) have no ticket file
        to be relative to, so they get the file name only. Today only the
        file board implements `upsert_artifacts_section`, so the remote
        rendering is written but not yet reachable — see
        `_tracker_call_upsert_artifacts_section`.
        """
    store = self._artifact_store
    if store is None:
        return ""
    is_file_board = (cfg.tracker.kind or "").strip().lower() == "file"
    board_root = cfg.tracker.board_root if is_file_board else None
    lines: list[str] = []
    for record in records:
        title = record.title or record.name
        size = format_bytes(record.byte_size)
        link: str | None = None
        if board_root is not None:
            try:
                link = os.path.relpath(
                    store.root / identifier / "files" / record.name, board_root
                )
            except (OSError, ValueError):
                link = None
        # Angle brackets keep names with spaces a valid CommonMark link.
        head = (
            f"- [{title}](<{link}>) — `{record.name}` ({size})"
            if link
            else f"- {title} — `{record.name}` ({size})"
        )
        lines.append(head)
        if record.summary:
            lines.append(f"  - {record.summary}")
    return "\n".join(lines)


def _tracker_call_upsert_artifacts_section(
    cfg: ServiceConfig, identifier: str, section_body: str
) -> None:
    client = _core().build_tracker_client(cfg)
    try:
        upsert = getattr(client, "upsert_artifacts_section", None)
        if upsert is not None:
            upsert(identifier, section_body)
    finally:
        client.close()


async def _artifact_sweep(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Drop artifact directories for tickets that left the board.

        Disabled when `artifacts.ttl_days <= 0`. Only directories whose
        ticket is no longer visible to the tracker AND untouched for the
        TTL are removed, so a live ticket never loses its deliverables.
        """
    store = self._artifact_store
    if store is None or cfg.artifacts.ttl_days <= 0:
        return
    try:
        known = await asyncio.to_thread(self._tracker_call_retained_identifiers, cfg)
    except Exception as exc:
        log.warning("artifact_sweep_fetch_failed", error=str(exc))
        return
    if not known:
        # None = tracker cannot enumerate; empty = it enumerated nothing.
        # A board directory that was renamed, unmounted, or lost to an I/O
        # error also globs to empty, and treating that as "every ticket is
        # gone" would delete artifacts nobody can regenerate. A genuinely
        # empty board has no artifacts to sweep either way.
        return
    try:
        await asyncio.to_thread(
            store.sweep,
            known_identifiers=known,
            ttl_days=cfg.artifacts.ttl_days,
        )
    except Exception as exc:
        log.warning("artifact_sweep_failed", error=str(exc))


def _tracker_call_retained_identifiers(cfg: ServiceConfig) -> set[str] | None:
    """Tickets whose artifacts the sweep must keep: present, not archived.

        Returns None when the tracker cannot enumerate the whole board —
        the sweep then does nothing rather than risk deleting artifacts for
        tickets it simply could not see.
        """
    client = _core().build_tracker_client(cfg)
    try:
        list_all = getattr(client, "list_all_identifiers", None)
        if list_all is None:
            return None
        known = {str(item) for item in list_all()}
        archive_state = (cfg.tracker.archive_state or "").strip()
        if archive_state:
            archived = client.fetch_issues_by_states([archive_state])
            known -= {issue.identifier for issue in archived}
        return known
    finally:
        client.close()


def _ensure_artifact_dir_git_excluded(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Best-effort: keep the workspace artifact magic dir out of git.

        `commit_workspace_on_done` stages the whole worktree, so without an
        ignore rule every collected deliverable would land in the feature
        branch and merge into the target branch. `.git/info/exclude` lives
        in the shared common dir — one line covers the host checkout and
        every linked worktree — and stays local, unlike editing the user's
        checked-in .gitignore.
        """
    common_dir = git_inspect.git_common_dir(cfg.workflow_path.parent)
    if common_dir is None:
        # Custom `after_create` hooks may build worktrees in a different
        # repository (the shipped monorepo template does). Say so: without
        # the ignore rule `commit_workspace_on_done` stages deliverables
        # into the feature branch and the Done merge carries them.
        log.warning(
            "artifact_dir_git_exclude_skipped",
            reason="workflow dir is not inside a git repository",
            workflow_dir=str(cfg.workflow_path.parent),
        )
        return
    exclude_path = common_dir / "info" / "exclude"
    # Leading slash anchors to each working tree's root, where the magic
    # directory actually lives. An unanchored `output/` would match at any
    # depth and silently untrack an unrelated `web/output/` for good.
    pattern = f"/{cfg.artifacts.dir}/"
    try:
        existing = (
            exclude_path.read_text(encoding="utf-8")
            if exclude_path.exists()
            else ""
        )
        if pattern in existing.splitlines():
            return
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        with exclude_path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(f"{pattern}\n")
        log.info(
            "artifact_dir_git_excluded",
            exclude=str(exclude_path),
            pattern=pattern,
        )
    except OSError as exc:
        log.warning("artifact_dir_git_exclude_failed", error=str(exc))


async def _archive_sweep(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Auto-archive terminal-state issues older than `archive_after_days`.

        Runs once per tick. Disabled when `archive_after_days <= 0`. Failures
        are logged and swallowed — one stale issue should not break the tick.
        """
    if cfg.tracker.archive_after_days <= 0:
        return
    try:
        terminal_issues = await asyncio.to_thread(
            self._tracker_call_terminal_issues, cfg
        )
    except Exception as exc:
        log.warning("archive_sweep_fetch_failed", error=str(exc))
        return
    stale = select_archivable(
        terminal_issues,
        terminal_states=cfg.tracker.terminal_states,
        archive_state=cfg.tracker.archive_state,
        archive_after_days=cfg.tracker.archive_after_days,
    )
    for issue in stale:
        try:
            await asyncio.to_thread(
                self._tracker_call_update_state,
                cfg,
                issue,
                cfg.tracker.archive_state,
            )
            log.info(
                "archive_sweep_moved",
                identifier=issue.identifier,
                target=cfg.tracker.archive_state,
            )
        except Exception as exc:
            log.warning(
                "archive_sweep_update_failed",
                identifier=issue.identifier,
                error=str(exc),
            )


# ------------------------------------------------------------------
# continuous-improvement heartbeat (plan §4)
# ------------------------------------------------------------------

async def _needs_sweep(self: Orchestrator, cfg: ServiceConfig) -> None:
    def refresh() -> None:
        states = needs_states(cfg)
        if not states:
            refresh_needs(cfg, [])
            return
        terminals = {normalize_state(state) for state in cfg.tracker.terminal_states}
        if all(normalize_state(state) in terminals for state in states):
            issues = self._tracker_call_terminal_issues(cfg)
        else:
            issues = self._invoke_shared_tracker_client(
                cfg, lambda client: client.fetch_issues_by_states(states),
            )
        refresh_needs(cfg, issues)

    try:
        await asyncio.to_thread(refresh)
    except Exception as exc:
        log.warning("needs_sweep_failed", error=str(exc))


def _maybe_schedule_needs_sweep(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Refresh `.symphony/needs.json` off the tick. Called every tick.

    Keeps at most one pending task so a slow tracker or notifier cannot pile
    up work; a board without Blocked lanes or a needs file does nothing.
    """
    needs_task = self._needs_sweep_task
    if (needs_states(cfg) or needs_path(cfg.workflow_path).exists()) and (
        needs_task is None or needs_task.done()
    ):
        self._needs_sweep_task = self._spawn_supervised(
            _needs_sweep(self, cfg), name="symphony-needs-sweep",
        )


def _maybe_schedule_continuous_improvement(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Fire at most one heartbeat run when due. Called every tick.

        Non-blocking: the actual work runs in a supervised background task,
        never a worker slot. Config is read from the live snapshot each call
        so web-side toggles take effect without a restart.
        """
    ci = cfg.continuous_improvement
    # Cache config-derived fields even while disabled so the web-API
    # status reflects the current snapshot.
    self._improvement_status.update(
        enabled=ci.enabled,
        interval_ms=ci.interval_ms,
        max_turns=ci.max_turns,
        agent_kind=ci.agent_kind,
        modes=list(ci.resolved_modes()),
    )
    if not ci.enabled:
        return
    # One run at a time: a second tick while in flight is a no-op.
    if self._improvement_task is not None and not self._improvement_task.done():
        return

    now = time.monotonic()
    interval_s = max(ci.interval_ms, 0) / 1000.0
    if self._next_improvement_due_monotonic is None:
        # First observation while enabled: arm the next-due one interval
        # out so a fresh start doesn't fire immediately.
        self._next_improvement_due_monotonic = now + interval_s
        return
    if now < self._next_improvement_due_monotonic:
        return

    # Due. Turn budget (0 == unlimited).
    if ci.max_turns and self._improvement_turns_used >= ci.max_turns:
        self._improvement_status["skipped_reason"] = "max_turns_reached"
        if not self._improvement_cap_warned:
            log.warning(
                "continuous_improvement_max_turns_reached",
                turns_used=self._improvement_turns_used,
                max_turns=ci.max_turns,
            )
            self._improvement_cap_warned = True
        return
    # Idle-board guard: postpone (don't consume the turn), re-check next
    # tick. Any live worker or retry-pending ticket counts as busy.
    if ci.require_idle_board and (
        self._running or self._retry or self._terminal_persist_pending
    ):
        self._improvement_status["skipped_reason"] = "board_busy"
        return
    # Per-mode cadence (`mode_interval_hours`): when every enabled mode is
    # still cooling down, re-arm the heartbeat without spending a turn.
    if not any_mode_due(cfg, cfg.workflow_path.parent):
        self._improvement_status["skipped_reason"] = "no_modes_due"
        self._next_improvement_due_monotonic = now + interval_s
        return

    self._improvement_status["skipped_reason"] = None
    self._last_improvement_monotonic = now
    self._next_improvement_due_monotonic = now + interval_s
    self._improvement_status.update(
        in_flight=True,
        current_phase="starting",
        last_started_at=_utc_iso_z(),
        last_result=None,
        last_error=None,
    )
    self._improvement_task = self._spawn_supervised(
        self._run_continuous_improvement(cfg),
        name="symphony-continuous-improvement",
    )
    self._improvement_task.add_done_callback(self._on_improvement_task_done)


async def _run_continuous_improvement(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Acquire the cross-process lease, delegate to the runner, record.

        A run consumes a turn when it *completes* (success or failure). A
        lease-held postpone returns early and consumes nothing. Runner
        exceptions are caught here so the tick loop is never affected.
        """
    workflow_dir = cfg.workflow_path.parent
    lease = self._improvement_lease or FileLease(lease_path_for(workflow_dir))
    if not lease.acquire():
        # Another orchestrator holds the heartbeat; postpone silently.
        interval_s = max(cfg.continuous_improvement.interval_ms, 0) / 1000.0
        self._next_improvement_due_monotonic = time.monotonic() + interval_s
        self._improvement_status.update(
            in_flight=False, current_phase=None, skipped_reason="lease_held"
        )
        return

    consumed = False
    try:
        result = await asyncio.wait_for(
            self._improvement_runner(
                cfg, workflow_dir, self._report_improvement_phase
            ),
            timeout=self._improvement_run_timeout_s,
        )
        self._improvement_status.update(
            last_result=result.status,
            skipped_reason=result.skipped_reason,
            tickets_created=result.tickets_created,
            last_verified_branch=result.verified_branch,
            last_verified_sha=result.verified_sha,
            last_mode_results=[
                {
                    "mode": outcome.mode,
                    "status": outcome.status,
                    "summary": outcome.summary,
                    "ticket_ids": list(outcome.ticket_ids),
                }
                for outcome in result.modes
            ],
            last_request_id=result.request_id,
        )
        consumed = True
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        self._improvement_status.update(
            last_result="failed",
            last_error=(
                "continuous improvement runner timed out after "
                f"{self._improvement_run_timeout_s:g}s"
            ),
        )
        log.error(
            "continuous_improvement_run_failed",
            error="runner timed out",
            error_type="TimeoutError",
        )
        consumed = True
    except Exception as exc:  # noqa: BLE001 — runner failure must not kill the loop
        self._improvement_status.update(last_result="failed", last_error=str(exc))
        log.error(
            "continuous_improvement_run_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        consumed = True
    finally:
        lease.release()
        if consumed:
            # Reset-in-flight semantics: a reset zeroes the counter mid-run;
            # this in-flight run still counts its own increment on finish.
            self._improvement_turns_used += 1
            self._improvement_status["last_finished_at"] = _utc_iso_z()
        self._improvement_status.update(in_flight=False, current_phase=None)


async def _run_improvement_agent(self: Orchestrator, task: AgentTask) -> str:
    """Give an agent-driven improvement mode one backend turn.

        This is the orchestrator-side capability the continuous-improvement
        module is handed (it must not import the orchestrator). The turn runs
        against the host repo with `cwd == workspace_root == workflow_dir`,
        exactly like a chat turn, and outside the dispatch slot accounting —
        the heartbeat already holds the idle board. The backend stays in its
        normal editing mode so the agent can write its one proposal file.

        F-12: "read only except the proposal file" used to be prompt text
        only, in the *host* tree. It is now also mechanical — the working
        tree is snapshotted before and after the turn, and a write outside
        `.symphony/continuous-improvement/proposals/` discards the mode's
        proposals (the caller records `not_proven`) and logs the paths.
        """
    cfg = self._workflow_state.current()
    if cfg is None:
        raise SymphonyError("no workflow configuration loaded")
    ci = cfg.continuous_improvement
    agent_cfg, _ = cfg_for_mode(cfg, "edit", ci.agent_kind or cfg.agent.kind)

    async def _ignore_event(_event: dict[str, Any]) -> None:
        return None

    backend = self._build_agent_backend(
        BackendInit(
            cfg=agent_cfg,
            cwd=task.cwd,
            workspace_root=task.cwd,
            on_event=_ignore_event,
        )
    )
    before = await asyncio.to_thread(_core()._worktree_status_snapshot, task.cwd)
    await backend.start()
    try:
        await backend.initialize()
        await backend.start_session(initial_prompt="", issue_title=None)
        result = await backend.run_turn(prompt=task.prompt, is_continuation=False)
    finally:
        await backend.stop()
    after = await asyncio.to_thread(_core()._worktree_status_snapshot, task.cwd)
    self._enforce_improvement_write_contract(task, before, after)
    return result.last_message or ""


def _enforce_improvement_write_contract(
    self: Orchestrator,
    task: AgentTask,
    before: dict[str, str] | None,
    after: dict[str, str] | None,
) -> None:
    """Discard a CI agent turn that wrote outside its contract."""
    if before is None or after is None:
        # Not a git worktree (or git unavailable): nothing to compare
        # against. The prompt remains the only gate, as before.
        return
    allowed = CI_AGENT_OUTPUT_PREFIX
    offending = sorted(
        path
        for path, code in after.items()
        if before.get(path) != code and not path.startswith(allowed)
    )
    if not offending:
        return
    try:
        task.output_path.unlink()
    except OSError:
        pass
    log.error(
        "ci_agent_wrote_outside_contract",
        mode=task.mode,
        cwd=str(task.cwd),
        paths=offending[:20],
        path_count=len(offending),
    )
    raise SymphonyError(
        "continuous-improvement agent wrote outside its contract; "
        f"proposals discarded: {', '.join(offending[:5])}",
        mode=task.mode,
    )


def _report_improvement_phase(self: Orchestrator, phase: str) -> None:
    self._improvement_status["current_phase"] = phase


def _on_improvement_task_done(self: Orchestrator, task: asyncio.Task[None]) -> None:
    # Identity check: only clear the ref if it still points at this task,
    # so a task launched after this one is never dropped.
    if self._improvement_task is task:
        self._improvement_task = None


def reset_continuous_improvement_turns(self: Orchestrator) -> None:
    """Web-API hook: zero the turn counter, clear a max_turns skip."""
    self._improvement_turns_used = 0
    self._improvement_cap_warned = False
    if self._improvement_status.get("skipped_reason") == "max_turns_reached":
        self._improvement_status["skipped_reason"] = None


def continuous_improvement_status(self: Orchestrator) -> dict[str, Any]:
    """Web-API status snapshot. Merges cached config fields, the live
        turn counter, in-flight flag, and the next-due wall-clock estimate.
        """
    status = dict(self._improvement_status)
    cfg = self.workflow_state.current()
    if cfg is not None:
        ci = cfg.continuous_improvement
        status.update(
            enabled=ci.enabled,
            interval_ms=ci.interval_ms,
            max_turns=ci.max_turns,
            agent_kind=ci.agent_kind,
            modes=list(ci.resolved_modes()),
        )
    status["turns_used"] = self._improvement_turns_used
    status["in_flight"] = (
        self._improvement_task is not None and not self._improvement_task.done()
    )
    status["next_due_at"] = self._improvement_next_due_iso()
    return status


def _improvement_next_due_iso(self: Orchestrator) -> str | None:
    due = self._next_improvement_due_monotonic
    if due is None:
        return None
    delta = max(due - time.monotonic(), 0.0)
    return _to_iso(datetime.now(timezone.utc) + timedelta(seconds=delta))
