#!/usr/bin/env sh
# sg-emit - supergoal Board state emitter (opt-in, best-effort, lock-free).
#
# Writes ONE heartbeat JSON per agent, replaced atomically (temp + rename). Correctness comes
# entirely from: one writer per file + atomic rename. No lock anywhere; `branch` is a display
# field, never a mutex, so agents may share a branch freely.
#
# Opt-in: emits only when the Board is enabled (the `$REGDIR/.enabled` flag file that tui/launch.sh
# creates, or SUPERGOAL_TUI=1). With no Board, this is a silent no-op that writes nothing.
# Best-effort: any failure -> one stderr line + exit 0. It must never abort the agent's real work.
#
# Identity is SELF-DERIVED from git in the current dir on every call (Claude Code tool calls do not
# persist exported env across calls, and have no stable per-agent OS pid - so we never rely on either).
# Pass --slot when an orchestrator runs several agents in the SAME worktree+branch, to keep their
# files distinct.
#
# Usage:
#   sg-emit --phase Critic [--mode GREENFIELD] [--task "Add JWT refresh"] \
#           [--task-status in-progress] [--note "2 reds open"] [--slot exec-3f] \
#           [--tasks-file board.json]   # full tasks[] array; else prior board is carried forward
#
# Env: SUPERGOAL_RUN_DIR (default $HOME/.supergoal/runs), SUPERGOAL_TUI=1 to force-enable.

set -u

REGDIR="${SUPERGOAL_RUN_DIR:-$HOME/.supergoal/runs}"

# --- opt-in gate: no Board, no files -------------------------------------------------------------
if [ ! -e "$REGDIR/.enabled" ] && [ "${SUPERGOAL_TUI:-0}" != "1" ]; then
  exit 0
fi

# --- parse args ----------------------------------------------------------------------------------
phase=""; mode=""; task=""; task_status=""; note=""; slot=""; tasks_file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --phase) phase="${2:-}"; shift 2 ;;
    --mode) mode="${2:-}"; shift 2 ;;
    --task) task="${2:-}"; shift 2 ;;
    --task-status) task_status="${2:-}"; shift 2 ;;
    --note) note="${2:-}"; shift 2 ;;
    --slot) slot="${2:-}"; shift 2 ;;
    --tasks-file) tasks_file="${2:-}"; shift 2 ;;
    *) shift ;;  # ignore unknown flags rather than abort
  esac
done

command -v jq >/dev/null 2>&1 || { echo "sg-emit: jq not found, skipped" >&2; exit 0; }

# --- self-derive identity from git (fall back to cwd when not a repo) -----------------------------
repo_path="$(git rev-parse --show-toplevel 2>/dev/null)" || repo_path=""
[ -n "$repo_path" ] || repo_path="$(pwd)"
repo="$(basename "$repo_path")"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" || branch="-"
[ -n "$branch" ] || branch="-"
# worktree root: --show-toplevel already resolves linked worktrees to their own root
worktree="$repo_path"
wt_hash="$(printf '%s' "$worktree" | cksum | cut -d' ' -f1)"

slugify() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-' | sed 's/--*/-/g; s/^-//; s/-$//'; }
agent_id="$(slugify "$repo-$branch-$wt_hash${slot:+-$slot}")"

now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
pid="${PPID:-0}"          # advisory only; readers use timestamp age as the primary liveness signal
host="$(hostname 2>/dev/null | cut -d. -f1)"; [ -n "$host" ] || host="-"

AGENTS_DIR="$REGDIR/agents"
mkdir -p "$AGENTS_DIR" 2>/dev/null || { echo "sg-emit: cannot mkdir $AGENTS_DIR, skipped" >&2; exit 0; }
DEST="$AGENTS_DIR/$agent_id.json"
TMP="$DEST.tmp.$$"

# --- prior heartbeat: carry forward started_at + tasks[] -----------------------------------------
prior="null"
[ -f "$DEST" ] && prior="$(cat "$DEST" 2>/dev/null || echo null)"
case "$prior" in "") prior="null" ;; esac

# tasks source: explicit file wins; else carry the prior board forward
tasks_json="$(printf '%s' "$prior" | jq -c '.tasks // []' 2>/dev/null || echo '[]')"
if [ -n "$tasks_file" ] && [ -f "$tasks_file" ]; then
  tasks_json="$(jq -c '.' "$tasks_file" 2>/dev/null || printf '%s' "$tasks_json")"
fi

# --- build the heartbeat (atomic write) ----------------------------------------------------------
if jq -n \
  --argjson prior "$prior" \
  --argjson tasks "$tasks_json" \
  --arg agent_id "$agent_id" \
  --arg repo_path "$repo_path" --arg repo "$repo" --arg branch "$branch" --arg worktree "$worktree" \
  --arg mode "$mode" --arg phase "$phase" --arg task "$task" --arg task_status "$task_status" \
  --arg note "$note" --arg now "$now" --arg host "$host" --argjson pid "${pid:-0}" '
  ($prior // {}) as $p
  | ($tasks
      | if $task != "" then
          (map(.title) | index($task)) as $i
          | if $i == null
            then . + [{id: ("t" + ((length)|tostring)), title: $task,
                       status: (if $task_status != "" then $task_status else "in-progress" end)}]
            else (if $task_status != "" then (.[$i].status = $task_status) else . end)
            end
        else . end) as $board
  | {
      schemaVersion: 1,
      agent_id: $agent_id,
      repo_path: $repo_path,
      repo: $repo,
      branch: $branch,
      worktree: $worktree,
      mode: (if $mode != "" then $mode else ($p.mode // null) end),
      phase: (if $phase != "" then $phase else ($p.phase // null) end),
      current_task: (if $task != "" then $task else ($p.current_task // null) end),
      started_at: ($p.started_at // $now),
      updated_at: $now,
      pid: $pid,
      host: $host,
      note: (if $note != "" then $note else null end),
      tasks: $board
    }' > "$TMP" 2>/dev/null && mv -f "$TMP" "$DEST" 2>/dev/null; then
  exit 0
else
  rm -f "$TMP" 2>/dev/null
  echo "sg-emit: write skipped for $agent_id" >&2
  exit 0
fi
