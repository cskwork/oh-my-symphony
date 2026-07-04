# 2026-07-05 - v0.10.0 release decision

## Goal

Merge the verified `dev` branch to `main` and publish a new minor release with
release notes that explain the reliability improvements, not just the version
bump.

## Decision

Bump `0.9.3` to `0.10.0` because the post-0.9.3 changes add runtime controls
and operator-visible safety behavior: state-local turn watchdogs, token
attention telemetry, prompt-context compaction, workspace ownership checks,
strict contract-failure scope, doctor warnings, and the issue-detail JSON
serialization fix.

- Rejected: `0.9.4`. The change set is larger than a patch because workflows
  can now opt into new runtime controls and the release changes how reliability
  failures are surfaced to operators.
- Rejected: tagging without synchronizing version surfaces. The package version,
  CLI `__version__`, public Pages badge, changelog, annotated tag, and GitHub
  release should agree.
- Rejected: a merge commit from `dev` to `main` when a fast-forward is possible.
  The branch history is linear, so fast-forwarding keeps release provenance
  simpler.

## Planned verification

- Confirm the release tag does not already exist.
- Run focused version/changelog checks on `dev` before committing the release
  bump.
- Push `dev`, then fast-forward `main` only after confirming `origin/main` is an
  ancestor of `origin/dev`.
- Run the full test suite on `main`.
- Create an annotated `v0.10.0` tag on the verified `main` commit.
- Publish GitHub release notes from the checked commit range and verify the
  remote tag, peeled tag target, and release metadata.

---

# 2026-07-05 - Default prompt compaction on

## Goal

Make prompt compaction the default behavior instead of requiring every workflow
to opt in.

## Decision

Set `agent.compact_issue_context` to default `true` in both the typed agent
config and the workflow builder fallback. Keep `agent.compact_issue_context:
false` as the explicit opt-out for custom boards that need every worker prompt
to include the full raw ticket history.

Release as `v0.10.1` instead of moving the already-published `v0.10.0` tag.
The follow-up is small but release-visible: package metadata, CLI version,
GitHub release notes, and the Pages badge should all include the new default.

- Rejected: deleting the flag. Some custom ticket formats may still need a
  rollback switch while the heading selector learns more board-specific
  section names.
- Rejected: changing only `AgentConfig`. Parsed workflow configs also use the
  builder fallback when the YAML omits the key, so both surfaces must agree.
- Rejected: retagging `v0.10.0`. A public release already exists, and moving a
  published tag would make downstream provenance ambiguous.

## Verification

- `rtk pytest tests/test_workflow.py -k "compact_issue_context" -q`
  passed: 3 tests.
- `rtk pytest tests/test_prompt_context.py tests/test_prompt.py::test_compact_issue_context_changes_rendered_prompt_description -q`
  passed: 8 tests.
- `python -m py_compile src/symphony/workflow/config.py src/symphony/workflow/builder.py`
  passed.
- `git diff --check`
  passed.
- Full suite on `dev` pre-push passed: 1110 passed, 2 skipped.
- Full suite on `main` passed locally and in the pre-push hook: 1110 passed,
  2 skipped.
- Confirmed `jira-symphony` has `agent.compact_issue_context: true` in
  `WORKFLOW.md`. The running local `jira-symphony` service was still version
  0.9.3 with an active worker, so it needs a restart/upgrade before relying on
  the 0.10.1 package default.
- Fresh remote-clone Codex E2E:
  `/private/tmp/symphony-codex-e2e-compact-xMLREd/repo`, commit
  `f8a5473532d574eeec38043fdae3ad0d536179db`.
  The E2E workflow intentionally omitted `agent.compact_issue_context`; loaded
  config reported `compact_issue_context=True`.
- Clean release-gate ticket `CODEX-E2E-005` reached `Human Review`, exited with
  `worker_exit reason=normal`, API reported `running=0` and `retrying=0`, and
  had no `## Contract Failure` or `## Contract Warning` heading.
- E2E artifacts:
  `docs/CODEX-E2E-005/work/compaction-default.md` contained
  `compact_issue_context default: true`,
  `docs/CODEX-E2E-005/qa/verify.log` contained
  `verified compact default true`, and
  `docs/CODEX-E2E-005/learn/handoff.md` contained the Learn handoff.

## E2E harness note

The first two temporary E2E tickets (`CODEX-E2E-003` and `CODEX-E2E-004`)
proved the worker could finish, but they were not accepted as the release gate:
the harness prompts omitted required contract sections or used the wrong
`AC Scorecard` table shape. The final ticket (`CODEX-E2E-005`) corrected the
harness to the validator's actual contract and is the evidence used for the
release decision.
