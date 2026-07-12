# Handoff: Factory-bundled autonomous-development skills

Generated: 2026-07-13 07:53 KST
Recipient: another coding agent or the same agent in a later session
Focus: independently review the final Stitch attribution correction, then finish Exact Verify and PR closeout
Status: in progress; WIP checkpoint requested by the user

## Goal

Make Oh My Symphony the only skill download needed for the beginner autonomous
factory. The installed package must contain pinned factory runtimes for
Supergoal, SuperPM, Superdesign, and SuperQA; generated projects remain
customizable and may use path-safe locally installed custom skills. Latest user
request: stop now, save a handoff, commit, and push.

## Current State

- Workspace: `/private/tmp/symphony-autonomous-factory`
- Branch: `feat/autonomous-dev-factory`
- Target: `dev`
- Pre-checkpoint HEAD: `ba07496a56d29aa0726a3af4a027fedf4fc33390`
- Pull request: https://github.com/cskwork/oh-my-symphony/pull/57
- Active services or sessions: none; all implementation/review subagents were completed or interrupted
- Run vault: `docs/changelog/2026-07/13-factory-bundled-skills/`
- Bundle: `src/symphony/factory/bundled_skills/` (161 non-cache files at handoff)
- Original checkout `/Users/danny/Documents/PARA/Resource/symphony-multi-agent` was restored after an accidental untracked patch; its only remaining untracked files are the two pre-existing user docs under `docs/`.

## Completed Work

- `symphony factory init` resolves the four standard skills from package resources before user skill roots, so empty-home initialization no longer requires global skill installs.
- Standard skill copies contain factory-scoped Supergoal delivery/WAYFINDER runtime, full advertised SuperPM runtime, full Superdesign runtime, and SuperQA runtime/launcher material with per-skill MIT licenses.
- Package resource copying works from filesystem installs and direct zip imports; shell assets are restored executable without following symlink targets.
- Wayfinder accepts normalized path-safe custom skill names; public `factory sync` resolves them from supported local roots. Unsafe/path-traversal names fail.
- `factory sync --force` is a real recovery path for incomplete generated skill copies. Valid customized copies are preserved only when the complete pinned runtime inventory is present.
- Recursive reference-integrity coverage includes Markdown, Python, shell, JS/MJS, JSON, CMD, HTML, TOML, and TCSS assets. It found and caused inclusion of `superpm/scripts/shoot.sh`.
- EN, KO, and operator docs explain that standard skills are bundled; Superdesign still requires Node.js 18+, `@playwright/cli`, skill/browser setup, and SuperQA browser runs require their external browser dependencies.
- SuperQA now carries the README required by its advertised editable-install path.
- Redistribution inventory includes pinned wrapper sources plus available upstream MIT/Apache license texts. It explicitly does not claim legal approval.
- Iteration 3 corrected the last review finding: Stitch is pinned to `4b4c7fb00d7d77d48403f6b7682c3fb502e0db0c`, its available MIT notice is retained, stale unavailable-license text was removed, and Superdesign attribution now names the shipped `assets.md` and `web.md` guidance.

## Decisions

- Package-first standard skills: reproducible one-download beginner setup. Rejected network fetching and mutable global-skill prerequisites because they add setup and version drift.
- Project-local customization: valid existing `skills/<name>` copies are preserved; `--force` is explicit for replacement.
- Factory-scoped Supergoal router: ships only routes emitted by the factory while retaining their full reference/template closure. Rejected a shallow SKILL.md sample because it passed installation but failed WAYFINDER at runtime.
- Full Superdesign/SuperQA runtime closure: their routers and scripts are cross-coupled. Rejected two-file samples as misleading.
- Attribution records are engineering inventory, not legal advice or approval.

## Files And Changes

- `src/symphony/cli/factory.py`: bundled Traversable resolution/copy, full validation, safe replacement, executable restoration, public force recovery.
- `src/symphony/factory/wayfinder.py`: path-safe custom skill metadata.
- `src/symphony/factory/bundled_skills/`: pinned skills, manifest, notices, upstream license texts, runtime closures.
- `pyproject.toml`: recursive package-data coverage and executable wheel assets.
- `tests/factory/test_bundled_skills.py`: empty-home, wheel/zip, closure, modes, licensing, custom, force, symlink, preservation tests.
- `tests/factory/test_cli.py`, `tests/factory/test_wayfinder.py`: public CLI and parser behavior.
- `README.md`, `README.ko.md`, `skills/symphony-skill/reference/factory.md`: beginner installation and runtime prerequisites.
- `docs/changelog/changelog-2026-07-13.md`: decisions and rejected alternatives.
- `docs/changelog/2026-07/13-factory-bundled-skills/`: Supergoal goal, plan, QA, loop ledger, and run state. It is intentionally not marked complete.
- Untouched user work: the original checkout's untracked `docs/changelog/changelog-2026-07-12.md` and `docs/plans/2026-07-12-autonomous-dev-factory-default-template.md`.

## Commands And Evidence

