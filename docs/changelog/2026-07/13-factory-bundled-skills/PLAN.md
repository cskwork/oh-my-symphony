# PLAN - Factory bundled skills

Frozen plan. A fresh-context implementer reads only this file.

## Approval

- Status: approved-by-user
- Record: 2026-07-13; user accepted the immediately preceding recommendation with "do as recommended"

## Intent

- Goal / constraints / tradeoffs / rejected approaches: Make Oh My Symphony the only skill download required for the default autonomous factory. Prefer repository-owned package assets over network fetching or mutable global installations. Keep the bundle to the runtime closure used by factory prompts. Preserve arbitrary custom-skill fallback and existing project customization. Reject a network installer because it adds beginner setup, availability, and version-drift failure modes.
- Completion promise: A clean installed package can initialize and sync all four standard skills with an empty home directory; wheel-content, focused tests, full factory tests, lint, and diff checks prove it; stop when every GOAL criterion is verified or a concrete blocker is recorded; `max_iterations: 8`.

## Steps

1. Add failing tests in `tests/factory/test_cli.py` that isolate `HOME`, assert package-owned Supergoal init, standard overlay sync, local custom-skill fallback, and actionable failure for an unknown missing skill.
2. Add minimal pinned runtime assets under `src/symphony/factory/bundled_skills/<skill>/`, including `SKILL.md`, required referenced runtime files, attribution/license material, and a package marker where needed.
3. Change `src/symphony/cli/factory.py` so the four standard names resolve from `importlib.resources` package assets first and arbitrary names fall back to current local search roots. Copy resources without assuming a filesystem-installed wheel.
4. Extend `pyproject.toml` package data and beginner documentation so wheel installs contain the bundle and users understand that standard skills require no separate installation while project copies remain customizable.
5. Record rationale and rejected alternatives in `docs/changelog/changelog-2026-07-13.md`; run focused, wheel, full factory, lint, and diff verification.

## Tools & Skills

- Symphony BOOTSTRAP route; Supergoal LEGACY role loop.
- `pytest -q tests/factory/test_cli.py`
- `pytest -q tests/factory`
- `ruff check src/symphony/cli/factory.py tests/factory`
- `python -m build --wheel`
- `python -m zipfile -l <wheel>`
- `git diff --check`

## Verification strategy

- Before proof: with `HOME=/private/tmp/symphony-empty-home`, factory init exits 1 with `required skill 'supergoal' is not installed`.
- Step 1-3 -> criteria 1-3; step 2 and 4 -> criterion 4; step 5 -> criterion 5.
- Trusted commands: `pytest -q tests/factory` (frozen_repo); wheel listing and isolated-HOME CLI smoke (evaluator_owned); `ruff check ...` and `git diff --check` (frozen_repo).

## Grounding ledger

- Where is the prerequisite introduced? -> `_skill_sources` searches only home skill roots -> add package-owned source resolution.
- What is actually shipped now? -> `pyproject.toml` includes factory templates but no skills -> add explicit bundle package data.
- How is customization preserved? -> `_copy_skills` already keeps valid destinations when not forced -> retain behavior; fallback only for unknown skill names.
- Why not fetch skills? -> one-download beginner requirement and autonomous reliability -> no network dependency.
