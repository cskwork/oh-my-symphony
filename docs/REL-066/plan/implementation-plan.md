# Implementation plan — REL-066

Extended detail for the `## Plan` section in `kanban/REL-066.md`. The
ticket body holds the executable instructions; this file captures the
fallback commands, risk register, and verification specifics that exceed
the body cap.

## Files and exact substitutions

| file | line | before | after |
|------|------|--------|-------|
| `pyproject.toml` | 7 | `version = "0.6.5"` | `version = "0.6.6"` |
| `src/symphony/__init__.py` | 47 | `__version__ = "0.6.5"` | `__version__ = "0.6.6"` |

Both files use UTF-8 with `\n` line endings. The Edit tool preserves
those by default. The substitution string is unique inside each file
(verified by `grep -n '0\.6\.5'` returning a single match per file
during Explore).

Note on `.omc/RELEASE_RULE.md:9`: the doc claims `__version__` lives at
`__init__.py:3`. Ground truth is line 47 (verified by direct read in
Plan). The doc drift is out of scope for REL-066 — flag in `Learn` for
a follow-up wiki update, do not edit `RELEASE_RULE.md` here.

## Verification commands (canonical)

Run from the worktree root `~/symphony_workspaces/REL-066`:

```bash
# 1. Both files declare 0.6.6 — lockstep check
grep -nE '^version = "0\.6\.6"$' pyproject.toml
grep -nE '^__version__ = "0\.6\.6"$' src/symphony/__init__.py

# 2. Diff is exactly the two files
git status --short
# expected stdout (two lines, order may vary):
#  M pyproject.toml
#  M src/symphony/__init__.py

# 3. Test gate
python -m pytest -q
# expected tail: "566 passed, 5 skipped" and exit code 0

# 4. No tag created inside the worktree
git tag -l v0.6.6
# expected stdout: empty (operator owns the tag per AC #6)
```

If `git status --short` shows extra paths, revert them with
`git checkout -- <path>` before proceeding. If pytest count drifts from
`566 passed, 5 skipped`, move the ticket to `Blocked` and paste the
failing-test tail (last ~50 lines) under
`docs/REL-066/plan/blocker.md` — do NOT silence the failure.

## Risks and mitigations

| risk | mitigation |
|------|------------|
| Edit only one of the two files (lockstep drift) | Plan step 3 requires `git status --short` to show exactly the two paths before proceeding to step 4. |
| Accidental drive-by edits to `CHANGELOG.md`, `README.md`, or `docs/architecture.md` | Out of scope per ticket "Out of scope" section; Plan step 3 catches via `git status`. |
| Attempting `git tag` / `git push` inside the sandboxed worktree | Plan step 5 explicitly forbids it. AC #6 reserves tag push for the operator. |
| Pytest baseline drift | Plan step 4 treats any delta as `Blocked`, not as success. Surface the delta in `## Implementation` rather than papering over. |
| `RELEASE_RULE.md:9` line-number drift | Verified ground truth is `__init__.py:47`. Flagged for `Learn` wiki update; not touched in this ticket. |

## Rollback

Trivial:

```bash
git checkout HEAD -- pyproject.toml src/symphony/__init__.py
```

The worktree branch `symphony/REL-066` is disposable; the operator can
also delete and re-create it without affecting `main`.

## Hands-off list

The agent must NOT run any of these inside the worktree:

- `git commit` — `auto_commit_on_done` hook owns the commit.
- `git tag -a v0.6.6 ...` — AC #6 reserves this for the operator.
- `git push` — sandbox lacks push permissions; would fail anyway.
- `gh release create v0.6.6 ...` — out-of-band operator step.
- Edits to `CHANGELOG.md`, `README.md`, `docs/architecture.md`,
  `PLAN.md`, or `docs/improvements/workflow-v0.5.2.md` — all out of
  scope.
