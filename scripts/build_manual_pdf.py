#!/usr/bin/env python3
"""Render docs/manual/<lang>/*.html to docs/manual/pdf/*.pdf with Chromium.

Used by .github/workflows/pages.yml before the Pages artifact is uploaded, and
locally for a preview. Requires ``pip install playwright && playwright install chromium``.

    python scripts/build_manual_pdf.py            # all four PDFs
    python scripts/build_manual_pdf.py --out /tmp # elsewhere
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "docs" / "manual"
DOCS = (("en", "cheatsheet"), ("en", "tutorial"), ("ko", "cheatsheet"), ("ko", "tutorial"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=MANUAL / "pdf")
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright missing: pip install playwright && playwright install chromium", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for lang, name in DOCS:
            src = MANUAL / lang / f"{name}.html"
            dst = args.out / f"oh-my-symphony-{name}-{lang}.pdf"
            page.goto(src.resolve().as_uri(), wait_until="load")
            page.emulate_media(media="print")
            page.pdf(
                path=str(dst),
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="width:100%;font-size:9px;color:#5c6470;padding:0 14mm;'
                    'display:flex;justify-content:space-between;">'
                    f"<span>oh-my-symphony · {name} · {lang}</span>"
                    '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
                ),
                margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
            )
            print(f"wrote {dst.relative_to(ROOT)} ({dst.stat().st_size // 1024} KB)")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
