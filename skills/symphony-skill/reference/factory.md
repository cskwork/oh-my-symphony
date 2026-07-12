# Autonomous development factory

Use this as the default beginner file-board path. It keeps ownership narrow:
Supergoal plans and delivers one Wayfinder slice; Symphony schedules tickets,
creates isolated worktrees, retries failures, and runs an independent Verify
turn.

```bash
symphony factory init /path/to/project --agent opencode
# Edit /path/to/project/wayfinder/tickets/*.md
symphony factory sync /path/to/project/wayfinder
symphony factory start /path/to/project/wayfinder
```

Wayfinder tickets use YAML frontmatter with stable `id`, `title`, `route`,
`blocked_by`, and `skills` fields, plus `## Acceptance criteria`, `## Proof
commands` (or `## Proof`), and `## Non-goals`. Routes are `GREENFIELD`,
`DEBUG`, or `LEGACY`. Every synchronized ticket receives `supergoal`. Add only
the `superdesign`, `superpm`, or `superqa` overlays when needed. Optional
`kind` values are `customer-research`, `research`, `design`, `product-spec`,
`qa`, and `ui`; optional `browser` must be a YAML boolean. `factory init`
prints a complete copy-ready schema.

Sync validates the full dependency graph before writing, imports every ticket
and its blocker edges by default, uses source provenance for idempotency, and
never rewrites a ticket after it leaves `Ready`. Use `--frontier-only` when an
operator deliberately wants to import only the currently actionable frontier.

`Ready` is a machine-owned dependency gate, not an agent stage. Sync validates
scope, acceptance checks, proof, and route; Symphony leaves unresolved
dependencies visible in `Ready` and promotes actionable tickets to `Build`
without creating a workspace or consuming an agent slot.

In `Build`, the Wayfinder ticket is the already approved scope and plan and its
sections are the run ledger. The worker reads Supergoal once and executes its
Build, full-spec, edge-case, adversarial-review, and exact-local-QA passes, but
does not create a second GOAL/PLAN/QA run vault. The Build contract requires a
short nonempty section for every pass before an independent `Verify` worker
can add the acceptance-criterion evidence table and move the ticket to Done.

`factory init` prints the exact Supergoal WAYFINDER prompt to use next.
It merges runtime ignore rules into `.gitignore` without replacing user rules:
board cards, worktrees, logs, and `WORKFLOW-PROGRESS.md` stay local runtime
state. Commit the Wayfinder spec and product code, not `kanban/*.md`.
`factory start` runs sync, Doctor (including any `--port` override), then the
managed service. A Doctor failure stops before process launch. Verified Done
work merges into the branch that launched the factory. Use
`examples/advanced/WORKFLOW.file.example.md` for the advanced production
pipeline.

Each worker's `kanban/` must link to the host board. The setup hook replaces
an empty checkout directory with that link and is safe to rerun. It stops with
a repair message if an older repository has tracked cards that produce a
nonempty real directory; continuing would let the worker edit a stale private
card that the scheduler cannot observe.
