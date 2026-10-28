# Symphony-managed project

This repository contains the user's product. Symphony is the separate
orchestrator that dispatches coding agents at `kanban/`, where each Markdown
ticket's `state:` frontmatter names its board column.

- Read `WORKFLOW.md` and the relevant tickets before changing the product.
- Run `symphony doctor ./WORKFLOW.md` before launching the orchestrator.
- `defaults: standard` loads packaged four-lane prompts and worktree hooks.
  Explicit workflow settings override those defaults.
- Use `symphony eject prompts`, `symphony eject hooks`, or `symphony eject all`
  to create project-local defaults for customization. Eject refuses overwrites.
- Worker prompts are rendered from the workflow's `prompts` configuration;
  workers do not consume the operator skill.
- Each ticket runs on its own `symphony/<ID>` Git worktree. Never hard-reset
  a worker worktree between turns; that discards in-progress work.
- If created with `--with-skill`, `skills/symphony-skill/SKILL.md` is the
  operator router. `.claude/skills/` links to that canonical skill directory.
