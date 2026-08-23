"""SPEC §9 — workspace manager and lifecycle hooks."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._shell import resolve_bash
from .errors import InvalidWorkspaceCwd, SymphonyError, WorkspaceBoardUnreachable
from .issue import workspace_key
from .logging import get_logger
from .utils.git_sandbox import (
    REMOTE_REJECTED,
    SANDBOX_WRITE_DENIED,
    UNKNOWN_FAILURE,
    classify_history_failure,
)
from .workflow import HooksConfig
from .workflow.constants import SYMPHONY_BRANCH_PREFIX

log = get_logger()

_OWNER_MARKER_DIR = ".symphony-workspace-owners"
_HOOK_OUTPUT_DIR = ".symphony-workspace-hook-output"
_OWNER_MARKER_VERSION = 1
_OWNER_IDENTITY_KEYS = ("workflow_dir", "board_root", "repo_root")
_UNSET: object = object()
_SETUP_FAILURE_STRINGS = (
    "PrismaConfigEnvError",
    "Cannot resolve environment variable",
    "Traceback",
    "ModuleNotFoundError",
)
_WORKTREE_REFRESH_GIT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class _LinkedWorktreeRefreshPlan:
    """The exact immutable state captured before a preserve fast-forward."""

    branch: str
    head_sha: str
    target_sha: str


def _try_rmtree_once(path: Path) -> tuple[bool, str | None, bool]:
    """Single rmtree attempt.

    Returns ``(success, last_error, retryable)``. ``retryable`` is True only
    for ``PermissionError`` on Windows — every other failure must propagate
    immediately so POSIX permission errors aren't masked.
    """
    try:
        shutil.rmtree(path)
        return True, None, False
    except FileNotFoundError:
        return True, None, False
    except PermissionError as exc:
        return False, str(exc), sys.platform == "win32"
    except OSError as exc:
        return False, str(exc), False


async def _force_rmtree(path: Path, *, attempts: int = 5) -> tuple[bool, str | None]:
    """Best-effort recursive delete with brief retry on Windows.

    Windows can hold a directory's handle open for tens of milliseconds after
    a child subprocess exits (the subprocess used the directory as its cwd),
    causing ``shutil.rmtree`` to fail with ``PermissionError`` even though the
    process is gone. The backoff uses ``await asyncio.sleep`` so concurrent
    workspace cleanups don't stall the event loop.
    """
    last_err: str | None = None
    for i in range(attempts):
        ok, err, retryable = _try_rmtree_once(path)
        if ok:
            return True, None
        last_err = err
        if not retryable or i == attempts - 1:
            return False, last_err
        await asyncio.sleep(0.05 * (i + 1))
    return False, last_err


def _git_repo_root(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    root = result.stdout.strip()
    if result.returncode != 0 or not root:
        return None
    return str(Path(root).resolve())


def _git_query(path: Path, *args: str) -> str | None:
    """Run one bounded, read-only Git query for workspace reuse safety."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=_WORKTREE_REFRESH_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _resolve_git_path(cwd: Path, value: str) -> Path:
    path = Path(value.strip())
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _local_branch_ref(workflow_dir: Path, branch: str) -> str | None:
    """Return the exact local ref for a configured branch name.

    Preserve refresh intentionally does not accept Git revision expressions,
    tags, remote-tracking refs, or arbitrary object IDs. The workflow config
    field is a branch *name*, and the host-side ref must therefore be exactly
    ``refs/heads/<name>``.
    """
    candidate = branch.strip()
    if not candidate or candidate != branch or candidate.startswith("refs/"):
        return None
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(workflow_dir),
                "check-ref-format",
                "--branch",
                candidate,
            ],
            capture_output=True,
            text=True,
            timeout=_WORKTREE_REFRESH_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or result.stdout.strip() != candidate:
        return None
    return f"refs/heads/{candidate}"


def _linked_worktree_refresh_plan(
    *,
    path: Path,
    workflow_dir: Path | None,
    branch: str,
    merge_target: str,
) -> _LinkedWorktreeRefreshPlan | None:
    """Capture a safe preserve fast-forward plan, or return ``None``.

    This deliberately proves the complete managed-worktree topology before
    allowing a preserve reuse to touch Git. A custom repository or a linked
    worktree belonging to another host is never refreshed here. The caller
    must still re-capture and compare this plan immediately before running
    the fast-forward, because branch and target refs can move concurrently.
    """
    if workflow_dir is None or not (path / ".git").is_file():
        return None
    target_ref = _local_branch_ref(workflow_dir, merge_target)
    if target_ref is None:
        return None

    workspace_root = _git_query(path, "rev-parse", "--show-toplevel")
    if not workspace_root:
        return None
    try:
        if Path(workspace_root.strip()).resolve() != path.resolve():
            return None
    except OSError:
        return None

    workspace_common = _git_query(path, "rev-parse", "--git-common-dir")
    host_common = _git_query(
        workflow_dir, "rev-parse", "--git-common-dir"
    )
    if not workspace_common or not host_common:
        return None
    try:
        if _resolve_git_path(path, workspace_common) != _resolve_git_path(
            workflow_dir, host_common
        ):
            return None
    except OSError:
        return None

    current_branch = _git_query(
        path, "symbolic-ref", "--quiet", "--short", "HEAD"
    )
    if not current_branch or current_branch.strip() != branch:
        return None

    # The canonical setup hook force-removes and re-adds the worktree. Do not
    # let that discard uncommitted or untracked (non-ignored) work.
    status = _git_query(
        path, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if status is None or status.strip():
        return None

    branch_sha = _git_query(
        path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"
    )
    host_branch_sha = _git_query(
        workflow_dir,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}^{{commit}}",
    )
    target_sha = _git_query(
        workflow_dir,
        "rev-parse",
        "--verify",
        "--quiet",
        f"{target_ref}^{{commit}}",
    )
    if not branch_sha or not host_branch_sha or not target_sha:
        return None
    branch_sha = branch_sha.strip()
    host_branch_sha = host_branch_sha.strip()
    target_sha = target_sha.strip()
    if branch_sha != host_branch_sha:
        return None
    if branch_sha == target_sha:
        return None

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(workflow_dir),
                "merge-base",
                "--is-ancestor",
                branch_sha.strip(),
                target_sha.strip(),
            ],
            capture_output=True,
            timeout=_WORKTREE_REFRESH_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _LinkedWorktreeRefreshPlan(
        branch=branch,
        head_sha=branch_sha,
        target_sha=target_sha,
    )


