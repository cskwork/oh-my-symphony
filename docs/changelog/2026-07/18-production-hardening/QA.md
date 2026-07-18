# QA - Production hardening

All testing results as succinct plain-language checklist sentences. Evidence lives in `qa/`.

- Verdict: PASS

## Before

- [x] Raw baseline suite passed: 1,384 passed, 6 skipped in 215.36 seconds; two startup tests emitted `aiohttp.web.NotAppKeyWarning` from `server.py:212`.
- [x] Baseline Ruff passed; corrected Pyright interpreter selection passed with 0 errors and 0 warnings; compileall passed.
- [x] The existing coverage artifact passed `coverage report --fail-under=80` at 84%; the final run must recapture coverage from the changed tree.
- [x] Unsandboxed `symphony doctor ./WORKFLOW.md` passed every check against the 16-card linked board; no launch or mutation was performed.
- [x] Disposable Git repro: an excluded `kanban/T-EX.md` descendant merged because the matcher accepted only the literal root.
- [x] Disposable Git repro: capture of an untracked file under `docs-host/` also staged and committed an unrelated tracked modification in that directory.
- [x] Fresh scope-audit Git repro: after staging a previously untracked capture during `merge --no-commit`, plain `git merge --abort` deleted that file; unstaging its exact NUL path manifest before abort preserved its bytes and restored it as untracked. The current commit-failure branch uses the unsafe plain-abort sequence.
- [x] Timer repro: a continuation at attempt 1 with `max_retries=1` and zero slots became retry attempt 2 and emitted `agent_retry_cap_exhausted`.
- [x] Frozen TUI-helper benchmark passed membership checks: the legacy two-scan median was 453.860 ms with 2,000 parses at 1,000 cards and 2,247.174 ms with 10,000 parses at 5,000 cards (five samples after one warm-up, 1,024-byte bodies).
- [x] Isolated build exploration produced wheel and sdist, but plain `uv build` could not write its default cache in the sandbox, wrote `.egg-info` into its source tree, and emitted the deprecated `project.license = { text = ... }` warning; the plan now builds from a temp copy with a temp cache and fixes metadata.
- [x] The frozen package verifier passed Ruff and byte-compilation, then failed RED on the expected `SetuptoolsDeprecationWarning` in its disposable source copy; it cleaned that copy and did not add `.egg-info` to the real worktree.

## Results

- [x] Owning suites passed independently: auto-merge 21 in 47.95 seconds; retry/dispatch 212 in 19.11 seconds; server/Web API 48 in 1.15 seconds; TUI/file tracker 112 in 24.90 seconds; package metadata 1 in 0.16 seconds.
- [x] Full changed-tree gate passed: 1,415 passed, 6 skipped in 148.57 seconds with 84.18% coverage; Ruff passed; Pyright reported 0 errors and 0 warnings.
- [x] Frozen benchmark passed exact ordering and parse-count checks: 1,000 cards used 1,000 parses with a 229.467 ms median, 0.5056 of the 453.860 ms baseline; 5,000 cards used 5,000 parses with a 1,200.783 ms median, 0.5344 of the 2,247.174 ms baseline. Both are below the 0.65 ceiling.
- [x] Package verifier passed: wheel/sdist, SPDX `Apache-2.0`, `LICENSE` and `NOTICE`, installed CLI/static resource, warning rejection, and no `.egg-info` pollution.
- [x] Workflow doctor passed every check when rerun outside the filesystem sandbox. The first sandboxed attempt failed only its external writable-workspace probe and is treated as an environment false negative, not a product pass.
- [x] CLI tester evidence passed audit: installed-artifact doctor, warning-as-error ephemeral startup, exact `ok` health, all 9 API smoke checks, smoke-card deletion, SIGTERM exit 0 with `shutdown_complete`, closed port, zero active/lease-holding runs, temp cleanup, and unchanged 16-ticket linked-board manifest.
- [x] All six DEBUG triplets passed: literal exclusion boundaries, exact NUL-manifest capture rollback, retry timer ownership, typed AppKey startup, TUI parse/order performance, and wheel/sdist metadata.
- [x] Independent AST verification covered 18 changed Python files and all 133 added/changed functions: every function is at most 50 lines and nesting is at most 4. The repaired lifecycle harness has 30 functions; its longest is `_run_lifecycle` at 47 lines and its maximum nesting is 1. Harness Ruff and byte-compilation passed.
- [x] Changed-symbol reconciliation found consumer coverage for all frozen production symbols across dispatch/timer/worker-exit, public auto-merge, server CLI/middleware, TUI refresh, and package build paths. The final scope is 10 production/config files, 6 test files, and the approved evaluator/vault artifacts; no orphan change was found.
- [x] The frozen TUI contract is preserved: compatibility helpers and package-level underscore re-exports remain, while app tests intentionally use `_fetch_tracker_snapshot`. Private attributes on `symphony.tui.app` were not promised by the plan and are not treated as a public compatibility break.
- [x] Fresh safety review found no unresolved high-severity issue introduced in the changed surface. Healthy registry acquisition rechecks ownership atomically; the unchanged registry-error fail-open behavior and non-atomic external tracker reads remain explicitly accepted residuals, not claims of this patch.
- [x] Final hygiene passed: `git diff --check`; no staged/source-tree build pollution or high-confidence secret match; `.domain-agent/` remains ignored; the linked board is byte-identical; the original checkout still contains only its two pre-existing untracked documents.

