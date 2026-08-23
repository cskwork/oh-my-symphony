# QA report - phase-transition refactor and Windows service stop

- Date: 2026-08-23
- Target: Windows working tree based on `1a5a7cece37032be333f4efb1050fb5561290793`
- Comparison: functional
- Verdict: PASS
- Browser actions: 25 / 100

## Plain-language result

The phase-transition refactor preserves the observed pipeline behavior, and
ordinary Windows managed-service stop now closes the real two-process venv
launcher/orchestrator shape without requiring `--force`. No test failures,
lint errors, type errors, live-service leftovers, or blocking review findings
remain.

## Impact coverage

- Real OpenCode pipeline: E2E-3 crossed Todo -> In Progress -> Verify ->
  Document -> Done with an orchestrator-observed phase transition; E2E-4
  independently produced and executed source/test/documentation proof.
- Live browser: board, issue, run, refresh, diagnostics, and state APIs passed;
  all checked application APIs returned 200. The only console observation was
  a non-breaking favicon 404.
- Native browser: 5 passed.
- Native orchestrator/lifecycle/release shard: 77 passed, 18 declared Windows
  symlink-capability skips.
- Full repository gate with browser E2E enabled: 2311 passed, 80 declared
  platform/capability skips, 0 failed, 0 errors in 622.41 seconds. JUnit reports
  `tests=2391`, `failures=0`, `errors=0`.
- Scratch doctor: PASS with only the workflow's intentional
  `agent.stage_contracts: off` warning.
- Live ordinary service stop: record launcher PID and health-serving PID were
  different, their exact random instance IDs matched, stop exited 0, both PIDs
  disappeared, port 9998 closed, and the record was cleared.

## Defect found and fixed during E2E

Initial live testing showed the service record holds the Windows venv launcher
PID while `/health` is served by its Python child. A direct PID-equality design
therefore failed closed without fixing ordinary stop. The final design creates
a bounded random ID per launch, passes it only in the copied child environment,
and exposes it with the serving PID in health. Automatic force is authorized
only by exact canonical workflow + exact instance ID and targets only that
proved serving PID; it never automatically force-kills the reusable recorded
launcher PID. Missing, malformed, stale, legacy, or unreachable identity proof
retains the record and performs no automatic force.

## Declared limits

- The 80 full-suite skips are explicit Windows/POSIX capability cases,
  principally unavailable symlink privilege and POSIX-only process semantics.
- Real Codex/Claude/Gemini/AGY/Kiro/Pi/Prime-Agent pipelines and Linear/Jira
  were not run because isolated credentials/projects were not supplied.
- Linux/macOS execution remains a complementary CI/platform gate.
- A narrow PID-incarnation race remains between the proved health response and
  Windows termination. Removing it requires a process handle or creation-time
  identity and is outside this scoped fix.

## Evidence policy

Only curated Markdown/JSON summaries are retained. Raw pytest logs, JUnit,
scratch worker files, and generated browser screenshots remain outside Git in
test-owned temporary directories, as required by `CONTRIBUTING.md`.
