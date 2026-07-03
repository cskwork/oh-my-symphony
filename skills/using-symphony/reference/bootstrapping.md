# Bootstrapping Symphony into another project

Use this when introducing Symphony to a repo that does not already carry the
standard operator bundle.

## Copy the full operator bundle

From inside the `oh-my-symphony` checkout:

```bash
TARGET=/path/to/target-project
cp tui-open.sh tui-open.bat "$TARGET/"
cp WORKFLOW.example.md "$TARGET/WORKFLOW.md"              # then edit
mkdir -p "$TARGET/docs" "$TARGET/tools" "$TARGET/scripts"
cp -R docs/symphony-prompts "$TARGET/docs/"
cp -R tools/board-viewer "$TARGET/tools/"                 # required for `--viewer-port`
cp scripts/symphony-setup-worktree.sh "$TARGET/scripts/"  # required by default after_create hook
chmod +x "$TARGET/scripts/symphony-setup-worktree.sh"
cp -R skills "$TARGET/"
cp AGENTS.md GEMINI.md "$TARGET/"
mkdir -p "$TARGET/.claude/skills"
ln -s ../../skills/using-symphony "$TARGET/.claude/skills/using-symphony"
ln -s ../../skills/symphony-oneshot "$TARGET/.claude/skills/symphony-oneshot"   # support bundle path
ln -s ../../skills/symphony-monorepo "$TARGET/.claude/skills/symphony-monorepo" # support bundle path
chmod +x "$TARGET/tui-open.sh"
```

> Warning: The board viewer (HTML/web UI at `--viewer-port`) auto-detects
> `<workflow-dir>/tools/board-viewer/server.py`. If that script is missing,
> `symphony service start` silently skips spawning the viewer — no error,
> no warning, just an absent `started board viewer pid=...` line and a
> `viewer_pid: null` in `.symphony/run/*.json`. Always copy `tools/board-viewer/`
> when bootstrapping, even if you don't think you need the web UI yet.

Copy `tui-open.sh` and `tui-open.bat` even for headless-first setups. The
launcher carries safety behavior that plain `symphony tui` does not: port
collision checks, doctor preflight, venv-first binary lookup, and real terminal
window spawning.

If the target project has no virtualenv, either install Symphony globally or
prepare a local one so the launcher can find it:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e <oh-my-symphony>
```

## Why these files matter

| File or directory | Purpose |
| --- | --- |
| `WORKFLOW.md` | Runtime config and prompt entrypoint |
| `docs/symphony-prompts/` | Worker prompts; dispatched agents read these |
| `skills/using-symphony/` | Canonical operator router skill |
| `skills/symphony-oneshot/`, `skills/symphony-monorepo/` | Support bundles for router templates, scripts, and references |
| `.claude/skills/using-symphony` | Claude Code discovery symlink to the router |
| `.claude/skills/symphony-*` | Compatibility symlinks so bundled templates can read support files by path |
| `AGENTS.md` | Codex entrypoint pointing to repo skills |
| `GEMINI.md` | Gemini entrypoint pointing to repo skills |
| `tui-open.sh`, `tui-open.bat` | One-shot board launchers |
| `tools/board-viewer/` | Web HTML board viewer for `--viewer-port` (silently no-ops if absent) |
| `scripts/symphony-setup-worktree.sh` | Worktree-setup body invoked by the default `after_create` hook in `WORKFLOW.example.md`. Without it, every fresh ticket dispatch fails at the hook stage with `No such file or directory`. |

`skills/using-symphony/SKILL.md` is the only operator activation route. Edit
only the canonical files under `skills/`; platform entrypoints should point at
them.

## Preserve the default pipeline

`WORKFLOW.example.md` ships with the supported production flow:

```text
Todo -> In Progress -> Verify -> Learn -> Human Review -> Done
```

Do not trim it to a smaller lane set unless the user explicitly asks. The base
prompt names these stages, Verify is the compulsory review/QA/merge gate, and
Learn writes back to `docs/llm-wiki/` for future tickets. Operators may skip an
idle Learn card to `Human Review`; agents should not self-skip it.

If the target project truly needs a different workflow, edit these together:

- `tracker.active_states`
- `tracker.terminal_states`
- `prompts.stages`
- the matching stage files under `docs/symphony-prompts/<flavor>/stages/`

Use `reference/customization.md` for lane and prompt changes.

## Pick the prompt flavor

- `tracker.kind: file` uses `docs/symphony-prompts/file/...`; the agent writes
  stage notes into the ticket file body.
- `tracker.kind: linear` uses `docs/symphony-prompts/linear/...`; the agent
  writes stage notes as Linear comments.

Copy only the flavor you need if you want a smaller target repo. Copying both
is fine when simplicity matters more than disk hygiene.

## First launch

Foreground board view:

```bash
./tui-open.sh
./tui-open.sh path/to/WORKFLOW.md
tui-open.bat
```

For managed headless operation and viewer commands, use
`reference/operations.md`.
