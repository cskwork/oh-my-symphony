# oh-my-symphony reference

This page holds the detail behind the [README](../README.md) quickstart,
organized by topic. Release history lives in [CHANGELOG.md](../CHANGELOG.md).

- [Install and agent CLIs](#install-and-agent-clis)
- [Optional components](#optional-components)
- [Projects and the protected source checkout](#projects-and-the-protected-source-checkout)
- [Autonomous development factory](#autonomous-development-factory)
- [Mock demo: no agent CLI required](#mock-demo-no-agent-cli-required)
- [WORKFLOW.md essentials](#workflowmd-essentials)
- [File board and ticket CLI](#file-board-and-ticket-cli)
- [Trackers: file, Linear, Jira](#trackers-file-linear-jira)
- [Agent backends](#agent-backends)
- [Preflight and proof](#preflight-and-proof)
- [Running Symphony](#running-symphony)
- [Web app and JSON API](#web-app-and-json-api)
- [Reverse proxy, tunnels, and API token](#reverse-proxy-tunnels-and-api-token)
- [Lane presets and stage contracts](#lane-presets-and-stage-contracts)
- [Verify gates](#verify-gates)
- [Needs: tickets waiting for an operator](#needs-tickets-waiting-for-an-operator)
- [Skills](#skills)
- [Chat intake](#chat-intake)
- [Per-ticket artefacts and board deliverables](#per-ticket-artefacts-and-board-deliverables)
- [Machine gate for application releases](#machine-gate-for-application-releases)
- [Continuous improvement (experimental)](#continuous-improvement-experimental)
- [PM Workspace](#pm-workspace)
- [Language (i18n)](#language-i18n)
- [Platforms](#platforms)
- [Architecture and source layout](#architecture-and-source-layout)
- [Tests and contributing](#tests-and-contributing)
- [Known limits](#known-limits)
- [Acknowledgements](#acknowledgements)

## Install and agent CLIs

Symphony is not on PyPI yet. Install it as a tool from GitHub, or editable
from a checkout:

```bash
uv tool install git+https://github.com/cskwork/oh-my-symphony

# or, inside a clone of the repository
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Both paths can create projects; `symphony project create` reads its bootstrap
files from the installed package, not from a checkout. Contributors install
core, the three optional packages, and the test toolchain with
`python -m pip install -e ".[dev]" -e packages/pm -e packages/chat -e packages/release`
(see [CONTRIBUTING.md](../CONTRIBUTING.md)).

Make the CLI for each backend you use available on `$PATH`:

| `agent.kind` | required CLI on `$PATH` |
|--------------|------------------------|
| `codex`      | `codex` (with `app-server` subcommand) |
| `claude`     | `claude` (Claude Code) |
| `gemini`     | `gemini` (Gemini CLI)  |
| `agy`        | `agy` (Antigravity CLI, install from Google Antigravity; Symphony appends `--dangerously-skip-permissions`) |
| `kiro`       | `kiro-cli` (Kiro CLI, install from `https://cli.kiro.dev/install`; run `kiro-cli login` or set `KIRO_API_KEY` for headless runs) |
| `opencode`   | `opencode` (OpenCode CLI, install with `npm install -g opencode-ai`; authenticate providers with `opencode auth login`) |
| `pi`         | `pi` (Pi coding-agent, `npm i -g @earendil-works/pi-coding-agent` or `curl -fsSL https://pi.dev/install.sh \| sh`; sign in once via `pi` → `/login` (OAuth, credentials cached at `~/.pi/agent/auth.json`), no env var needed) |
| `prime-agent` | `prime-agent` (Prime Agent, install with `curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh \| sh`; sign in once via `prime-agent` → `/login`, or provide a provider API key; credentials cached at `~/.prime/agent/auth.json`) |

## Optional components

Three features ship as separate distributions in this repository. They are
not on PyPI; install them from a checkout, next to core. Import paths are
unchanged (`symphony.pm`, `symphony.chat`, `symphony.release`).

| Directory | Distribution | Adds |
|-----------|--------------|------|
| `packages/pm` | `oh-my-symphony-pm` | PM Workspace (Artifacts page, `/api/v1/pm/*`, `pm:` keys), `symphony pm demo` |
| `packages/chat` | `oh-my-symphony-chat` | Web operator chat, `/api/v1/chat/*`, intent cards and intent filing |
| `packages/release` | `oh-my-symphony-release` | App release contracts (ADR 0002), `symphony release check` |

```bash
python -m pip install ./packages/pm ./packages/chat ./packages/release
# editable, for development
python -m pip install -e ".[dev]" -e packages/pm -e packages/chat -e packages/release
# uv tool install, from the checkout
uv tool install --force . --with ./packages/pm --with ./packages/chat --with ./packages/release
```

Each package pins the exact core version (`oh-my-symphony==0.29.0`). XLSX
exchange in the PM Workspace also needs the core extra `pm-exchange`.

Without a package, core keeps working and says what is missing:

- `symphony pm` and `symphony release` print
  `... is not installed; install it from the oh-my-symphony checkout: python -m pip install ./packages/<name>`
  and exit 2.
- The web UI hides the Artifacts (PM) and Chat entries; the `/board` payload
  reports `pm_available` and `components` (`{"pm": ..., "chat": ..., "release": ...}`).
- Enabling any `pm:` capability (`authoring`, `exchange`, `assistance`,
  `handoff`) is a config error that names the install command.
- A ticket labelled `app-release`, `app-release-finalizer`, or
  `release-cycle-verifier` (or one with durable release authority in
  `state.db`) is refused, parked in `Blocked` with the install hint, and never
  merged (fail closed).
- `symphony doctor` FAILs `app.release-contract` on a board that uses app
  release delivery.

## Projects and the protected source checkout

Keep the `oh-my-symphony` checkout as the control plane. Doctor and normal
startup refuse a `WORKFLOW.md` in Symphony's own canonical Git repository
(including linked worktrees), so agents cannot edit the orchestrator that
launched them.

```bash
symphony project create "My App" --path ../my-app
# or adopt an existing directory; Git and missing Symphony files are added safely
symphony project add /path/to/existing-project --name "My App"
symphony project start my-app
symphony hub                          # multi-project hub, default 127.0.0.1:8787
```

Other project commands: `symphony project list` (`ls`), `status [ID]`,
`stop ID [--timeout 10] [--force]`, `remove ID` (`rm`; unregisters without
deleting files). `create` and `add` accept `--id`, `--workflow` (default
`WORKFLOW.md`), `--host`, `--port` (default: next unused port from 9999), and
`--with-skill`. `create --path` defaults to a sibling of the Symphony source
for an editable install, otherwise to `./<id>` in the current directory.
`start` accepts `--replace` and `--skip-doctor`.

A bootstrapped project receives three tracked files, committed in one
`chore: initialize Symphony project` commit:

- `WORKFLOW.md`: about 20 lines with `defaults: standard` (see
  [WORKFLOW.md essentials](#workflowmd-essentials)); `server.host` and
  `server.port` are the registered values, so `symphony doctor` checks the
  port this project will really use.
- `AGENTS.md`: the project-facing agent guide from
  [`src/symphony/templates/project/AGENTS.md`](../src/symphony/templates/project/AGENTS.md).
  Gemini CLI users set `context.fileName` to `AGENTS.md` in
  `.gemini/settings.json`.
- `kanban/.gitkeep`.

Prompts and the worktree setup script are read from the package until you run
`symphony eject`. `--with-skill` also copies the operator skill to
`skills/symphony-skill/` and links `.claude/skills/symphony-skill` to it.
With `preset: deep` (web project creation), the deep preset's prompt files are
copied as well.

The Hub and every project board show the canonical repository, workflow, and
"Issues are stored here" board paths. Use **Manage projects** to enter a name
and local path. A missing path becomes a new Git repository; an existing
non-Git directory is initialized in place; an existing Git repository keeps
its metadata and files. Symphony adds only missing operator files and never
stages unrelated changes.

Selecting another project starts its service when needed and navigates to that
independent board. Services and workers for the previous project keep running
against their original repository, so project switching cannot retarget
active jobs.

> **Existing non-Git directories:** Symphony initializes Git and commits only
> the Symphony files it adds. Existing product files remain untracked to avoid
> accidentally committing secrets or local artifacts. Review and commit those
> files before dispatching tickets so worker worktrees include them.

## Autonomous development factory

For a new file-board project, the default path is the Wayfinder -> Symphony
-> Supergoal loop in a separate product repository:

```bash
git init my-product
cd my-product
symphony factory init . --agent opencode
git add . && git commit -m "Initialize Symphony factory"
# Create Wayfinder tickets under wayfinder/tickets/, then:
symphony factory sync ./wayfinder
symphony factory start ./wayfinder
```

Flags:

- `factory init [TARGET]`: `--agent`/`--agent-kind BACKEND` (default
  `opencode`), `--force` (replaces the generated `WORKFLOW.md`, prompts,
  scripts, Wayfinder map, and Supergoal skill; their customizations are lost;
  unrelated product files and `.gitignore` rules are kept).
- `factory sync [TARGET]`: `--wayfinder DIR`, `--prefix` (default `TASK`),
  `--frontier-only`, `--force` (replaces bundled/custom skill copies; project
  skill customizations are lost).
- `factory start [TARGET]`: syncs, runs doctor, then starts the managed
  service. Same flags as sync, plus `--agent` (used only for automatic init;
  an existing workflow is kept) and `--port`.

Factory init preserves existing `.gitignore` content and adds ignores for
live board cards, worktrees, logs, and `WORKFLOW-PROGRESS.md`. These are
shared runtime state; Wayfinder specs and delivered product code remain
commit-ready.

The generated board uses `Ready -> Build -> Verify -> Done`, with `Blocked`
for missing authority or environment. Supergoal owns each ticket's
test-first delivery loop; Symphony owns dependency scheduling, worktrees,
retries, and a fresh Verify turn. Sync imports the complete graph and blocker
edges by default, so later slices start automatically when their
dependencies finish; `--frontier-only` imports only tickets whose
dependencies are already Done. Add optional overlays explicitly in Wayfinder
frontmatter: `superdesign` for UI, `superpm` for product research/spec work,
and `superqa` for browser/runtime proof. The former production profile
remains at `examples/advanced/WORKFLOW.file.example.md` as a fully explicit
example; `symphony project create` writes the minimal `defaults: standard`
workflow instead.

Verified Done tickets merge into the launch branch locally, and bounded
Build/Verify budgets stop runaway agent loops. Publishing is opt-in through
`agent.auto_merge_push_target: true` after configuring an upstream.

**Recovery.** When a ticket is `Blocked`, read the card's `Blocker` section
first for the cause and the preserved worktree path. Fix the Git error or
token-limit problem, then request verification again with
`symphony board update <ID> --state Verify --workflow ./WORKFLOW.md`. A
paused ticket does not run just because its state moves; resume it with `P`
in the TUI or `POST /api/v1/<ID>/resume` on the running service. When the
real merge succeeds, Symphony-written recovery diagnostics are marked
`Resolved Blocker (history)` and the original text stays as history;
operator-written Blockers are kept. Board `Done` alone does not confirm the
target merge or worktree cleanup. See the
[factory guide](../src/symphony/templates/project/skills/symphony-skill/reference/factory.md) for limits and
recovery, and the
[PowerShell recovery steps](../src/symphony/templates/project/skills/symphony-skill/reference/factory.md#powershell-recovery).

**Bundled skills and browser runtimes.** The four standard skills are pinned
inside the Symphony package and copied into each project, so beginners do not
install them separately and can still customize the project copies. Browser
execution is not dependency-free:

- Superdesign gates need Node.js 18+, `@playwright/cli`
  (`npm install -g @playwright/cli@latest`), `playwright-cli install --skills`,
  and either system Chrome or Playwright Chromium
  (`npx playwright install chromium`).
- A SuperQA browser run needs `textual`, `playwright`, `pyyaml`, and
  `pillow`, plus Playwright Chromium (`python -m playwright install chromium`).
- These runtimes may require a network-enabled first-time setup.

## Mock demo: no agent CLI required

The bundled mock backend speaks the same JSON-RPC protocol as Codex but does
no real work; it simulates turns and emits token-usage ticks. Use it to see
cards move before installing an agent CLI.

```bash
# Keep the source checkout protected; create the demo as a separate project.
# This writes a complete, doctor-clean WORKFLOW.md for real agents. Leave it
# alone and give the mock its own workflow file next to it.
symphony project create "Symphony Demo" --path ../symphony-demo
cd ../symphony-demo

cat > WORKFLOW.mock.md <<'YAML'
---
tracker: { kind: file, board_root: ./kanban,
           active_states: [Todo, "In Progress", Verify, Document],
           terminal_states: ["Human Review", Done, Blocked, Archive] }
polling: { interval_ms: 5000 }
workspace: { root: ~/symphony_workspaces/symphony-demo-mock }
hooks:
  # Link the host board into each workspace so doctor and the runtime see it.
  after_create: 'ln -sfn "$SYMPHONY_WORKFLOW_DIR/${SYMPHONY_BOARD_ROOT_NAME:-kanban}" "${SYMPHONY_BOARD_ROOT_NAME:-kanban}"'
  before_run:   ": noop"
  after_run:    "echo done"
agent:  { kind: codex, max_concurrent_agents: 2, max_turns: 4, max_total_turns: 60 }
codex:  { command: python -m symphony.mock_codex }
server: { port: 9999 }
---
You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
YAML

symphony board init ./kanban           # also adds the sample ticket DEMO-001
symphony board new TASK-1 "smoke test"
symphony doctor ./WORKFLOW.mock.md     # every line should read PASS
symphony tui ./WORKFLOW.mock.md
```

Within about 5 seconds both Todo cards (DEMO-001 and TASK-1) grow a green ●
indicator with a turn counter and climbing token totals. Quit with `Ctrl-C`.

- Cards stay in their original column under the mock. Only a real agent
  rewrites `kanban/TASK-1.md` to move the card to **Done**. The mock exists to
  prove the orchestrator → backend → workspace → hooks pipeline end-to-end
  without an LLM call.
- Delete `WORKFLOW.mock.md` afterwards or keep it for smoke tests; the real
  board always reads `WORKFLOW.md`.
- Mock tunables: `SYMPHONY_MOCK_TURN_SECONDS=12`,
  `SYMPHONY_MOCK_FAIL_EVERY_N_TURNS=3`, and others in
  [`src/symphony/mock_codex.py`](../src/symphony/mock_codex.py).

## WORKFLOW.md essentials

`symphony project create` / `project add` write a minimal four-lane file-board
`WORKFLOW.md`:

```yaml
---
defaults: standard
tracker:
  kind: file
  board_root: ./kanban
workspace:
  root: ~/symphony_workspaces/my-app
  reuse_policy: refresh
agent:
  kind: codex
  auto_merge_push_target: false
server:
  host: 127.0.0.1
  port: 9999
---

You are working on {{ issue.identifier }}: {{ issue.title }}.
```

**`defaults: standard`** (opt-in) merges the package defaults in
[`src/symphony/templates/project/defaults.yaml`](../src/symphony/templates/project/defaults.yaml)
under your keys: the four-lane `tracker` states and descriptions, `prompts`
(pointing at the package's prompt files), all four `hooks` (the
`after_create` hook runs the package copy of `symphony-setup-worktree.sh`), and
the per-agent command blocks (`codex`, `claude`, `gemini`, `agy`, `kiro`,
`opencode`, `pi`, `prime_agent`).

- **Explicit keys win.** Maps merge key by key, so a partial `hooks:` block
  overrides only the hooks it names. `prompts.stages` and
  `tracker.state_descriptions` are replaced as a whole when you set them.
- `standard` is the only accepted value; anything else is a config error.
- Without the key, nothing is filled in and the workflow behaves exactly as
  in 0.28.

**`symphony eject [prompts|hooks|all] [WORKFLOW]`** (default `all`) copies the
package files into the project (`docs/symphony-prompts/file/...` for
`prompts`, `scripts/symphony-setup-worktree.sh` for `hooks`) and writes the
matching `prompts:` / `hooks:` keys into `WORKFLOW.md`, so you can edit them.
It refuses to overwrite any existing file or symlink and undoes its writes on
failure.

[`WORKFLOW.example.md`](../WORKFLOW.example.md) targets Linear and needs an API
key; the root [`WORKFLOW.file.example.md`](../WORKFLOW.file.example.md) is the
factory profile; [`examples/advanced/WORKFLOW.file.example.md`](../examples/advanced/WORKFLOW.file.example.md)
spells every block out. Without `defaults: standard`, four blocks matter for
first-run sanity:

```yaml
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress", Verify, Document]
  terminal_states: ["Human Review", Done, Blocked, Archive]

workspace:
  root: ~/symphony_workspaces

hooks:
  # Each ticket gets its own workspace at workspace.root/<ID>.
  # The shipped default attaches it as a `git worktree` of the host repo
  # on a `symphony/<ID>` branch — host working tree stays untouched.
  # Use `: noop` instead while you experiment without a host repo.
  after_create: |
    : noop                       # ← swap for the worktree default in the example workflow
  before_run: |
    : noop                       # runs before every agent turn
  after_run: |
    echo "run finished at $(date)"

prompts:
  # Symphony sends base plus only the file for the ticket's current state.
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
    Verify: ./docs/symphony-prompts/file/stages/verify.md
    Document: ./docs/symphony-prompts/file/stages/document.md
    Done: ./docs/symphony-prompts/file/stages/done.md
```

> **Warning:** the shipped `WORKFLOW.example.md` and
> `examples/advanced/WORKFLOW.file.example.md` attach the per-ticket workspace
> as a **git worktree** of the host repo (the directory containing
> `WORKFLOW.md`) on a `symphony/<ID>` branch. Worker edits stay on that ticket
> branch. When `agent.auto_merge_on_done` is enabled, the orchestrator merges
> once at Done; do not duplicate that merge by hand. With automatic merge
> disabled, merge or open a PR when you are satisfied. If your code lives in a
> *different* remote than the `WORKFLOW.md` repo, swap the hook for
> `git clone <remote> .`. While experimenting without any repo, use `: noop`.

**Custom prompts.** `prompts.base` + `prompts.stages` point at editable files
under `docs/`. Symphony sends `base` plus only the prompt file for the
ticket's current state, keeping each turn small. If the `prompts` block is
absent, the inline body of `WORKFLOW.md` still works as the legacy fallback.
Prompts are also editable from the web app's **Workflow** page (same files,
no restart).

**Polling.** `polling.interval_ms` defaults to 30000 (30 s).

## File board and ticket CLI

Each ticket is one Markdown file with YAML frontmatter at `kanban/<ID>.md`.
The orchestrator only **reads** ticket files; the agent **writes** them when
it transitions state.

```bash
symphony board init ./kanban
# → initialized board at ./kanban, sample ticket DEMO-001.md

symphony board new TASK-1 "Fix flaky pagination test" \
  --priority 2 \
  --labels backend,test \
  --description "tests/test_pagination.py::test_cursor_advance is flaky on CI."

# Structured creation: dependencies, request grouping, body from file/stdin.
symphony board new TASK-2 "Add regression test" \
  --blocked-by TASK-1 \
  --request REQ-1 \
  --label test --label ci \
  --description-file ./spec.md      # or `-` to read stdin

symphony board new TASK-3 "title" --agent-kind codex   # pin a backend
```

`new` validates before writing:

- the identifier must match `^[A-Za-z][A-Za-z0-9_-]{0,63}$` (so a
  model-generated id can never escape the board root);
- the state must come from `tracker.active_states` / `terminal_states`;
- every `--blocked-by` target must exist on the board;
- added edges must keep the dependency graph acyclic (violations print the
  cycle path and exit non-zero).

The web API's issue create/update endpoints apply the same rules. Edit an
existing ticket through the same validation instead of hand-writing
frontmatter:

```bash
symphony board update TASK-2 --add-blocked-by BUG-7   # keeps existing blockers
symphony board update TASK-2 --blocked-by TASK-1      # replaces the list
symphony board update BUILD-1 --state Build           # reopen a slice
symphony board update TASK-2 --request REQ-2
```

Inspect:

```bash
symphony board ls                    # all tickets
symphony board ls --state Todo       # filter by state
symphony board show TASK-1           # full body (the agent's ## Resolution lives here)
symphony board graph                 # dependency DAG (topological, indented)
symphony board graph --request REQ-1 # only one request group
ls ~/symphony_workspaces/TASK-1      # the workspace the agent operated in
```

Move tickets manually only to unstick them; normally the agent transitions
tickets itself, following the stage prompts. The orchestrator re-evaluates on
the next poll tick.

```bash
symphony board mv TASK-1 Blocked         # forces a state transition
symphony board update TASK-1 --state Blocked --add-blocked-by BUG-7
```

On success the agent rewrites `kanban/<ID>.md` to set `state: Done` and append
a `## Resolution` section. That file edit is what moves the card. Columns
follow the ticket file's `state` field; the green ● running indicator is
overlaid on the card and does **not** change its column, so a running ticket
stays in **Todo** until the agent rewrites the file.

For file-board workflows, `agent.auto_triage_actionable_todo` defaults to
`true`: a Todo ticket with a body and an `Acceptance Criteria` section moves to
In Progress with a one-line `## Triage` note without spending a model turn.
Bug tickets, blocked tickets, ambiguous tickets, and Linear trackers still use
the Todo prompt.

## Trackers: file, Linear, Jira

- **File** (`tracker: { kind: file, board_root: ./kanban }`): Markdown tickets
  live in Git next to your code. No API key or signup.
- **Linear**: see [`WORKFLOW.example.md`](../WORKFLOW.example.md)
  (`tracker.kind: linear`, `project_slug`, `api_key: $LINEAR_API_KEY`). The
  Codex `linear_graphql` client tool is only advertised when
  `agent.kind=codex`.
- **Jira**: see
  [`examples/WORKFLOW.jira.example.md`](../examples/WORKFLOW.jira.example.md)
  (`tracker.kind: jira`, `endpoint`, `project_slug`, `email: $JIRA_EMAIL`,
  `api_key: $JIRA_API_TOKEN`).

Browser issue CRUD is file-tracker only; Linear/Jira boards degrade to
read-only live status. Full request graphs are file-board-only.

## Agent backends

Set `agent.kind` in `WORKFLOW.md`:

```yaml
agent:
  kind: claude          # codex | claude | gemini | agy | kiro | opencode | pi | prime-agent | <profile name>

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000

pi:
  command: pi --mode json -p ""
  resume_across_turns: true
  turn_timeout_ms: 3600000

prime_agent:
  command: prime-agent -p --mode json
  resume_across_turns: true
  turn_timeout_ms: 3600000
```

Each backend reads its own block (`codex`, `claude`, `gemini`, `agy`, `kiro`,
`opencode`, `pi`, `prime_agent`); only the one matching the resolved kind is
used at runtime. `agent.kind: antigravity` aliases to `agy`.

**Plain-text CLI profiles.** `agent.profiles` declares any agent CLI that
reads the prompt on stdin and prints its answer on stdout, with no Python:

```yaml
agent:
  kind: my-cli
  profiles:
    my-cli:
      command: 'my-cli run "$(cat)"'   # required
      format: text                     # `text` (default) or `acp`, below
      unattended_flags: [--yes]        # appended unless already in command
      continuation_flag: --continue    # added on continuation turns
      continuation_position: before_prompt_arg  # or `append` (default)
      resume_across_turns: true        # default false
      turn_timeout_ms: 3600000         # timeouts default like other backends
```

`before_prompt_arg` inserts the flags before a trailing `"$(cat)"` argument
and appends them otherwise. Profile names are lowercased and cannot reuse a
built-in kind (`agy`, `antigravity`, `codex`, ...): that is a config error,
not an override. Unknown keys and formats other than `text` and `acp` are
config errors. `agent.kind`, `agent.stage_kinds`, ticket pins, chat sessions,
`agent.fallback_kinds` and `continuous_improvement.agent_kind` can select a
profile. `symphony doctor` checks the profile command's
executable. The built-in `agy` and `kiro` kinds run on the same profile
engine, configured through their own blocks.

**Agent Client Protocol (ACP) profiles.** `format: acp` runs any CLI that
speaks [ACP](https://agentclientprotocol.com) (protocol v1, JSON-RPC over
stdio) through one backend, [`backends/acp.py`](../src/symphony/backends/acp.py),
with no Python per agent. One agent process and one ACP session live for the
whole worker run; continuation turns send another `session/prompt` on the same
session. Verified on 2026-10-28 with Kiro (a "Reply with exactly: ok" turn
returned `ok` in 18.6 s):

```yaml
agent:
  kind: kiro-acp
  profiles:
    kiro-acp:
      command: kiro-cli acp --trust-all-tools
      format: acp
```

- The CLI must already be logged in; Symphony does not authenticate it.
- Permission requests are approved unattended, except commands flagged
  dangerous, which are denied.
- `unattended_flags`, `continuation_flag`, and `continuation_position` are
  ignored, because turns reuse the session.
- Per-turn token counts are not reported (the ACP usage field is still a
  draft); context size and cost are passed through as events, and the
  zero-token telemetry warning is not raised for ACP or plain-text profiles.
- The command's stdout is the JSON-RPC channel. Anything else printed there
  (for example a login-shell profile banner) is logged as a malformed line;
  ten in a row fail the turn, so keep shell profiles quiet on stdout.
- Not verified on Windows. `gemini --acp` (license error at `session/new`) and
  `opencode acp` (local plugin load failure) did not work on the test machine
  for account and plugin reasons outside Symphony.

**Codex model.** `codex.model` is unset by default, so the Codex CLI's own
default model applies. Set it to pin one (0.28 pinned `gpt-5.5`).

**Per-ticket pin.** A file-board ticket can opt into a different backend with
frontmatter:

```yaml
agent:
  kind: codex
```

The flat alias `agent_kind: codex` is also accepted for hand-edited cards.
Command and timeout settings still come from the matching global block.

**Per-state routing.** `agent.stage_kinds` maps board states to agent kinds so
cheap/fast agents can own light lanes (for example `Todo: gemini`,
`Document: gemini`) while a strong default handles Plan/Build/Review.
Resolution per dispatch: ticket `agent_kind` pin > `agent.stage_kinds[state]`
> `agent.kind`. The backend is re-resolved at every stage change, including
in-run lane transitions within one dispatch, so a ticket that goes In
Progress → Verify → Document gets each lane's configured backend.

**Fallback.** List `agent.fallback_kinds` (ordered, distinct, supported kinds)
so a quota-exhausted backend hands the ticket to the next one instead of
pausing for an operator.

**Heavy stages.** The stall detector cancels a worker that produced no
progress event within its backend's `stall_timeout_ms`. Widening that value
widens it for every lane; `agent.stall_timeout_ms_by_state` widens only the
heavy lanes:

```yaml
agent:
  stall_timeout_ms_by_state:
    Verify: 900000     # 15 min of silence is normal while the suite runs
```

The budget is resolved against the backend the ticket actually runs on (its
`agent_kind` pin or `stage_kinds` route), not the workflow default, so
`claude.stall_timeout_ms` applies to claude-pinned tickets on a codex-default
board.

### How each backend runs

| Backend | Invocation and session model |
|---------|------------------------------|
| Codex | One `codex app-server` subprocess per issue speaking the current JSON-RPC stdio protocol; multi-turn within one process. Pin to `codex-cli ≥ 0.39`. |
| Claude Code | `claude -p --output-format stream-json --verbose` (NDJSON). No persistent server; each turn spawns a fresh process with `--resume <session-id>` from turn 2. |
| Gemini | `gemini -p ""`, one-shot per turn (stdin prompt → stdout result). No native sessions; Symphony synthesizes a `gemini-<uuid>` id for bookkeeping. |
| AGY / Antigravity | `agy --print "$(cat)"` plus `--dangerously-skip-permissions`, one-shot per turn; `--continue` on continuation turns when `resume_across_turns` is true. |
| Kiro | `kiro-cli chat --no-interactive --trust-all-tools ...` headless chat. Kiro does not treat piped stdin as the first message, so Symphony bridges stdin into the positional input with `"$(cat)"` and adds `--resume` on continuation turns. Accepts `KIRO_API_KEY` or `kiro-cli login`. |
| OpenCode | `opencode run --format json --auto`, prompt passed as the documented `message` argument; adds `--session <id>` once OpenCode reports a real session id. |
| Pi | `pi --mode json -p ""` (JSONL), fresh process per turn with `--session <id>` from turn 2; usage accumulated from `message_end` events. Supports Anthropic / OpenAI / Gemini / Bedrock under one CLI ([pi.dev](https://pi.dev)); auth delegated to `~/.pi/agent/auth.json`. |
| ACP profile | The profile `command` as one long-lived process: `initialize` → `session/new` → `session/prompt` per turn, streaming `session/update`. A dead agent is relaunched between turns and the session resumed when the agent supports `session/resume` or `session/load`. |
| Prime Agent | `prime-agent -p --mode json` (same JSONL protocol as Pi), fresh process per turn with `--resume <id>` from turn 2. Auth through `~/.prime/agent/auth.json` or provider environment variables ([Prime Agent](https://github.com/cskwork/prime-agent)). On file boards, a clean exit after the ticket reaches a terminal state is accepted even if Prime Agent closes stdout before flushing `agent_end`; non-zero exits still fail. |

The `AgentBackend` Protocol hides these differences. The orchestrator only
sees normalized events (`session_started`, `turn_completed`, `turn_failed`,
…) and the latest usage / rate-limit snapshots.

### Adding a backend

A plain-text CLI needs only an `agent.profiles` entry (see above). A CLI with
a stream protocol worth parsing needs an adapter under
`src/symphony/backends/`. Adding one (for example an Ollama-driven local
model) means:

1. implementing the `AgentBackend` Protocol in a new module,
2. registering it in `build_backend()` (`src/symphony/backends/__init__.py`),
3. adding a `<kind>Config` dataclass under `src/symphony/workflow/` and
   threading it through `build_service_config` + `validate_for_dispatch`,
4. extending `SUPPORTED_AGENT_KINDS`.

The bar for upstreaming: passes the existing factory + event normalization
tests, does not bleed protocol-specific types into the orchestrator, and
ships a default `<kind>` block in `WORKFLOW.example.md`.

## Preflight and proof

```bash
symphony doctor ./WORKFLOW.md
```

```
PASS  server.port=9999              127.0.0.1:9999 is free
PASS  agent.kind=claude             claude → /usr/local/bin/claude
FAIL  hooks.after_create            contains placeholder 'my-org/my-repo' — every dispatch will fail with rc=128. Switch to the worktree default or replace with a real clone / `: noop`.
PASS  workspace.root=~/symphony_workspaces  exists and is writable
PASS  tracker.board_root            ./kanban (3 tickets)
```

Exit code is `0` when all checks pass, `1` if any FAIL, `2` if `WORKFLOW.md`
cannot be loaded. Doctor catches the common first-run failures in one pass:
port collision, missing CLI on `$PATH`, the shipped placeholder clone URL,
unwritable workspace, missing board directory. It also reports
`board.deep_merge_contract`, `agent.stage_contracts`, the API token gate, and
(on Windows) `shell.bash`.

Checks added in 0.29:

- `verify.command=<lane>`: each [verify command](#verify-gates)'s executable
  resolves on `$PATH` (WARN if not; shell builtins are skipped).
  `verify.config` FAILs on an invalid `verify:` block.
- `claude.bash_permissions`: WARN when an active lane runs Claude with
  `--permission-mode acceptEdits` and neither `.claude/settings.json` nor
  `.claude/settings.local.json` has a `permissions.allow` entry starting with
  `Bash`. Headless workers are then denied every shell command; a v0.25 live
  run lost a ticket to exactly this. Fix it with a rule like this, and commit
  the file, because workers run in worktrees of the committed tree:

  ```json
  {"permissions": {"allow": ["Bash(python3 -m pytest:*)", "Bash(git merge-tree:*)"]}}
  ```

- `app.release-contract`: FAIL when the board uses app release delivery but
  `packages/release` is not installed.

`symphony doctor --run-verify` also runs each verify command once in a
temporary detached worktree of `HEAD` (`verify.run=<lane>`: exit code,
timeout, duration, and the output tail on failure). The worktree is always
removed; a removal failure is reported as `verify.cleanup`.

`symphony [WORKFLOW]` and `symphony tui` run a doctor-lite preflight (agent
CLI on `$PATH`, `after_create` placeholder) before starting and print one
actionable sentence on failure; `symphony service start` runs the full
`symphony doctor`. When
the port is taken, startup reports which process holds it.

After doctor passes, prove the workflow through the runtime surfaces:

```bash
symphony ./WORKFLOW.md --port 9999
curl -s http://127.0.0.1:9999/api/v1/health
symphony runs ./WORKFLOW.md --limit 5      # --issue ID filters; --limit default 50, clamped 1-200
python scripts/smoke_web_api.py --base-url http://127.0.0.1:9999
```

`/api/v1/health` reports `starting`, `ok`, or `degraded`; `symphony runs`
prints recent registry attempts; the smoke script checks health, state, board,
static assets, issue CRUD, refresh, workflow, and stats.

## Running Symphony

### TUI

```bash
symphony tui ./WORKFLOW.md
# equivalent
symphony ./WORKFLOW.md --tui
```

The TUI needs an interactive terminal (TTY). Without one, `--tui` exits with
an error; run headless or use `symphony service start` and the web board
instead.

Columns are tracker states (`active_states` first, then `terminal_states`).
Cards show identifier + title, priority, labels (or blockers), and a runtime
indicator:

- **● green.** Running; shows `turn N`, last event, accumulated tokens.
- **↻ yellow.** In the retry queue; shows `retry #N` and the last error.
- **✓ green.** Completed in this session.

The header shows agent, tracker, workflow, language, running/retrying counts,
token totals, and provider-reported rate-limit headroom when available.

Key bindings (`?` opens a grouped help modal; the most-used keys are also in
the footer):

| Key                        | Action                                              |
|----------------------------|-----------------------------------------------------|
| `q`                        | Quit (asks twice while workers run; drains cleanly) |
| `r`                        | Force a refresh + re-poll the tracker               |
| `?`                        | Open / close the key-binding help modal             |
| `tab` / `shift+tab`        | Move focus to next / previous card                  |
| `j`/`k`, `↓`/`↑`           | Scroll the focused lane                             |
| `g`/`G`, `home`/`end`      | Jump to top / bottom                                |
| `space`/`pgdn`, `b`/`pgup` | Page down / up                                      |
| `enter` / `esc`            | Open / close the full-detail modal                  |
| `p`                        | Show / hide the detail pane                         |
| `]` / `[`                  | Focus the detail pane / return to the board         |
| `1`–`9` / `0`              | Zoom that lane (others shrink) / reset zoom         |
| `t` / `T`                  | Next / previous page of lanes                       |
| `+` / `-`                  | More / fewer lanes per page                         |
| `d`                        | Toggle compact / rich cards                         |
| `n` / `e`                  | Register a new ticket / edit the focused one        |
| `a` / `c`                  | Archive a Done card / confirm a Human Review card   |
| `S`                        | Skip Document for the focused ticket                |
| `P`                        | Pause / resume the focused running worker           |
| `s`                        | Open the run statistics screen                      |
| `L`                        | Cycle TUI + doc language                            |
| `/`                        | Open the filter prompt (`esc` clears)               |

Mouse: clicking a card focuses it; the wheel scrolls its lane.

**TUI + JSON API together.** Pin `server.port` in `WORKFLOW.md` and launch with
`--tui`; the web admin UI and JSON API are served on the same port:

```yaml
server: { port: 8765 }
```

```bash
symphony --tui ./WORKFLOW.md
curl -s http://127.0.0.1:8765/api/v1/state | jq
```

`--port N` overrides the workflow value; drop the `server` block to disable
the HTTP API entirely.

### Headless and `WORKFLOW-PROGRESS.md`

```bash
symphony ./WORKFLOW.md                  # headless; progress mirror auto-on
symphony ./WORKFLOW.md --no-progress-md # headless; no progress file
```

Other root flags: `--port`, `--host`, `--log-level`, `--progress-md-path`,
`--keep-awake` / `--no-keep-awake`, `--version`.

A live `WORKFLOW-PROGRESS.md` is rewritten next to the workflow file on every
tick and every state change in between:

```markdown
# Symphony Progress
_Updated: 2026-05-16 14:22:31 UTC_

## Kanban
| State        | Tickets |
|--------------|---------|
| Todo         | OLV-005, OLV-006 |
| In Progress  | OLV-002 (8m12s · 12k tok) |
| Verify       | OLV-001 |
| Done         | OLV-003, OLV-004 |

## Recent transitions
- `2026-05-16 14:22:31Z`  **OLV-002**  Todo → In Progress
- `2026-05-16 14:18:04Z`  **OLV-001**  In Progress → Verify
```

```yaml
progress:
  enabled: true                     # default true; CLI --no-progress-md wins
  path: docs/STATUS.md              # default: WORKFLOW-PROGRESS.md beside WORKFLOW.md
  max_transitions: 20               # how many recent transitions to keep
```

The mirror is read-only output written atomically; do not edit it by hand.

### Managed background service

Prefer the service command over ad-hoc shell jobs. It records the workflow it
started under `.symphony/run/<workflow-hash>.json`, so the same `WORKFLOW.md`
cannot be started again on a second port by accident:

```bash
symphony service start ./WORKFLOW.md --port 9999   # also --host (default 127.0.0.1), --replace, --skip-doctor
symphony service status ./WORKFLOW.md
symphony service restart ./WORKFLOW.md             # --host, --port, --skip-doctor, --timeout, --force
symphony service stop ./WORKFLOW.md                # --timeout (default 10), --force
symphony service logs ./WORKFLOW.md                # --lines (default 80)
```

`service start` runs `symphony doctor` before spawning and starts the
orchestrator with Python's module runner; the web admin UI is served on the
orchestrator port. Commands are launched without a shell, so the same path
works on macOS, Linux, and Windows.

### Logs

Symphony writes structured logs to **stderr only**. To keep them, redirect at
launch, then `tail -F log/symphony.log`:

```bash
mkdir -p log
symphony tui ./WORKFLOW.md 2>> log/symphony.log
symphony ./WORKFLOW.md --port 9999 2>&1 | tee -a log/symphony.log
```

### macOS keep-awake

While a run is active, Symphony holds a wake-lock on macOS so the screen saver
or lock screen cannot interrupt a long unattended pipeline. Disable per run
with `--no-keep-awake`, or persist:

```yaml
system:
  keep_awake: false   # default true; CLI --no-keep-awake also wins
```

Non-macOS hosts log `keep_awake_skipped` and continue without a wake-lock.

### Slack notifications (optional, off by default)

With this block, Symphony posts one message per tracker state transition.
Omit it and nothing is sent.

```yaml
notifications:
  slack:
    webhook_url: $SLACK_WEBHOOK_URL    # required; $VAR resolved at load time
    enabled: true                       # default true when webhook is set
    notify_on_states: []                # empty = every transition; or e.g. [Done, Blocked]
    templates:                          # optional per-state overrides
      Done: "✅ ${identifier} ${title} (${workflow})"
      Blocked: "🚧 ${identifier} blocked — ${title}"
    username: Symphony
    icon_emoji: ":robot_face:"
    timeout_ms: 5000
```

Template placeholders: `${identifier}` `${title}` `${prev_state}`
`${next_state}` `${workflow}` `${reason}`. Unknown keys render literally and
never raise. Network errors are caught and logged
(`slack_notify_network_error`), so a Slack outage cannot block transitions.

### How dispatch works

```
┌────────────┐    poll      ┌──────────────┐    matches active_states
│  kanban/   │  ─────────▶  │ Orchestrator │  ─────────────────────────┐
│  *.md      │   30s tick   │ (scheduler)  │                            │
└────────────┘              └──────────────┘                            ▼
      ▲                            │                          ┌──────────────────┐
      │                            │ creates workspace        │  Workspace       │
      │ agent writes               ▼                          │  ~/sym…/TASK-1   │
      │ ## Resolution     ┌──────────────────┐                │  + after_create  │
      │ + state: Done     │  AgentBackend    │  ◀────────────│    hook ran      │
      └───────────────────│  (codex/claude/  │                └──────────────────┘
                          │   gemini/...)    │                          │
                          │  per-turn loop   │  before_run hook ──▶ turn(s)
                          └──────────────────┘                          │
                                                                        ▼
                                                                  after_run hook
```

## Web app and JSON API

```bash
symphony ./WORKFLOW.md --port 9999
# open http://127.0.0.1:9999/
```

`/` serves the built-in web Kanban app (no build step, no signup, loopback
only):

- **Projects.** Switch among independent project boards, inspect each
  repository / workflow / issue-store path, and create or open projects.
- **Board.** Create / edit / delete issues, drag cards between columns, watch
  live run badges (turn count, tokens), pause / resume workers, and skip
  Document for tickets that do not need wiki write-back. The board defaults to
  the four active agent lanes; `Human Review`, `Done`, `Blocked`, and
  `Archive` stay in the compact **Review and parked** group until you switch
  to `All`. Switch from **Lanes** to **Request** for a read-only,
  scheduler-authored dependency execution list with queue ranks, waves,
  capacity waits, retry ownership, and final dispatch refusals. Full request
  graphs are file-board-only; Linear/Jira show an explicit unsupported state.
- **Workflow.** Add / delete / rename / reorder columns and edit each column's
  stage prompt. Changes write back into `WORKFLOW.md` frontmatter with comments
  preserved; tickets in renamed or removed columns migrate automatically.
- **Git.** Inspect history, task branches, comparisons, and diffs; delete
  branches; merge verified work; push branches; or open a pull request.
- **Chat.** Operator chat with the intent gate (see [Chat intake](#chat-intake)).
- **Preview.** Start, restart, or stop a loopback-only product preview from a
  detached target-branch checkout, with a health check, URL, and bounded logs.
- **Runs.** Search and filter recorded attempts; inspect bounded, redacted
  lifecycle timelines, token usage, workspace/branch/commit references; and
  download a diagnostic JSON bundle. Because this includes local paths and
  failure excerpts, its API is available only to loopback clients.
- **Stats.** Tokens per day, throughput, per-column dwell time, per-agent
  totals, average cycle time (from `.symphony/stats.jsonl`).
- **Settings.** Branch policy (`agent.feature_base_branch` /
  `agent.auto_merge_target_branch`) from a real local-branch dropdown, lane
  presets, and continuous-improvement controls. The header refresh button
  triggers an orchestrator `poll + reconcile`.

| Method | Path                              | Purpose                                      |
|--------|-----------------------------------|----------------------------------------------|
| GET    | `/api/v1/health`                  | Tick-loop / tracker / run-registry health    |
| GET    | `/api/v1/state`                   | Snapshot of running, retrying, totals, limits |
| GET    | `/api/v1/board`                   | Columns + issues + live run info             |
| GET    | `/api/v1/requests`                | Request groups + scheduler summary (file board) |
| GET    | `/api/v1/requests/{id}/schedule` | Read-only dependency graph + consumed scheduler decisions |
| GET    | `/api/v1/runs?issue=&limit=&query=&status=&agent=` | Search/filter run attempts       |
| GET    | `/api/v1/runs/{run_id}`           | Bounded run metadata and lifecycle timeline  |
| GET    | `/api/v1/runs/{run_id}/diagnostic`| Download redacted diagnostic JSON            |
| POST/PATCH/DELETE | `/api/v1/issues[...]`  | Issue CRUD (file tracker)                    |
| PUT    | `/api/v1/workflow/states`         | Column add / delete / rename / reorder       |
| GET/PUT| `/api/v1/workflow/prompts/<state>`| Read / edit a column's stage prompt          |
| PUT    | `/api/v1/workflow/branch-policy`  | Update feature base / merge target branches  |
| GET/POST | `/api/v1/workflow/presets[...]` | List lane presets / apply one (`/apply`)     |
| *      | `/api/v1/chat/...`                | Operator chat sessions + WebSocket stream    |
| GET/POST | `/api/v1/projects[...]`         | List, create, inspect, and open projects      |
| GET/POST | `/api/v1/git/...`               | Branches, history, compare/diff, merge, push, PR |
| GET/POST | `/api/v1/preview[...]`          | Preview status plus start/restart/stop        |
| GET    | `/api/v1/stats?days=N`            | Aggregated run statistics                    |
| POST   | `/api/v1/refresh`                 | Coalesced trigger of poll + reconcile        |
| POST   | `/api/v1/{id}/pause` `/resume`    | Hold / release a running worker              |
| POST   | `/api/v1/issues/{id}/skip-document` | Move idle Document ticket to Human Review (deprecated alias: `/skip-learn`) |
| POST   | `/api/v1/{id}/confirm-done` | Confirm Human Review through host history/merge/hooks/cleanup; requires retained workspace and refuses run-bound release evidence |

**TUI vs web app.** The web app is the full browser editor for file boards; it
uses the same tracker/workflow modules as the CLI. The TUI is keyboard-first:
create/edit tickets, archive, confirm Done-gated cards, pause/resume, skip
Document, filter, and inspect details. Intentionally out of scope:

- No drag-drop inside the terminal TUI. Use the web board, `symphony board mv
  ID State`, or the tracker UI.
- No full agent-output log pane. Agent stdout/stderr goes to the structured
  log; tail it in a side terminal.
- No direct Linear/Jira mutation from the web board.

## Reverse proxy, tunnels, and API token

The board trusts loopback. Behind cloudflared, ngrok, nginx, or any other
proxy, the browser arrives under a public name that loopback allowlists cannot
know, and project creation fails with `forbidden_origin` (or
`forbidden_host`). Declare the public front door:

```bash
SYMPHONY_TRUSTED_ORIGINS=https://symphony.example.com symphony ./WORKFLOW.md --port 9999
```

- Comma-separate several entries. A bare hostname matches any scheme and
  port; `*` trusts every origin (avoid it).
- Once trusted origins are declared, the loopback-only routes (project
  management, `/_debug/tasks`) no longer trust the proxy's loopback TCP
  connection. They read the real client address from `X-Forwarded-For`
  (first entry) or `Forwarded: for=`, so the proxy must set one of those
  headers, and only a loopback client passes. Without trusted origins those
  headers are ignored.
- A mutating browser request (POST/PUT/PATCH/DELETE) must carry
  `Content-Type: application/json`, even with an empty body. A plain
  `curl -X POST` from a shell still works without one.

> **Warning:** everything the tunnel exposes is reachable by whoever can reach
> the tunnel. Put authentication (for example Cloudflare Access) in front of it.

Symphony can also require a bearer token on every operator `/api/` request:

```bash
export SYMPHONY_TRUSTED_ORIGINS=https://symphony.example.com
export SYMPHONY_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
symphony ./WORKFLOW.md --host 0.0.0.0 --port 9999

curl -H "Authorization: Bearer $SYMPHONY_API_TOKEN" \
  http://127.0.0.1:9999/api/v1/state
```

- With the token set, the web app prompts after its first `401` and keeps the
  value in tab-scoped `sessionStorage`; API fetches and the Chat WebSocket
  attach it automatically. An unset or blank value keeps the loopback default.
- Use one whitespace-free random value and keep it out of `WORKFLOW.md`, shell
  history, screenshots, and logs. `symphony doctor` reports whether the gate
  is enabled and warns about values that can never match.
- Browsers cannot set an Authorization header on a WebSocket handshake, so
  Chat sends the token once as `?token=`. Configure proxies and access logs to
  omit or redact query strings, or put an upstream access layer in front.
- The managed-service controller uses a separate random per-launch capability
  (256 random bits) only for `/api/v1/health`; it never persists or reuses the
  operator API token. Health capability headers must be URL-safe and 32–128
  characters; short or malformed manually inherited service IDs never bypass
  bearer authentication.

## Lane presets and stage contracts

Boards start from a preset and stay customizable:

- **default.** The 4-lane board `Todo → In Progress → Verify → Document`. Short
  stage prompts; the stage contracts in `orchestrator/contracts.py` are the
  mechanical gate. Complex work is a ticket DAG (`--blocked-by` /
  `--request`), not extra lanes.
- **deep.** An optional 8-lane pipeline `Intake → Research → Plan → Review →
  Build → QA → Verify → Document` for complex deliveries. Each lane carries
  its own lean gate (Verify/Document run a literal `grep 'verdict: GREEN'`
  check), and the orchestrator re-checks the same facts at every lane
  transition (the vault file exists, its verdict line is present, the ticket
  section was appended), rewinding the ticket with `## Contract Failure`
  otherwise. The Plan lane spawns the Build/QA/Verify/Document ticket DAG via
  `symphony board new --blocked-by --request`.

Switch presets from the web **Settings** page, or via
`GET /api/v1/workflow/presets` + `POST /api/v1/workflow/presets/apply`.
Applying a preset round-trips through the same comment-preserving
`WORKFLOW.md` machinery as lane CRUD; tickets in removed lanes migrate to a
fallback state. Lane add, delete, rename, and per-column prompt editing keep
working afterwards.

### Deep preset merge contract

Every deep lane is a separate ticket with its own worktree on its own
`symphony/<ID>` branch. A downstream lane only sees a Build slice that has
already landed on the branch its worktree was cut from, so branch policy is
part of the preset's contract:

```yaml
agent:
  auto_merge_on_done: true        # the orchestrator merges each ticket at Done
  auto_merge_push_target: true    # push + verify the target upstream (false = local-only)
  feature_base_branch: ""         # both empty = the host's current branch
  auto_merge_target_branch: ""    # must resolve to the SAME branch
```

- The **orchestrator** merges a ticket's branch when it reaches `Done`. No
  lane merges by hand. Verify proves, Document documents.
- `auto_merge_push_target` defaults to `true`, so a successful merge also
  pushes and verifies the target branch's configured upstream. Set it to
  `false` for a local-only run: the same dirty-tree, conflict, and explicit
  `--no-ff` checks still run, but no `git push` or `git ls-remote` is
  attempted. The local-only choice applies to automatic Done merges and the
  web UI's manual Git merge; ticket final-history snapshotting also stays
  local until the target merge. Release-evidence snapshots remain local audit
  records in either mode.
- Build merges are gated by the **Review** lane's `verdict: PASS` (spawned
  Build tickets stay `blocked_by` the request ticket, which only reaches
  `Done` after Review passes), *not* by Verify. A merged slice is reviewed,
  not yet verified.
- A Verify `verdict: RED` reopens the offending Build tickets and holds
  delivery: `DOCUMENT-*` has its own `verdict: GREEN` gate, so it cannot run
  until the reopened slice is re-built, re-merged, and re-verified.

`symphony doctor` reports this as `board.deep_merge_contract` and fails when a
deep board disables `auto_merge_on_done` or points the feature base and merge
target at different branches.

### Stage contracts on a customized board

The mechanical evidence floor (`orchestrator/contracts.py`) is gated by
`agent.stage_contracts`:

| value            | behaviour                                                        |
|------------------|------------------------------------------------------------------|
| `auto` (default) | enforce on a shipped preset: default lanes, or exactly the deep lanes |
| `on`             | always enforce the default contract set, whatever the lanes are called |
| `off`            | never enforce; the stage prompts are the only gate               |

Under `auto`, renaming a lane (`Document` → `Docs`) turns the validator off and
your prompts become the gate. It is never silent: Symphony logs
`stage_contracts_disabled` at every config load, `symphony doctor` reports
`agent.stage_contracts`, `GET /api/v1/workflow` exposes
`agent.stage_contracts_enabled`, and the Settings page shows a hint. Set
`stage_contracts: on` to keep the shipped contracts on a renamed board.

See [PIPELINE.md](PIPELINE.md) for the stage contracts in depth and
[PIPELINE-DEMO.md](PIPELINE-DEMO.md) for a walkthrough.

## Verify gates

Stage contracts check what the agent *wrote*. Verify gates let your own
command decide whether a ticket may leave a lane (implementation:
[`workflow/verify.py`](../src/symphony/workflow/verify.py)).

```yaml
verify:
  timeout_ms: 600000              # default; per command run
  commands:                       # keyed by the lane being LEFT
    "In Progress": python -m pytest -q
    Verify: python -m pytest -q && ruff check .
```

- When a worker moves a ticket forward out of a listed lane (to the next
  active lane or to a successful terminal state such as Done), Symphony runs
  the command with `bash -c` in the ticket workspace. A move that jumps over
  listed lanes (In Progress straight to Done) runs each skipped lane's command
  too, in lane order, and a failure names the skipped lane. A worker that
  crashes after a forward edit still faces the gate when its exit is handled;
  only an operator or reconcile cancellation skips it. Rewinds and moves to
  `Blocked` do not trigger it. `SYMPHONY_GATE_LANE` names the lane whose
  command is running.
- The environment adds `SYMPHONY_ISSUE_ID`, `SYMPHONY_ISSUE_IDENTIFIER`,
  `SYMPHONY_FROM_STATE`, `SYMPHONY_TO_STATE`, `SYMPHONY_WORKFLOW_DIR`,
  `SYMPHONY_WORKSPACE_ROOT`, and `SYMPHONY_PYTHON` to the hook branch
  variables.
- Exit 0 passes. A non-zero exit or a timeout (the whole process group is
  killed) appends a `## Verify Failure` section to the ticket, with the
  command, the exit code or "timed out after N s", the duration, and the last
  60 lines of combined output, then rewinds the ticket to the lane it left.
  The usual rewind budget applies.
- Verify runs whether or not `agent.stage_contracts` is on. When the lane also
  has a stage contract, both must pass.
- Every run appends one JSON line to `.symphony/traces/<ID>.jsonl`: `ts`,
  `issue`, `from`, `to`, `command`, `exit_code`, `timed_out`, `duration_ms`,
  `passed`.
- Lane names match case-insensitively. Unknown keys under `verify:`, empty
  commands, and a non-positive `timeout_ms` are config errors.

Check commands before launching with `symphony doctor` (PATH lookup) and
`symphony doctor --run-verify` (one real run against `HEAD`).

## Needs: tickets waiting for an operator

Symphony keeps `.symphony/needs.json` next to `WORKFLOW.md`, refreshed off the
main tick (implementation: [`needs.py`](../src/symphony/needs.py)). It lists
every ticket in a Blocked terminal lane or the budget-exhausted lane:

```json
{"TASK-1": {"state": "Blocked", "since": "2026-10-28T09:12:00Z",
            "reason": "verify command failed: python -m pytest -q",
            "action": "make this command pass: python -m pytest -q"}}
```

- `reason` and `action` come from the last `## Verify Failure`,
  `## Environment Block`, `## Blocked`, or `## Contract Failure` section of the
  ticket (`Reason:` / `Action:` lines when present, otherwise its first two
  lines). Without one, they read `unknown` and `open the ticket`.
- `since` is the first time the ticket was seen waiting.
- With notifications configured, one message goes out per reason group (all
  tickets sharing a reason) per UTC day; `.symphony/needs-sent.json` records
  what was sent.
- `symphony board show <ID>` prints
  `needs: <reason>; action: <action> (since <time>)`.

## Skills

Drop a skill next to `WORKFLOW.md` and attach it to a ticket in frontmatter:

```
skills/
└── tdd/
    └── SKILL.md      # ---\n name: tdd\n description: test first\n--- + body
```

```yaml
# kanban/TASK-7.md frontmatter
skills: [tdd]
```

On dispatch, each attached skill's body is appended to the first-turn prompt
under `## Attached skills`. Skills are not exposed in the web/TUI issue forms;
add them by hand. Unknown skill names are reported to the agent as "not found"
instead of silently dropped.

## Chat intake

Requires the optional `packages/chat` distribution (see
[Optional components](#optional-components)); without it the Chat entry is
hidden. The admin UI's **Chat** page is backed by the same agent CLIs. Each new session
can use Claude Code, Codex, Gemini CLI, AGY, Kiro, OpenCode, Pi, or Prime
Agent; the workflow's configured agent is the default. Chat is the front door
of a delivery cycle with exactly one human gate:

1. **Propose.** Type a request. The agent asks at most two clarifying
   questions, only when the request is genuinely ambiguous, then writes a
   plain-language summary and an **intent card**: Problem, Evidence (each
   claim labelled verified or assumed), Success criteria as checkboxes, Out of
   scope, Constraints, Open questions, and a track (`full`, or `micro` when the
   exact files are known and an existing command proves success). The server
   scans the card with the sdlc-kit trip-wire heuristics (migrations,
   deletion, public API, security, infra); hits are shown on the card,
   recorded as `## Trip-wires` on the ticket, and force the `full` track.
2. **Approve.** Press **Approve intent** or reply `approve`. Any other reply
   supersedes the card so the agent can propose a revision. This is the only
   human decision in the cycle.
3. **Deliver.** The server, never the agent, files the request ticket through
   the validated board tool and records `.sdlc/work/<slug>/intent.md` (the
   sdlc-kit stage-1 artifact, with an `## Approval` section). On a deep-preset
   board the ticket lands in `Intake` and the pipeline runs unattended. On the
   default board it lands in the first active state and Todo triage routes it.

Asking for a **new application** in edit mode makes the agent propose a
separate project with `preset: deep`. Existing tickets can still be edited in
edit mode with `symphony board new` / `board update`; the request itself is
never filed by the agent, and the intent card works in Q&A mode too.

## Per-ticket artefacts and board deliverables

Every artefact a ticket produces lives under `docs/<TICKET-ID>/<stage>/`. See
[PIPELINE.md](PIPELINE.md#per-ticket-artefact-root) for the layout, what to
commit, and the `${LLM_WIKI_PATH:-./docs/llm-wiki}/` carve-out.

Files a reviewer just wants to *open* (screenshots, exported reports, PDFs)
go elsewhere. An agent saves them to `.symphony-artifacts/` at its workspace
root, and after each turn Symphony copies new files to
`.symphony/artifacts/<TICKET-ID>/` on the host. They appear in the web ticket
drawer (images preview inline) and as an `## Artifacts` list on the ticket,
and they survive workspace removal at Done. The directory is kept out of Git,
so deliverables never land in the merge.

Optional `.symphony-artifacts/manifest.json` adds titles:

```json
{ "artifacts": [{ "file": "login.png", "title": "Login page", "summary": "After the fix" }] }
```

```yaml
artifacts:
  enabled: true                 # false turns collection off entirely
  dir: .symphony-artifacts      # workspace directory agents write into
  max_file_mb: 25
  max_ticket_mb: 200
  ttl_days: 30                  # drop artifacts 30 days after a ticket is archived; 0 disables
  require_for_done: false       # true blocks Done until a ticket has at least one artifact
```

## Machine gate for application releases

Requires the optional `packages/release` distribution (see
[Optional components](#optional-components)); without it, release-labelled
tickets are parked in `Blocked` and never merged. Production application
delivery is opt-in. Add the `app-release` label to the
Verify ticket, add `app-release-finalizer` to the delivery ticket the contract
names, and commit `release-contract.yaml` at the project root. Background:
[ADR 0002](adr/0002-machine-enforced-app-release-contracts.md).

On every forward Verify transition (even when `agent.stage_contracts` is off)
Symphony resolves the configured target branch on the host, requires the
workspace contract bytes to match `release-contract.yaml` at that commit
exactly, and checks every declared runner source against both the target blob
and the workspace hash. Tickets without the label are unaffected.

```yaml
schema_version: 1
target_branch: main
finalizer_ticket: DELIVER-1
implementation_tickets: [BUILD-1, BUILD-2]
launch:
  command: npm run dev
  ready_url: http://127.0.0.1:3000
runner:
  command: npm run release:qa
  sources:
    - path: tools/release-runner.mjs
      sha256: <64 lowercase hex>
viewports:
  desktop: {width: 1440, height: 900}
  tablet: {width: 768, height: 1024}
  mobile: {width: 390, height: 844}
checks:
  - {id: feature.primary-flow, kind: feature, description: Primary flow works, repair_group: workflow, required_viewports: [desktop, mobile]}
  - {id: control.navigation, kind: control, description: Every navigation control works, repair_group: navigation, required_viewports: [desktop, mobile]}
  - {id: visual.layout, kind: visual, description: Layout matches the product brief, repair_group: presentation, required_viewports: [desktop, tablet, mobile]}
  - {id: responsive.no-clipping, kind: responsive, description: Content does not clip or overlap, repair_group: presentation, required_viewports: [desktop, tablet, mobile]}
  - {id: accessibility.keyboard, kind: accessibility, description: Primary flow is keyboard operable, repair_group: accessibility, required_viewports: [desktop]}
  - {id: reliability.runtime, kind: reliability, description: Runtime stays free of unexpected errors, repair_group: runtime, required_viewports: [desktop]}
```

Each verifier writes release outputs only below `docs/<VERIFIER>/qa/`. The
manifest path is fixed at `release-evidence.json`; native results and cited
artifacts sit beside it. The manifest binds the raw contract hash, the full
target SHA, the runner output, the exact check coverage, the viewport
coverage, and at least one contained, non-empty, hashed artifact per check. A
real evidence file has exactly one row per contract check (one shown here):

```json
{
  "schema_version": 1,
  "verifier_ticket": "VERIFY-1",
  "contract_sha256": "<64 lowercase hex>",
  "target_branch": "main",
  "target_sha": "<full 40-character commit>",
  "runner": {
    "name": "project-native",
    "command": "npm run release:qa",
    "exit_code": 0,
    "results_path": "docs/VERIFY-1/qa/native-results.json",
    "results_sha256": "<64 lowercase hex>"
  },
  "checks": [{
    "id": "feature.primary-flow", "status": "PASS",
    "expected": "Primary flow completes", "actual": "Completed",
    "repro": "npm run release:qa -- feature.primary-flow",
    "viewports": ["desktop", "mobile"],
    "artifacts": [{"path": "docs/VERIFY-1/qa/primary-flow.png", "sha256": "<64 lowercase hex>"}]
  }],
  "console_errors": [],
  "failed_requests": []
}
```

The hashed native result is a JSON object with exactly `schema_version`,
`verifier_ticket`, `contract_sha256`, `target_branch`, `target_sha`, and
`checks`; each result check carries only `id` and `status` and must match the
evidence exactly. Run the same host validator a transition uses:

```bash
symphony release check ./WORKFLOW.md --ticket VERIFY-1 --workspace /path/to/verifier-workspace
```

Rules:

- Missing, malformed, unsafe, or stale evidence rewinds the same verifier.
  The evidence runner command must equal the contract command exactly. Its
  exit code is zero if and only if every exact native and evidence check
  status is PASS.
- A coherent nonzero RED creates only three kinds of repair group: product
  check, console/network, and ancestry. There is no derivative exit-code
  repair. Symphony groups failures into idempotent repair tickets plus one
  fresh verifier, and the finalizer waits until those finish.
- The fresh verifier carries the expected contract hash in a durable
  `release-contract-sha256-<hash>` label and binds its lineage with
  `release-finalizer-<ticket>`. It replaces only that finalizer's historical
  release-verifier blockers and leaves unrelated dependencies alone.
- Labels opt a ticket in and help diagnosis; after the first dispatch they no
  longer grant approval. Symphony stores the verifier, finalizer, expected
  contract, unique cycle generation, and exact verifier and finalizer runs in
  `.symphony/state.db`.
- The finalizer cannot run until the verifier sits in an explicit success
  terminal with no live lease, and it must record completion in the same
  bound run. A verifier run that is already bound and live may continue after
  GREEN.
- If an approved verifier is redispatched after a restart or returned to an
  active lane, Symphony creates a new pending generation, moves it back to
  Verify, and requires fresh evidence. It never lends old approval to a new
  run.
- The host owns repair and fresh-verifier creation. Symphony reserves a
  deterministic file-board identifier in SQLite before creating each
  lifecycle ticket, then reconciles that exact ticket. Losing the process
  after the file write, concurrent services, and a worker editing labels
  cannot produce a second ticket for the same release fingerprint and repair
  key.
- Final delivery stores a completion token for the exact terminal ticket
  bytes and replacement generation. Any later rewrite invalidates the
  approval and starts a fresh Verify cycle.
- On startup, Symphony reopens release state that is stale, or terminal but
  unproven. It takes a dedicated cleanup lease before committing or removing a
  lingering evidence workspace, so a live peer run wins safely and an
  in-progress cleanup blocks a gate replacement.
- Upgrading a database with pre-provenance release rows writes a timestamped
  backup beside `.symphony/state.db` while the service runs, assigns durable
  generations and evidence identity, and converts legacy pending or approved
  rows to fresh pending cycles. That invalidation is deliberate: those rows
  cannot prove exact-run completion.

> **Limits:**
> - App-release verifier runs and repair or fresh-verifier mutation need
>   `tracker.kind: file`. On a Linear or Jira board, Symphony refuses a
>   labeled card before acquiring a run lease or starting an agent turn, and it
>   does not rewind the external card; a card that already advanced needs an
>   operator to rewind it. Boards that have not opted in behave as before and
>   get no doctor warning.
> - The target is resolved locally and read-only, at
>   `refs/heads/<target_branch>` in the workflow repository. Symphony does not
>   fetch a remote, compare a deployed revision, or synchronize the branch.
>   Synchronize the local target and serialize external writers before you
>   release.
> - `symphony release check` validates evidence; it does not grant host
>   lifecycle authority.
> - Hashing the contracted sources freezes the runner definition for one
>   verification cycle. Declared external tooling and its runtime stay outside
>   that guarantee by design. An operator still has to run independent browser
>   QA against the final target.

## Continuous improvement (experimental)

**Experimental and fully opt-in.** With no `continuous_improvement:` block in
`WORKFLOW.md`, nothing runs. The heartbeat periodically inspects the repo
*without touching product code* and files findings as normal board tickets,
which then flow through the ordinary pipeline.

```yaml
continuous_improvement:
  enabled: true
  interval_ms: 1800000            # heartbeat cadence
  modes: [readiness, blocked_fixes, security, market_research,
          feature_improvements]
  mode_interval_hours:            # per-mode cadence floor (optional)
    market_research: 168          # weekly
  max_improvement_tickets_per_run: 3
```

| Mode | What it does |
| --- | --- |
| `readiness` | Runs tests / lint / type-check on a proven baseline; failures become bug tickets. |
| `blocked_fixes` | Triages `Blocked` / `Human Review` tickets into a linked fix ticket carrying a root-cause note (`blocked_by` edge back to the source). |
| `security` | Optional dependency/vulnerability scans (`pip-audit`, `npm audit`) into patch tickets. A missing scanner is *not available*, never a finding. |
| `market_research` | One agent turn surveys current trends and competitor features for **this** app (from README/docs/wiki) and proposes improvements with evidence links. |
| `feature_improvements` | One agent turn reviews UX and code health and proposes improvements. |

`enabled: true` with no `modes:` means readiness only. Proposal tickets are
capped per run, de-duplicated against open tickets, labelled `ci`, and grouped
under one `REQ-CI-<date>-<n>` request. The agent-driven modes get a short
prompt (overridable in `docs/symphony-prompts/ci/`) and may write nothing but
their JSON proposal file; the heartbeat files the tickets. Modes and cadence
are also editable from the web **Settings** page. Scoring rubric:
[continuous-improvement/rubric.md](continuous-improvement/rubric.md).

## PM Workspace

PM Workspace is a local Artifacts screen for planning a change (Idea,
Requirements, Design, Plan, Code Files) before implementation, approving one
revision, and handing it off as ordinary file-board tickets. It is disabled
unless the `pm` switches are enabled in `WORKFLOW.md`. Try it on sample
content without touching a real project:

```bash
symphony pm demo             # English
symphony pm demo --lang ko   # Korean; --path DIR picks the folder
```

The demo needs `git` and Node.js 20.19.0 or newer, and is not verified on
Windows. PM Workspace requires the optional `packages/pm` distribution (see
[Optional components](#optional-components)). Its browser assets live in
`packages/pm/src/symphony/pm/static/`; rebuild the vendored bundle with
`npm ci && npm run build:pm-assets` inside `packages/pm`. Details:
[pm-workspace.md](pm-workspace.md) and
[pm-workspace-operations.md](pm-workspace-operations.md).

## Language (i18n)

`tui.language` (`en` default, or `ko`) sets the TUI language; the
`SYMPHONY_LANG` environment variable overrides it, and `L` in the TUI cycles
it. The same setting drives artefact language: every prompt gets a one-line
directive so kanban comments and `docs/<id>/<stage>/*.md` come back in the
chosen language, and `{{ language }}` is available to prompt templates.

```yaml
tui:
  language: en
```

## Platforms

Symphony runs on macOS, Linux, and Windows. Windows needs Git Bash (Git for
Windows); doctor reports `FAIL shell.bash` without it, and the WSL `bash.exe`
launcher is rejected. On Windows, prefer Windows Terminal for the TUI; the
legacy cmd.exe console may glitch colors. See
[platform-compat.md](../src/symphony/templates/project/skills/symphony-skill/reference/platform-compat.md)
for per-platform setup and caveats, and the factory guide's
[Windows notes](../src/symphony/templates/project/skills/symphony-skill/reference/factory.md#windows-service-interruption).

## Architecture and source layout

A multi-agent fork of [OpenAI's Symphony reference implementation](https://github.com/openai/symphony).
Upstream polls a tracker and runs a Codex session inside a per-issue
workspace. This fork keeps that file-first orchestrator and adds eight agent
backends plus data-only `agent.profiles` (plain-text and ACP), the TUI and web operator surfaces, SQLite run leases, restart-safe
issue flags, and locked Markdown ticket writes.

**Reliability ledger.** `.symphony/state.db` is a single-node SQLite ledger.
Active run leases block duplicate dispatch across restarts, dead-owner
processes are fenced and reaped, and the next Run attempt can resume from the
latest completed-turn checkpoint. Retry / pause / budget-exhausted flags
survive process exit. Markdown tickets remain the human source of truth;
SQLite holds runtime coordination state that should not be hand-edited.

See [architecture.md](architecture.md) and the [ADRs](adr/).

```
src/symphony/
  backends/          AgentBackend Protocol + factory + normalized events;
                     codex.py, claude_code.py, gemini.py, agy.py, kiro.py,
                     opencode.py, pi.py, prime_agent.py adapters;
                     plain_cli.py (format: text) and acp.py (format: acp)
                     run agent.profiles
  trackers/          TrackerClient Protocol + factory; file.py (locked
                     Markdown ticket mutations), jira.py, linear.py, _retry.py
  workflow/
    parser.py        WORKFLOW.md frontmatter/body parser
    config.py        frozen config dataclasses (incl. agent.stage_kinds)
    builder.py       ServiceConfig construction + validation
    mutate.py        comment-preserving workflow edits for the web UI
    presets.py       lane presets (4-lane default, 8-lane deep)
    preflight.py     dispatch-time validation
    verify.py        verify: gates (config, runner, traces)
  orchestrator/
    core.py          scheduler/state machine (blocked_by-aware dispatch)
    run_registry.py  SQLite WAL run leases + issue flags
    contracts.py     stage-contract validation helpers
    release_seam.py  only door to the optional release package; fail-closed
                     parking when it is absent
  cli/
    main.py          root dispatch + `symphony [WORKFLOW]`
    board.py         `symphony board ...` validated ticket tool + `graph`
    doctor.py        `symphony doctor` WORKFLOW.md preflight checks
    eject.py         `symphony eject`
    factory.py, project.py, pm.py, release.py   subcommands (pm/release
                     print an install hint without their package)
  factory/           factory templates and bundled skills
  templates/project/ project bootstrap: WORKFLOW.md, AGENTS.md,
                     defaults.yaml, prompts, setup script, operator skill
  utils/             auto_merge.py, git_inspect.py, git_ops.py, git_sandbox.py,
                     archive.py, keep_awake.py, wiki_sweep.py
  notifications/     Slack state-transition notifications
  tui/               Textual Kanban TUI package
  web/static/        built-in browser app assets
  webapi/            web app REST routes (one module per resource) + static SPA serving
  server.py          aiohttp server, health/state/refresh routes
  hub.py             multi-project hub
  projects.py        multi-project registry + managed service metadata
  product_preview.py detached target-branch preview lifecycle
  agent_modes.py     qa/edit interaction-mode configs (chat + improvement sweep)
  needs.py           .symphony/needs.json + daily needs notifications
  continuous_improvement.py  idle-time improvement-proposal loop
  i18n.py            TUI/doc language switching
  stats.py           .symphony/stats.jsonl aggregation
  skills.py          SKILL.md discovery + prompt injection
  service.py         `symphony service` background lifecycle
  progress_md.py     WORKFLOW-PROGRESS.md live mirror
  mock_codex.py      demo backend via `python -m symphony.mock_codex`
  agent.py           back-compat shim re-exporting backends.* symbols

packages/            optional distributions (same `symphony.*` namespace)
  pm/src/symphony/pm/            PM Workspace, routes, static/ browser assets
  chat/src/symphony/chat/        chat sessions (__init__), intent.py, routes.py
  release/src/symphony/release/  authority, contracts, cycle, transition, cli
```

## Tests and contributing

PRs welcome. External contributions target `dev` by default; see
[CONTRIBUTING.md](../CONTRIBUTING.md) and the PR template. Before opening one:

```bash
python -m pip install -e ".[dev]" -e packages/pm -e packages/chat -e packages/release
npm ci --prefix packages/pm  # PM Node UI tests
python -m pytest -q          # must stay green
python -m ruff check src tests packages/pm packages/chat packages/release
symphony-pyright             # pins Pyright to the interpreter that runs it
```

`tests/test_core_without_pm.py`, `test_core_without_chat.py`, and
`test_core_without_release.py` check that core works without each package;
CI also installs the core wheel alone.

The suite covers the upstream conformance suite, backend unit tests (factory,
event normalization, per-CLI command/session handling), board-tool DAG
validation, run-registry persistence, file-tracker locking, web API
contracts, chat intake, lane presets, and Textual `Pilot`-driven TUI smoke
tests. Subprocess-driven integration tests against real CLIs are kept out of
CI on purpose; run them locally.

## Known limits

Inherited from upstream:

- No SSH worker extension; single-host only.
- No tracker adapters beyond Linear, Jira, and the file-based Kanban.

Fork-specific:

- Run leases and issue flags persist in SQLite, but Symphony does not
  reattach to an in-process worker after a hard crash. Markdown ticket state
  is the recovery checkpoint.
- Claude Code's mid-turn streaming usage events are read but not surfaced; the
  terminal `result` event is the source of truth for token totals.
- OpenCode token usage is parsed best-effort from JSON events; unknown event
  shapes leave totals at zero instead of failing completed turns.
- Gemini, AGY, and Kiro do not report token usage in stable form, so totals
  stay at zero for those backends.
- Gemini has no multi-turn continuity (the CLI has no session protocol). AGY
  and Kiro continuations use their CLI flags but do not expose token
  accounting.
- ACP profiles report no per-turn token counts, and are not verified on
  Windows.
- PRD LR-4 (one engine, thin clients) is not done: `symphony tui` still runs
  its own orchestrator in-process.

## Acknowledgements

Built on OpenAI's [Symphony](https://github.com/openai/symphony) reference
implementation (Apache-2.0), which provides the orchestrator, scheduler, and
workspace lifecycle. See [NOTICE](../NOTICE). The TUI uses Will McGugan's
[Textual](https://textual.textualize.io) and [rich](https://github.com/Textualize/rich).
Pipeline stage rules adapt the evidence-first ideas of
[cskwork/backend-dev-skills](https://github.com/cskwork/backend-dev-skills) (MIT).
Licensed under [Apache 2.0](../LICENSE).
