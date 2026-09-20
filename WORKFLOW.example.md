---
tracker:
  kind: linear
  project_slug: my-team-project
  api_key: $LINEAR_API_KEY
  active_states: [Todo, "In Progress", Verify, Document]
  terminal_states: ["Human Review", Done, Blocked, Archive, Closed, Cancelled, Canceled, Duplicate]
  # Auto-archive sweep — terminal-state issues whose `updated_at` is older
  # than `archive_after_days` move to `archive_state` on each poll tick.
  # Set `archive_after_days: 0` to disable the sweep (TUI `a` hotkey still
  # works). 30 days is a safe default for visible projects.
  archive_state: Archive
  archive_after_days: 30
  # Optional one-line legend rendered under each TUI column header.
  state_descriptions:
    Todo: "Triage; route to In Progress"
    "In Progress": "Plan + TDD implementation + self-critique"
    Verify: "Review + QA + Merge Gate"
    Document: "Docs + wiki write-back; Done unless intervention"
    "Human Review": "Manual intervention or explicit review before Done"
    Done: "Verified complete"
    Archive: "Auto-archived after 30 days idle"

polling:
  interval_ms: 30000

# Wiki integrity sweep — see `symphony wiki-sweep --help`. The orchestrator
# runs the sweep automatically after every Nth `Done` transition. Set
# `sweep_every_n: 0` to disable; the manual CLI still works either way.
wiki:
  sweep_every_n: 10
  root: ./docs/llm-wiki

workspace:
  root: ~/symphony_workspaces