def _git_command(
    path: Path, *args: str
) -> subprocess.CompletedProcess[bytes] | None:
    """Run one bounded Git mutation/query for preserve reuse."""
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=_WORKTREE_REFRESH_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _refresh_linked_worktree_in_place(
    *,
    path: Path,
    workflow_dir: Path,
    branch: str,
    merge_target: str,
    base_branch: str,
) -> bool:
    """Fast-forward one clean managed worktree without executing hooks.

    The target SHA is captured from the host and passed as an immutable
    object ID to ``git merge --ff-only``. Re-capturing the plan immediately
    before the mutation fences earlier branch/target races. The target may
    advance after capture; the worktree deliberately remains at the captured
    ancestor and the normal delivery gate handles that later drift. A failed
    A pre-mutation eligibility check or capture race is a no-op from the
    caller's point of view: preserve reuse never falls back to an arbitrary
    ``after_create``. Once the guarded merge is attempted, any failure blocks
    dispatch with ``SymphonyError``.
    """
    plan = _linked_worktree_refresh_plan(
        path=path,
        workflow_dir=workflow_dir,
        branch=branch,
        merge_target=merge_target,
    )
    if plan is None:
        return False

    # The first capture can become stale while the event loop is yielding to
    # the thread. Re-capture and require byte-for-byte equality before Git is
    # allowed to update the checked-out branch.
    confirmed = _linked_worktree_refresh_plan(
        path=path,
        workflow_dir=workflow_dir,
        branch=branch,
        merge_target=merge_target,
    )
    if confirmed != plan:
        return False

    result = _git_command(path, "merge", "--ff-only", plan.target_sha)
    if result is None:
        raise SymphonyError(
            "preserve worktree refresh could not complete the guarded fast-forward",
            path=str(path),
            branch=plan.branch,
            target=plan.target_sha,
        )
    if result.returncode != 0:
        raise SymphonyError(
            "preserve worktree refresh failed during the guarded fast-forward",
            path=str(path),
            branch=plan.branch,
            target=plan.target_sha,
            returncode=result.returncode,
        )

    head_after = _git_query(
        path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"
    )
    branch_after = _git_query(
        workflow_dir,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}^{{commit}}",
    )
    if (
        head_after is None
        or branch_after is None
        or head_after.strip() != plan.target_sha
        or branch_after.strip() != plan.target_sha
    ):
        raise SymphonyError(
            "preserve worktree refresh postcondition failed; refusing dispatch",
            path=str(path),
            branch=plan.branch,
            expected=plan.target_sha,
            head=head_after.strip() if head_after else "",
            branch_ref=branch_after.strip() if branch_after else "",
        )

    # The setup hook normally enables worktree config. Keep the migration
    # tolerant of older worktrees; the only branch mutation above was the
    # guarded fast-forward to the captured target SHA.
    config_result = _git_command(path, "config", "extensions.worktreeConfig", "true")
    if config_result is None or config_result.returncode != 0:
        raise SymphonyError(
            "preserve worktree refresh could not enable worktree-local config",
            path=str(path),
            branch=plan.branch,
        )
    config_result = _git_command(
        path, "config", "--worktree", "symphony.basesha", plan.target_sha
    )
    if config_result is None or config_result.returncode != 0:
        raise SymphonyError(
            "preserve worktree refresh could not update symphony.basesha",
            path=str(path),
            branch=plan.branch,
            target=plan.target_sha,
        )
    config_result = _git_command(
        path, "config", "--worktree", "symphony.mergetargetbranch", merge_target
    )
    if config_result is None or config_result.returncode != 0:
        raise SymphonyError(
            "preserve worktree refresh could not update symphony.mergetargetbranch",
            path=str(path),
            branch=plan.branch,
            target=merge_target,
        )
    config_result = _git_command(
        path, "config", "--worktree", "symphony.basebranch", base_branch
    )
    if config_result is None or config_result.returncode != 0:
        raise SymphonyError(
            "preserve worktree refresh could not update symphony.basebranch",
            path=str(path),
            branch=plan.branch,
            base=base_branch,
        )
    basesha = _git_query(path, "config", "--worktree", "--get", "symphony.basesha")
    if basesha is None or basesha.strip() != plan.target_sha:
        raise SymphonyError(
            "preserve worktree refresh did not persist symphony.basesha",
            path=str(path),
            branch=plan.branch,
            expected=plan.target_sha,
            actual=basesha.strip() if basesha else "",
        )
    final_head = _git_query(
        path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"
    )
    final_branch = _git_query(
        workflow_dir,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}^{{commit}}",
    )
    if (
        final_head is None
        or final_branch is None
        or final_head.strip() != plan.target_sha
        or final_branch.strip() != plan.target_sha
    ):
        raise SymphonyError(
            "preserve worktree refresh raced after config update; refusing dispatch",
            path=str(path),
            branch=plan.branch,
            expected=plan.target_sha,
            head=final_head.strip() if final_head else "",
            branch_ref=final_branch.strip() if final_branch else "",
        )
    return True


@dataclass(frozen=True)
class Workspace:
    path: Path
    workspace_key: str
    created_now: bool


