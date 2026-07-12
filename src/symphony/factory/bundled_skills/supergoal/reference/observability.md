# Observability - the Supergoal Board state layer

Optional. A live, in-browser dashboard of every agent's mode + workflow stage + Jira-like task board,
across concurrent agents on different repos/branches/worktrees. This file is the producer side (state
emission); the reader UI lives in `tui/`. Load only when a Board run is requested.

**Baseline-first invariant.** Droppable. If no agent emits, every gate and mode still passes unchanged.
No gate reads these files; no mode fails when emission is absent or partial. It adds observability,
never a delivery gate. Deleting `~/.supergoal/runs` breaks nothing.

## The one load-bearing idea

Correctness comes entirely from **one writer per file + atomic rename**. Everything else (lock-free,
crash-safe, shared-branch-safe, tailable) follows, with no lock anywhere.

- One heartbeat JSON per agent, replaced atomically (`tmp.$$` -> `mv -f`). A reader's `read()` returns
  the old-complete or new-complete file, never a torn one.
- `branch` is a display field, never a mutex. Two agents on `feat/login` write two distinct files, so
  they never serialize and never lock - other agents use that branch freely.

## File layout

```
${SUPERGOAL_RUN_DIR:-$HOME/.supergoal/runs}/
  .enabled                       # opt-in flag (tui/launch.sh creates it); absent => sg-emit no-ops
  agents/<agent_id>.json         # heartbeat, one per agent, replaced atomically
```

Registry lives **outside any target repo** on purpose: writing into the repo would dirty `git status`,
which ARCHITECTURE/REVIEW-ONLY treat as a baseline violation. Schema: `templates/observability/heartbeat.schema.json`.

## Emitting - `templates/observability/sg-emit.sh`

```
sg-emit --phase Critic [--mode GREENFIELD] [--task "Add JWT refresh"] \
        [--task-status in-progress] [--note "2 reds open"] [--slot exec-3f] [--tasks-file board.json]
```

- **Opt-in:** emits only when `$REGDIR/.enabled` exists or `SUPERGOAL_TUI=1`. No Board => silent no-op.
- **Best-effort:** any failure (no `jq`, no disk, no perms) -> one stderr line + `exit 0`. Never aborts work.
- **Self-derived identity:** repo/branch/worktree come from `git` in the cwd on every call. Claude Code
  tool calls do not persist exported env between calls and have no stable per-agent OS pid, so the
  helper relies on neither - it recomputes identity each call. Pass `--slot <id>` when an orchestrator
  runs several agents in the SAME worktree+branch, to keep their files distinct.
- **Carry-forward:** without `--tasks-file`, the prior `tasks[]` board is preserved; a named `--task`
  updates that task's `status` (or appends it). `started_at` is immutable across emits.
- **`jq` is a declared dependency** of the helper (used for the atomic merge).

## Lifecycle (called by the conductor at loop boundaries)

| Stage | Emit |
|---|---|
| Register (Frame) | `sg-emit --phase Frame --mode <MODE> --task "<first>" --task-status backlog` |
| Phase update | `sg-emit --phase Build\|ImproveFullSpec\|ImproveEdgeCases\|MandatoryAdversarialReview\|ExactVerify` (optional `Critic\|Fixer`; carries the board forward) |
| Task move | `sg-emit --task "<t>" --task-status backlog\|in-progress\|review\|done\|blocked` |
| Clean exit | `sg-emit --phase Done` |

Personas run isolated and cannot see each other; the **conductor** owns the loop boundary and makes the
emits (same pattern as the conductor summing sub-budgets into `state.json`, `reference/qa-only.md`).

## Reader liveness (the dashboard classifies; writers never reap)

Timestamp is the **primary** signal; pid is an optional same-host refinement only.

```
alive : (now - updated_at) <= 45s
stale : 45s < (now - updated_at) <= 180s
dead  : (now - updated_at) > 180s   OR   (same host AND kill -0 pid fails)
done  : phase == "Done"
```

`kill -0 pid` is valid only when `host` == the reader's host; cross-host falls back to timestamp alone.
PID is advisory (no stable per-agent pid under Claude Code), so a missing/false pid never alone marks an
agent dead - timestamp does. Writers never touch peers' files; there is no lock and no cleanup race.

## Cleanup

Per-run files are disposable. Housekeeping is a plain `find "$SUPERGOAL_RUN_DIR/agents" -mtime +3
-delete` (cron or manual) - no coordination, nothing downstream depends on it. v1 does not auto-prune.

## Concurrency hazards & how the design avoids each

| Hazard | Avoided by |
|---|---|
| Torn read | write `tmp.$$`, atomic `mv -f` within one dir |
| Lost update / two writers clobber | one writer per file; no shared mutable file => no lock |
| Shared-branch collision | branch is data, not a lock; distinct `agent_id` per agent (worktree hash + `--slot`) |
| Crashed writer mid-emit | crash before `mv` leaves a harmless `.tmp` + the prior good `.json` intact |
| Networked FS (rename not atomic across mounts) | temp is in the same dir as dest (same mount). NOTE: on NFS `$HOME`, `rename(2)` atomicity is not guaranteed across all servers - a known limitation, local `$HOME` is the supported case |
| PID reuse | pid is advisory; liveness is timestamp-primary, so a reused pid cannot resurrect a stale agent |
