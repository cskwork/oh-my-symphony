### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

**Allowed tools (advisory).** Read full repo, diff, payload sources (DB / schema / OpenAPI), ticket body. Write only under `docs/{{ issue.identifier }}/qa/` and as ticket comments. Run boot recipes, payload replay (curl / httpie / `requests`), `pytest` / `playwright`, and the repro spec. Do NOT edit production code — failures rewind to In Progress.

Execute real code against both builds and prove the diff works. Inspecting the diff is not QA.

1. Read `docs/{{ issue.identifier }}/work/` and the latest `## Review` / `## Review Findings` for the intended delivery.

2. Map the API surface from the diff: method, path, auth, request schema, response schema. Append as `## API Surface`. No API → jump to **Non-API fallbacks**.

3. Source 3-5 realistic payloads (happy / edge / invalid):
   - Preferred: a database MCP tool (`mcp__*postgres*`, `mcp__*mysql*`, `mcp__*sqlite*`, `mcp__*mssql*`, `mcp__*bigquery*`, `mcp__*mongodb*`) or the `database` skill — inspect schema, sample rows with `SELECT ... LIMIT`.
   - Fallback: synthesize from schema / model / DTO / migration / OpenAPI files; tag each file `"_source": "synthesized from <path>"`.
   - Save as `docs/{{ issue.identifier }}/qa/payloads/<scenario>.json`. Mask PII. Never invent fields the schema does not declare.

4. Boot As-Is and To-Be:
   - If `qa.boot.command` is set in `WORKFLOW.md`: run it with `SYMPHONY_QA_PORT` exported from `qa.boot.asis_port` / `qa.boot.tobe_port`, merge `qa.boot.env`, bring up `qa.boot.compose_file` first if specified. Else use the project's standard boot on two free ports.
   - As-Is = `git config symphony.basesha` checked out via `git worktree add ../asis $(git config symphony.basesha)`. To-Be = current HEAD.
   - If `qa.boot.health_url` is set: poll `${url//\$\{PORT\}/<port>}` until 200 or fail QA.

5. Replay every payload against both builds, capturing status, body, headers of interest, and `latency_ms` (wall-clock). Save raw to `docs/{{ issue.identifier }}/qa/runs/<scenario>.{asis,tobe}.json` with `latency_ms` at top level. Tear down both servers and `git worktree remove ../asis`.

6. Diff and judge:
   - Write per-scenario diff to `docs/{{ issue.identifier }}/qa/diff/<scenario>.diff`. Confirm only the intended change — no surprise renames, leaked PII, broken unrelated scenarios, or status regressions on invalid/unauthorized rows.
   - Performance gate from `qa.regression_budget`: for each scenario where As-Is `latency_ms` ≥ `min_baseline_ms`, fail if To-Be `latency_ms` > `latency_factor × As-Is`. Record breach as `scenario | as-is ms | to-be ms | factor`. `latency_factor: 0` disables.

7. Bug repro closure (`bug` label only): re-run `docs/{{ issue.identifier }}/reproduce/repro.spec.ts` against To-Be, save to `docs/{{ issue.identifier }}/qa/repro-after.log`; it must pass. Never skip.

8. Append `## QA Evidence`: payload data source (DB tool + query, or `synthesized from <schema file>`), boot recipe, exact commands with exit codes, a `scenario × {As-Is status, As-Is ms, To-Be status, To-Be ms, verdict}` matrix, the repro re-run line for `bug` tickets, and paths under `docs/{{ issue.identifier }}/qa/`. Inside the same section add the required `## AC Scorecard` sub-block:
   `signal | source (Plan ## Done Signals row) | result (pass/fail) | evidence path`
   Every row in Plan's `## Done Signals` must appear. A missing or `fail` row fails QA — proceed to step 9.

9. On any failure (correctness, latency, repro, or any server-reported HIGH issue): set state to `In Progress`, append `## QA Failure` naming the scenario and exact field/status/latency/severity that regressed, stop. Never silence, retry, or skip.

10. On pass: set state to `Learn`.

---

**Non-API fallbacks** (only when step 2 finds no API surface):
- Tests: run the full suite (`pytest -q`, `npm test`, `pnpm test`, `go test ./...`, `mvn test`, `cargo test`). All must pass.
- Web UI: write a Playwright (or Cypress) spec at `docs/{{ issue.identifier }}/qa/e2e.spec.ts`; save traces, videos, HAR under `docs/{{ issue.identifier }}/qa/`.
- CLI: run the command, assert exit code and stdout/stderr / file output, save to `docs/{{ issue.identifier }}/qa/cli.log`.

Step 7 (bug repro closure) still applies in non-API mode if `reproduce/repro.spec.ts` exists.
