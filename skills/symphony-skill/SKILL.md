---
name: symphony-skill
description: Single Symphony operator router for Kanban tickets, service/TUI runs, workflow prompts, delegation, production-ready app delivery planning, OneShot runs, monorepo bootstrap, and worker failure triage.
---

# Using Symphony

Symphony is a polling orchestrator that reads Kanban tickets and runs a
coding-agent CLI (Codex, Claude Code, Gemini, AGY/Antigravity, Kiro, OpenCode,
or Pi) against each ticket in an isolated workspace. This file is the single
operator router: classify the request, load only the matching reference, then
act.

Start by reading the target `WORKFLOW.md` and one or two real `kanban/*.md`
files. Symphony behavior is workflow-specific, and forks commonly customize
lanes, prompts, hooks, workspace roots, and agent backends.

## Route

State the route in one line, then open only the reference that route needs.

| Signal | Route | Read |
| --- | --- | --- |
| add/list/show/move tickets, start service/TUI/API, inspect state | OPERATE | `reference/operations.md` |
| edit `WORKFLOW.md`, agent kind, hooks, prompts, workspace, Slack hooks | CONFIGURE | `reference/workflow-config.md` |
| rename lanes, add state prompts, change pipeline shape | CUSTOMIZE | `reference/customization.md` |
| break a large request into Symphony board tickets | DELEGATE | `reference/delegation.md` |
| one prompt should become a full evidence-gated delivery pipeline | ONESHOT | `oneshot/reference/operations.md` |
| OneShot plan quality, ticket slicing, QA/PDF, vault, or lane gates | ONESHOT-DEEP | `oneshot/reference/decomposition.md`, then the needed OneShot reference |
| bootstrap Symphony into another repo | BOOTSTRAP | `reference/bootstrapping.md` |
| bootstrap isolated worktrees for a monorepo/polyrepo | MONOREPO | `monorepo/references/workflow-template.md` and `monorepo/scripts/setup-monorepo.sh` |
| worker exit, auth stall, blank TUI, stuck service, platform issue | TRIAGE | `reference/troubleshooting.md` or `reference/platform-compat.md` |

Branch-specific subfolders under `oneshot/` and `monorepo/` intentionally do
not have their own `SKILL.md`. They provide templates, scripts, and deep
references for this router.

## Core Model

- The orchestrator reads ticket files and dispatches eligible work; the worker
  agent edits the ticket file to move state and append reports.
- Each ticket runs in its own workspace under `workspace.root` (default
  `~/symphony_workspaces/<ID>`). The default hooks attach that directory as a
  `git worktree` on `symphony/<ID>`, leaving the host working tree untouched.
- Ticket IDs are an ordering contract. For multi-ticket work, create
  `TASK-001`, then `TASK-002`, then `TASK-003` in task-list order; Symphony
  sorts by stable numeric suffix before mutable fields like priority.
- The default Verify prompt expects the `symphony/<ID>` branch to be merged or
  proven ready against the configured target branch before the ticket moves to
  `Learn`.

## Board Ticket Quality Gate

Before registering more than one ticket, write the task list first and reject
bad slices:

- Work-type route: classify the request before ticket creation. Use the bugfix
  shape for defects, feature/enhancement shape for bounded behavior changes,
  app-delivery shape for customer-facing products, release-verification shape
  for final integrated proof, docs/config shape for non-runtime edits, and
  research/spike shape for unknowns. Do not force every task through the
  product-delivery shape.
- Independently testable: if tests require unbuilt work, merge the slice or
  add `blocked_by`.
- Self-contained prompt: the ticket description includes goal, scope, files,
  acceptance criteria, tests, dependencies, and done evidence.
- App-delivery work starts with discovery: target customer, core
  workflows, must-have functionality, data/auth/deployment assumptions, and a
  final merged-app release verification ticket.
- Human Review history gate: every ticket that reaches Human Review must have
  its final card/wiki/evidence record committed and pushed, with the remote SHA
  recorded. If commit/push cannot be proven, the ticket belongs in Blocked, not
  Human Review.
- Final integration loop: after implementation tickets are committed, pushed,
  and merged, the release-verification ticket runs full functionality QA on the
  merged target. Any defect becomes a new Kanban bug ticket with repro evidence
  and `blocked_by`; the release ticket loops until integration passes.
- One contract owner: a ticket owns one behavior/API/data contract, not a
  grab bag.
- Small enough for one worker: rough limit <=5 files and <=500 net lines for a
  Build ticket.
- Ordered IDs: assign suffixes by walking the task list top to bottom, then
  create files in that same order.

Ticket descriptions are worker prompts. Do not register vague tickets like
`implement frontend`; register a bounded slice with observable checks.

## Non-Negotiable Preflight

Run this before launching or debugging a workflow:

```bash
symphony doctor ./WORKFLOW.md
```

