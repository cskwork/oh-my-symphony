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

## Planned verification

- `rtk pytest tests/test_workflow.py -k "compact_issue_context" -q`
- `rtk pytest tests/test_prompt_context.py tests/test_prompt.py::test_compact_issue_context_changes_rendered_prompt_description -q`
- `python -m py_compile src/symphony/workflow/config.py src/symphony/workflow/builder.py`
- `git diff --check`
- Confirm `jira-symphony` has prompt compaction enabled by config or default.
- Publish `v0.10.1` and rerun a fresh Codex E2E from remote state.
