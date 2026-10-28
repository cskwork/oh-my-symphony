# Contributing

Thanks for helping improve Symphony. This repo is public, and external pull
requests are welcome.

## Branch and PR target

- Open pull requests against `dev` unless a maintainer asks for a different
  target.
- Keep `main` release-ready; it receives changes after they have been verified
  on `dev`.
- Prefer small, focused PRs. Separate backend behavior, UI, docs, and release
  metadata when they are not part of the same user-visible change.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]" -e packages/pm -e packages/chat -e packages/release
npm ci --prefix packages/pm
```

The PM Workspace is a separate, optional distribution (`oh-my-symphony-pm`)
under `packages/pm/`. It is not on PyPI, so install it from the checkout next
to core as shown. The full suite expects it, and its Node UI tests need
`npm ci --prefix packages/pm`. Core must also keep working without it:
`tests/test_core_without_pm.py` covers that in-process, and CI installs the
core wheel alone. The operator chat (`oh-my-symphony-chat`, `packages/chat/`)
follows the same pattern; `tests/test_core_without_chat.py` covers core without
it.

App release contracts (ADR 0002) are the optional distribution
`oh-my-symphony-release` under `packages/release/`, installed the same way.
Without it core fails closed for `app-release` tickets;
`tests/test_core_without_release.py` covers that.

## Required verification

Run the full test suite and project checks after installing the development
dependencies before opening or updating a PR:

```bash
python -m pytest -q
python -m ruff check src tests packages/pm packages/chat packages/release
symphony-pyright
```

For workflow, service, or board changes, also run:

```bash
symphony doctor ./WORKFLOW.md
```

For backend adapter changes, include the most relevant targeted test command
and manual smoke evidence. Real Codex, Claude Code, Gemini, and Pi CLI
integration checks are useful when available, but CI only requires the local
test suite because contributors may not have every agent CLI installed.

### Windows note

Some tooling that launches the venv through a trampoline (wrappers, hidden
consoles) can receive stray console Ctrl+C broadcasts that kill a full
`pytest` run mid-suite (typically at `shutdown_default_executor`). If a
full-suite run dies partway with a `KeyboardInterrupt`, run it detached
from any console instead:

```pwsh
# PowerShell, from the repo root
$env:SYMPHONY_BROWSER_E2E = '1'   # optional: include Playwright browser e2e
Start-Process -FilePath ".venv\Scripts\python.exe" `
  -ArgumentList "-m pytest -q --junitxml=pytest-results.xml" `
  -WorkingDirectory "." -WindowStyle Hidden
```

## Development standards

- Keep agent-specific protocol details inside `src/symphony/backends/`.
  The orchestrator should see normalized backend events, not raw CLI payloads.
- Preserve the existing `WORKFLOW.md` configuration shape unless the PR also
  updates examples, docs, and validation.
- Add or update tests for behavior changes. For bug fixes, include a regression
  test that would fail without the fix when practical.
- Do not commit secrets, local run state, logs, virtualenvs, or generated
  browser artifacts.
- Update README or `docs/` when the user-facing behavior or operator workflow
  changes.

## PR checklist

Before requesting review, make sure the PR includes:

- A clear summary of the behavior change.
- Verification commands and results.
- Screenshots or terminal output for TUI or browser-visible changes.
- Notes about compatibility, migration, or follow-up work if applicable.

Maintainers merge after the PR targets `dev`, CI is green, and the verification
evidence is sufficient for the risk of the change.
