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
    - ``trackers``      — tracker protocol + ``file`` / ``jira`` / ``linear`` adapters (SPEC §11)

* Agents
    - ``backends``      — codex / claude_code / gemini / agy / kiro / opencode / pi (SPEC §10)
    - ``agent``         — backwards-compat shim for ``symphony.backends.*``

* Orchestration
    - ``orchestrator``  — state machine (SPEC §7,8,16)
    - ``progress_md``   — WORKFLOW-PROGRESS.md mirror
    - ``service``       — ``symphony service`` persistence

* User interfaces
    - ``cli``           — ``symphony`` CLI subpackage
        - ``cli.main``    — root entry (``symphony [WORKFLOW]``, dispatches subcommands)
        - ``cli.board``   — ``symphony board ...`` file-tracker helper
        - ``cli.doctor``  — ``symphony doctor`` WORKFLOW.md preflight checks
    - ``tui``           — Textual Kanban TUI
    - ``server``        — HTTP server (web Kanban app + JSON API)
    - ``webapi``        — web app REST routes + static SPA serving
    - ``skills``        — SKILL.md discovery + prompt injection
    - ``stats``         — run-stats event store + aggregation

* Utilities (``symphony.utils``)
    - ``utils.archive``     — auto-archive helpers
    - ``utils.auto_merge``  — symphony branch → host repo merge
    - ``utils.keep_awake``  — OS sleep prevention
    - ``utils.wiki_sweep``  — wiki integrity sweep

* Demo / fixtures
    - ``mock_codex``    — mock Codex app-server, runnable as ``python -m symphony.mock_codex``
"""

__version__ = "0.10.0"
