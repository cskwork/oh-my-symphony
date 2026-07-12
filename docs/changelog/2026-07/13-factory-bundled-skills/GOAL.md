# GOAL - Factory bundled skills

Single source of done. Only the verifier ticks a box.

## Original Request

> do as recommended

## Spec

Ship version-pinned, minimal runtime copies of Supergoal, SuperPM, Superdesign,
and SuperQA inside the Oh My Symphony Python package. `symphony factory init`
must work with an empty home directory and copy Supergoal into the generated
project. `factory sync` must copy any standard overlay named by Wayfinder
ticket metadata without requiring a global skill installation. Unknown custom
skills may continue to resolve from supported user skill directories. Existing
valid project skill copies remain user-owned and are not overwritten without
`--force`.

Non-goals: installing agent CLIs, changing the factory lane model, or
automatically updating customized project skill copies.

## Success Criteria

- [ ] Factory init succeeds with an empty home directory and creates a valid `skills/supergoal` runtime - verify: `pytest -q tests/factory/test_cli.py`
- [ ] Standard SuperPM, Superdesign, and SuperQA ticket overlays sync from package assets with no global installations - verify: `pytest -q tests/factory/test_cli.py tests/factory/test_lifecycle.py`
- [ ] Unknown custom skills still resolve from supported local skill roots and missing unknown skills fail actionably - verify: `pytest -q tests/factory/test_cli.py`
- [ ] The built wheel contains every bundled skill file needed at runtime - verify: `python -m build --wheel && python -m zipfile -l dist/*.whl`
- [ ] Focused and full regression checks pass with clean scope - verify: `pytest -q tests/factory && ruff check src/symphony/cli/factory.py tests/factory && git diff --check`

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | auto-fix | resolved | Package-first standard skills versus locally installed versions | Pin package copies for reproducibility; preserve customization in generated project copies and local fallback for unknown skills | isolated-HOME tests |
