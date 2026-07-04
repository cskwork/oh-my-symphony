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
