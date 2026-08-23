# Live managed-pipeline E2E shard

## Verdict

**Live phase pipeline: PASS. Final managed-service teardown: PASS.**

Two real OpenCode runs against the scratch file board jointly prove the refactored live phase path and the material worker output. E2E-3 traversed the requested board sequence and exercised an orchestrator-observed backend phase transition. E2E-4 independently proved the generated source, plain-assert test, and documentation before normal terminal cleanup removed its workspace.

The first teardown attempt exposed a Windows launcher/child PID mismatch and
failed. That discovery produced the instance-bound lifecycle correction. The
final normal-host rerun used ordinary `service stop` and removed both processes,
closed the port, and cleared the record.

## Preflight and isolation

- Workflow: `<scratch>\WORKFLOW.md` under the isolated `sym-e2e` project.
- Read before launch: workflow plus completed tickets E2E-1 and E2E-2.
- CLI: `<repo>\.venv\Scripts\symphony.exe`, version 0.20.1.
- Imported implementation: `<repo>\src\symphony\orchestrator\core.py`.
- Doctor: exit 0 within the hard 180-second bound. Every required check passed. The only warning was the configured `agent.stage_contracts: off` evidence-gate warning.
- Product source/tests changed by this shard: none.

## E2E-3 — live phase mechanics

- Ticket: E2E-3, created in Todo and auto-triaged to In Progress.
- Run ID: `8e4bb6157a984481b36b39c7af388001`.
- Board transition evidence:
  - Todo → In Progress: `2026-08-23T11:23:12Z` (`stats.jsonl`).
  - In Progress → Verify: `2026-08-23T11:24:39Z` (`WORKFLOW-PROGRESS.md`).
  - Verify → Document: `2026-08-23T11:27:23Z` (`WORKFLOW-PROGRESS.md`).
  - Document → Done: `2026-08-23T11:28:14Z` (`WORKFLOW-PROGRESS.md`).
- Refactored phase event: registry event 42 / structured log at `11:24:57Z`, `from_state="in progress"`, `to_state=verify`, `turn=2`, `is_rewind=false`.
- Completion: registry `status=normal`, `state=Done`, `failure_class=null`; structured log records `worker_finally_entered outcome=normal`, `worker_exit_pop popped=true`, and `worker_exit reason=normal` at `11:28:45Z`.
- Run accounting: 2 completed turns, 633,132 total tokens, 328.1 seconds; final service counts were running=0 and retrying=0.

E2E-3's workspace was removed by normal terminal cleanup before independent inspection. E2E-4 below was added specifically to close that material-evidence gap.

## E2E-4 — material proof before cleanup

- Ticket: E2E-4, created fresh while the same managed service remained live.
- Run ID: `155ba226b2af49e8a0778b3c672e79da`.
- A three-second monitor captured the workspace before Done cleanup.
- Independent test command used the current repository venv Python with bytecode writes disabled. It exited 0 and printed:

```text
PASS: phase_proof() == phase-e2e-ok
```

- Captured source returns exactly `phase-e2e-ok`.
- Captured test contains a plain `assert` under `if __name__ == "__main__"`.
- Captured documentation records the command, output, and exit code.
- Original live-workspace SHA-256 values:
  - `phase_proof.py`: `1e2cf7b3a7d429c93246e2ffb901297d3309a80c058cb217b0664f459f69a295`
  - `test_phase_proof.py`: `1e63846ffa636ab38e184168ace526f4125360881a5331449147e4fd76261ad2`
  - `docs/E2E-4/Document/overview.md`: `40993d38a46ddde6ef529e14bb9cc92c1acdab9a4014e5669d581c2d152a27b7`
- Completion: registry `status=normal`, `state=Done`, `failure_class=null`; structured log records `worker_finally_entered outcome=normal`, `worker_exit_pop popped=true`, and `worker_exit reason=normal` at `11:36:04Z`.
- Run accounting: 1 completed turn, 358,173 total tokens, 261.6 seconds; the monitor ended at `11:36:05Z` with Done, running=0, retrying=0, source/test captured, and documentation captured.

The exact hashes and verification result are retained in this curated Markdown.
Generated scratch source, raw command output, and logs are not committed.

## Live state before teardown

- `GET /`: HTTP 200, HTML admin application returned.
- `GET /api/v1/state`: health `ok`, no degraded reasons, running=0, retrying=0.
- `symphony service status`: running on `127.0.0.1:9998`, PID 15484.
- The scratch board has E2E-1 through E2E-4 in Done.

## Managed-service teardown

The final corrected run proved the real Windows wrapper shape twice:

- Run 1: recorded launcher PID 24420, health-serving PID 7000.
- Run 2 after the fail-closed correction: recorded launcher PID 36976,
  health-serving PID 22184.
- In each run the PIDs differed and the 43-character random instance ID matched
  exactly between the private record and live health response.
- Ordinary `symphony service stop --timeout 1` exited 0 without `--force`.
- Both exact PIDs were absent, port 9998 was closed, and `.symphony/run/`
  contained zero service records.

Classification: **PASS**. The initial failure is retained above as the defect
discovery that changed the design; it is not the final product result.

## Uncovered risk

E2E-4 performed all requested ticket writes during one backend turn. The file-board poller therefore observed `(new) → In Progress → Done`, while the final ticket retained separate In Progress, Verify, and Document notes. This is consistent with the workflow's explicit `stage_contracts: off` warning: rapid worker-authored intermediate states can be coalesced and do not force a backend rebuild. E2E-3 separately exercised the orchestrator-observed phase-transition path, but this shard does not claim that prompt-only workflows mechanically enforce one turn per lane.
