# Release version bump

## Getting the Feel (For Beginners)

### Why release version bumps exist

Every Symphony build carries a single version number (`X.Y.Z`). It tells
installers, GitHub releases, and `pip` which artefact to pin. When new
work merges into `main`, the version stays put — until an operator
explicitly cuts a release. The bump is what flips the project from "the
old code is the current install" to "the new code is the current install".

The simplest way for a beginner to picture it:

`Merged work on main → version still old → release ticket bumps two files → tag pushed → installers see new version`

There are five terms you need to internalise at this stage.

| Term | Plain-English meaning |
|---|---|
| Version source | A file that declares the current published version string |
| Lockstep | Two files that must always show the same value; editing one alone is a defect |
| chore(release) | The commit type used for version bumps; carries the new version in the subject |
| Tag | A git pointer (`v0.6.6`) that names a specific commit as the release point |
| Out-of-band step | Work the operator does outside the worktree (pushing the tag, drafting the GitHub Release) |

To make it concrete:

Two PRs (`#44`, `#45`) just merged into `main` adding a workflow/tui/orchestrator
package split. Nothing is published yet — installers still see `0.6.5`.
A release ticket bumps `pyproject.toml` and `src/symphony/__init__.py` from
`0.6.5` to `0.6.6` in a single commit. Symphony's `auto_commit_on_done`
hook writes the subject as `chore(release): v0.6.6`. After the orchestrator
merges the branch back to `main`, the operator runs `git tag -a v0.6.6`
and `git push origin v0.6.6` themselves — the agent never touches tags.

The decision rule that matters at this stage:

**Just remember this: bump both version files in one commit, never just one — and never push the tag from inside the worktree.**

When you're ready to go deeper, read [[production-pipeline]] for how the
ticket flows through the eight stages, and [[workspace-auto-commit-excludes]]
for how the commit subject gets rewritten by the hook.

## Technical Reference

**Summary:** Symphony's published version is declared in exactly two files
and the project policy treats them as a strict lockstep pair. `.omc/RELEASE_RULE.md`
forbids release automation tools (`bump2version`, `tbump`, `semantic-release`);
the bump is a hand edit. The Symphony orchestrator owns the commit via
`auto_commit_on_done`, but tag creation and `gh release create` are
out-of-band operator steps because the agent runs sandboxed without push
permissions.

**Invariants & Constraints:**
- Two version sources must match exactly: `pyproject.toml` (`[project]` table, `version = "X.Y.Z"`) and `src/symphony/__init__.py` (`__version__ = "X.Y.Z"`).
- `chore(release)` commits touch ONLY the two version files. CHANGELOG, README, and `docs/architecture.md` are explicitly out of scope for the bump ticket (REL-066 ticket spec confirms; `docs/architecture.md` version mentions are historical release-history bullets pinned to release commit SHAs, not current-version declarations).
- The commit subject is fixed: `chore(release): vX.Y.Z`. Symphony's `auto_commit_on_done` rewrites the placeholder `[no-test] wip:` HEAD into this subject from `.symphony/commit-message.txt` during the Done transition.
- QA gate is `python -m pytest -q` matching the prior-release baseline (v0.6.6: `566 passed, 5 skipped`). No new tests required; a test asserting `__version__ == "X.Y.Z"` would be tautological and is explicitly waived.
- The agent must NOT run `git tag`, `git push`, or `gh release create` inside the worktree. AC #6 of release tickets reserves these for the operator (sandboxed agents lack push permissions).
- `[no-test]` waiver applies: bump commits touch production paths (`pyproject.toml` + `src/symphony/__init__.py`) outside the docs/kanban/.symphony auto-exempt set, but ticket spec + `.omc/RELEASE_RULE.md:26-32` + v0.6.4/v0.6.5 precedent all designate `pytest -q` as the test gate for metadata-only bumps.

**Files of interest:**
- `pyproject.toml:7` — `version = "X.Y.Z"` under `[project]`. Authoritative version source #1.
- `src/symphony/__init__.py:47` — `__version__ = "X.Y.Z"`. Authoritative version source #2. (Note: `.omc/RELEASE_RULE.md:9` currently cites line 3 — this is a stale line-number reference, ground truth is :47.)
- `.omc/RELEASE_RULE.md` — release policy + the "no automation" rule + lockstep statement. Treat as the policy authority; if its line-number citations drift, prefer reading the source files.
- `.symphony/commit-message.txt` — staging area for the `chore(release): vX.Y.Z` subject the `auto_commit_on_done` hook applies.
- `CHANGELOG.md` — operator step, not part of the bump ticket. Operator moves `[Unreleased]` content into a dated `[X.Y.Z]` section after merge, before tagging.
- `.github/workflows/tests.yml` — CI gate (Python 3.12 on ubuntu-latest, push/PR to main/dev). No tag-triggered workflow exists; tag push does not trigger a release workflow.

**Observability hooks:**
- none — this is a metadata-only edit pattern, not an observable runtime surface.

**Decision log:**
- 2026-05-19 | REL-066 | Bumped 0.6.5 → 0.6.6 to package surgical-decomposition PRs #44/#45. Created this wiki entry. Noted `.omc/RELEASE_RULE.md:9` cites `__init__.py:3` but ground truth is `:47` — left unfixed in this ticket per "no drive-by edits outside the two version files" rule; flagged as follow-up.
- Historical: v0.6.4 (`git show 79072e4`) and v0.6.5 lockstep reconcile (`git show 39b4a59`) establish the two-file hand-edit precedent referenced by every subsequent release.

**Last updated:** 2026-05-19 by REL-066.
