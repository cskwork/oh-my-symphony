"""Symphony — coding agent orchestration service (SPEC v1).

Layout (see ``AGENTS.md`` for full SPEC references):

* Foundation (no internal deps)
    - ``errors``        — exception hierarchy
    - ``logging``       — structured logger
    - ``i18n``          — TUI/agent doc localization
    - ``issue``         — normalized ``Issue`` domain (SPEC §4.1.1)
    - ``_shell``        — bash resolution for hooks

* Config & domain
    - ``workflow``      — WORKFLOW.md parser + typed config (SPEC §5,6)
    - ``workspace``     — workspace lifecycle & hooks (SPEC §9)
    - ``prompt``        — Liquid-compatible templating (SPEC §5.4, §12)

* Storage
    - ``trackers``      — tracker protocol + ``file`` / ``linear`` adapters (SPEC §11)

* Agents
    - ``backends``      — codex / claude_code / gemini / pi (SPEC §10)
    - ``agent``         — backwards-compat shim for ``symphony.backends.*``

* Orchestration
    - ``orchestrator``  — state machine (SPEC §7,8,16)
    - ``archive``       — auto-archive helpers
    - ``auto_merge``    — symphony branch → host repo merge
    - ``progress_md``   — WORKFLOW-PROGRESS.md mirror
    - ``service``       — ``symphony service`` persistence

* User interfaces
    - ``cli``           — ``symphony`` CLI entry point
    - ``board_cli``     — ``symphony board ...`` helper
    - ``tui``           — Textual Kanban TUI
    - ``server``        — optional HTTP JSON API

* Utilities
    - ``doctor``        — WORKFLOW.md preflight checks
    - ``keep_awake``    — OS sleep prevention
    - ``wiki_sweep``    — wiki integrity sweep
    - ``mock_codex``    — mock Codex app-server for demos/tests
"""

__version__ = "0.6.3"
