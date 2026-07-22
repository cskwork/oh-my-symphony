# Frontier 003 Integration Brief

This brief records landed core seams for the later provisioner/runtime builder. It does not amend the passed plan.

## Worker and retry ownership

- `_dispatch` resolves `WorkspaceManager.path_for`, acquires the run lease, creates `RunningEntry`, then starts the
  worker. Capture the exact manager, worktree runtime generation, workflow identity, route-pair digest, and attempt-
  record revision in that entry before task creation.
- `_run_agent_attempt` currently dereferences `self._workspace_manager` separately for create, before-run, after-run,
  and cleanup. Use the captured manager for the complete attempt so a reload cannot split ownership.
- Generic timer retry `_process_retry` fetches candidates and calls `_dispatch` without routing filtering. It must
  pass the same worktree durable-admission gate as initial dispatch.
- Generic worker-error exit schedules a retry even for auto-paused errors. A specialized durable worktree failure
  needs its own bounded outcome/entry flag that suppresses `_schedule_retry`; due worktree backoff re-enters only on
  a later poll.

## Reload and runtime

- Construct one process-lifetime `AidtWorktreeRuntime` in `Orchestrator.__init__`; it is not a `WorkspaceManager`
  child. Startup and every replacement manager receive the same provider/guard.
- `_on_tick` currently replaces the manager when `workspace_root` changes before dispatch validation. Publish a new
  immutable runtime generation only after full profile validation; invalid reload retains the last guard, closes
  nomination, and performs no partial manager/delegate handoff.
- The stable workflow-relative registry, not the current manager root, decides whether a path is already AIDT-owned.

## Candidate boundary

- Current routing result is applied before candidate fetch and `filter_routing_candidates` excludes all managed IDs.
  Pass its provisionable set, then pass survivors through runtime attempt-record admission before slot/conflict checks.
- Coordinators/review/stale/retained remain excluded. A tick nomination is recorded in the entry but worker create
  and `before_run` re-attest the route pair/generation.

## Terminal paths

- Generic startup, reconcile, inactive, and Done paths call commit/merge/hooks/remove through several sites.
  Delegate recognition must occur before those mutations. Frontier 003 production authority is deny-all, so every
  AIDT terminal path preserves and does not mark cleanup complete.
- `_reconcile_running` must not set `workspace_cleanup_started` after an owned-preserved result. Later authorized
  delivery cleanup will require the same active owner; generic Done is never an issuer.
- Existing unmanaged behavior and method signatures remain compatible through UNMANAGED disposition.

## Health

- Add an `aidt_worktree` health snapshot beside `aidt_routing`, sourced from the process-lifetime runtime. It exposes
  only enabled/status/counts/generation/category/ref/last-success/consecutive-failures; never paths, lock keys, Git
  output, URLs, card prose, or environment.

## Required integration tests

- initial and retry dispatch share nomination/admission/create/final-barrier order;
- reload between nomination/create/before-run cannot start a stale backend;
- captured manager survives workspace-root replacement;
- specialized durable failure schedules no generic retry;
- corrupt/missing/removed/disabled ownership never reaches generic hook/commit/merge/rmtree;
- generic startup/reconcile/Done preserves AIDT and leaves cleanup pending;
- never-enabled/unmanaged tests retain exact landed behavior and lazy imports.