Backward-trace: clean

## DEBUG Gate

```text
GATE.owner.exclusion=tests/test_auto_merge.py::test_auto_merge_exclusions_use_literal_pathspec_boundaries
GATE.alt_repro.exclusion=direct git --literal-pathspecs descendant/metachar/tab-newline/prefix boundary: pass
GATE.conformance.exclusion=expected changed roots rc=1 and prefix near-miss rc=0; actual=1/1/1/0
GATE.owner.capture=tests/test_auto_merge.py::test_auto_merge_partial_capture_add_failure_restores_repo + test_auto_merge_commit_hook_failure_restores_captured_files
GATE.alt_repro.capture=standalone generated merge script with failing commit hook and NUL manifest: pass
GATE.conformance.capture=expected exact HEAD/MERGE_HEAD/status/index/worktree/file-byte restoration; actual=equal
GATE.owner.retry=tests/test_orchestrator_dispatch.py::test_retry_eligibility_classifies_transient_and_durable_outcomes
GATE.alt_repro.retry=real timer-state one-slot unresolved blocker then blocker dispatch: pass
GATE.conformance.retry=expected attempt/kind/claim preserved with non-slot wait and no cap escalation; actual=preserved
GATE.owner.appkey=tests/test_server_routes.py::test_run_server_uses_typed_aiohttp_application_key
GATE.alt_repro.appkey=installed-wheel startup with PYTHONWARNINGS=error and exact health ok: pass
GATE.conformance.appkey=expected one typed AppKey singleton and unchanged Host guard; actual=identity shared and 48 route tests pass
GATE.owner.tui=tests/test_tui.py::test_app_file_refresh_parses_each_ticket_once
GATE.alt_repro.tui=frozen 1000/5000-card changed-tree benchmark: pass
GATE.conformance.tui=expected N parses and baseline order hashes; actual=1000/5000 with exact hashes
GATE.owner.packaging=tests/test_package_metadata.py::test_pyproject_declares_pep639_license_metadata
GATE.alt_repro.packaging=direct wheel-and-sdist archive inspection: pass
GATE.conformance.packaging=expected SPDX Apache-2.0 plus LICENSE/NOTICE and no deprecation warning; actual=exact
```

## Commands

