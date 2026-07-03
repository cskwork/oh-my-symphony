# Four-agent todo rerun release blockers - 2026-07-03

## Decision

Do not merge `feat/e2e-production-hardening` to `dev` or `main`, and do not cut
the next minor release yet. The rerun improved several earlier failures, but the
release condition is still not proven.

Current local release tag is `v0.8.0`; the operator-specified next release
target is `v0.8.1` / `0.8.1` after the blockers below are fixed and the full
gate is green.

Version-source conflict to resolve before tagging:

- `git describe --tags --abbrev=0` -> `v0.8.0`
- `pyproject.toml` -> `0.9.1`
- `src/symphony/__init__.py` -> `0.9.1`

Do not tag until the intended release number is reconciled in both version
source files.

## Follow-up Status - Current Working Tree

Update after `dev` promotion:

- `feat/e2e-production-hardening` and `dev` now both point to
  `998bdeef78c6cf130297bcc08f2e9196e21ad57b`.
- Remote `origin/dev` was read back at the same SHA.
- A clean `origin/dev` clone at `/private/tmp/symphony-release-e2e-14zh0I/repo`
  started all four release-gate tickets on port `10086`.
- The run is not a release pass. `REL-303` (Claude) failed immediately and
  auto-paused with `turn_error: turn_failed: success`, proving the Claude
  backend still masked the real provider error when an error result also used
  `subtype: success`.
- The follow-up fix changes the Claude failure reason to prefer actionable
  result/error/API fields and records that reason in the emitted failure event.
- Cleanup passed for this failed attempt: forced service stop closed port
  `10086`, and a process sweep found no command line referencing the temp root.

The forced-stop leak blocker is fixed in the current working tree:

- OpenCode, Pi, Claude, and Gemini now reap their active subprocess when a
  worker task is cancelled during shutdown.
- `service stop --force` also scans recent rows owned by the stopped
  orchestrator PID and POSIX processes whose command line references one of
  that orchestrator's registered workspace paths.
- Live smoke evidence: `/private/tmp/symphony-stop-smoke-GDrYsW` started one
  fake OpenCode worker on port `10085` with backend PID `18456`;
  `service stop ./WORKFLOW.md --timeout 2 --force` stopped the service, `curl`
  to `/api/v1/state` failed with connection error `7`, `ps -p 18456` returned
  no process row, and a process sweep found no command line referencing the
  smoke or prior full-run temp roots.

The release gate is still red:

- The latest full four-agent E2E run performed for this blocker work did not
  reach a clean all-agent handoff.
- OpenCode and Pi pass `scripts/static_todo_browser_acceptance.py` against the
  saved app artefacts from `/private/tmp/symphony-full-e2e-M5J5f4`.
- Claude currently cannot prove a fresh run on this machine: an isolated
  `claude -p --output-format stream-json --verbose --permission-mode
  acceptEdits` probe returned `is_error: true` with a session-limit message and
  reset time `7:30pm (Asia/Seoul)`.
- The Claude backend now treats explicit `is_error: true` as failure even when
  the terminal subtype says `success`, so future rate limits surface as
  backend failures instead of empty successful turns.
- Codex reached `Blocked`; the saved app uses `<script type="module">`, which
  Chromium blocks from `file://`, so the static app never boots under the
  release acceptance path.
- `scripts/static_todo_browser_acceptance.py` is now checked in as the
  operator-level browser acceptance gate and fails fast on that module/CORS
  boot error.
- Do not merge, tag, or publish until a fresh four-agent run satisfies the full
  acceptance gate below.

## Follow-up Status - Fresh `dev` Rerun

Status at `2026-07-03 18:14 KST` from
`/private/tmp/symphony-release-e2e-r3-ywgA6n/repo` on port `10090`:

- The temp workflow used the normal `kanban/` board and isolated workspaces at
  `/private/tmp/symphony-release-e2e-r3-ywgA6n/workspaces`.
- `symphony doctor ./WORKFLOW.md` passed before launch.
- `REL-301` (OpenCode) is still running in `Verify`; final handoff is not
  proven.
- `REL-302` (Pi) is still running in `In Progress`; final handoff is not
  proven.
