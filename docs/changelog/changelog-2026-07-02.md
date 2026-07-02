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

## Review outcomes (2026-07-02)

Three independent reviews (security, backend, SPA JS) ran against the branch;
all CRITICAL/HIGH findings were fixed and regression-tested the same day:

- **CRITICAL** — ticket identifiers from GET/DELETE routes reached
  `board_root / f"{id}.md"` unvalidated; on Windows a `%5C`-encoded backslash
  traverses out of the board. Fixed with the same identifier whitelist used
  on create, applied to every route parameter.
- **HIGH** — stats appends and skill-file reads ran synchronously on the
  event loop. StatsStore now enqueues to a single-worker FIFO executor
  (order preserved, `read_events` flushes first); skill rendering moved to
  `asyncio.to_thread` at both dispatch sites.
  - Rejected: queue + hand-rolled daemon thread — the one-worker executor
    gives the same non-blocking behavior with exact flush semantics for free.
- **HIGH** — a YAML typo in hand-edited WORKFLOW.md became an unlogged 500;
  now a 400 carrying the ruamel parse message.
- **MEDIUM** — Host allowlist extended to GET (DNS-rebinding reads); omitted
  column descriptions preserved on PUT (None=keep, ""=clear); unstable
  stats-store singleton key; per-request catch-all logging.
- Deliberately NOT fixed: stats.jsonl rotation (greppable local file, small
  at realistic ticket volume — revisit if a board ever exceeds ~100k events).

SPA JS review (verdict: XSS clean; two HIGH fixed):
- **HIGH** — drawer state/priority/agent/skills controls kept showing a
  failed edit; every field now reverts to the last saved value on PATCH
  failure, matching title/labels.
- **HIGH** — the 5s poll re-rendered the board mid-drag, removing the drag
  source node and silently cancelling the HTML5 drag. The poll now always
  fetches (so the connection dot stays truthful even while a form is
  focused — the review's MEDIUM) but holds DOM updates while an overlay
  input is focused or a `.dragging` card exists.
- **MEDIUM** — labels lowercase client-side to mirror server normalization.
- **LOW** — dead `_key`/`nextWfKey`/`pollTimer` removed; markdown links get
  `noreferrer`.

---

# 2026-07-02 — 4-stage pipeline simplification

## Goal

Collapse the default agent pipeline from many operator-visible lanes into four
active states: `Todo`, `In Progress`, `Verify`, and `Learn`. Keep the quality
gates, but move them inside fewer stages so the board is easier to scan.

## Decisions

### 1. Merge stages, not just UI groups

`Plan` and `Critic` are now sections inside `In Progress`; `Review`, `QA`, and
Merge Gate are sections inside `Verify`. The contract validator follows the
same state names, so the UI, prompt, and state machine describe one real
workflow.

- Rejected: grouping old lanes visually while keeping old state names. That
  would make the web/TUI simpler but leave retries, contracts, and prompts
  harder to explain.
- Rejected: skipping Verify for trivial tickets. Verify owns Merge Gate, so
  skipping it risks shipping unmerged or unreviewed work.

### 2. Keep Learn lightweight and skippable

Learn is now only wiki write-back and Human Review handoff. Operators can skip
it from the TUI (`S`) or web/API, which appends `## Learn Skipped` and moves the
ticket to `Human Review` without starting an agent turn.

- Rejected: deleting Learn. The wiki write-back is still useful when work
  produces durable repository knowledge.
- Rejected: letting running Learn workers be skipped. The skip action refuses
  running tickets to avoid racing worker-owned ticket writes.

### 3. Remove skills UI but preserve the engine

The web nav/page, web create/detail controls, and TUI create/detail controls no
longer expose skills. Existing `skills:` ticket frontmatter still round-trips
through the backend and prompt injection remains intact for power users.

- Rejected: deleting `skills.py` and `Issue.skills`. That would break existing
  boards and remove a useful advanced feature.

### 4. Move demos under `examples/`

The repo root now keeps the current workflow examples; demo/smoke/Jira files
and demo boards live under `examples/`. Moved workflows use `../docs/...`
prompt/wiki paths because workflow-relative resolution changes after the move.

### 5. Keep tracked fixtures on the new defaults

Docs-only test fixtures now point at the retained `verify.md` prompt instead of
removed stage filenames. That keeps tracked fixtures from teaching removed
default lanes while preserving custom-state tests that intentionally exercise
arbitrary board names.

### 6. Make the web board default to active work

The web board now opens on the four active lanes only. Non-empty terminal lanes
render as a compact terminal group, and the `All` toggle expands every
configured column when an operator needs full board surgery.

- Rejected: deleting terminal states from `/api/v1/board`. They are still
  workflow data and are needed for scripts, stats, and manual recovery.
- Rejected: always showing `Human Review` as a fifth full lane. It is operator
  work, not agent pipeline work, so the compact group keeps the 4-stage board
  promise while leaving review cards visible.

### 7. Preserve exhausted turn-budget guards across polls

Full E2E exposed a redispatch loop: a Learn ticket that hit
`max_total_turns` was marked exhausted, then the next stale-claim prune removed
that guard and dispatched the same ticket again. `_claimed` still prunes when no
worker owns the ticket, but `_turn_budget_exhausted` now remains until process
restart or explicit operator movement.

- Rejected: making the E2E harness tolerate repeated dispatch. The loop burns
  agent turns and hides a real scheduler bug.
- Rejected: moving every exhausted ticket to `Blocked` by default. Existing
  boards rely on `agent.budget_exhausted_state` as an opt-in persistence policy.

## Breaking Change

Boards with custom active states or prompt mappings that still use `Explore`,
`Plan`, `Critic`, `Review`, or `QA` need a manual `WORKFLOW.md` migration to the
4-stage layout before adopting the new prompt templates.

---

# 2026-07-02 - post-E2E hardening research plan

## Decision

Create `docs/plans/2026-07-02-post-e2e-hardening-plan.md` as a follow-up plan,
not implementation, because the 4-stage branch is already committed and pushed.
The plan prioritizes tracked browser E2E and API smoke coverage first, then
budget-exhausted UI, mobile lane controls, and terminal-state wording.

- Rejected: folding these follow-ups into the shipped 4-stage commit. That would
  obscure the already-verified pipeline migration.
- Rejected: making browser E2E mandatory in default `pytest`. Browser binaries
  and local sandbox behavior are too environment-sensitive for every developer
  run.

---

# 2026-07-02 - post-E2E hardening implementation

## Decision

Ship the plan as additive hardening: optional Python Playwright E2E, a stdlib
live API smoke script, budget-exhausted attention payloads, mobile active-lane
tabs, and clearer terminal-state wording.

- Rejected: parsing every smoke response as JSON. `/static/app.js` is plain
  JavaScript, so the smoke helper now falls back to raw text for non-JSON
  responses.
- Rejected: applying grid-only mobile CSS to the board. The board uses flex
  columns, so the mobile lane behavior changes rendered columns and flex sizing
  directly while leaving `All` as the full editable board.
- Rejected: duplicating README board text. The existing active-lane sentences
  were refined in English and Korean to name **Review and parked**.
