# 2026-07-02 — Web Kanban revamp (multica/Archon-style)

## Goal

Revamp the UX layer into a full web + TUI product: user-registered issues with
attachable skills, user-editable workflow (kanban columns add/delete/rename),
per-column prompt editing, and a dedicated stats page — on top of the existing
orchestrator, which stays untouched in its core loop.

## Decisions

### 1. Web UI lives in the main package, served by the orchestrator's aiohttp server

`src/symphony/web/static/` (vanilla HTML/CSS/JS, no build step) served at `/`
by the existing `server.py` app. New REST endpoints get direct in-process
access to the tracker, workflow config, and orchestrator snapshot.

- Rejected: upgrading `tools/board-viewer` (separate stdlib server + proxy).
  Full CRUD needs workflow mutation + tracker writes; proxying doubles every
  endpoint and leaves two servers to keep in sync. The board-viewer is now
  deprecated in place.
- Rejected: React/Vite SPA. A node build step breaks the "pip install, no
  signup, no build" ethos; the multica look is achievable with plain CSS.

### 2. WORKFLOW.md stays the single source of truth; UI edits round-trip via ruamel.yaml

Column add/delete/rename and branch policy write back into WORKFLOW.md YAML
frontmatter using ruamel.yaml round-trip parsing, which preserves the file's
extensive comments and key order. Hand edits and UI edits coexist.
The orchestrator already hot-reloads WORKFLOW.md per poll (`WorkflowState.reload`),
so UI changes apply on the next tick without restart.

- Rejected: overlay file (`.symphony/workflow-overrides.yml`) merged at load —
  two sources of truth confuse hand-editing users.
- Rejected: PyYAML rewrite — destroys comments users rely on.

### 3. Skills = SKILL.md directories, attached per issue, injected into the first-turn prompt

`GET /api/v1/skills` scans `<workflow_dir>/skills/*/SKILL.md`. Issues gain a
`skills: [name, ...]` frontmatter list (new `Issue.skills` field). At dispatch,
the orchestrator appends each attached skill's SKILL.md body (size-capped)
under `## Attached skills` after the rendered stage prompt.

- Rejected: template-variable injection (`{{ skills }}`) — would require every
  existing workflow/prompt file to opt in; appending works with all of them.

### 4. Stats = append-only JSONL event store, aggregated on read

`.symphony/stats.jsonl` next to the existing `token_ema.json`. The orchestrator
appends events (turn tokens, phase transitions, run outcomes) via a failure-
tolerant `StatsStore`. `GET /api/v1/stats?days=N` computes aggregates on read.

- Rejected: SQLite — a second storage engine for data this small is overkill;
  JSONL is greppable and matches the file-first ethos.

## API surface (new)

```
GET    /api/v1/board                     columns + issues + live run info
POST   /api/v1/issues                    create issue
GET    /api/v1/issues/{id}               detail (frontmatter + body + run)
PATCH  /api/v1/issues/{id}               update fields / move state
DELETE /api/v1/issues/{id}               delete ticket file
GET    /api/v1/workflow                  states, descriptions, prompt map
PUT    /api/v1/workflow/states           add/remove/rename columns
GET    /api/v1/workflow/prompts/{state}  prompt file content
PUT    /api/v1/workflow/prompts/{state}  save prompt file
GET    /api/v1/skills                    available skills
GET    /api/v1/stats?days=N              aggregates for stats page
GET    /api/v1/git/branches              local branches (branch policy UI)
PUT    /api/v1/workflow/branch-policy    feature base / merge target
```

Guardrails: prompt paths must resolve inside the workflow dir; state and issue
identifiers validated; a state with a running worker cannot be removed; at
least one active and one terminal state must remain; removed states migrate
their tickets to the first active state.