- `REL-303` (Claude) is paused on a visible provider quota error:
  `You've hit your session limit · resets 7:30pm (Asia/Seoul)`.
- `REL-304` (Codex) reached `Blocked` because the required browser gate failed
  before app load: Chromium tried to use the operator's default profile/crashpad
  path and hit macOS `Operation not permitted` on
  `~/Library/Application Support/Chromium/Crashpad/settings.dat`.
- A host rerun of the Codex app with an isolated browser profile passed the
  operator gate, so the app is not the current observed failure. The release
  blocker is the shared browser-gate launch environment used by workers.
- The release gate remains red. Do not merge to `main`, tag, or publish until a
  fresh clone from the fixed `dev` branch completes all acceptance gates.

Additional status from the replacement run:

- `REL-403` was cancelled after the Claude provider quota pause; the restart
  ticket is `REL-405`, which dispatched and moved to `In Progress`.
- `REL-404` (Codex) generated an app that passes the host browser gate, but the
  worker marked it `Blocked` because its ticket workspace could not run the
  browser gate reliably.
- Root cause found in the setup hook: it preferred `python3.11` while
  `pyproject.toml` requires `>=3.12`, and it installed only `.[dev]` even
  though the release browser gate needs the `browser` extra.
- Follow-up fix in the main checkout: prefer `python3.12`/`python3.13` first
  and install `.[dev,browser]` in `scripts/symphony-setup-worktree.sh`.
- This fix does not by itself prove Codex can launch Chromium inside Codex's
  workspace sandbox. The host gate passes, but a fresh run is still required to
  determine whether worker-side browser launch is now reliable for all agents.

## Follow-up Status - Codex Browser Sandbox RCA

Status at `2026-07-03 20:31 KST` from the r7 clean-dev rerun:

- `REL-704` (Codex) generated an app that passed the operator-side browser gate:
  `.venv/bin/python scripts/static_todo_browser_acceptance.py
  /private/tmp/symphony-release-e2e-r7-VCauY2/workspaces/REL-704/examples/e2e-todo/codex`
  -> `PASS`.
- The same gate failed inside the Codex worker before app interaction:
  `bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer...:
  Permission denied (1100)`.
- Root cause: the shipped workflow ran Codex with `thread_sandbox:
  workspace-write` and `turn_sandbox_policy: workspace-write`. That sandbox
  still allowed file edits after writable-root injection, but it blocked
  Playwright Chromium's macOS Mach bootstrap path.
- Fix: the shipped `WORKFLOW.md` now sets Codex `thread_sandbox` and
  `turn_sandbox_policy` to `danger-full-access` for this browser-gated
  production workflow.
- Rejected: adding another isolated browser profile. The current gate already
  isolates `HOME`, cache, and config, and the app passes from the operator
  shell.
- Rejected: treating the worker failure as app-quality failure. Host proof
  shows the app is valid; the failing layer is Codex worker sandboxing.
- Rejected: skipping the browser gate or asking workers to manually claim it.
  Prior Pi and OpenCode failures prove real browser execution is the release
  authority.

## Goal

Run Symphony end-to-end with four live agent backends building the same static
todo app:

- OpenCode: `examples/e2e-todo/opencode/`
- Pi: `examples/e2e-todo/pi/`
- Claude: `examples/e2e-todo/claude/`
- Codex: `examples/e2e-todo/codex/`

The expected outcome is all four tickets reaching the human handoff state with
durable Node and browser evidence, no active retries, no leaked child processes,
and no unresolved contract failures.

## Run Evidence

Run root:

- Temp repo: `/private/tmp/symphony-e2e-todo-rerun-OfAuBV/repo`
- Temp workspaces: `/private/tmp/symphony-e2e-todo-rerun-OfAuBV/workspaces`
- Service: `http://127.0.0.1:10082`

Preflight:

- `symphony doctor ./WORKFLOW.md` passed in the real repo.
- Focused regression suite passed: `173 passed`.
- Temp doctor passed after setting port `10082` and four-worker concurrency.
- All four tickets dispatched, and all four `after_create` hooks completed.

Agent status at release decision:

