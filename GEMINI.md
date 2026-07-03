# GEMINI.md — Gemini CLI entry point

This repo is **Symphony**, a polling orchestrator that dispatches coding
agents (Codex / Claude Code / Gemini / Pi) at a Kanban board. This file is
the discovery point Gemini CLI reads on startup so the **operator** — the
human or agent running `symphony` — has the same skill guidance Claude Code
gets from `.claude/skills/`.

## Skill activation

Gemini activates Symphony through one operator skill after reading its
metadata: read the `description`, then load
`skills/using-symphony/SKILL.md` (via `Read` or `read_file`) and follow its
route table.

Source of truth lives in `skills/using-symphony/`. `.claude/skills/` is a thin
symlink layer for Claude Code's native discovery only — do not edit through it.
`skills/using-symphony/oneshot/` and `skills/using-symphony/monorepo/` are
branch-specific subfolders for templates/scripts/references used by the
router, not separate activation routes.

## Available skill (operator-facing)

### `using-symphony`

> Use when the user wants to dispatch coding agents (Codex / Claude Code /
> Gemini / Pi) against a Kanban board via this `oh-my-symphony` repo
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

Dispatched Gemini workers (running inside a per-ticket workspace) do
**not** consume these operator skills. Worker behavior comes from
`WORKFLOW.md`'s `prompts.base` + `prompts.stages` map, which renders stage
prompts from `docs/symphony-prompts/<flavor>/`. That prompt layer is
already cross-platform — codex/claude/gemini/pi workers all receive the
same rendered prompt for a given ticket state.

## Tool mapping

The skill files use Claude Code tool names (`Read`, `Bash`, `Edit`,
`Glob`, `Grep`, `Skill`). The Gemini equivalents:

| Claude Code  | Gemini CLI              |
|--------------|-------------------------|
| `Read`       | `read_file`             |
| `Bash`       | `run_shell_command`     |
| `Edit`       | `edit` / `replace_file_content` |
| `Glob`       | `glob`                  |
| `Grep`       | `search_file_content`   |
| `Skill`      | `activate_skill`        |

## Conventions for this repo

- Read `WORKFLOW.md` and a couple of `kanban/*.md` files before any
  recommendation — settings vary per fork.
- Run `symphony doctor ./WORKFLOW.md` before launching anything.
- See the `BOOTSTRAP` route in `skills/using-symphony/SKILL.md` for the full
  file set required when copying Symphony into another project.