class WorkspaceManager:
    """§9.1, §9.2 — sanitized per-issue workspace directories."""

    def __init__(
        self,
        root: Path,
        hooks: HooksConfig,
        *,
        workflow_dir: Path | None = None,
        board_root: Path | None = None,
        reuse_policy: str = "preserve",
        hook_env: dict[str, str] | None = None,
    ) -> None:
        self._root = root.resolve()
        self._hooks = hooks
        self._workflow_dir = workflow_dir
        self._board_root = board_root
        self._reuse_policy = reuse_policy
        self._hook_env = dict(hook_env or {})
        self._owner_identity = self._build_owner_identity()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def update_hooks(
        self,
        hooks: HooksConfig,
        *,
        workflow_dir: Path | None = None,
        board_root: Path | None | object = _UNSET,
    ) -> None:
        # §6.2 — apply reloaded hooks to future executions.
        self._hooks = hooks
        identity_changed = False
        if workflow_dir is not None:
            self._workflow_dir = workflow_dir
            identity_changed = True
        if board_root is not _UNSET:
            self._board_root = board_root if isinstance(board_root, Path) else None
            identity_changed = True
        if identity_changed:
            self._owner_identity = self._build_owner_identity()

    def update_reuse_policy(self, reuse_policy: str) -> None:
        self._reuse_policy = reuse_policy

    def update_hook_env(self, hook_env: dict[str, str] | None) -> None:
        self._hook_env = dict(hook_env or {})

    def path_for(self, identifier: str) -> Path:
        key = workspace_key(identifier)
        return (self._root / key).resolve()

    async def create_or_reuse(self, identifier: str) -> Workspace:
        key = workspace_key(identifier)
        path = (self._root / key).resolve()
        self._enforce_root_containment(path)

        if path.exists() and not path.is_dir():
            raise SymphonyError(
                "workspace path occupied by non-directory", path=str(path)
            )

        created_now = not path.exists()
        if not created_now:
            self._enforce_workspace_owner(key, path)
        path.mkdir(parents=True, exist_ok=True)

        should_run_after_create = created_now or self._reuse_policy == "refresh"
        if (
            not created_now
            and self._reuse_policy == "preserve"
            and self._hooks.after_create
            and self._workflow_dir is not None
        ):
            # Preserve never re-runs an arbitrary hook. For the exact linked
            # worktree topology shipped by Symphony, it can safely repair a
            # clean branch that has already been merged into the target in
            # place. Every other reuse shape remains untouched.
            merge_target = (
                self._hook_env.get("SYMPHONY_MERGE_TARGET_BRANCH", "").strip()
                or self._hook_env.get("SYMPHONY_FEATURE_BASE_BRANCH", "").strip()
            )
            if not merge_target:
                merge_target = (
                    await asyncio.to_thread(
                        _git_query,
                        self._workflow_dir,
                        "symbolic-ref",
                        "--quiet",
                        "--short",
                        "HEAD",
                    )
                    or ""
                ).strip()
            base_branch = self._hook_env.get(
                "SYMPHONY_FEATURE_BASE_BRANCH", ""
            ).strip()
            if not base_branch and self._workflow_dir is not None:
                base_branch = (
                    await asyncio.to_thread(
                        _git_query,
                        self._workflow_dir,
                        "symbolic-ref",
                        "--quiet",
                        "--short",
                        "HEAD",
                    )
                    or ""
                ).strip()
            if merge_target:
                refreshed = await asyncio.to_thread(
                    _refresh_linked_worktree_in_place,
                    path=path,
                    workflow_dir=self._workflow_dir,
                    branch=f"{SYMPHONY_BRANCH_PREFIX}{key}",
                    merge_target=merge_target,
                    base_branch=base_branch,
                )
                if refreshed:
                    log.info(
                        "workspace_reuse_fast_forward",
                        path=str(path),
                        branch=f"{SYMPHONY_BRANCH_PREFIX}{key}",
                        target=merge_target,
                    )
        if should_run_after_create and self._hooks.after_create:
            try:
                await self._run_hook("after_create", self._hooks.after_create, path)
            except Exception:
                if created_now:
                    # §9.4 — after_create failure is fatal; clean partial directory.
                    ok, err = await _force_rmtree(path)
                    if not ok:
                        log.warning(
                            "workspace_cleanup_incomplete", path=str(path), error=err
                        )
                raise

        self._enforce_board_reachable(path)
        self._write_workspace_owner_marker(key)
        return Workspace(path=path, workspace_key=key, created_now=created_now)

    def _board_link_name(self) -> str | None:
        """Board root relative to the workflow dir, or None when outside it."""
        if self._board_root is None or self._workflow_dir is None:
            return None
        try:
            relative = self._board_root.resolve().relative_to(
                self._workflow_dir.resolve()
            )
        except (ValueError, OSError):
            return None
        text = relative.as_posix()
        return text or None

    def _enforce_board_reachable(self, path: Path) -> None:
        """Fail the dispatch when the workspace board is not the host board.

        A workspace whose `<board>` entry is a real directory (link fallback
        on Windows, stale copy, wrong `board_root`) swallows every ticket
        write the agent makes: the orchestrator keeps reading the host board,
        never sees the transition, and re-dispatches forever. Catching it here
        costs one `resolve()` and turns a silent money-burning loop into a
        named error before the first turn.
        """
        name = self._board_link_name()
        if name is None or self._board_root is None:
            return
        linked = path / name
        if not linked.exists():
            if self._hooks.after_create:
                log.warning(
                    "workspace_board_link_missing",
                    workspace=str(linked),
                    host_board=str(self._board_root),
                )
            return
        try:
            same = linked.resolve() == self._board_root.resolve()
        except OSError:  # pragma: no cover - resolve on a broken link
            same = False
        if not same:
            raise WorkspaceBoardUnreachable(
                "workspace board is not the host board",
                workspace=str(linked),
                host_board=str(self._board_root),
            )

    async def before_run(self, path: Path) -> None:
        if self._hooks.before_run:
            await self._run_hook("before_run", self._hooks.before_run, path)

    async def after_run_best_effort(self, path: Path) -> None:
        if not self._hooks.after_run:
            return
        # If the agent (or an external process) removed the workspace before we
        # got here, skip the hook — spawning bash with a missing cwd raises an
        # opaque FileNotFoundError that callers cannot act on. Logging at
        # INFO keeps the trail without the false-alarm warning.
        if not path.exists():
            log.info("hook_after_run_skipped_missing_cwd", path=str(path))
            return
        try:
            await self._run_hook("after_run", self._hooks.after_run, path)
        except Exception as exc:  # §9.4 — log and ignore.
            log.warning("hook_after_run_failed", path=str(path), error=str(exc))

    async def after_done_best_effort(
        self, path: Path, *, identifier: str, title: str
    ) -> bool:
        """Fire `hooks.after_done` once when a ticket reached `Done`.

        Called by the orchestrator after `commit_workspace_on_done` and
        before `remove`. Lenient by default — failures log a warning and
        return False so the caller can apply a policy (warn-and-continue
        vs preserve-and-block). Returns True when the hook ran cleanly
        or was a no-op (no hook configured, missing path).
        """
        if not self._hooks.after_done:
            return True
        if not path.exists():
            log.info("hook_after_done_skipped_missing_cwd", path=str(path))
            return True
        try:
            await self._run_hook(
                "after_done",
                self._hooks.after_done,
                path,
                extra_env={
                    "SYMPHONY_ISSUE_ID": identifier,
                    "SYMPHONY_ISSUE_TITLE": title or "",
                },
            )
            return True
        except Exception as exc:
            log.warning("hook_after_done_failed", path=str(path), error=str(exc))
            return False

    async def remove(self, path: Path) -> None:
        path = path.resolve()
        try:
            self._enforce_root_containment(path)
        except InvalidWorkspaceCwd as exc:
            log.error("refused_remove_outside_root", path=str(path), error=str(exc))
            return
        if not path.exists():
            return
        if self._hooks.before_remove:
            try:
                await self._run_hook("before_remove", self._hooks.before_remove, path)
            except Exception as exc:  # §9.4 — log and ignore.
                log.warning("hook_before_remove_failed", path=str(path), error=str(exc))
        ok, err = await _force_rmtree(path)
        if not ok:
            log.warning("workspace_remove_failed", path=str(path), error=err)

    def _enforce_root_containment(self, path: Path) -> None:
        """§9.5 invariant 2."""
        try:
            path.resolve().relative_to(self._root)
        except ValueError as exc:
            raise InvalidWorkspaceCwd(
                "workspace path escapes workspace root",
                path=str(path),
                root=str(self._root),
            ) from exc

    def _build_owner_identity(self) -> dict[str, str]:
        identity: dict[str, str] = {}
        if self._workflow_dir is None and self._board_root is None:
            return identity
        if self._workflow_dir is not None:
            workflow_dir = self._workflow_dir.resolve()
            identity["workflow_dir"] = str(workflow_dir)
            repo_root = _git_repo_root(workflow_dir)
            if repo_root:
                identity["repo_root"] = repo_root
        if self._board_root is not None:
            identity["board_root"] = str(self._board_root.resolve())
        return identity

    def _owner_marker_path(self, key: str) -> Path:
        return self._root / _OWNER_MARKER_DIR / f"{key}.json"

    def _read_workspace_owner_marker(self, key: str) -> dict[str, object] | None:
        marker = self._owner_marker_path(key)
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _enforce_workspace_owner(self, key: str, path: Path) -> None:
        if not self._owner_identity:
            return
        marker = self._read_workspace_owner_marker(key)
        if marker is None:
            return
        recorded = marker.get("identity")
        if not isinstance(recorded, dict):
            return
        for field in _OWNER_IDENTITY_KEYS:
            current_value = self._owner_identity.get(field)
            recorded_value = recorded.get(field)
            if not current_value or not recorded_value or current_value == recorded_value:
                continue
            raise SymphonyError(
                "workspace owner mismatch",
                path=str(path),
                field=field,
                current=current_value,
                recorded=recorded_value,
            )

    def _write_workspace_owner_marker(self, key: str) -> None:
        if not self._owner_identity:
            return
        marker = self._owner_marker_path(key)
        payload = {
            "version": _OWNER_MARKER_VERSION,
            "workspace_key": key,
            "identity": self._owner_identity,
        }
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            tmp = marker.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(marker)
        except OSError as exc:
            raise SymphonyError(
                "workspace owner marker write failed",
                path=str(marker),
                error=str(exc),
            ) from exc

    def _metadata_key_for_cwd(self, cwd: Path) -> str:
        try:
            return cwd.resolve().relative_to(self._root).parts[0]
        except (ValueError, IndexError):
            return cwd.name or "unknown"

    def _write_hook_output_artifacts(
        self,
        *,
        name: str,
        cwd: Path,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> Path | None:
        key = self._metadata_key_for_cwd(cwd)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        base = self._root / _HOOK_OUTPUT_DIR / key / f"{stamp}-{name}"
        combined = stdout.decode("utf-8", errors="replace") + stderr.decode(
            "utf-8", errors="replace"
        )
        warnings = [token for token in _SETUP_FAILURE_STRINGS if token in combined]
        payload = {
            "hook": name,
            "cwd": str(cwd),
            "returncode": returncode,
            "stdout": str(base.with_suffix(".stdout")),
            "stderr": str(base.with_suffix(".stderr")),
            "warning_patterns": warnings,
        }
        try:
            base.parent.mkdir(parents=True, exist_ok=True)
            base.with_suffix(".stdout").write_bytes(stdout)
            base.with_suffix(".stderr").write_bytes(stderr)
            meta_path = base.with_suffix(".json")
            meta_path.write_text(
                json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            log.warning(
                "hook_output_artifact_failed",
                hook=name,
                cwd=str(cwd),
                error=str(exc),
            )
            return None
        if warnings:
            log.warning(
                "hook_output_warning_patterns",
                hook=name,
                cwd=str(cwd),
                artifact=str(meta_path),
                patterns=warnings,
            )
        return meta_path

    async def _run_hook(
        self,
        name: str,
        script: str,
        cwd: Path,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        timeout_s = max(self._hooks.timeout_ms, 0) / 1000.0
        log.info("hook_start", hook=name, cwd=str(cwd))
        # §9.4 — run script via `bash -lc` with workspace cwd.
        #
        # We deliberately route through a worker thread + blocking
        # `subprocess.run` instead of `asyncio.create_subprocess_exec`. The
        # asyncio child-watcher is fragile under Textual on macOS (Python
        # 3.12): subprocesses spawn fine, exit fine, but `await proc.wait()`
        # never resolves because the watcher never observes the SIGCHLD
        # / waitpid event. The symptom is a zombie `<defunct>` child and a
        # worker stuck forever inside the timeout-cleanup `await
        # process.wait()`. Using `subprocess.run` in a thread bypasses the
        # watcher entirely — `os.waitpid` runs in the worker thread and
        # returns deterministically.
        env = {
            **os.environ,
            "SYMPHONY_WORKFLOW_DIR": str(self._workflow_dir)
            if self._workflow_dir
            else "",
        }
        # Host-computed hook values (board location, branch policy, etc.) are
        # part of the lifecycle contract, not just workspace creation.  Every
        # hook receives the same base values; lifecycle-specific values passed
        # by the caller still win below.
        if self._hook_env:
            env.update(self._hook_env)
        if extra_env:
            env.update(extra_env)

        def _do_run() -> subprocess.CompletedProcess[bytes]:
            # `stdin=DEVNULL` is mandatory, not cosmetic. When Symphony is
            # launched in the background (e.g. `nohup ... &` or systemd
            # without a TTY), the orchestrator process inherits a closed or
            # half-broken fd 0. Without an explicit redirect here, the hook
            # script — and any grandchild it spawns (e.g.
            # `python -m venv .venv` inside `after_create`) — inherits the
            # same broken fd. CPython then aborts at startup with
            #   Fatal Python error: init_sys_streams: can't initialize sys
            #   standard streams / OSError: [Errno 9] Bad file descriptor
            # and the hook fails with returncode 1, surfacing as a
            # confusing `hook after_create exited 1`. Pinning stdin to
            # /dev/null guarantees a usable fd 0 for the hook regardless
            # of how the parent was started.
            return subprocess.run(
                [resolve_bash(), "-lc", script],
                cwd=str(cwd),
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout_s if timeout_s > 0 else None,
                env=env,
                check=False,
            )

        try:
            result = await asyncio.to_thread(_do_run)
        except subprocess.TimeoutExpired as exc:
            stdout = _coerce_output_bytes(exc.stdout)
            stdout = _strip_shell_exit_trailer(stdout)
            stderr = _coerce_output_bytes(exc.stderr)
            artifact = self._write_hook_output_artifacts(
                name=name,
                cwd=cwd,
                returncode=-1,
                stdout=stdout,
                stderr=stderr,
            )
            log.error(
                "hook_timeout",
                hook=name,
                cwd=str(cwd),
                artifact=str(artifact) if artifact is not None else "",
            )
            message = f"hook {name} timed out"
            if artifact is not None:
                message = f"{message}; full output: {artifact}"
            raise SymphonyError(message, hook=name) from exc

        rc = result.returncode or 0
        stderr_bytes = result.stderr or b""
        stdout_bytes = _strip_shell_exit_trailer(result.stdout or b"")
        artifact = self._write_hook_output_artifacts(
            name=name,
            cwd=cwd,
            returncode=rc,
            stdout=stdout_bytes,
            stderr=stderr_bytes,
        )
        stderr_text = _truncate(stderr_bytes.decode("utf-8", errors="replace")).strip()
        stdout_text = _truncate(stdout_bytes.decode("utf-8", errors="replace")).strip()
        if rc != 0:
            log.error(
                "hook_failed",
                hook=name,
                cwd=str(cwd),
                returncode=rc,
                stderr=stderr_text,
                artifact=str(artifact) if artifact is not None else "",
            )
            message = f"hook {name} exited {rc}"
            if stderr_text:
                message = f"{message}; stderr: {stderr_text}"
            elif stdout_text:
                message = f"{message}; stdout: {stdout_text}"
            if artifact is not None:
                message = f"{message}; full output: {artifact}"
            raise SymphonyError(message, hook=name, returncode=rc)
        log.info(
            "hook_completed",
            hook=name,
            cwd=str(cwd),
            stdout=stdout_text,
            artifact=str(artifact) if artifact is not None else "",
        )


def _truncate(value: str, limit: int = 400) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...(truncated)"


# Git for Windows bash appends this terminal-init clear sequence (cursor
# home + erase screen + erase scrollback) to a login shell's stdout when
# the script exits via the explicit `exit` builtin — even with
# --noprofile/--norc, so it is the binary itself, not any profile. POSIX
# bash never emits it. It is shell chrome, not hook output, so exactly
# this trailer is stripped from the end of captured stdout.
_SHELL_EXIT_TRAILER = b"\x1b[H\x1b[2J\x1b[3J"


def _strip_shell_exit_trailer(stdout: bytes) -> bytes:
    if stdout.endswith(_SHELL_EXIT_TRAILER):
        return stdout[: -len(_SHELL_EXIT_TRAILER)]
    return stdout


def _coerce_output_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


COMMIT_OK = "committed"
COMMIT_NOOP = "nothing_to_commit"
COMMIT_FAILED = "commit_failed"


@dataclass(frozen=True)
class CommitOutcome:
    """What the host-side workspace snapshot actually achieved.

    ``failure_kind`` is only meaningful for :data:`COMMIT_FAILED`; it lets the
    orchestrator tell a sandbox/permission problem it can retry apart from one
    that needs a human (see ``utils.git_sandbox.classify_history_failure``).
    """

    status: str
    detail: str = ""
    failure_kind: str = UNKNOWN_FAILURE

    @property
    def durable(self) -> bool:
        """Whether the workspace's work is safely in git history."""
        return self.status in (COMMIT_OK, COMMIT_NOOP)


async def commit_workspace_on_done(
    path: Path,
    *,
    identifier: str,
    title: str,
    exit_reason: str | None = None,
    state: str | None = None,
    timeout_s: float = 60.0,
    extra_excludes: tuple[str, ...] = (),
) -> CommitOutcome:
    """Snapshot the per-ticket workspace into one git commit on worker exit.

    Always called before `WorkspaceManager.remove()` — the goal is that no
    work the agent left in the worktree gets discarded by `git worktree
    remove --force`. Fires for every exit (Done, Cancelled, Blocked,
    error, timeout, reconcile-terminated) when `auto_commit_on_done` is
    on; the commit message includes the exit reason / state for non-Done
    cases so a quick `git log` makes the situation obvious.

    Lenient by design — every failure (missing path, no diffs, pre-commit
    rejection, signing error, timeout) logs a warning and returns a
    :class:`CommitOutcome` instead of raising. We never raise out of the
    worker exit path; a failed auto-commit is a housekeeping miss surfaced
    by the warning, not a regression that blocks the queue. Callers that
    gate a state transition on durable history inspect the returned
    outcome; callers that only want the snapshot can ignore it.

    Reuses any enclosing git repo (`git -C path rev-parse --git-dir`).
    Only initialises a new repo when the workspace has no git ancestor,
    so workspaces nested inside an existing project repo just add a
    commit to that project's history rather than creating a nested
    `.git`. With the worktree-default hooks the commit lands on the
    `symphony/<ID>` branch the worktree is checked out on.
    """
    if not path.exists():
        log.info("auto_commit_skipped_missing_workspace", path=str(path))
        return CommitOutcome(COMMIT_FAILED, detail=f"workspace missing: {path}")

    safe_title = (title or "").replace("\n", " ").strip()[:200] or "(no title)"
    normalized_state = (state or "").strip().lower()
    suffix = ""
    if normalized_state == "done":
        # Work reached Done — message stays clean even when the cleanup
        # path (reconcile / startup) supplied an exit_reason.
        suffix = ""
    elif normalized_state:
        suffix = f" [state: {state}]"
    elif exit_reason and exit_reason != "normal":
        suffix = f" [exit: {exit_reason}]"
    msg = f"{identifier}: {safe_title}{suffix}"

    # One-commit-per-ticket: if the worktree's `after_create` recorded a
    # fork point in `git config symphony.basesha`, soft-reset to that base
    # so all per-turn commits + still-uncommitted changes collapse into a
    # single commit with the ticket subject. When no base is recorded
    # (legacy workspaces, non-worktree setups), fall back to a plain
    # commit-on-top — preserves correctness without forcing operators to
    # re-bootstrap. If the Verify stage already merged the branch into the
    # recorded `symphony.mergetargetbranch` (a `--no-ff` merge), the base
    # advances to the merge base with that target so the squash lands on
    # the merged tip instead of resetting past it — otherwise the branch
    # is rewritten onto an orphan lineage and the post-Done fallback merge
    # computes its merge base at the stale fork point, guaranteeing
    # add/add conflicts on anything both sides touched after the merge.
    # `git add -A .` (note the explicit pathspec) scopes the snapshot to the
    # workspace path. Without the `.`, `git add -A` walks the entire
    # enclosing repo and would sweep in unrelated host-side changes when the
    # workspace is a subdir of an existing project (the file-tracker smoke
    # configuration is the canonical example). Stays equivalent to `-A`
    # alone in the worktree case where cwd is the worktree root.
    script = (
        'set -u\n'
        'if ! git rev-parse --git-dir >/dev/null 2>&1; then\n'
        '  git init -q || exit 41\n'
        'fi\n'
        'BASE="$(git config --get symphony.basesha 2>/dev/null || true)"\n'
        'TARGET="$(git config --get symphony.mergetargetbranch 2>/dev/null || true)"\n'
        '# Already-merged branch: advance BASE to the merge base with the\n'
        '# recorded target so the squash below preserves the merged lineage\n'
        '# instead of resetting onto the pre-merge fork point (see comment\n'
        '# above this script). Never-merged branches leave BASE untouched.\n'
        'if [ -n "$BASE" ] && [ -n "$TARGET" ] && git rev-parse --verify --quiet "${TARGET}^{commit}" >/dev/null 2>&1; then\n'
        '  MB="$(git merge-base HEAD "$TARGET" 2>/dev/null || true)"\n'
        '  if [ -n "$MB" ] && [ "$MB" != "$BASE" ] && git merge-base --is-ancestor "$BASE" "$MB" 2>/dev/null; then\n'
        '    BASE="$MB"\n'
        '  fi\n'
        'fi\n'
        'ADD_PATHS=(.)\n'
        # Caller-supplied exclusions (the ticket artifact directory) are
        # applied in *this* worktree's repo, whichever that is. The
        # `.git/info/exclude` rule written at startup only covers the
        # workflow repo, and a custom `after_create` hook may build the
        # worktree in a different one — the shipped monorepo template does.
        + "".join(
            "ADD_PATHS+=(%s)\n" % shlex.quote(f":(exclude){item}")
            for item in extra_excludes
            if item
        )
        + 'while IFS= read -r exclude_path; do\n'
        '  [ -n "$exclude_path" ] && ADD_PATHS+=(":(exclude)$exclude_path")\n'
        'done < <(git config --get-all symphony.autocommitExclude 2>/dev/null || true)\n'
        'git add -A -- "${ADD_PATHS[@]}" || exit 42\n'
        'HAS_STAGED=1\n'
        'git diff --cached --quiet -- . && HAS_STAGED=0\n'
        'HAS_NEW_COMMITS=0\n'
        'if [ -n "$BASE" ] && git rev-parse --verify "$BASE" >/dev/null 2>&1; then\n'
        '  HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || echo "")"\n'
        '  if [ -n "$HEAD_SHA" ] && [ "$HEAD_SHA" != "$BASE" ]; then\n'
        '    HAS_NEW_COMMITS=1\n'
        '  fi\n'
        'fi\n'
        'if [ "$HAS_STAGED" -eq 0 ] && [ "$HAS_NEW_COMMITS" -eq 0 ]; then\n'
        '  echo "auto_commit: nothing to commit"\n'
        '  exit 0\n'
        'fi\n'
        'if [ "$HAS_NEW_COMMITS" -eq 1 ]; then\n'
        '  # Collapse every commit since the recorded fork point + any\n'
        '  # currently-staged changes into one. --soft preserves the index\n'
        '  # and working tree so the final `git commit` captures everything.\n'
        '  git reset --soft "$BASE" || exit 44\n'
        'fi\n'
        'DELETE_COUNT="$(git diff --cached --name-only --diff-filter=D -- . | wc -l | tr -d "[:space:]")"\n'
        'PROTECTED_DELETE="$(git diff --cached --name-only --diff-filter=D -- '
        'pyproject.toml WORKFLOW.md WORKFLOW.example.md WORKFLOW.file.example.md '
        '2>/dev/null | sed -n "1p")"\n'
        'if [ -n "$PROTECTED_DELETE" ]; then\n'
        '  echo "auto_commit: refusing protected deletion: $PROTECTED_DELETE"\n'
        '  exit 45\n'
        'fi\n'
        'if [ "${DELETE_COUNT:-0}" -gt 25 ]; then\n'
        '  echo "auto_commit: refusing destructive snapshot with $DELETE_COUNT deleted files"\n'
        '  exit 45\n'
        'fi\n'
        'git commit -m "$SYMPHONY_AUTO_COMMIT_MSG" || exit 43\n'
    )
    env = {
        **os.environ,
        "SYMPHONY_AUTO_COMMIT_MSG": msg,
    }

    def _do_run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [resolve_bash(), "-lc", script],
            cwd=str(path),
            capture_output=True,
            timeout=timeout_s if timeout_s > 0 else None,
            env=env,
            check=False,
        )

    log.info("auto_commit_start", path=str(path), identifier=identifier)
    try:
        result = await asyncio.to_thread(_do_run)
    except subprocess.TimeoutExpired:
        log.warning("auto_commit_timeout", path=str(path), identifier=identifier)
        return CommitOutcome(
            COMMIT_FAILED, detail=f"auto-commit timed out after {timeout_s}s"
        )
    except Exception as exc:
        log.warning(
            "auto_commit_spawn_failed",
            path=str(path),
            identifier=identifier,
            error=str(exc),
        )
        return CommitOutcome(COMMIT_FAILED, detail=f"spawn failed: {exc}")

    rc = result.returncode or 0
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    if rc == 0:
        log.info(
            "auto_commit_completed",
            path=str(path),
            identifier=identifier,
            stdout=_truncate(stdout),
        )
        # The script prints this and exits 0 when the tree was already clean
        # and no commits sit above the recorded fork point.
        if "auto_commit: nothing to commit" in stdout:
            return CommitOutcome(COMMIT_NOOP, detail=stdout.strip())
        return CommitOutcome(COMMIT_OK, detail=stdout.strip())
    log.warning(
        "auto_commit_failed",
        path=str(path),
        identifier=identifier,
        returncode=rc,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
    )
    combined = f"{stdout}\n{stderr}".strip()
    return CommitOutcome(
        COMMIT_FAILED,
        detail=combined,
        failure_kind=classify_history_failure(combined),
    )


HISTORY_OK = "recorded"
HISTORY_LOCAL_ONLY = "local_only"
HISTORY_PUSH_FAILED = "push_failed"
HISTORY_COMMIT_FAILED = "commit_failed"

# Exit codes of `_PUSH_VERIFY_SCRIPT`, kept in one place so the mapping below
# reads as a table rather than as scattered magic numbers.
_PUSH_DETACHED = 10
_PUSH_NO_UPSTREAM = 11
_PUSH_REJECTED = 12
_PUSH_SHA_MISMATCH = 13

# Push only when the branch already has an upstream, exactly like the agent
# prompt's Final History Gate did. A branch with no upstream is a deliberate
# local-only ticket; creating a remote branch here would be Symphony making an
# outward-facing decision the operator never asked for.
_PUSH_VERIFY_SCRIPT = (
    'set -u\n'
    'BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || true)"\n'
    'if [ -z "$BRANCH" ]; then echo "detached HEAD"; exit 10; fi\n'
    'LOCAL_SHA="$(git rev-parse HEAD 2>/dev/null || true)"\n'
    'printf "BRANCH=%s\\nLOCAL_SHA=%s\\n" "$BRANCH" "$LOCAL_SHA"\n'
    'UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '
    "'@{u}' 2>/dev/null || true)\"\n"
    'if [ -z "$UPSTREAM" ]; then echo "no upstream configured"; exit 11; fi\n'
    'REMOTE="${UPSTREAM%%/*}"\n'
    'git push "$REMOTE" "$BRANCH" || exit 12\n'
    'REMOTE_SHA="$(git ls-remote "$REMOTE" "refs/heads/$BRANCH" '
    "| awk '{print $1}')\"\n"
    'printf "REMOTE_SHA=%s\\n" "$REMOTE_SHA"\n'
    'if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then exit 13; fi\n'
)


@dataclass(frozen=True)
class HistoryGateResult:
    """Outcome of the host-side Final History Gate.

    The gate exists because the agent cannot be trusted to write git history:
    it runs inside a sandbox whose writable roots may exclude the object
    database (see ``utils.git_sandbox``), and a failed housekeeping commit
    used to strand a finished ticket in ``Blocked``. The orchestrator runs
    unsandboxed, so it can always record the delivery — and it maps the three
    materially different outcomes onto three different ticket states.
    """

    status: str
    detail: str = ""
    branch: str = ""
    local_sha: str = ""
    remote_sha: str = ""
    failure_kind: str = UNKNOWN_FAILURE

    @property
    def durable(self) -> bool:
        """Work is in local git history and cannot be lost by a worktree prune."""
        return self.status in (HISTORY_OK, HISTORY_LOCAL_ONLY, HISTORY_PUSH_FAILED)

    @property
    def retryable(self) -> bool:
        """A wider-permission retry could plausibly succeed."""
        return (
            self.status == HISTORY_COMMIT_FAILED
            and self.failure_kind == SANDBOX_WRITE_DENIED
        )


def _parse_push_fields(stdout: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("BRANCH", "LOCAL_SHA", "REMOTE_SHA"):
            fields[key] = value.strip()
    return fields


async def finalize_delivery_history(
    path: Path,
    *,
    identifier: str,
    title: str,
    state: str | None = None,
    push: bool = True,
    timeout_s: float = 180.0,
) -> HistoryGateResult:
    """Record the ticket's final delivery from the host, then verify the remote.

    Runs the same snapshot commit as :func:`commit_workspace_on_done` and, when
    the branch has an upstream, pushes and re-reads the remote tip with
    `git ls-remote` so a silently-refused push cannot pass as recorded.

    Never raises: every failure becomes a :class:`HistoryGateResult` the
    orchestrator turns into a ticket state.
    """
    commit = await commit_workspace_on_done(
        path,
        identifier=identifier,
        title=title,
        state=state,
        timeout_s=timeout_s,
    )
    if not commit.durable:
        return HistoryGateResult(
            HISTORY_COMMIT_FAILED,
            detail=commit.detail,
            failure_kind=commit.failure_kind,
        )
    if not push:
        return HistoryGateResult(HISTORY_LOCAL_ONLY, detail="push disabled")

    def _do_push() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [resolve_bash(), "-lc", _PUSH_VERIFY_SCRIPT],
            cwd=str(path),
            capture_output=True,
            timeout=timeout_s if timeout_s > 0 else None,
            env={**os.environ},
            check=False,
        )

    try:
        result = await asyncio.to_thread(_do_push)
    except subprocess.TimeoutExpired:
        log.warning("history_gate_push_timeout", path=str(path), identifier=identifier)
        return HistoryGateResult(
            HISTORY_PUSH_FAILED,
            detail=f"push timed out after {timeout_s}s",
            failure_kind=REMOTE_REJECTED,
        )
    except Exception as exc:
        log.warning(
            "history_gate_push_spawn_failed",
            path=str(path),
            identifier=identifier,
            error=str(exc),
        )
        return HistoryGateResult(
            HISTORY_PUSH_FAILED, detail=f"push spawn failed: {exc}"
        )

    rc = result.returncode or 0
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    fields = _parse_push_fields(stdout)
    branch = fields.get("BRANCH", "")
    local_sha = fields.get("LOCAL_SHA", "")
    remote_sha = fields.get("REMOTE_SHA", "")
    combined = f"{stdout}\n{stderr}".strip()

    if rc == 0:
        log.info(
            "history_gate_recorded",
            identifier=identifier,
            branch=branch,
            local_sha=local_sha,
            remote_sha=remote_sha,
        )
        return HistoryGateResult(
            HISTORY_OK,
            detail=combined,
            branch=branch,
            local_sha=local_sha,
            remote_sha=remote_sha,
        )
    if rc in (_PUSH_DETACHED, _PUSH_NO_UPSTREAM):
        # Nothing to verify remotely — the commit is the whole delivery record.
        log.info(
            "history_gate_local_only",
            identifier=identifier,
            branch=branch,
            local_sha=local_sha,
            reason="detached" if rc == _PUSH_DETACHED else "no_upstream",
        )
        return HistoryGateResult(
            HISTORY_LOCAL_ONLY,
            detail=combined,
            branch=branch,
            local_sha=local_sha,
        )
    reason = (
        "remote tip does not match the local commit"
        if rc == _PUSH_SHA_MISMATCH
        else "push was refused"
    )
    log.warning(
        "history_gate_push_failed",
        identifier=identifier,
        branch=branch,
        returncode=rc,
        local_sha=local_sha,
        remote_sha=remote_sha,
        stderr=_truncate(stderr),
    )
    return HistoryGateResult(
        HISTORY_PUSH_FAILED,
        detail=f"{reason}\n{combined}".strip(),
        branch=branch,
        local_sha=local_sha,
        remote_sha=remote_sha,
        failure_kind=(
            REMOTE_REJECTED if rc == _PUSH_REJECTED else classify_history_failure(combined)
        ),
    )


_BRANCH_HISTORY_SCRIPT = (
    'set -u\n'
    'BRANCH="${SYMPHONY_HISTORY_BRANCH:?}"\n'
    'LOCAL_SHA="$(git rev-parse --verify --quiet "${BRANCH}^{commit}" || true)"\n'
    'if [ -z "$LOCAL_SHA" ]; then echo "branch not found: $BRANCH"; exit 20; fi\n'
    'printf "BRANCH=%s\\nLOCAL_SHA=%s\\n" "$BRANCH" "$LOCAL_SHA"\n'
    'UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '
    '"${BRANCH}@{u}" 2>/dev/null || true)"\n'
    'if [ -z "$UPSTREAM" ]; then echo "no upstream configured"; exit 11; fi\n'
    'REMOTE="${UPSTREAM%%/*}"\n'
    'if [ "${SYMPHONY_HISTORY_PUSH:-1}" = "1" ]; then\n'
    '  git push "$REMOTE" "$BRANCH" || exit 12\n'
    'fi\n'
    'REMOTE_SHA="$(git ls-remote "$REMOTE" "refs/heads/$BRANCH" '
    "| awk '{print $1}')\"\n"
    'printf "REMOTE_SHA=%s\\n" "$REMOTE_SHA"\n'
    'if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then exit 13; fi\n'
)

_BRANCH_MISSING = 20


async def verify_branch_history(
    repo_dir: Path,
    *,
    branch: str,
    push: bool = True,
    timeout_s: float = 180.0,
) -> HistoryGateResult:
    """Check (and optionally publish) a ticket branch's history from the host repo.

    The workspace-scoped gate needs the worktree to still exist. This variant
    reads the branch directly out of the host repo, so it still works after the
    worktree has been pruned — which is the situation when a ticket was parked
    in ``Blocked`` and its workspace was reaped on the way out.

    Never raises; failures come back as a :class:`HistoryGateResult`.
    """

    def _run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [resolve_bash(), "-lc", _BRANCH_HISTORY_SCRIPT],
            cwd=str(repo_dir),
            capture_output=True,
            timeout=timeout_s if timeout_s > 0 else None,
            env={
                **os.environ,
                "SYMPHONY_HISTORY_BRANCH": branch,
                "SYMPHONY_HISTORY_PUSH": "1" if push else "0",
            },
            check=False,
        )

    try:
        result = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return HistoryGateResult(
            HISTORY_PUSH_FAILED,
            detail=f"branch history check timed out after {timeout_s}s",
            branch=branch,
        )
    except Exception as exc:
        return HistoryGateResult(
            HISTORY_COMMIT_FAILED, detail=f"spawn failed: {exc}", branch=branch
        )

    rc = result.returncode or 0
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    fields = _parse_push_fields(stdout)
    local_sha = fields.get("LOCAL_SHA", "")
    remote_sha = fields.get("REMOTE_SHA", "")
    combined = f"{stdout}\n{stderr}".strip()

    if rc == 0:
        return HistoryGateResult(
            HISTORY_OK,
            detail=combined,
            branch=branch,
            local_sha=local_sha,
            remote_sha=remote_sha,
        )
    if rc == _BRANCH_MISSING:
        # No branch means no delivery record at all — not a permissions issue.
        return HistoryGateResult(
            HISTORY_COMMIT_FAILED, detail=combined, branch=branch
        )
    if rc == _PUSH_NO_UPSTREAM:
        return HistoryGateResult(
            HISTORY_LOCAL_ONLY, detail=combined, branch=branch, local_sha=local_sha
        )
    return HistoryGateResult(
        HISTORY_PUSH_FAILED,
        detail=combined,
        branch=branch,
        local_sha=local_sha,
        remote_sha=remote_sha,
        failure_kind=(
            REMOTE_REJECTED if rc == _PUSH_REJECTED else classify_history_failure(combined)
        ),
    )


def validate_agent_cwd(cwd: Path, workspace_root: Path) -> None:
    """§9.5 invariants 1 + 2 — refuse to launch outside workspace root."""
    cwd = cwd.resolve()
    workspace_root = workspace_root.resolve()
    try:
        cwd.relative_to(workspace_root)
    except ValueError as exc:
        raise InvalidWorkspaceCwd(
            "agent cwd not under workspace root",
            cwd=str(cwd),
            root=str(workspace_root),
        ) from exc
    if not cwd.is_dir():
        raise InvalidWorkspaceCwd("agent cwd is not a directory", cwd=str(cwd))