| Agent | Ticket | Evidence | Release status |
| --- | --- | --- | --- |
| OpenCode | `RERUN-201` | External Node harness and browser QA passed | Not proven: Symphony Verify worker was still active when stopped |
| Pi | `RERUN-202` | Node harness passed | Blocked: real browser empty-state check failed |
| Claude | `RERUN-203` | In Progress completed; harness passed externally | Not proven: operator explicitly stopped the Claude Verify loop |
| Codex | `RERUN-204` | Node harness and browser QA passed; reached Human Review after citation repair | Mostly proven, but contract-citation UX needs regression coverage |

Cleanup:

- `symphony service stop ./WORKFLOW.md --timeout 15 --force` stopped the
  service process.
- The stop left temp-run `opencode` and `pi` child process groups alive; they
  had to be terminated manually with `kill -TERM -56429 -62953`.

## Blockers

### P0. Pi browser behavior failed

The Pi app passed its zero-dependency Node harness but failed real browser proof:

```text
empty_display=block
main_display=none
empty_visible=False
```

Root cause in generated app:

- `examples/e2e-todo/pi/app.js:420` sets `#empty-state` display to visible when
  there are no todos.
- `examples/e2e-todo/pi/app.js:426-428` then hides `#main`.
- `#empty-state` is inside `#main`, so the visible empty-state text is hidden by
  its parent.

This proves the release gate cannot treat a DOM shim or Node harness as final
browser authority for UI work.

Fix plan:

1. Let the Pi Verify stage complete on a fresh rerun, or rewind Pi and require
   it to repair the empty-state defect.
2. Keep the Verify prompt rule that browser UI work must use Playwright or
   headless Chromium.
3. Add an operator-level browser acceptance script for this four-agent release
   gate so external proof cannot be skipped when a worker only documents manual
   browser gaps.
4. Rerun Pi from a fresh workspace and require:
   - empty state visible on clean `localStorage`;
   - add via Enter and button;
   - toggle visible strikethrough;
   - delete;
   - All, Active, Completed filters;
   - active count;
   - clear completed;
   - inline edit Enter save, Escape cancel, empty edit delete;
   - todos and filter persist across reload.

### P0. Full four-agent lifecycle did not finish

The conditional release request requires "all working". This rerun did not
complete as a clean four-agent lifecycle:

- `RERUN-201` was still in `Verify` with an active OpenCode process.
- `RERUN-202` was still in `Verify` with an active Pi process.
- `RERUN-203` was paused after the requested Claude stop.
- `RERUN-204` reached `Human Review`, but had already taken a contract-citation
  rewind to repair evidence coordinates.

Fix plan:

1. Repeat the run after fixing the browser proof issue.
2. Do not count an agent as passing unless its ticket reaches `Human Review`
   or another expected terminal handoff state.
3. Require `/api/v1/state` to report no unexpected `running` or `retrying`
   entries before release.
4. Record final `kanban/<ticket>.md` state and top QA artefact paths for all
   four agents.

### P0. Forced service stop leaked child agent processes

After stopping the temp service, the service port closed but temp-run child
processes remained:

- `opencode run ... RERUN-201`
- `pi ... RERUN-202`

Fix plan:

1. Reproduce with a temp workflow running at least one active worker.
2. Inspect service-stop shutdown path and ensure it asks the orchestrator to
   cancel active workers before the service process exits.
3. Ensure force-stop terminates child process groups owned by active backends.
4. Add a regression test that starts a fake long-running child process and
   asserts service stop leaves no descendant process.
5. Re-run the temp four-agent gate and confirm `ps` has no paths under the temp
   run root after stop.

### P1. Evidence-contract citation behavior is too easy to trip

Codex initially triggered `stage_contract_failed` because evidence cells used
repo-root paths and prose/source anchors instead of docs-root-relative artefact
paths. The worker later repaired this and reached `Human Review`, but the first
failure shows the prompt-contract boundary is fragile.

Current code:

- `src/symphony/orchestrator/contracts.py:349-400` extracts cited evidence paths.
- `docs/symphony-prompts/file/stages/verify.md:15-16` tells workers to add QA
  evidence and a scorecard, but does not explicitly say evidence cells must be
  docs-root-relative artefact paths only.

