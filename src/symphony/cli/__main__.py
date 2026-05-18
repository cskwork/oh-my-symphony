"""``python -m symphony.cli`` entry point.

``symphony.service`` launches the orchestrator as
``python -m symphony.cli WORKFLOW --host ... --port ...``; this module
preserves that command after the CLI moved from ``cli.py`` into the
``cli/`` subpackage.
"""

from __future__ import annotations

import sys

from .main import main


if __name__ == "__main__":
    sys.exit(main())
