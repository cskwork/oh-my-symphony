---
name: symphony-monorepo
description: Use when bootstrapping Symphony into an existing monorepo (multi-service Git repo) so each ticket gets an isolated git worktree workspace, the upstream 7-stage prompts are installed, and Claude Code permissions are wired up bidirectionally. Triggers on "set up symphony in my monorepo", "bootstrap symphony workspaces", "WORKFLOW.md per service", "worktree hooks", "symphony monorepo".
---

# Symphony for Monorepos

Generic recipe to wire Symphony into a polyrepo or monorepo where each ticket should land in its own isolated git worktree under `.symphony/workspaces/<ticket-id>`. This skill stays workspace-agnostic — no project-specific service names, branch conventions, or paths.

For operator-level usage (creating tickets, running TUI, triaging failures) see `using-symphony`. For one-shot dispatch see `symphony-oneshot`.

## What this skill solves

| Pain | Fix |
|------|-----|
| `workspace.root` inside the git repo → Symphony tries to init nested repo → `returncode=128` | Use a sibling `.symphony/workspaces/` and a worktree-based `after_create` hook |
| Spawned child Claude inherits parent hooks/skills → 47k token cache_creation per spawn + `read_timeout` storms | `--setting-sources project` on `claude.command` |
| Backend worktree cannot Read/Grep sibling services → Plan stage hallucinates | `--add-dir "$SYMPHONY_WORKFLOW_DIR"` (monorepo root, not narrower) |
| Jinja `{{ issue.identifier }}` evaluates as literal inside hook shell | Use `$(basename "$PWD")` for ticket id, `$SYMPHONY_WORKFLOW_DIR` for repo root |
| Same branch already checked out elsewhere → worktree add fails or resets work | Pre-check `git worktree list --porcelain`, skip if occupied |
| Claude Code blocks file access outside cwd when symphony repo is sibling to workspace | Bidirectional `permissions.additionalDirectories` in `.claude/settings.local.json` |

## Prerequisites

- Symphony installed (`pip install -e ".[dev]"` from this repo, `symphony` on PATH)
- Coding-agent CLI on PATH (`claude`, `codex`, `gemini`, or `pi`) — pick per ticket
- Target workspace is a git repository

## 1. Bootstrap

```bash
SYMPHONY_HOME=/path/to/oh-my-symphony \
WORKSPACE_ROOT=/path/to/your/monorepo \
  bash "$SYMPHONY_HOME/skills/symphony-monorepo/scripts/setup-monorepo.sh"
```

What the script does (all idempotent):

