# Release Rules
<!-- last-analyzed: 2026-05-18T00:00:00Z -->

## Version Sources

Both files must be bumped in lockstep:

- `pyproject.toml` — `version = "X.Y.Z"` (line 7, under `[project]`)
- `src/symphony/__init__.py` — `__version__ = "X.Y.Z"` (line 3)

No release automation script (no bump2version / release-it / semantic-release / changesets). Bumps are done by hand and committed with `chore(release): vX.Y.Z`.

## Release Trigger

Manual. Operator runs:

1. Bump the two version files.
2. Update `CHANGELOG.md` (move `[Unreleased]` content into a dated `[X.Y.Z]` section).
3. Commit: `chore(release): vX.Y.Z`.
4. Annotated tag: `git tag -a vX.Y.Z -m "vX.Y.Z"`.
5. `git push origin main && git push origin vX.Y.Z`.
6. `gh release create vX.Y.Z` with body (CHANGELOG entry, highlights, verification steps).

No tag-triggered CI workflow exists. The tag push and the GitHub Release are two separate operator steps.

## Test Gate

- Command: `python -m pytest -q`
- CI job: `.github/workflows/tests.yml` → `pytest` (matrix: Python 3.10 / 3.11 / 3.12 on ubuntu-latest)
- Triggers on `push` and `pull_request` to `main` and `dev` branches, plus `workflow_dispatch`.
- Tests are required to be green pre-release; no bypass flag in CI.
- Prior release (v0.6.2) noted "Full pytest suite: 538 passed, 5 skipped, 0 failed" as evidence in commit body.

## Registry / Distribution

- No PyPI publish step in CI. The package is not currently published to PyPI; distribution is git-tag + GitHub Release only. Install path is `pip install -e .` from a clone or `pip install git+https://github.com/cskwork/symphony-multi-agent@vX.Y.Z`.
- No Dockerfile, no npm package, no Cargo crate.

## Release Notes Strategy

- Convention: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format in `CHANGELOG.md`, with Conventional Commit subjects in git history (`feat:`, `fix:`, `docs:`, `chore:`).
- `CHANGELOG.md` carries an `[Unreleased]` placeholder at the top and a `## [X.Y.Z] — YYYY-MM-DD — <theme>` section per release.
- GitHub Release body (set via `gh release create` or the GitHub UI) is the long-form companion to the CHANGELOG entry — includes Highlights, "What's in (since vPREV)" with PR refs, and "Verified live" / verification notes when applicable.
- No `.github/release-body.md` committed pre-tag; the release body is composed at `gh release create` time from the CHANGELOG entry.

## CI Workflow Files

- `.github/workflows/tests.yml` — pytest matrix on push/PR to main/dev (the only workflow).

## First-Time Setup Gaps

- **No tag-triggered release workflow.** If automatic GitHub Release creation, asset upload, or PyPI publish is wanted, a `.github/workflows/release.yml` on `push: tags: [v*]` would be needed. For now the operator does `gh release create` by hand.
- **PyPI not configured.** `pyproject.toml` has the metadata to publish (name, version, license, deps), but no `twine` or `flit` upload happens. Not a defect — just a deliberate choice that the operator should be aware of when answering "how do I install a tagged release".