| Command | Source | Proves |
|---|---|---|
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q` | frozen_repo | Repository behavior baseline |
| `/opt/anaconda3/bin/python -m ruff check src tests` | frozen_repo | Static lint gate |
| `/opt/anaconda3/bin/python -m pyright --pythonpath /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python` | frozen_repo | Source type gate with project dependencies |
| `/opt/anaconda3/bin/python -m coverage report --fail-under=80` | frozen_repo | Existing baseline coverage artifact clears the CI floor |
| `symphony doctor ./WORKFLOW.md` | frozen_repo | Operator configuration validity |
| `PYTHONPATH=src /opt/anaconda3/bin/python docs/changelog/2026-07/18-production-hardening/qa/benchmark_file_board_snapshot.py --cards 1000 5000 --samples 5 --warmups 1 --body-bytes 1024 --expect-implementation legacy_two_scan` | evaluator | Exact current TUI helper latency, parse count, and ordered issue membership |
| `/opt/anaconda3/bin/python docs/changelog/2026-07/18-production-hardening/qa/verify_package.py --source .` | evaluator | Isolated build/install, warning, SPDX metadata, legal files, CLI/resource, and source-pollution gate |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/test_auto_merge.py -k 'literal_pathspec_boundaries or stages_only_untracked or partial_capture_add_failure or commit_hook_failure'` | agent_detected | Literal excluded-root decisions and exact NUL-manifest capture rollback regressions |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/test_dispatch_state.py tests/test_orchestrator_dispatch.py -k 'only_slot_holding or retry_eligibility_classifies or standard_backoff_retry_owns_slot or capacity_wait_preserves or poll_failure_preserves or releases_durable_rejection or preserves_active_owner or waits_for_unresolved_blocker_then_recovers or non_slot_blocker_wait or reparks_paused_ticket'` | agent_detected | Retry classification, attempt/kind preservation, slot ownership, blocker progress, and durable release |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/test_server_routes.py::test_run_server_uses_typed_aiohttp_application_key` | agent_detected | Warning-as-error typed aiohttp application-key startup |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/test_tui.py -k 'fetch_tracker_snapshot or app_file_refresh'` | agent_detected | One-scan file snapshot, ordered partition, mutation visibility, and close safety |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/test_package_metadata.py` | agent_detected | PEP 639 build-floor, SPDX, and legal-file metadata contract |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/test_auto_merge.py` | agent_detected | Complete auto-merge compatibility suite |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/test_dispatch_state.py tests/test_orchestrator_dispatch.py` | agent_detected | Complete retry/dispatch compatibility suites |
| `/opt/anaconda3/bin/python -m ruff check src tests` | agent_detected | Full changed-tree lint gate |
| `/opt/anaconda3/bin/python -m pyright --pythonpath /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python` | agent_detected | Full changed-tree type gate with project dependencies |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` | agent_detected | Full repository behavior and recaptured coverage floor |
| `PYTHONPATH=src /opt/anaconda3/bin/python docs/changelog/2026-07/18-production-hardening/qa/benchmark_file_board_snapshot.py --cards 1000 5000 --samples 5 --warmups 1 --body-bytes 1024 --expect-implementation single_snapshot --baseline-json docs/changelog/2026-07/18-production-hardening/qa/benchmark-before.json --max-ratio 0.65` | agent_detected | Changed-tree parse count, ordered membership, and frozen latency ratio |
| `/opt/anaconda3/bin/python docs/changelog/2026-07/18-production-hardening/qa/verify_package.py --source .` | agent_detected | Changed-tree wheel/sdist metadata, install, resource, warning, and pollution gate |
| `/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/symphony doctor ./WORKFLOW.md` | agent_detected | Operator workflow, shell, agent, hooks, workspace, tracker, and viewer checks |
| `git diff --check` | agent_detected | Patch whitespace hygiene |
| `bash /Users/danny/Documents/PARA/Resource/supergoal-skill/templates/qa-gate.sh docs/changelog/2026-07/18-production-hardening cli` | qa_auditor | Literal CLI evidence gate |
| `AST changed-function line/nesting scan over tracked and untracked Python files` | qa_auditor | Repository `<=50` lines and `<=4` nesting rule |

## QA

Tool: CLI
Action count: 6 grouped CLI/API actions (build-install, doctor, start-health, API smoke, SIGTERM, teardown assertions).
- [x] Installed-artifact build/install: isolated `uv build` and fresh-venv `uv pip install` exited 0; wheel `oh_my_symphony-0.14.0-py3-none-any.whl` SHA-256 was `3e2c0e3f3dad68bd740969150bfaf1691b82f1ec706479c0857ecca6b7e45a92`; no Setuptools deprecation warning appeared.
- [x] Operator scenario: the installed `symphony doctor` exited 0 against the disposable workflow and passed port, shell, agent CLI, prompt, hook, writable workspace, and empty file-board checks. It emitted one non-fatal warning that the separate legacy board-viewer script was absent from the disposable runtime root; the built-in HTTP surface was exercised directly below.
- [x] Startup/health scenario: installed `symphony` ran with `PYTHONWARNINGS=error`, parsed ephemeral port `59884` from its own output, and returned HTTP 200 with health status exactly `ok` on the first successful probe.
- [x] API golden/regression scenario: `scripts/smoke_web_api.py` exited 0 with unique prefix `QAPH46BF66FB`; all 9 checks passed (health, state, board, static asset, create, detail, patch, refresh, workflow/stats/skills).
- [x] API cleanup/shutdown edge scenario: an external board/API probe found zero prefixed smoke cards; SIGTERM produced exit 0 and `shutdown_complete`; port `59884` was closed on the first post-exit connection attempt; `.symphony/state.db` contained 0 active and 0 lease-holding runs.
- [x] Isolation/teardown scenario: exact sorted relative-path plus SHA-256 manifests for all 16 original linked-board tickets matched before/after (`0e9be1b8057a69cb3d7d59d91c9f73f29d7369c2d883102885527c07a7dd91a9`); the evaluator temp source, venv, workflow, board, workspace, and registry root were removed.
- Served URL: `http://127.0.0.1:59884` (disposable; confirmed closed after teardown).
- Repeatable command: `PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python docs/changelog/2026-07/18-production-hardening/qa/runtime_lifecycle_harness.py --source . --evidence-dir docs/changelog/2026-07/18-production-hardening/qa`.
- Evidence: `qa/runtime-lifecycle.json`, `qa/runtime-{build,venv,install,doctor,server,smoke}.txt`, `qa/original-board-{before,after}.json`, and `qa/runtime_lifecycle_harness.py`.
- Impact Matrix covered: installed CLI/package invocation, disposable workflow preflight, foreground startup, exact health, built-in HTTP CRUD/static/refresh contracts, API card cleanup, graceful signal shutdown, socket closure, registry lease cleanup, linked-board isolation, and evaluator temp cleanup.
- Impact Matrix uncovered: real worker dispatch/descendant cleanup, managed service and standalone viewer, browser/TUI interaction, and live Jira/Linear adapters; no evidence claim is made for those groups.