1. Creates `$WORKSPACE_ROOT/.symphony/{kanban,workspaces,prompts,logs}/`
2. Copies upstream 7-stage prompts from `$SYMPHONY_HOME/docs/symphony-prompts/file/` into `.symphony/prompts/`
3. Appends `.symphony/` to `$WORKSPACE_ROOT/.gitignore` if missing
4. Registers bidirectional `permissions.additionalDirectories` in both `.claude/settings.local.json` files so Claude Code can read across the two repos
5. Symlinks this skill (and the host repo's other symphony skills) into `$WORKSPACE_ROOT/.claude/skills/` so Claude Code discovers them

It does **not** generate per-service `WORKFLOW.<svc>.md` — that's workspace-specific. See `references/workflow-template.md` for a template.

## 2. WORKFLOW.md per service

One `WORKFLOW.<svc>.md` per service you want orchestrated. Key fields:

```yaml
tracker:
  kind: file
  board_root: ./.symphony/kanban
  active_states: [Todo, Explore, "In Progress", Review, QA, Learn]
  terminal_states: [Closed, Cancelled, Duplicate, Done, Archive, Blocked]
  archive_state: Archive
  archive_after_days: 30

polling:
  interval_ms: 30000

workspace:
  root: ./.symphony/workspaces      # MUST be outside any git repo
```

See `references/workflow-template.md` for the full schema with hooks and prompts.

## 3. Worktree-based `after_create` hook

The hook receives `$SYMPHONY_WORKFLOW_DIR` (the directory containing the WORKFLOW.md) and `$PWD` (the workspace dir Symphony just `mkdir`'d).

```yaml
hooks:
  after_create: |
    set -e
    ISSUE_ID="$(basename "$PWD")"
    SERVICE_DIR="$SYMPHONY_WORKFLOW_DIR/<service-subdir>"
    BRANCH="<prefix>/$ISSUE_ID"
    BASE_REF="origin/<base-branch>"

    git -C "$SERVICE_DIR" fetch origin

    # Idempotent: already a worktree
    [ -e "$PWD/.git" ] && exit 0

    # Guard: same branch already checked out elsewhere → skip (don't steal it)
    if git -C "$SERVICE_DIR" worktree list --porcelain | grep -qE "^branch refs/heads/${BRANCH}$"; then
      echo "[symphony] ${BRANCH} occupied by another worktree — skip" >&2
      exit 0
    fi

    # Attach existing branch, OR create new from base
    if git -C "$SERVICE_DIR" show-ref --verify --quiet "refs/heads/${BRANCH}"; then
      git -C "$SERVICE_DIR" worktree add "$PWD" "${BRANCH}"
    else
      git -C "$SERVICE_DIR" worktree add "$PWD" -b "${BRANCH}" "$BASE_REF"
    fi
```

Rules:

- **Never use `-B` (capital)**: that resets the branch tip to `$BASE_REF` and can destroy work.
- **Always use `-b` (lowercase) only when creating fresh** — distinguished by the `show-ref` check above.
- **Skip on occupied branch**, do not auto-clean — Symphony retries safely, and a stuck branch is a signal that someone is using it.

## 4. `claude.command` tuning

```yaml
claude:
  command: >
    claude -p --output-format stream-json --verbose
    --dangerously-skip-permissions
    --disable-slash-commands
    --setting-sources project
    --add-dir "$SYMPHONY_WORKFLOW_DIR"
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 30000
  stall_timeout_ms: 300000
```

Why each flag matters:

| Flag | Purpose |
|------|---------|
| `-p` | print mode (non-interactive) |
| `--output-format stream-json --verbose` | NDJSON event stream Symphony parses |
| `--dangerously-skip-permissions` | Tool auto-approval inside workspace.root only |
| `--disable-slash-commands` | Children should not invoke slash commands |
| `--setting-sources project` | **Do not inherit `~/.claude/settings.json` hooks/plugins.** Without this, every spawn pays ~47k tokens of parent hook context and frequently times out on `read_timeout_ms`. |
| `--add-dir "$SYMPHONY_WORKFLOW_DIR"` | Expose the whole monorepo root, not just `.symphony/kanban`. Narrower scopes cause Plan-stage hallucinations because sibling services become unreadable. |

**Do not pass `--bare`** — it disables keychain auth and produces `Not logged in · Please run /login`.

`read_timeout_ms` below 30000 is too tight for Claude Code's startup hook context loading; you will see `read_timeout` followed by Symphony retries.

For other agents:

- Codex: `workspace-write` covers cwd-external reads by default — no extra flag needed.
- Gemini: `--skip-trust` for non-interactive mode.
- Pi: no special flag.

## 5. Bidirectional Claude Code permissions

Symphony's host repo and the workspace monorepo are siblings. Claude Code's default permission scope is the cwd, so reading the other side fails until both `.claude/settings.local.json` files list the other path under `permissions.additionalDirectories`.

`setup-monorepo.sh` does this automatically via `jq`. Manual form:

```json
{
  "permissions": {
    "additionalDirectories": ["/absolute/path/to/the/other/repo"]
  }
}
```

`.claude/settings.local.json` is gitignored on both sides — that is fine. The setup script is the propagation channel; teammates clone, run setup, get the same config.

## 6. Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `symphony: command not found` | venv not activated |
| `Not logged in · Please run /login` | `--bare` is set on `claude.command`. Remove it. |
| `fatal: Cannot update paths and switch to branch 'fix/{{'` | Jinja2 inside hook shell. Use `$(basename "$PWD")` and `$SYMPHONY_WORKFLOW_DIR` |
| `hook_failed cwd=.../<ticket> returncode=128` | `workspace.root` is inside a git repo, or hook tries `git init` in a non-empty dir. Use worktree-based `after_create` instead. |
| `read_timeout` retry storm | `read_timeout_ms` < 30000 |
| Children respond in a chatty/personal tone, or every spawn re-creates 47k tokens of cache | Parent hooks inherited. Add `--setting-sources project` |
| Plan stage hallucinates code that does not exist in sibling services | `--add-dir` too narrow. Expose `$SYMPHONY_WORKFLOW_DIR` (monorepo root) |
| Same branch occupied by another worktree → workspace creation fails repeatedly | Add the `git worktree list --porcelain` guard from §3 |
| Korean (or other non-ASCII) output garbled | `LANG=ko_KR.UTF-8`, `SYMPHONY_LANG=ko`, `tui.language: ko` |

## 7. References

- `references/workflow-template.md` — annotated WORKFLOW.md skeleton with hooks
- `references/claude-command-options.md` — every claude.command flag explained
- `references/worktree-hooks.md` — full hook recipes (`before_run`, `after_run`)
- `scripts/setup-monorepo.sh` — the bootstrap script
