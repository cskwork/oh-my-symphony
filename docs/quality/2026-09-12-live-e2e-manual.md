# Live E2E and manual review, 2026-09-12

Base: `dev` at `ad62d41`. Scope: complete existing E2E sets, one isolated real-provider delivery, demonstrated defects, and English/Korean Pages manuals. No tag or GitHub Release was requested. Remote delivery remains the coordinator's responsibility.

## Existing E2E sets

```text
SYMPHONY_BROWSER_E2E=1 .venv/bin/pytest -q \
  tests/test_agent_lifecycle_e2e.py tests/test_deep_preset_e2e.py \
  tests/test_web_browser_e2e.py tests/test_webapi.py \
  tests/test_web_api_smoke_script.py
115 passed in 39.77s
```

These cover the default lifecycle, per-phase backend rebuilds, deep request/DAG release, real Chromium board/chat/settings/stat flows, and API input/error paths. The deep tests explicitly reject Review without PASS and keep Build blocked until the request reaches Done. Their backend is controlled; they are not real-provider deep-pipeline evidence.

## Real provider and service proof

Created a separate Git project and project registry under `/private/tmp/symphony-live-e2e-20260912`. Doctor passed before service launch. Service PID 81761 used port 18763, independent of the user's port 9999 service. Codex CLI 0.154.0 reported `gpt-6-astra`, with `low` reasoning in each session's original turn metadata.

The bounded task implemented `greet(name)` and four standard-library unittest cases. Configuration retained the default four lanes and automatic mechanical contracts, but used a concise scope-specific shared prompt, disabled automatic Todo triage to exercise the real Todo lane, skipped dependency installation, and selected a local-only merge into scratch `main`. No remote was configured or pushed. Stage prompts remained the shipped prompts. No subagents were requested by the task.

| Checkpoint | Observed result, UTC |
| --- | --- |
| Intent proposal | Real provider returned a pending proposal at 04:19:10. Board had zero tickets before approval, independently confirmed in the browser. |
| Approval | Authorized approval action created `REQ-1` at 04:21:22. |
| Todo → In Progress | 04:22:17, fresh phase session. |
| In Progress → Verify | 04:23:53, implementation and required work evidence present. |
| Verify → Document | 04:25:47, QA and merge preflight evidence present. |
| Done | 04:27:28, normal worker exit after four turns. No additional human approval. |
| Local merge | 04:27:30, exactly one merge; `push_target=false`. |

Scratch main commit `fa851d6606ed9b6e03d08e4a2519d7b7c11a793f` has parents `09b63b00adbf271fc8a52c38f8ed3c5782b14027` and task commit `0946871cb6e6adb572f554583119ff4cbc2dd426`. `git rev-list --count --merges 09b63b0..main` returned 1. Independent `python3 -m unittest -v` on merged main passed all four cases. The board showed Done 1, running 0 and retrying 0. The scratch service was then stopped; its repository and evidence remain available locally.

The Verify scorecard honestly marked future wiki/Done/merge evidence as pending; this appeared as the existing soft scorecard warning. It did not fabricate a completed merge before the host performed it. Required missing-output rejection is separately covered by the controlled E2E tests above; this successful real run did not need a contract rewind.

## Demonstrated defects fixed

1. **Codex token subsets were counted twice.** The live intent session's original record reported input 31,080, cached input 12,928, output 269, total 31,349. Symphony displayed total 44,277 by adding cached input again. Its v2 adapter also added reasoning output again. The official [Codex response conversion](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/sse/responses.rs#L130-L156) copies inclusive input/output totals and reads cache/reasoning from their detail fields. Actual worker records also contained nonzero reasoning counts of 7, 55, 25 and 45. The adapter now retains the inclusive input/output totals and their sum, preserving its three-key interface and cumulative-overwrite behavior.
2. **The first-ticket hint appeared beneath a completed ticket.** The independent real browser showed the Done card and "No tickets yet" together. The predicate checked only active rendered lanes. It now checks whether the entire board is empty. A Chromium regression failed before the change and passed afterward, also verifying that a truly empty board retains the hint.
3. **Public guidance contradicted current behavior.** Manuals implied mandatory Human Review and a manual/Verify-stage merge. They now explain intent approval, automatic completion, exception holds, the deep request-to-DAG split and the single configured host merge. The homepage's repeated Prime Agent name was removed.
4. **Manual tables overflowed mobile screens.** At width 390, measured document widths were 482–516 pixels. Narrow-screen table wrapping now keeps all four manuals at 390 pixels. Print rules retain code blocks and tutorial tables together; the cheatsheet avoids a mostly empty extra page.

Token correction applies to newly processed notifications. Historical persisted statistics, including this already-running E2E service's values, were not migrated or rewritten. The corrected result is established by captured-event replay and tests, not by claiming that the old service's displayed counters changed.

## Provider usage, not billing

