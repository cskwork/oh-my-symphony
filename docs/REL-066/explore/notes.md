# Explore notes — REL-066

## Version sources confirmed

- `pyproject.toml:7` → `version = "0.6.5"` (target: `0.6.6`).
- `src/symphony/__init__.py:47` → `__version__ = "0.6.5"` (target:
  `0.6.6`).
- Repo-wide `0.6.[0-9]+` grep returns six files: only the two above are
  authoritative. The remaining four mention historical versions:
  - `docs/improvements/workflow-v0.5.2.md` — older release notes.
  - `docs/architecture.md:193` — release-history bullet pinning the
    `0.6.5` lockstep commit `39b4a59`. **Not a current-version
    declaration**; do not edit.
  - `PLAN.md` — historical plan capture.
  - `CHANGELOG.md` — out of scope per ticket "Out of scope" clause.

## Release rule (memorised from .omc/RELEASE_RULE.md)

1. Lockstep `pyproject.toml` and `src/symphony/__init__.py`.
2. No automation tool; commit subject must be `chore(release): vX.Y.Z`.
3. Test gate: `python -m pytest -q`. v0.6.2 evidence in commit body
   recorded "538 passed, 5 skipped, 0 failed". REL-066 baseline (from
   ticket) is "566 passed, 5 skipped".
4. Tag push and `gh release create` happen **outside the worktree**.
   The agent must not attempt `git tag` / `git push`.

## Historical precedent

- `git show 79072e4` (v0.6.4 release): touched `pyproject.toml`,
  `src/symphony/__init__.py`, `CHANGELOG.md`, `README.md`. The current
  ticket excludes the `CHANGELOG`/`README` lines; that scope was a
  policy decision (see ticket "Out of scope" section).
- `git show 39b4a59` (v0.6.5 lockstep): one-line `pyproject.toml`
  reconciliation when the prior bump left the two files out of sync.
  Reinforces: bump must touch *both* files in one commit.

## Risk inventory

- **Drift risk**: editing only one of the two files leaves Symphony's
  own release-rule check failing on next release. Mitigated by editing
  both in this turn.
- **Out-of-scope drift**: editing `CHANGELOG.md`, `README.md`, or
  `docs/architecture.md` would expand the diff beyond the ticket
  contract. Explicitly not touched.
- **Tag push attempt**: doing `git tag` inside the worktree fails
  (agent runs sandboxed and lacks push permissions per AC #6). The
  operator handles tagging out-of-band.
- **Test baseline drift**: AC #4 pins "566 passed, 5 skipped". If
  pytest reports a different count, QA must surface the delta rather
  than silently accept it.

## Touched-file overlap check

The two files to edit (`pyproject.toml`, `src/symphony/__init__.py`)
are root-level / package-init metadata. No other in-flight ticket
realistically claims these (no kanban ticket in `Todo`/`Explore`/`Plan`/
`In Progress` references them — checked via `kanban/` directory listing
during explore). Overlap risk is effectively zero.
