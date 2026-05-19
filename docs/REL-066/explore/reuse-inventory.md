# Reuse Inventory — REL-066

This is a `chore(release)` ticket: two metadata strings to edit. No
shared helper is appropriate because there is no release-automation
tool in this repo (see `.omc/RELEASE_RULE.md` — "No release automation
script. Bumps are done by hand"). Listing the candidates considered,
including why rejected:

| candidate | path:line | reuse_fit (0-1) | adapt_cost | notes |
|-----------|-----------|------------------|------------|-------|
| bump2version / tbump / semantic-release | n/a | 0 | high | Not present in deps; introducing one is a tooling change outside the chore(release) scope. |
| `tools/`-based one-shot bump script | tools/ (none exists) | 0 | high | No precedent; the project explicitly hand-edits both files (see `.omc/RELEASE_RULE.md`). |
| Direct file edit on `pyproject.toml:7` + `src/symphony/__init__.py:47` | pyproject.toml:7, src/symphony/__init__.py:47 | 1 | low | Matches the historical pattern (`git show 79072e4` v0.6.4 release; `git show 39b4a59` v0.6.5 lockstep). Two-line surgical change. |

Conclusion: `reuse_from = none`; the only sustainable path is the
two-string edit pattern the repo already uses. Justification line for
Plan: "Repo convention is hand-edit; introducing automation here would
expand scope beyond a metadata bump."