Fix plan:

1. Add contract tests for three evidence-cell shapes:
   - `qa/qa.log` exists under `docs/<id>/` and passes.
   - `docs/<id>/qa/qa.log` either normalizes or fails with a clearer message.
   - prose like `No secrets in examples/foo.js:1` is not silently treated as a
     docs artefact path without a clear instruction.
2. Tighten Verify prompt language: scorecard/security evidence cells must cite
   files under `docs/<id>/` as `qa/...` or `work/...`; source anchors belong in
   those detail files.
3. Keep the hard failure for fabricated evidence paths; only improve path
   coordinate clarity and prose handling.

### P1. OpenCode Verify liveness is not enough to prove completion

OpenCode no longer false-stalled during the long turn, but the Verify worker was
still active when the release decision was made. External browser checks passed,
yet Symphony had not completed the stage.

Fix plan:

1. Let OpenCode Verify complete in the rerun and record whether it reaches
   `Learn` and `Human Review`.
2. If it stays active after browser QA is complete, capture the OpenCode stdout
   JSON and backend events.
3. Add a bounded stage-duration diagnostic if a worker keeps emitting heartbeat
   events but does not advance state for a long Verify turn.

### P2. Pause is not immediate cancellation

The operator asked to stop the Claude loop. `pause` correctly prevented future
turns, but the active Claude process required a manual process-group terminate.

Fix plan:

1. Decide whether Symphony needs a separate immediate `cancel` API/action in
   addition to pause.
2. If yes, implement cancel as "pause future turns and terminate current worker
   process group".
3. Add tests that `pause` parks future work, while `cancel` ends current work.

### P2. Pi backend 429s remain an availability risk

Pi recovered from backend-internal 429 retries in this run, but the state should
make overload/rate-limit waits clear to operators.

Fix plan:

1. Keep surfacing Pi internal retries in logs.
2. Add or verify an attention/status surface for active Pi retry waits.
3. Distinguish "upstream overloaded but retrying" from app-quality failures in
   the final E2E report.

## Acceptance Gate Before Merge

Do not merge or release until all checks below are true:

1. Fresh four-agent run starts from a clean temp clone.
2. `symphony doctor ./WORKFLOW.md` passes before launch.
3. All four `after_create` hooks complete without git lock errors.
4. All four tickets reach `Human Review` or the agreed terminal handoff state.
5. `/api/v1/state` has no unexpected `running` or `retrying` entries.
6. Every generated todo app passes
   `scripts/static_todo_browser_acceptance.py <app-dir>`.
7. Every generated todo app has a passing Node harness.
8. No `stage_contract_failed` remains unresolved in the final ticket body.
9. `symphony service stop --force` leaves no temp-run child agent processes.
10. Real repo checks pass:
    - `symphony doctor ./WORKFLOW.md`
    - `.venv/bin/python -m pytest tests/test_orchestrator_dispatch.py tests/test_workflow_pipeline_prompt.py tests/test_workspace.py -q`
    - full test suite if time allows before tagging

## Release Steps After Gate Is Green

Only after the gate passes:

1. Merge `feat/e2e-production-hardening` into `dev` with `--no-ff`.
2. Run tests on `dev`, push, and verify the remote branch SHA.
3. Merge `dev` into `main` with `--no-ff`.
4. Reconcile both version sources to the operator-approved release number
   `0.8.1`:
   - `pyproject.toml`
   - `src/symphony/__init__.py`
5. Commit the release bump as `chore(release): v0.8.1`.
6. Tag `v0.8.1` on the release commit.
7. Push `main` and the tag.
8. Create the GitHub release only after remote SHA and tag verification.

## Rejected Alternatives

- Rejected: merge now because most checks passed. The Pi browser failure and
  incomplete worker lifecycle violate the user's release condition.
- Rejected: count Node harnesses as enough for browser UI work. The Pi failure
  is a concrete counterexample.
- Rejected: tag `v0.8.1` before `dev` and `main` are green. That would publish
  unproven orchestration behavior and leave the release hard to unwind.
