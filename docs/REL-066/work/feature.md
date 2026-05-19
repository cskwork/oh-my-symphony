# Release notes — v0.6.6

**What changed**: Symphony's published version string moves from `0.6.5` to `0.6.6` in the two files that declare it.

**How a user observes it**:

- `pip show oh-my-symphony` reports `Version: 0.6.6` once the release artefact is published.
- `python -c "import symphony; print(symphony.__version__)"` prints `0.6.6`.
- After the operator pushes `v0.6.6` (AC #6, out-of-band), `git describe --tags` resolves to `v0.6.6` on the merged commit.

**Knobs / flags**: none. No new public API, no schema migration, no configuration change.

**What this release packages** (informational, not in scope of the bump itself):

- PR #44 — surgical decomposition of `workflow/`, `tui/`, and `orchestrator/` into cohesive packages.
- PR #45 — companion architecture mapping in `docs/architecture.md` with indirection rules.

**Operator follow-up** (outside this ticket per AC #6):

```
git tag -a v0.6.6 -m "release v0.6.6"
git push origin v0.6.6
```

The agent does NOT create or push the tag from inside the worktree.
