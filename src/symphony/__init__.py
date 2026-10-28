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
    - ``backends``      — codex / claude_code / gemini / agy / kiro / opencode / pi / prime_agent (SPEC §10)
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

from importlib.util import find_spec as _find_spec
from pkgutil import extend_path as _extend_path

__version__ = "0.29.0"

# Optional components ship as separate distributions under `packages/` and
# contribute `symphony.<name>` subpackages from their own install location.
# Editable installs keep the trees apart, so merge every `symphony/` directory
# on sys.path; wheels installed together already share one. Pyright cannot
# follow this merge, so core imports of optional subpackages carry an ignore
# comment.
__path__ = _extend_path(__path__, __name__)

# name -> (importable module that proves the install, human label, package dir)
OPTIONAL_COMPONENTS: dict[str, tuple[str, str, str]] = {
    "pm": ("symphony.pm", "the PM Workspace", "packages/pm"),
    "release": ("symphony.release", "app release contracts", "packages/release"),
    "chat": ("symphony.chat", "the operator chat", "packages/chat"),
}


def component_installed(name: str) -> bool:
    """Whether the optional component *name* can be imported."""
    module, _label, _directory = OPTIONAL_COMPONENTS[name]
    return _find_spec(module) is not None


def install_hint(name: str) -> str:
    """One-line explanation of how to install the optional component *name*."""
    _module, label, directory = OPTIONAL_COMPONENTS[name]
    return (
        f"{label} is not installed; install it from the oh-my-symphony "
        f"checkout: python -m pip install ./{directory}"
    )


def installed_components() -> dict[str, bool]:
    """Availability of every optional component, for API payloads."""
    return {name: component_installed(name) for name in OPTIONAL_COMPONENTS}


PM_INSTALL_HINT = install_hint("pm")


def pm_installed() -> bool:
    """Whether the optional ``symphony.pm`` package can be imported."""
    return component_installed("pm")
