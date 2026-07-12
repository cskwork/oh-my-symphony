"""SPEC §9 — workspace manager and lifecycle hooks."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._shell import resolve_bash
from .errors import InvalidWorkspaceCwd, SymphonyError
from .issue import workspace_key
from .logging import get_logger
from .workflow import HooksConfig

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

        self._write_workspace_owner_marker(key)
        return Workspace(path=path, workspace_key=key, created_now=created_now)

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
        if name == "after_create" and self._hook_env:
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
        stdout_bytes = result.stdout or b""
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


def _coerce_output_bytes(
    value: bytes | bytearray | memoryview | str | None,
) -> bytes:
    if value is None:
        return b""
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return bytes(value)


async def commit_workspace_on_done(
    path: Path,
    *,
    identifier: str,
    title: str,
    exit_reason: str | None = None,
    state: str | None = None,
    timeout_s: float = 60.0,
) -> None:
    """Snapshot the per-ticket workspace into one git commit on worker exit.

    Always called before `WorkspaceManager.remove()` — the goal is that no
    work the agent left in the worktree gets discarded by `git worktree
    remove --force`. Fires for every exit (Done, Cancelled, Blocked,
    error, timeout, reconcile-terminated) when `auto_commit_on_done` is
    on; the commit message includes the exit reason / state for non-Done
    cases so a quick `git log` makes the situation obvious.

    Lenient by design — every failure (missing path, no diffs, pre-commit
    rejection, signing error, timeout) logs a warning and returns. We
    never raise out of the worker exit path; a failed auto-commit is a
    housekeeping miss surfaced by the warning, not a regression that
    blocks the queue.

    Reuses any enclosing git repo (`git -C path rev-parse --git-dir`).
    Only initialises a new repo when the workspace has no git ancestor,
    so workspaces nested inside an existing project repo just add a
    commit to that project's history rather than creating a nested
    `.git`. With the worktree-default hooks the commit lands on the
    `symphony/<ID>` branch the worktree is checked out on.
    """
    if not path.exists():
        log.info("auto_commit_skipped_missing_workspace", path=str(path))
        return

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
        '# Keep an empty sentinel: Bash 3.2 + `set -u` treats expansion of an\n'
        '# empty declared array as an unbound variable. Loop bodies skip it.\n'
        'EXCLUDE_PATHS=("")\n'
        'while IFS= read -r exclude_path; do\n'
        '  [ -n "$exclude_path" ] && EXCLUDE_PATHS+=("$exclude_path")\n'
        'done < <(git config --get-all symphony.autocommitExclude 2>/dev/null || true)\n'
        '# Do not pass ignored exclusions as negative pathspecs to `git add`:\n'
        '# an ignored symlink replacing a tracked directory makes Git reject\n'
        '# that explicit path. Stage the workspace, then restore exclusions in\n'
        '# the index from HEAD. The explicit `.` still prevents host leakage.\n'
        'git add -A -- . || exit 42\n'
        'for exclude_path in "${EXCLUDE_PATHS[@]}"; do\n'
        '  [ -n "$exclude_path" ] || continue\n'
        '  if git rev-parse --verify HEAD >/dev/null 2>&1; then\n'
        '    git reset -q HEAD -- "$exclude_path" || exit 42\n'
        '  else\n'
        '    git rm -r -q --cached --ignore-unmatch -- "$exclude_path" || exit 42\n'
        '  fi\n'
        'done\n'
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
        '# A soft reset stages the complete commit range, including excluded\n'
        '# paths an agent may have committed. Restore those paths once more\n'
        '# against the selected squash base before committing.\n'
        'for exclude_path in "${EXCLUDE_PATHS[@]}"; do\n'
        '  [ -n "$exclude_path" ] || continue\n'
        '  git reset -q HEAD -- "$exclude_path" || exit 42\n'
        'done\n'
        'if git diff --cached --quiet -- .; then\n'
        '  echo "auto_commit: nothing to commit"\n'
        '  exit 0\n'
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
        return
    except Exception as exc:
        log.warning(
            "auto_commit_spawn_failed",
            path=str(path),
            identifier=identifier,
            error=str(exc),
        )
        return

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
        return
    log.warning(
        "auto_commit_failed",
        path=str(path),
        identifier=identifier,
        returncode=rc,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
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