Original Codex session records for the four worker phases, excluding the separate intent chat:

| Field | Original provider total |
| --- | ---: |
| Input, including cached input | 1,219,476 |
| Cached input subset | 1,097,088 |
| Output, including reasoning | 8,255 |
| Reasoning output subset | 132 |
| Total | 1,227,731 |

The old service displayed 2,324,951 tokens, exactly the original total plus cached input and reasoning subsets. The separate intent chat used 31,349 total tokens. These are cumulative context-traffic counts, not a financial cost estimate.

## Verification and artifacts

- Token regression RED: 2 failures, including replay of the captured live usage. GREEN: `tests/test_backends.py tests/test_backend_contract.py`, 237 passed, 3 skipped in 11.70s.
- Terminal-only hint RED: 1 browser failure. GREEN: 1 passed, 6 deselected in 7.50s. The coordinator independently reloaded the real Done board and confirmed the misleading hint was absent.
- Ruff, Pyright and translation checks passed; translations cover 575 English/Korean keys. `scripts/sync_manual_tables.py --check` and `git diff --check` passed.
- Local Chromium checked the homepage in both languages for version 0.24.0, current workflow text and one Prime Agent entry. All four manual pages had no horizontal document overflow at widths 1440 and 390. No page JavaScript errors were observed.
- Four PDF manuals were rendered with `scripts/build_manual_pdf.py` and visually reviewed as page images: each cheatsheet has 3 pages and each tutorial has 8 pages. They remain generated, ignored files under `docs/manual/pdf`; Pages regenerates them from the reviewed HTML/CSS.
- Local evidence: `/private/tmp/symphony-full-e2e-20260912.log`, `/private/tmp/symphony-live-e2e-20260912/`, `/private/tmp/symphony-doc-browser-check.log`, `/private/tmp/symphony-manual-renders/reviewed/`. Independent board captures: `/private/tmp/symphony-e2e-before-approval.png`, `/private/tmp/symphony-e2e-after-approval.png`, `/private/tmp/symphony-e2e-done.png`, `/private/tmp/symphony-e2e-done-fixed.png`. Capability tokens and full provider transcripts are not included in this report.

Final full CI coverage passed with process-inspection access:

```text
.venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80
2562 passed, 15 skipped in 502.74s
Total coverage: 85.44% (required 80%)
Exit: 0
```

The new terminal-only browser regression was added after this run's collection and was executed separately with Chromium, as recorded above. Browser tests are opt-in and skipped by the standard CI command. Final manual-table tests also passed: 2 passed in 0.73 seconds. Full log: `/private/tmp/symphony-e2e-final-coverage.log`.

## Limits

The real-provider proof covers a bounded Python task through the default workflow. It does not establish real-provider deep-DAG execution, every backend, remote tracker mutation, public deployment, or app-release verifier/finalizer behavior. Tripwire heuristics also matched the word "authorized" via the broad `auth` pattern, promoting this proposal from micro to full; that documented heuristic behavior was observed, not changed. This pass does not claim absence of other defects.


## Follow-up: terminal cancellation and recovery dependencies

Concurrent `origin/dev` handoff evidence at `0d05096` described an archived FIX worker retaining the sole dispatch slot through pause/retry. The finding was reproduced on the integrated branch using an actual asyncio task cancelled by `_reconcile_running`, with controlled tracker responses. Archive, Cancelled and Blocked cases all failed before the fix: 3 failed, 2 passed. The five new cases include reopened-active and missing-refresh counterexamples.

Worker exit now refreshes tracker state after the existing task-ownership pop guard. A cancellation that is still terminal releases its retry/claim and does not create a new pause. Reconcile retains workspace-cleanup ownership; cancellation does not become success or invoke merge/Done hooks. If the fresh card is active, or cannot be read, existing pause/retry behavior is preserved. Existing operator pause flags are not cleared. Focused cancellation, stale-owner and reconciliation tests passed: 12 passed, 264 deselected in 1.28 seconds.

The separate recovery dependency behavior was documented, not bypassed. `auto_recover_blocked: false` stops opening new FIX work; it does not erase blockers or disable completed-FIX evidence checks. The pipeline guide and both tutorials describe resolving the FIX normally, or deliberately cancelling/archiving unused FIX work and removing its reviewed dependency edge before manual rerun. No additional provider run was used for this follow-up.

Follow-up final CI coverage passed, exit 0: `2567 passed, 16 skipped in 497.92s`, coverage **85.45%**, exceeding the required 80%. Command: `.venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80`. Log: `/private/tmp/symphony-terminal-cancel-coverage.log`. Ruff, Pyright, translation and manual-table synchronization checks passed. Both revised tutorial PDFs remain eight pages; the new recovery row and footer were visually inspected together without clipping. The final tutorial images and PDF hashes were regenerated under `/private/tmp/symphony-manual-renders/reviewed/`.
