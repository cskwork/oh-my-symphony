#!/usr/bin/env python3
"""Keep the manual's key/command tables in sync with the running code.

The TUI cheatsheet tables are generated from ``symphony.tui.screens.HELP_SECTIONS``
and the CLI table from ``symphony.cli.main.SUBCOMMANDS`` so the PDF can never
drift from what the app actually binds. Korean captions live in this file;
``--check`` fails when a new binding or subcommand has no translation or when
an HTML file is out of date.

    python scripts/sync_manual_tables.py          # rewrite docs/manual/*/cheatsheet.html
    python scripts/sync_manual_tables.py --check  # exit 1 if anything is stale
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from symphony.cli.main import SUBCOMMANDS  # noqa: E402
from symphony.tui.screens import HELP_SECTIONS  # noqa: E402

MANUAL = ROOT / "docs" / "manual"
LANGS = ("en", "ko")

SECTION_KO = {"Board": "보드", "Navigate": "이동", "Layout": "레이아웃", "Ticket": "티켓"}

ACTION_KO = {
    "quit (asks twice while workers run)": "종료 (워커 실행 중이면 두 번 확인)",
    "refresh + re-poll the tracker": "새로고침 + 트래커 재폴링",
    "this help": "이 도움말",
    "cycle TUI + doc language": "TUI·문서 언어 순환",
    "run statistics": "실행 통계",
    "filter cards by id / title / label": "id · 제목 · 라벨로 카드 필터",
    "close filter, reset zoom": "필터 닫기, 확대 해제",
    "focus next / previous card": "다음 / 이전 카드로 포커스",
    "scroll the focused lane": "포커스된 레인 스크롤",
    "jump to top / bottom": "맨 위 / 맨 아래로",
    "page down / up": "페이지 아래 / 위",
    "open the full-detail modal": "상세 모달 열기",
    "show / hide the detail pane": "상세 패널 표시 / 숨기기",
    "focus the detail pane / back to the board": "상세 패널로 포커스 / 보드로 복귀",
    "zoom that lane / reset zoom": "해당 레인 확대 / 확대 해제",
    "next / previous page of lanes": "다음 / 이전 레인 페이지",
    "more / fewer lanes per page": "페이지당 레인 늘리기 / 줄이기",
    "toggle compact / rich cards": "컴팩트 / 리치 카드 전환",
    "new ticket (file board only)": "새 티켓 (파일 보드 전용)",
    "edit the focused ticket (file board only)": "포커스된 티켓 편집 (파일 보드 전용)",
    "archive the focused Done card": "포커스된 Done 카드 아카이브",
    "confirm the focused Human Review card as Done": "포커스된 Human Review 카드를 Done으로 승인",
    "skip Document for the focused card": "포커스된 카드의 Document 단계 건너뛰기",
    "pause / resume the focused running worker": "실행 중인 워커 일시정지 / 재개",
}

SUBCOMMAND_KO = {
    "tui": "칸반 TUI 열기 (--tui와 동일)",
    "doctor": "실행 전 WORKFLOW.md 사전 점검",
    "board": "파일 칸반 보드 관리 (init/ls/new/mv/update/show/graph)",
    "service": "오케스트레이터를 백그라운드 서비스로 실행",
    "project": "독립 프로젝트 서비스 등록·실행",
    "hub": "여러 프로젝트를 모아 보는 허브 서버",
    "runs": "최근 실행 기록 출력",
    "release": "애플리케이션 릴리스 증빙 검증",
    "wiki-sweep": "docs/llm-wiki 중복 슬러그·고아 문서 점검",
}

MARKER = re.compile(
    r"(<!-- generated:(?P<name>[a-z-]+) -->)(?P<body>.*?)(<!-- /generated -->)",
    re.S,
)


def _kbd(keys: str) -> str:
    """Render "a / b, c / d" as kbd boxes; a bare "/" is itself a key."""
    if keys.strip() == "/":
        return "<kbd>/</kbd>"
    groups = []
    for group in keys.split(", "):
        parts = [p.strip() for p in group.split(" / ")]
        groups.append(" / ".join(f"<kbd>{html.escape(p)}</kbd>" for p in parts))
    return ", ".join(groups)


def render_tui_keys(lang: str) -> str:
    out: list[str] = []
    for title, rows in HELP_SECTIONS:
        heading = title if lang == "en" else SECTION_KO[title]
        out.append(f'<div class="card"><h3>{html.escape(heading)}</h3><table>')
        for key, action in rows:
            caption = action if lang == "en" else ACTION_KO[action]
            out.append(f"<tr><td>{_kbd(key)}</td><td>{html.escape(caption)}</td></tr>")
        out.append("</table></div>")
    return "\n" + "\n".join(out) + "\n"


def render_cli_subcommands(lang: str) -> str:
    out = ["<table><tr><th>", "Command" if lang == "en" else "명령", "</th><th>",
           "What it does" if lang == "en" else "하는 일", "</th></tr>"]
    for name, desc in SUBCOMMANDS:
        caption = desc if lang == "en" else SUBCOMMAND_KO[name]
        out.append(f"<tr><td><code>symphony {html.escape(name)}</code></td><td>{html.escape(caption)}</td></tr>")
    out.append("</table>")
    return "\n" + "".join(out) + "\n"


RENDERERS = {"tui-keys": render_tui_keys, "cli-subcommands": render_cli_subcommands}


def render_file(path: Path, lang: str) -> str:
    text = path.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in RENDERERS:
            raise SystemExit(f"{path}: unknown generated block '{name}'")
        return f"{match.group(1)}{RENDERERS[name](lang)}{match.group(4)}"

    return MARKER.sub(replace, text)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    missing = [a for _, rows in HELP_SECTIONS for _, a in rows if a not in ACTION_KO]
    missing += [n for n, _ in SUBCOMMANDS if n not in SUBCOMMAND_KO]
    if missing:
        print("missing Korean captions:", *missing, sep="\n  ", file=sys.stderr)
        return 1
    stale: list[Path] = []
    for lang in LANGS:
        path = MANUAL / lang / "cheatsheet.html"
        rendered = render_file(path, lang)
        if rendered != path.read_text(encoding="utf-8"):
            stale.append(path)
            if not check:
                path.write_text(rendered, encoding="utf-8")
    if check and stale:
        print("stale manual tables; run scripts/sync_manual_tables.py:", *stale, sep="\n  ", file=sys.stderr)
        return 1
    print("manual tables " + ("in sync" if not stale else f"updated: {', '.join(p.name for p in stale)}"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
