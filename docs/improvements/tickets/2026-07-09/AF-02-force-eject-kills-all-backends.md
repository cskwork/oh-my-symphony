# AF-02 — Force-eject must kill non-codex process groups too

Route: LEGACY | Severity: P1 | Confidence: CONFIRMED | Blocked by: none
Unblocks: AF-10 (lease reclaim needs the recorded agent pid/pgid)

## Defect

`_force_eject_zombie` only kills the OS process group when
`entry.codex_app_server_pid is not None` (`core.py:4997-4998`). That pid is
populated by the codex backend alone (`core.py:4301`,
`orchestrator/entries.py:84`). For claude/opencode/gemini/agy/kiro/pi
zombies, force-eject frees the slot and schedules a retry **without killing
anything** — the wedged subprocess keeps running (burning tokens, holding
files/worktree) arbitrarily long, and its eventual wake-up is what arms the
AF-01 race.

## Fix direction

Record the child pid/process-group for every backend at spawn (they already
`start_new_session=True`), surface it on the running entry via a
backend-agnostic field (e.g. `agent_pgid`, keeping `codex_app_server_pid` as
an alias or migrating it), and have `_force_eject_zombie` call
`kill_process_group` whenever a pid is recorded, logging per-backend. The
per-turn family exposes the pid transiently per turn — the entry field must
be updated at each turn spawn.

## Acceptance checks

- [ ] RED first: force-eject a fake non-codex entry carrying a recorded pgid;
  assert `kill_process_group` is invoked (spy) — fails on current `main`.
- [ ] WHEN any backend's worker is force-ejected THEN its recorded process
  group receives the kill and `force_eject_killed_process_group` logs with
  backend kind.
- [ ] WHEN no pid was recorded (spawn failed early) THEN force-eject still
  frees the slot and schedules the retry (current behavior preserved).
- [ ] Backend contract suite extended: each backend records a pid on the
  entry during a live turn (`tests/test_backend_contract.py`).
- [ ] Full suite green.

## Non-goals

Exit-path identity checks (AF-01); startup-time reclaim kills (AF-10);
changing `safe_proc_wait` reaping.

## Resolution — 2026-07-10

Resolved on dev (793813a): every backend records its child process group on
the running entry and force-eject kills any recorded group, logging per
backend; entries without a recorded pid keep the slot-free/retry behavior.
Run evidence: `docs/changelog/2026-07/10-af-02-force-eject/`.
