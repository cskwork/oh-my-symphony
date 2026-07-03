### VERIFY -- when state is `Verify`

**Allowed tools (advisory).** Read full diff, tests, `docs/{{ issue.identifier }}/work/`, and ticket sections. Write tests/evidence under `docs/{{ issue.identifier }}/qa/` and ticket comments. Run real commands. You may run Merge Gate commands. Do NOT make unrelated source edits.

Verify has three jobs: review, QA, and merge preflight/merge.

1. Review the diff against the ticket and `## Acceptance Tests`.
2. Append `## Security Audit` with exactly 7 rows: secrets, input-validation, injection, xss, csrf, authz, rate-limit. Use `pass`, `fail`, or `n/a`.
3. If any CRITICAL/HIGH/MEDIUM issue exists, append `## Review Findings` as a severity table, set state to `In Progress`, and stop. Otherwise append `## Review`.
4. Run the real acceptance checks. Save durable proof under `docs/{{ issue.identifier }}/qa/`.
   - For trivial non-runtime changes, the QA half may be short: run the relevant static/content check and explain why no runtime path changed.
   - Browser UI work must drive Playwright/headless Chromium against `file://` or a tiny static server for core flows (add/toggle/edit-cancel via Escape/delete/filter/reload persistence as applicable). DOM shims are smoke only, never final Verify authority.
   - If browser deps are unavailable, append `## Environment Block` naming what is missing, set state to `Blocked`, and stop.
   - For bugs, close the reproduction loop by saving `docs/{{ issue.identifier }}/qa/repro-after.log`.
5. Append `## QA Evidence` with commands, exit codes, and top evidence paths.
6. Append `## AC Scorecard` with one row per acceptance criterion: signal, source, result, evidence path.
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