hooks:
  # Default: attach the per-ticket workspace as a git worktree of the
  # host repo on a symphony/<ID> branch. The host working tree is never
  # touched while the ticket is active. The default Verify gate merges the
  # feature branch into the target branch before the ticket can move to Document.
  # A human later confirms Done from the TUI (`c`) or board viewer.
  #
  # If your code lives in a *different* remote than where WORKFLOW.md
  # sits (common with Linear setups where the config repo is config-only),
  # replace the worktree commands with a `git clone <remote> .` instead.
  # Body extracted to scripts/symphony-setup-worktree.sh — see C4 in
  # docs/improvements/workflow-v0.5.2.md. The script worktree-adds the
  # ticket branch, records basesha/basebranch/mergetargetbranch, and (when
  # a host-owned `kanban/` exists) symlinks/junctions it back. Linear
  # trackers read from their API so the symlink loop is a no-op here.
  after_create: |
    bash "$SYMPHONY_WORKFLOW_DIR/scripts/symphony-setup-worktree.sh"
  before_run: |
    # NEVER `git reset --hard` inside a worktree — it discards in-progress
    # work between turns. Just refresh remotes; let the agent decide if/when
    # to rebase.
    set -uo pipefail
    git fetch origin --quiet || true
  after_run: |
    # Per-turn commit-or-amend. The branch stays at the same number of
    # commits across turns (amends in place when HEAD is already a `wip:`
    # commit), but every completed turn is durably written to .git/objects
    # so even a hard crash (SIGKILL, host reboot) won't lose work. The
    # orchestrator squashes everything into a single `<ID>: <title>` commit
    # on exit — see auto_commit_on_done.
    set -uo pipefail
    git add -A -- . ':(exclude).symphony' 2>/dev/null || true
    if git diff --cached --quiet 2>/dev/null; then
      echo "run finished at $(date) (no changes)"
      exit 0
    fi
    # Classify the staged diff so the wip subject carries machine-readable
    # markers. `[no-test]` = production code changed with no paired test
    # file in the same diff (workflow-v0.5.2 § B1 — review.md promotes it
    # to a HIGH finding). `[scope-expand]` = a rewind dispatch (set by the
    # orchestrator when SYMPHONY_REWIND_SCOPE is exported) but the diff
    # touched a file outside the parsed scope list (workflow-v0.5.2 § A2).
    # Both markers can stack.
    STAGED_FILES="$(git diff --cached --name-only 2>/dev/null || true)"
    PROD_CHANGED=0
    TESTS_CHANGED=0
    SCOPE_EXPAND=0
    SCOPE_FILES=""
    if [ -n "${SYMPHONY_REWIND_SCOPE:-}" ]; then
      SCOPE_FILES="$(printf '%s' "$SYMPHONY_REWIND_SCOPE" \
        | tr ',' '\n' \
        | sed -n 's/.*"file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    fi
    NL=$(printf '\nx'); NL=${NL%x}
    OLDIFS="$IFS"
    IFS="$NL"
    for f in $STAGED_FILES; do
      [ -n "$f" ] || continue
      case "$f" in
        tests/*|*_test.py|*.test.ts|*.test.tsx|*_test.go)
          TESTS_CHANGED=1
          ;;
      esac
      case "$f" in
        tests/*|docs/*|kanban/*|.symphony/*|*.md|LICENSE|LICENSE.*|NOTICE|CHANGELOG*|README*|AGENTS.md|GEMINI.md)
          : # carve-out: docs/license/wiki edits never count as production change
          ;;
        *)
          PROD_CHANGED=1
          ;;
      esac
      if [ -n "$SCOPE_FILES" ] && [ "$SCOPE_EXPAND" = 0 ]; then
        in_scope=0
        for s in $SCOPE_FILES; do
          if [ "$f" = "$s" ]; then
            in_scope=1
            break
          fi
        done
        if [ "$in_scope" = 0 ]; then
          SCOPE_EXPAND=1
        fi
      fi
    done
    IFS="$OLDIFS"
    PREFIX=""
    if [ "$PROD_CHANGED" = 1 ] && [ "$TESTS_CHANGED" = 0 ]; then
      PREFIX="${PREFIX}[no-test]"
    fi
    if [ -n "${SYMPHONY_REWIND_SCOPE:-}" ] && [ "$SCOPE_EXPAND" = 1 ]; then
      PREFIX="${PREFIX}[scope-expand]"
    fi
    # Honors any pre-commit hooks in the host repo — if they fail, this
    # turn's snapshot fails and the next turn picks up where files are.
    MSG="$(sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q;}' .symphony/commit-message.txt 2>/dev/null || true)"
    [ -n "$MSG" ] || MSG="turn $(date -u +%FT%TZ)"
    case "$MSG" in wip:*) COMMIT_MSG="$MSG" ;; *) COMMIT_MSG="wip: $MSG" ;; esac
    LAST="$(git log -1 --format=%s 2>/dev/null || echo "")"
    # On amend, preserve any markers the previous turn already set so a
    # later test-passing turn doesn't drop the historical `[no-test]`.
    # Markers are sticky within a wip subject.
    PRIOR_PREFIX=""
    case "$LAST" in
      *"[no-test]"*) PRIOR_PREFIX="${PRIOR_PREFIX}[no-test]" ;;
    esac
    case "$LAST" in
      *"[scope-expand]"*) PRIOR_PREFIX="${PRIOR_PREFIX}[scope-expand]" ;;
    esac
    MERGED_PREFIX=""
    case "$PRIOR_PREFIX$PREFIX" in
      *"[no-test]"*) MERGED_PREFIX="${MERGED_PREFIX}[no-test]" ;;
    esac
    case "$PRIOR_PREFIX$PREFIX" in
      *"[scope-expand]"*) MERGED_PREFIX="${MERGED_PREFIX}[scope-expand]" ;;
    esac
    if [ -n "$MERGED_PREFIX" ]; then
      COMMIT_MSG="${MERGED_PREFIX} ${COMMIT_MSG}"
    fi
    if [ "${LAST#wip:}" != "$LAST" ]; then
      git -c user.email=symphony@local -c user.name=symphony \
          commit --amend -m "$COMMIT_MSG" >/dev/null 2>&1 || true
    else
      git -c user.email=symphony@local -c user.name=symphony \
          commit -m "$COMMIT_MSG" >/dev/null 2>&1 || true
    fi
    echo "run finished at $(date)"
  before_remove: |
    # Detach the worktree before Symphony rmtree's the dir, otherwise
    # `.git/worktrees/<ID>` lingers until `git worktree prune`. By this
    # point the orchestrator has already auto-committed any leftover
    # changes (see agent.auto_commit_on_done).
    set -uo pipefail
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?}"
    WORKTREE_PATH="$PWD"
    ISSUE_ID="${SYMPHONY_ISSUE_ID:-$(basename "$WORKTREE_PATH")}"
    for dir in ${SYMPHONY_BOARD_ROOT_NAME:-kanban}; do
      git -C "$HOST_REPO" update-index --no-skip-worktree -- "$dir/$ISSUE_ID.md" 2>/dev/null || true
    done
    git -C "$HOST_REPO" worktree remove --force "$WORKTREE_PATH" 2>/dev/null || true

agent:
  kind: codex          # codex | claude | gemini | agy | kiro | opencode | pi | prime-agent
  # Optional per-state backend routing: cheap/fast agents on light lanes,
  # the default `kind` everywhere else. Precedence per dispatch:
  # per-ticket `agent_kind` frontmatter pin > stage_kinds > kind.
  # stage_kinds:
  #   Todo: gemini
  #   Document: gemini
  # Provider accounts per agent kind, in preference order. A quota/usage-limit
  # error benches the active account board-wide for `account_quota_cooldown_ms`
  # and pins the ticket to the next one, retrying instead of auto-pausing.
  # Accounts exhaust within a kind before `fallback_kinds` escalates to another
  # kind. `env` is overlaid on the dispatch environment, so an account is
  # whatever variable that backend reads for its profile.
  # accounts: {}
  account_quota_cooldown_ms: 3600000
  max_concurrent_agents: 1
  # This is the per-attempt execution cap. In prompt templates,
  # {{ turn_number }}/{{ max_turns }} reports the ticket lifetime position/cap.
  max_turns: 100
  # Hard per-ticket budget across continuation attempts. Prevents an
  # active-state ticket from restarting forever and wasting tokens.
  max_total_turns: 200
  # Continue interrupted work from the latest completed turn after a confirmed
  # process cleanup. Set false to force a fresh agent session after restart.
  crash_continuation: true
  # Dispatch ordering: fifo preserves registration order; dag is opt-in and
  # ranks by priority, longest downstream dependency chain, then registration.
  # Starvation promotion always remains first.
  scheduling_policy: fifo
  # Hard token ceiling by workflow state. The global cap is the default for
  # Document; In Progress and Verify get larger build/verification budgets.
  max_total_tokens: 100000000
  max_total_tokens_by_state:
    "In Progress": 500000000
    Verify: 500000000
  # Per-lane stall budget (ms), falling back to the resolved backend's
  # `stall_timeout_ms`. Heavy lanes (a Verify that runs a full suite) go quiet
  # far longer than light ones; widen just that lane instead of every backend.
  # stall_timeout_ms_by_state:
  #   Verify: 900000
  budget_exhausted_state: Blocked
  # Soft cap for Verify/Document rewinds back into In Progress. Set 0 to disable.
  max_attempts: 3
  # Mechanical evidence floor (orchestrator/contracts.py):
  #   auto (default) — enforce only when every active lane is a default-preset
  #                    lane (Todo / In Progress / Verify / Document). Renaming
  #                    a lane therefore turns it OFF — logged as
  #                    `stage_contracts_disabled` and reported by
  #                    `symphony doctor`, never silent.
  #   on             — enforce whatever the lanes are called.
  #   off            — never enforce; the stage prompts are the only gate.
  stage_contracts: auto
  # Cap on auto-retries scheduled after a worker exits with a non-normal
  # outcome (timeout, crash, transient backend error). On exhaustion the
  # orchestrator stops scheduling further retries, appends an
  # `## Escalation` note to the ticket, and moves the ticket to the first
  # terminal state mentioning `block` or `human` (else `Blocked`). 0 = no
  # cap (legacy: retry forever with exponential backoff).
  max_retries: 3
  # File-board only: route obvious Todo tickets with Acceptance Criteria to
  # In Progress without spending a model turn. Bug/blocked/ambiguous tickets
  # still run Todo.
  auto_triage_actionable_todo: true
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    Todo: 1
    "In Progress": 1
    Verify: 1
    Document: 1
  # When a ticket reaches Done cleanly, snapshot the workspace into one
  # git commit (`<identifier>: <title>`). If the workspace is nested
  # inside an existing repo, the commit lands there; otherwise `git init`
  # runs first. Set to false if your workspace is an existing repo with
  # strict commit-style rules you don't want auto-touched.
  auto_commit_on_done: true
  # Merge policy for the Verify -> Document gate. Verify must merge the
  # `symphony/<ID>` feature branch into this target before setting Document.
  auto_merge_on_done: true
  # Publish and verify the target upstream after the local --no-ff merge.
  # Set false for a local-only run; no git push or git ls-remote is attempted.
  auto_merge_push_target: true
  # Branch/ref used as the start point for new `symphony/<ID>` feature
  # branches. Empty string = current host branch. The board viewer can
  # update this from its real git branch dropdown.
  feature_base_branch: ""
  # Branch to merge into. Empty string = use the branch the feature branch
  # was created from/current host branch (most flexible). The board viewer
  # can update this from its real git branch dropdown.
  auto_merge_target_branch: ""
  # Workspace-only roots that must not differ on the ticket branch. Linear
  # has no host symlink roots, so this stays empty. File-board workflows
  # usually set this to ["kanban"] in WORKFLOW.file.example.md.
  auto_merge_exclude_paths: []
  # Legacy escape hatch: paths under the host repo whose currently
  # untracked files should be folded into the same merge commit. Prefer
  # branch-local docs/ so reports and wiki updates merge normally.
  auto_merge_capture_untracked: []
  #   - docs
  #   - llm-wiki

codex:
  command: codex app-server
  model: gpt-5.5
  reasoning_effort: high
  approval_policy: never
  # Sandbox trade-off — read before changing:
  #   `workspace-write` (default below) keeps codex confined to the worker
  #   workspace and is the safer choice for fresh clones / shared machines.
  #   When `after_create` symlinks host-repo dirs (kanban, prompt, ...) into
  #   the workspace, symphony's codex backend now scans those symlinks at
  #   start() and auto-injects `-c sandbox_workspace_write.writable_roots`
  #   so writes through them succeed without widening the sandbox. Wrapper
  #   scripts can read `$SYMPHONY_CODEX_WRITABLE_ROOTS` (os.pathsep-joined)
  #   and pass the same override to codex themselves.
  #   Codex v2 denies network by default under the string shorthand. The
  #   tagged turn policy below keeps workspace confinement while allowing
  #   package-registry downloads. Use `turn_sandbox_policy: workspace-write`
  #   instead for offline-only workers. Reserve `danger-full-access` for a
  #   proven OS-capability blocker, not registry access.
  thread_sandbox: workspace-write
  turn_sandbox_policy: {type: workspaceWrite, networkAccess: true}
  turn_timeout_ms: 3600000
  read_timeout_ms: 20000
  stall_timeout_ms: 300000

claude:
  # `--permission-mode acceptEdits` is required for an unattended worker:
  # without it every file write waits for an interactive approval that never
  # arrives, and the ticket looks stalled. `--add-dir` extends Claude Code's
  # write scope to host directories the `after_create` hook links into the
  # worktree (a file board's `kanban/`); a Linear board needs no board dir,
  # but keeping the flag costs nothing if the path does not exist.
  command: 'claude -p --output-format stream-json --verbose --permission-mode acceptEdits --add-dir "$SYMPHONY_WORKFLOW_DIR"'
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 20000
  stall_timeout_ms: 300000

gemini:
  # `gemini -p` (no argument) prints help in Gemini CLI 0.39+; pass an
  # empty `""` so the prompt comes from stdin. Symphony appends `--yolo`
  # for unattended worker runs and keeps its own local session id.
  command: 'gemini -p ""'
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 20000
  stall_timeout_ms: 300000

agy:
  # Antigravity CLI (`agy`) is the forward path for Gemini-style Google agent
  # runs. Symphony bridges the stdin prompt into `--print "$(cat)"`, appends
  # `--dangerously-skip-permissions`, and adds `--continue` on continuation
  # turns when resume_across_turns is true.
  command: agy --print "$(cat)"
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 20000
  stall_timeout_ms: 300000

kiro:
  # Kiro headless mode accepts KIRO_API_KEY or a confirmed `kiro-cli login`.
  # Kiro does not read piped stdin as the first message, so this shell bridge
  # passes Symphony's rendered prompt as the required positional chat input.
  # Continuation turns insert `--resume` before the prompt argument.
  command: 'kiro-cli chat --no-interactive --trust-all-tools "$(cat)"'
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 20000
  stall_timeout_ms: 300000

opencode:
  # `opencode run [message..]` is OpenCode's documented scripting path.
  # Symphony appends the prompt as a shell-quoted message argument and adds
  # `--session <id>` on continuation turns after OpenCode reports a session id.
  command: opencode run --format json --auto
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 20000
  stall_timeout_ms: 300000

pi:
  # `pi --mode json -p ""` emits JSONL events; stdin carries the prompt and
  # `--session <id>` is appended automatically on continuation turns.
  # Auth: sign in once with `pi` → `/login` (OAuth). Credentials are cached
  # at `~/.pi/agent/auth.json` and inherited by every subprocess Symphony
  # spawns — no env var or `--api-key` flag is needed.
  command: 'pi --mode json -p ""'
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 20000
  stall_timeout_ms: 300000

prime_agent:
  # Prime Agent emits the same JSONL protocol as Pi. Symphony appends
  # `--resume <id>` on continuation turns. Install with the Prime Agent
  # installer, then authenticate once with `prime-agent` → `/login`.
  # Credentials are cached at `~/.prime/agent/auth.json`; provider API keys
  # in the environment are also supported. `symphony doctor` reports a
  # missing auth file as an advisory warning, not a hard failure.
  command: 'prime-agent -p --mode json'
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 20000
  stall_timeout_ms: 300000

server:
  port: 9999            # optional JSON API; the primary UI is `symphony tui`

# Optional one-click Product Preview in the admin web UI. Tailor and uncomment
# this recipe for the product; a configured command defaults to enabled, and
# `enabled: false` opts out. Commands are trusted WORKFLOW config, executed
# argv-only in a clean checkout of the merge target. The API cannot override
# command, cwd, branch, or port; preview stays loopback.
# preview:
#   cwd: web
#   command: npm run preview -- --host ${HOST} --port ${PORT}
#   health_path: /
#   url_path: /
#   startup_timeout_ms: 30000
#   release_ticket: RELEASE-001
#   acceptance:
#     - Final acceptance suite passes
#     - Release evidence is attached

# Slack (or future channels) notifications. Entirely opt-in: omit this whole
# block and no messages are sent. The webhook URL is the only required field;
# resolve it from an env var with `$NAME` so secrets stay out of the workflow.
#
# Default behaviour when enabled: one message per tracker state transition.
# Set `notify_on_states` to a non-empty list to subscribe selectively (e.g.
# PMs may only want Done / Blocked pings). Custom per-state templates use
# `string.Template` placeholders: ${identifier} ${title} ${prev_state}
# ${next_state} ${workflow} ${reason}.
#
# notifications:
#   slack:
#     webhook_url: $SLACK_WEBHOOK_URL
#     enabled: true
#     notify_on_states: []           # empty = every transition
#     templates:
#       Done: "✅ ${identifier} ${title} (${workflow})"
#       Blocked: "🚧 ${identifier} blocked — ${title}"
#     username: Symphony
#     icon_emoji: ":robot_face:"
#     timeout_ms: 5000


tui:
  language: en               # `en` (default) or `ko`. SYMPHONY_LANG env overrides.
                             # Also drives artefact language: every prompt is
                             # prefixed with a one-line directive so kanban
                             # comments and docs/<id>/<stage>/*.md come back in
                             # the chosen language. `{{ language }}` is also
                             # exposed to this template for `{% if %}` branches.

prompts:
  base: ./docs/symphony-prompts/linear/base.md
  stages:
    Todo: ./docs/symphony-prompts/linear/stages/todo.md
    "In Progress": ./docs/symphony-prompts/linear/stages/in-progress.md
    Verify: ./docs/symphony-prompts/linear/stages/verify.md
    Document: ./docs/symphony-prompts/linear/stages/document.md
    Done: ./docs/symphony-prompts/linear/stages/done.md

---

This workflow uses stage-specific prompt files configured under `prompts`.
Customize `docs/symphony-prompts/linear/` to change the agent instructions.
If the `prompts` block is removed, Symphony falls back to this short legacy body.

You are working on {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
Follow the board state instructions configured for this workflow.