- Before: empty-home `factory init` exited 1 with `required skill 'supergoal' is not installed`.
- TDD build: new bundle tests initially failed; successive repair loops reproduced missing route closure, unreachable custom skills, nonexistent force guidance, shallow validation, symlink replacement, lost executable modes, missing SuperQA README, missing Superdesign prerequisites, missing SuperPM script, and stale Stitch attribution.
- Latest pre-attribution full proof: `pytest -q tests/factory` -> `108 passed`.
- Latest pre-attribution focused review: `70 passed`; wheel inventory `160/160`; installed-wheel and direct-zip gates executed with usage exit 2, not permission exit 126; clean-venv SuperQA editable install passed.
- Iteration 3 builder: focused attribution/closure -> `14 passed`; Ruff and `git diff --check` passed.
- Handoff check after interruption: `PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/factory/test_bundled_skills.py` -> `30 passed`.
- Handoff lint/check: focused Ruff and `git diff --check` -> passed.
- Not yet proven after iteration 3: fresh independent adversarial review, full factory suite, rebuilt wheel inventory/execution, repository-wide suite, Supergoal commit gate, updated GitHub CI.

## Remaining Plan

1. Independently re-review iteration 3 attribution edits.
   - Why: the previous final reviewer found one stale Stitch record; the builder fixed it immediately before this handoff, but review was interrupted.
   - Where: `THIRD_PARTY_NOTICES.md`, `MANIFEST.json`, `superdesign/reference/sources.md`, `tests/factory/test_bundled_skills.py`, latest `R-LOOP.md` section.
   - Command: run the focused attribution/closure tests and inspect the Stitch MIT byte match and pinned commit.
   - Done when: a fresh no-edit reviewer reports no merge-blocking findings.
   - Risk: attribution inventory is not legal approval.

2. Run Exact Verify from current checkpoint.
   - Why: no full or wheel proof was rerun after iteration 3.
   - Where: feature worktree and a fresh temporary install directory.
   - Command: `pytest -q tests/factory`; focused Ruff; Pyright for touched Python; `git diff --check`; build a fresh wheel; compare source/wheel inventory and modes; empty-home installed-wheel init/sync; direct zipimport init/copy/gate execution; clean-venv SuperQA editable install.
   - Done when: all commands pass with exact outputs recorded in `QA.md`.
   - Risk: wheel build may emit the pre-existing setuptools `project.license` deprecation warning.

3. Close the Supergoal run vault.
   - Why: checkpoint state currently has unchecked GOAL criteria, a non-final QA verdict, and no completion marker.
   - Where: `GOAL.md`, `QA.md`, `run-state.json`, then `Z-2026-07-13.md`.
   - Command: run the Supergoal commit gate after all evidence is written.
   - Done when: every criterion is checked by the verifier, `Backward-trace: clean`, QA PASS, completion promise fulfilled, and commit gate exits 0.
   - Risk: do not rewrite older R-LOOP sections; append corrections.

4. Update PR #57 after final verification.
   - Why: this handoff commit is a WIP checkpoint, not merge approval.
   - Where: GitHub PR #57 and branch `feat/autonomous-dev-factory`.
   - Command: push final verified commit; inspect `gh pr checks 57` and mergeability.
   - Done when: current GitHub CI is green and the PR body distinguishes bundled skills from external browser/tool prerequisites.
   - Risk: do not merge until steps 1-3 are complete.

## Verification Plan

- `PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/factory`
- `/opt/anaconda3/bin/ruff check src/symphony/cli/factory.py src/symphony/factory/wayfinder.py tests/factory`
- focused Pyright for touched Python files
- `git diff --check`
- fresh wheel build and exact source-versus-wheel inventory/mode comparison
- empty-home installed-wheel and direct-zip init/sync/copy/gate smokes
- clean-venv SuperQA editable install
- fresh no-edit adversarial review and Supergoal commit gate

## Risks And Assumptions

- The WIP checkpoint is deliberately not a completion claim and should not be merged.
- Browser engines, Node.js, `@playwright/cli`, and related browser dependencies are not embedded in the skill bundle.
- Third-party notices preserve available texts and provenance but require maintainer/legal review for public redistribution policy.
- GitHub CI shown before this push belongs to the previous commit and is stale until the new checkpoint run starts.

## Suggested Skills

- `supergoal`: finish adversarial review, Exact Verify, and vault/commit gate.
- `symphony-skill`: verify factory init/sync/start behavior and PR-facing operator docs.
- `handoff`: update this packet only if another pause occurs.

## Resume Prompt

Resume `/private/tmp/symphony-autonomous-factory` from `docs/handoffs/2026-07-13-0753-factory-bundled-skills.md`. First run a fresh no-edit adversarial review of the iteration-3 Stitch attribution correction. If clean, execute the full Exact Verify plan, close the Supergoal run vault, and update PR #57. Do not merge the WIP checkpoint before those gates pass.

## Redactions

- None. No credentials, tokens, cookies, or secrets are recorded.
