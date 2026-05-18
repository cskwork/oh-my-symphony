"""``symphony`` command-line entry points.

The console script (``pyproject.toml``'s ``[project.scripts] symphony``)
resolves to ``symphony.cli:main`` — re-exported here from :mod:`.main` so
the move from ``cli.py`` to ``cli/`` keeps the published entry point
working without rewriting the pyproject.

``python -m symphony.cli`` is handled by :mod:`.__main__`, which the
managed service launches as a subprocess (see ``symphony.service``).
"""

from __future__ import annotations

from .main import main

__all__ = ["main"]
