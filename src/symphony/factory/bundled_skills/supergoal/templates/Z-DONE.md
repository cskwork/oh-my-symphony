# DONE <YYYY-MM-DD>

Completion marker. Save into the vault as `Z-<YYYY-MM-DD>.md`. Created ONLY when every `GOAL.md`
Success Criterion (and QA Case) is checked - never earlier.

- Branch: <run_branch> -> <target/integration branch>
- Completed: <ISO 8601 timestamp>
- Criteria: all checked (GOAL.md)
- Gate: `bash templates/commit-gate.sh <vault> <browser|cli|none>` PASS
