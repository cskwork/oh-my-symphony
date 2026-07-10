### VERIFY -- prove it and merge safely

**Allowed tools (advisory).** Read full diff, tests, `docs/{{ issue.identifier }}/work/`, and ticket sections. Write tests/evidence under `docs/{{ issue.identifier }}/qa/` and ticket comments. Run real commands. You may run Merge Gate commands. Do NOT make unrelated source edits.

Verify has three jobs: review, QA, and merge preflight/merge. Goal for this lane: make a yes/no delivery decision a human can trust. The card must say what worked, what failed, what is not covered, how to re-run the proof, and whether the branch merged cleanly.

1. Review the diff against the ticket, `## Plan`, `## Acceptance Tests`, and `## Done Signals`. Check that the implementation still matches the user's goal and did not add orphan scope.
2. Append `## Security Audit` with exactly 7 rows: secrets, input-validation, injection, xss, csrf, authz, rate-limit. Use `pass`, `fail`, or `n/a`; evidence must point to durable `qa/...` or `work/...` artifacts when a row needs proof. `n/a` rows may carry a short reason instead; every `pass`/`fail` row must cite the artifact (e.g. `qa/security-audit.md`), never a source anchor like `todo.py:54`.
3. If any CRITICAL/HIGH/MEDIUM issue exists, append `## Review Findings` as a severity table with problem, evidence path, requested fix, and scope. Set state to `In Progress`, and stop. Otherwise append `## Review` with the clean-review reason.
4. Run the real acceptance checks. Save durable proof under `docs/{{ issue.identifier }}/qa/`. Write evidence like a short QA report:
   - What worked.
   - What did not work.
   - What was not covered or remains `Not proven`.
   - How to re-run the check.
   - For trivial non-runtime changes, the QA half may be short: run the relevant static/content check and explain why no runtime path changed.
   - Browser UI work must drive Playwright/headless Chromium against `file://` or a tiny static server for core flows (add/toggle/edit-cancel via Escape/delete/filter/reload persistence as applicable). DOM shims are smoke only, never final Verify authority.
   - Test the exact declared launch path. If the app claims direct `file://` support, fail on module-script/CORS boot errors instead of switching to HTTP.
   - If browser deps are unavailable, append `## Environment Block` naming what is missing, set state to `Blocked`, and stop.
   - For bugs, close the reproduction loop by saving `docs/{{ issue.identifier }}/qa/repro-after.log`.
   - Full integration gate for app-delivery or release/integration tickets: run against the committed target branch, not an unmerged worker branch. Confirm prerequisite tickets are integrated and, when an upstream exists, compare local and remote SHAs before testing. Run clean install/build, declared start command, readiness probe, core customer workflows, and console/network/server review. If anything fails, append `## Integration Defects`, register new Kanban/board bug tickets with repro steps, logs, expected behavior, fix boundary, and verification commands, add those IDs to this ticket's `blocked_by`, set state to `Blocked`, and stop. When blockers complete, rerun from scratch; do not move to Learn or Human Review until the merged target passes.
5. Append `## QA Evidence` with a command manifest: command, exit code, evidence path, what it proves, what it does not prove, and how to re-run.
6. Append `## AC Scorecard` with one row per acceptance criterion: signal, source, result, evidence path.
   - Evidence cells must cite files under `docs/{{ issue.identifier }}/` as `qa/...` or `work/...`. Put source anchors and prose inside those evidence files, not in the table cell.
   - Valid evidence cells: `qa/pytest.log` — or `qa/evidence.md` (secrets section) when a qualifier helps. Invalid: `todo.py:54`, `README.md:13`, or bare prose such as "No secrets in code." — record those inside the cited artifact instead.
7. If any required command fails or evidence disproves an AC, append `## QA Failure`, set state to `In Progress`, and stop.
{% if agent.auto_merge_on_done %}
8. Merge Gate:
   - Resolve target in order: `agent.auto_merge_target_branch`, `agent.feature_base_branch`, current host branch.
   - Run `git merge-tree --write-tree <target-branch> symphony/{{ issue.identifier }}` from the host repo. Save output to `docs/{{ issue.identifier }}/qa/merge-tree.log`.
   - Do not use `git status -uno --porcelain` as merge proof. Dirty host worktree is a separate safety check, not committed-branch conflict proof.
   - If committed target/branch merge conflicts exist: set state to `Blocked`, append `## Merge Failure` with exact command, target branch, and conflicted paths, then stop.
   - If clean: check whether host dirty tracked files overlap `git diff --name-only <target-branch>..symphony/{{ issue.identifier }}`. Block only on actual overlap or workspace-only path changes.
   - If safe: create the explicit `--no-ff` merge commit on the target branch and record target branch, feature branch, command, and merge SHA under `## Merge Status`.
{% else %}
8. Merge Gate is disabled (`agent.auto_merge_on_done` is false). Append `## Merge Status` noting this workflow intentionally leaves branch integration to the operator.
{% endif %}
9. Set state to `Learn`.
