"""Regression guard — every lazy import inside `cli/main.py` must resolve.

Bug history
-----------

The v0.7.0 release shipped with ``from .tui import KanbanTUI`` inside
``src/symphony/cli/main.py``. After commit d5c4477 moved
``src/symphony/tui.py`` into the ``src/symphony/tui/`` package, the
relative import resolved as ``symphony.cli.tui`` (non-existent), so
``symphony --tui`` crashed at startup with ``ModuleNotFoundError``.
None of the existing tests imported ``symphony.cli.main`` AND triggered
the ``args.tui`` branch, so the broken lazy import slipped through.

Why a general guard, not a one-off test
---------------------------------------

The class of bug is "a function-body ``from X import Y`` references a
module that doesn't exist after a refactor moved or renamed it".
Module-import-time tests don't catch it because lazy imports only fire
when the surrounding code path runs. Rather than write one targeted
test per ``args.tui`` / subcommand branch — and forget to add a test
for the next lazy import that lands — this module scans the file with
AST and parametrizes a single resolution check across every such
import. New lazy imports added later are covered automatically.

Why scoped to cli/main.py
-------------------------

That's the binary entry point. A broken lazy import there crashes the
user on the first invocation, which is what the v0.7.0 bug felt like.
Lazy imports elsewhere in the codebase are best validated by the tests
that exercise those code paths (they were green for the v0.7.0
release; only the CLI entry point lacked coverage of its lazy
branches).
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLI_MAIN = _REPO_ROOT / "src" / "symphony" / "cli" / "main.py"
_CLI_MAIN_MODULE = "symphony.cli.main"


def _resolve_relative(module_name: str, *, level: int, target: str | None) -> str:
    """Resolve a relative ``from .X import Y`` against ``module_name``.

    Mirrors Python's own relative-import resolution: each leading dot
    drops one package level off the importer's module name.
    """
    if level == 0:
        return target or ""
    parts = module_name.split(".")
    # ``from .X import Y`` (level=1) inside ``a.b.c.main`` resolves
    # against package ``a.b.c`` — drop the final ``main`` component
    # plus ``level - 1`` parent packages.
    base_parts = parts[: -level] if level <= len(parts) else []
    base = ".".join(base_parts)
    if target:
        return f"{base}.{target}" if base else target
    return base


def _collect_lazy_imports(
    source: str, module_name: str
) -> list[tuple[str, str]]:
    """Return ``(resolved_module, imported_name)`` for every lazy
    ``from X import Y`` inside ``source``.

    "Lazy" = the ``ImportFrom`` statement lives inside a function or
    async function body. Top-level imports are skipped because the
    surrounding module-import already validates them.
    """
    tree = ast.parse(source, filename=str(_CLI_MAIN))
    out: list[tuple[str, str]] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(func):
            if not isinstance(child, ast.ImportFrom):
                continue
            resolved = _resolve_relative(
                module_name, level=child.level or 0, target=child.module
            )
            for alias in child.names:
                out.append((resolved, alias.name))
    return out


_LAZY_IMPORTS = _collect_lazy_imports(
    _CLI_MAIN.read_text(encoding="utf-8"), _CLI_MAIN_MODULE
)


def test_lazy_imports_were_discovered() -> None:
    """Guard the guard — the AST scan must find at least one lazy import.

    If a future refactor inlines every lazy import in cli/main.py, this
    test reminds the author to delete this whole module. Silent zero
    coverage is the same as no test.
    """
    assert _LAZY_IMPORTS, (
        f"AST scan of {_CLI_MAIN} found zero lazy imports. Either every "
        "lazy import was inlined (delete this test file) or the AST "
        "predicate is broken."
    )


@pytest.mark.parametrize(
    ("module_path", "name"),
    _LAZY_IMPORTS,
    ids=[f"{m}.{n}" for m, n in _LAZY_IMPORTS],
)
def test_cli_lazy_import_resolves(module_path: str, name: str) -> None:
    """Each lazy ``from X import Y`` in cli/main.py must resolve at runtime.

    Failure mode this catches: a code-mover refactor that relocates a
    module without updating the lazy import in cli/main.py. The v0.7.0
    ``from .tui import KanbanTUI`` regression is the canonical example
    — fixed in commit e714adf.

    ``from X import Y`` succeeds when either of:

    * ``X`` is a module that exports a top-level name ``Y``
      (function, class, constant, ...).
    * ``X.Y`` is itself an importable submodule
      (``from . import board`` style).

    Mirroring Python's resolution order, the submodule probe is the
    fallback: we accept whichever path the interpreter would take.
    """
    module = importlib.import_module(module_path)
    if hasattr(module, name):
        return
    # `from X import Y` where Y is a submodule — would succeed at
    # runtime once Python imports `X.Y`. Probe it explicitly.
    try:
        importlib.import_module(f"{module_path}.{name}")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            f"`from {module_path} import {name}` would crash at runtime: "
            f"`{module_path}` neither exports `{name}` nor contains a "
            f"submodule of that name ({exc})."
        ) from exc