## Reproduction Fidelity

- Fidelity level: synthetic-representative
- Residual risk from data gap: Git behavior uses disposable real repositories and retry behavior uses the real timer/state transition with injected tracker boundaries. The installed runtime smoke intentionally uses an empty file board and does not dispatch a worker.
- Post-deploy confirmation plan: Run a separately scoped live Jira/Linear check and real worker/descendant cleanup scenario before claiming those external layers; neither is required by this patch's frozen compatibility contract.

## Residual Risk

- Blocking: none.
- Accepted lease residual: healthy SQLite acquisition rechecks ownership under `BEGIN IMMEDIATE`, so the changed path does not create duplicate leases. A losing advisory eligibility check can defer a retry until a later tick/prune, and the pre-existing registry-error path can dispatch without a lease across processes. That fail-open policy is unchanged and needs a separately scoped registry-reliability design.
- Accepted external-adapter residual: Jira and Linear preserve their two sequential queries on one client and intentionally do not claim an atomic snapshot. Their fake-client and 83 adapter tests passed, but live JQL/GraphQL, authentication, pagination, rate-limit, and cross-query mutation behavior were not exercised.
- Accepted runtime/evaluator residual: real worker descendant cleanup, managed service/viewer, and browser/TUI interaction were not exercised. The successful lifecycle used an empty board, so its zero-row registry proof does not prove worker-lease cleanup. Readiness uses bounded port and health waits sequentially, and cleanup-failure evidence finalization was not fault-injected.
- Scope decision: the frozen TUI plan retains helper and package-level underscore re-exports and moves the app monkeypatch seam to `_fetch_tracker_snapshot`; it does not promise private `symphony.tui.app._fetch_candidates` or `_fetch_terminals` attributes. No compatibility scope was expanded during QA.
