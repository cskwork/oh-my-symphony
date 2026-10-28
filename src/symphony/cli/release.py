"""`symphony release check` — host-side application release validation.

The implementation ships in the optional ``oh-my-symphony-release``
distribution (``symphony.release.cli``); without it the command only prints
the install hint.
"""

from __future__ import annotations

import sys

from .. import component_installed, install_hint


def main(argv: list[str] | None = None) -> int:
    if not component_installed("release"):
        print(f"symphony release: {install_hint('release')}", file=sys.stderr)
        return 2
    from ..release.cli import main as release_main  # pyright: ignore[reportMissingImports]

    return release_main(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
