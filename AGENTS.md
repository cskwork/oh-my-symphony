# AGENTS.md — Codex CLI entry point

This repo is **Symphony**, a polling orchestrator that dispatches coding
agents (Codex / Claude Code / Gemini / AGY / Kiro / OpenCode / Pi) at a Kanban board. This file is
the discovery point that Codex (and any other `AGENTS.md`-respecting CLI)
reads on startup so the **operator** — the human or agent running
`symphony` — has the same skill guidance Claude Code gets from
`.claude/skills/`.

## Source of truth: `skills/using-symphony`

Operator-side routing lives in one skill:
`skills/using-symphony/SKILL.md`. It has YAML frontmatter (`name`,
`description`, optional triggers) and routes requests to the right reference
or support bundle. `.claude/skills/` is a thin symlink layer for Claude Code's
native discovery — do not edit through it, edit the canonical files under
`skills/`.

`skills/symphony-oneshot/` and `skills/symphony-monorepo/` are support bundles
with templates, scripts, and references used by the router. They intentionally
do not expose separate `SKILL.md` activation routes.

## Available skill (operator-facing)

Load `skills/using-symphony/SKILL.md` when the user's request matches the
trigger description below. Open only the reference page named by the router's
decision table.

### `using-symphony`

> Use when the user wants to dispatch coding agents (Codex / Claude Code /
> Gemini / AGY / Kiro / OpenCode / Pi) against a Kanban board via this `oh-my-symphony` repo
> — adding/listing/transitioning tickets, launching the TUI, inspecting
> orchestrator state, customizing the workflow (lanes, per-state prompts),
> delegating sub-tasks to free up context, one-shotting a prompt into an
> evidence-gated board, bootstrapping a monorepo workflow, or diagnosing
> dispatch failures.
> Triggers on phrases like "add a symphony task", "run symphony", "dispatch
> this ticket", "symphony board", "WORKFLOW.md", "symphony tui won't start",
> "ticket failed with worker_exit", "customize kanban states", "deploy
> pipeline workflow", "delegate to symphony", "agent.kind: pi", "agent
> silent for N seconds", "one-shot this", "decompose and dispatch with proof",
> or "symphony monorepo".

Entry: `skills/using-symphony/SKILL.md`

## Worker-side guidance

Dispatched workers (the agent CLI running inside a per-ticket workspace) do
**not** consume these operator skills. Worker behavior is driven by
`WORKFLOW.md`'s `prompts.base` + `prompts.stages` map, which renders stage
prompts from `docs/symphony-prompts/<flavor>/`. That layer is already
cross-platform — codex/claude/gemini/pi workers all receive the same
rendered prompt for a given ticket state.

## Conventions for this repo

- Read `WORKFLOW.md` and a couple of `kanban/*.md` files before any
  recommendation — settings vary per fork.
- Run `symphony doctor ./WORKFLOW.md` before launching anything.
- See the `BOOTSTRAP` route in `skills/using-symphony/SKILL.md` for the full
  file set required when copying Symphony into another project.