Fix FAIL lines first. Doctor catches the common launch blockers: port
collisions, missing agent CLI, missing Pi auth, placeholder clone URLs,
unwritable workspaces, and missing board directories.

## Guardrails

- When bootstrapping Symphony into another project, copy the launcher scripts,
  skill pointers, `docs/symphony-prompts`, and platform entry files. Do not
  leave the operator with only a bare `WORKFLOW.md`; read
  `reference/bootstrapping.md` for the exact bundle.
- Preserve the shipped four active lanes (`Todo`, `In Progress`, `Verify`,
  `Learn`) unless the user explicitly requests a custom workflow. If you change
  lanes, update both `tracker.active_states` and `prompts.stages`.
- Pick the prompt flavor that matches the tracker:
  `tracker.kind: file` uses `docs/symphony-prompts/file/...`;
  `tracker.kind: linear` uses `docs/symphony-prompts/linear/...`.
- Keep detailed lane behavior in `prompts.base` and `prompts.stages` files,
  not in a huge inline `WORKFLOW.md` body.
- Do not use `git reset --hard` in `before_run`; it can erase the agent's
  previous-turn work before it is finalized.

## Common Starts

Add one file-board ticket and open the managed TUI launcher:

```bash
symphony board init ./kanban
symphony board new TASK-001 "<title>" --description "<spec>"
./tui-open.sh ./WORKFLOW.md
```

Run headless with service state and browser viewer:

```bash
symphony service start ./WORKFLOW.md --port 9999 --viewer-port 8765
symphony service status ./WORKFLOW.md
curl -s http://127.0.0.1:9999/api/v1/state | jq
```

The `--port` (9999) root serves a browsable web app (`oh-my-symphony`), not
just the JSON API. **"Open the orchestrator" defaults to opening
`http://127.0.0.1:9999/`** (`open`/`xdg-open`/`start`); the `--viewer-port`
board (8765) is the secondary card board — open it only when asked for the
board view.

Use `symphony service ...` for normal headless operation. It writes
per-workflow run state under `.symphony/run/` and refuses duplicate starts for
the same `WORKFLOW.md`, preventing two orchestrators from dispatching the same
board.

For smoke demos without an installed agent CLI, set `codex.command: python -m
symphony.mock_codex`; see `reference/operations.md`.

### Offer Slack notifications during bootstrap

When initializing or rewriting a `WORKFLOW.md`, ask the operator whether
they want each state transition broadcast to Slack — it is the cheapest
hook for PMs to follow a board without opening the TUI. Make it a
question, not a default:

> "Optional: post each ticket transition to Slack? If yes I need an
> incoming-webhook URL (or env-var name) and either 'every stage' or a
> filtered subset like Done + Blocked."

If they accept, add the block from `reference/workflow-config.md`
(`Notifications (Slack)` section). If they decline, omit it. The feature
is off whenever the block is absent — no extra cleanup needed.

## What To Read Next

| Need | Read |
| --- | --- |
| Bootstrap Symphony into a project | `reference/bootstrapping.md` |
| Add/list/show/move tickets, run TUI/API/service | `reference/operations.md` |
| Edit `WORKFLOW.md`, agent kind, hooks, tracker, workspace | `reference/workflow-config.md` |
| Rename lanes, add per-state prompts, customize pipelines | `reference/customization.md` |
| Delegate independent sub-tasks to Symphony workers | `reference/delegation.md` |
| Run a single prompt through the OneShot pipeline | `oneshot/reference/operations.md` |
| Improve OneShot issue decomposition | `oneshot/reference/decomposition.md` |
| Bootstrap a monorepo worktree workflow | `monorepo/references/workflow-template.md` |
| Diagnose `worker_exit`, `hook_failed`, blank TUI, auth stalls | `reference/troubleshooting.md` |
| Set up/debug Windows, macOS, Linux behavior | `reference/platform-compat.md` |
| Configure `.gitignore` for Symphony-generated docs/logs | `reference/gitignore-recommendations.md` |

## Headless Triage Signals

If a service appears stuck, read `log/symphony.log` and the JSON state. Useful
events include:

- `dispatch issue_id=...` - ticket picked up
- `hook_completed hook=after_create` - workspace seeded
- `agent_session_started session_id=` - backend CLI started
- `agent_turn_completed turn=N total_tokens=...` - a turn finished
- `agent_turn_failed ... stderr_tail=[...]` - backend failure; inspect stderr
- `worker_exit reason=normal` - clean end-to-end completion

If `dispatch` appears but no `agent_session_started` follows within about a
minute, inspect backend auth, command, and stdin behavior. See
`reference/troubleshooting.md`.

## When Not To Use This Skill

- The user wants to write code inside a workspace Symphony already created for
  them; handle it as a normal coding task using that agent backend's
  conventions.
- The user is asking general Linear API questions outside a Symphony workflow;
  use the project README and upstream Linear docs instead.
