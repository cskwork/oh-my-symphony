### BUILD — run Supergoal

Intake and Wayfinder planning are complete. Do not inspect `WORKFLOW.md`, the
factory prompt files, or the WAYFINDER map.
Begin directly with the current ticket, then read only the relevant product
source and tests.
Do not inventory the workspace, Git history, tool versions, or unrelated files
unless the ticket makes one of them relevant. Start the first failing proof as
soon as the relevant source and test seam are known.

The current Wayfinder ticket is the approved scope and plan, and that ticket is the run ledger.
Do not create separate Supergoal run-vault files (`GOAL.md`, `PLAN.md`,
`QA.md`, `run-state.json`, `R-LOOP.md`, or a dated `Z-*.md`). Do not read the
Supergoal `delivery-gate` reference or run-vault templates; this factory
adapter replaces those artifacts with the ticket sections below.
Do not create any other process-evidence files or directories, including
`docs/<ticket>/` role outputs. Keep all factory process evidence in the shared
ticket ledger and edit only product files required by the ticket.

Read each attached skill's `SKILL.md` once. Do not read other Supergoal
references or templates for factory Build; the sequential adapter below is
the complete per-ticket procedure. Do not rediscover the skill or reread its
instructions. Use `supergoal` and the ticket's GREENFIELD/DEBUG/LEGACY route to
deliver exactly this ticket test-first. The current Symphony worktree is the
Supergoal run worktree; do not create nested worktrees. Record the plan as
auto-approved because this is an explicitly autonomous factory run.

Run Build -> Improve full spec -> Improve edge cases -> Mandatory Adversarial
Review -> Exact Verify/QA sequentially in the current worker. When this backend
cannot create subagents, preserve the fresh-role boundaries as concise,
separate passes instead of spawning or simulating another worktree. Apply
`superdesign` to UI/design work and `superpm` to customer research or
product-spec work only when attached in ticket metadata.

Keep the evidence concise. Append each section immediately after its pass
rather than deferring the whole ledger until the end; every section is nonempty:

- `## Implementation`: smallest change and test-first result.
- `## Full Spec Review`: acceptance gaps checked and any fix made.
- `## Edge Case Review`: grounded boundaries checked and any fix made.
- `## Adversarial Review`: no-edit attempt to disprove completion, findings,
  and the outcome after any required fix and re-review.
- `## Test Evidence`: Exact local QA commands and results.

Then set state to `Verify`. Do not mark Done from Build.
