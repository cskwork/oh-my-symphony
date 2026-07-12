from pathlib import Path

import pytest

from symphony.factory.wayfinder import parse_wayfinder_ticket


def test_parser_reads_route_dependencies_and_explicit_overlays(tmp_path: Path) -> None:
    path = tmp_path / "001-dashboard.md"
    path.write_text(
        """---
id: dashboard
title: Build the dashboard
route: GREENFIELD
blocked_by: [foundation]
skills: [superdesign, superqa, superdesign]
---

## Acceptance criteria

- WHEN the page opens THEN it SHALL show current status.

## Proof commands

- `npm test`

## Non-goals

- Unrelated work.
""",
        encoding="utf-8",
    )

    ticket = parse_wayfinder_ticket(path)

    assert ticket.title == "Build the dashboard"
    assert ticket.route == "GREENFIELD"
    assert ticket.key == "dashboard"
    assert ticket.blocked_by == ("foundation",)
    assert ticket.skills == ("supergoal", "superdesign", "superqa")
    assert "Acceptance criteria" in ticket.description


def test_parser_requires_title_route_and_acceptance(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text("---\nid: bad\ntitle: Incomplete\n---\n", encoding="utf-8")

    try:
        parse_wayfinder_ticket(path)
    except ValueError as exc:
        assert "Route" in str(exc)
    else:
        raise AssertionError("invalid Wayfinder ticket was accepted")


def test_overlay_routing_uses_metadata_not_title_heuristics(tmp_path: Path) -> None:
    path = tmp_path / "ui.md"
    path.write_text(
        """---
id: ui
title: Design a polished dashboard UI
route: GREENFIELD
blocked_by: []
skills: []
---

## Acceptance criteria

- The dashboard is usable.

## Proof

- Manual check.

## Non-goals

- Unrelated work.
""",
        encoding="utf-8",
    )

    assert parse_wayfinder_ticket(path).skills == ("supergoal",)


def test_overlay_routing_infers_ui_browser_and_product_research_metadata(
    tmp_path: Path,
) -> None:
    ui = tmp_path / "ui.md"
    ui.write_text(
        """---
id: ui
title: Build the dashboard
route: GREENFIELD
kind: design
browser: false
blocked_by: []
skills: []
---

## Acceptance criteria

- The dashboard is usable.

## Proof

- Browser check.

## Non-goals

- Unrelated work.
""",
        encoding="utf-8",
    )
    research = tmp_path / "research.md"
    research.write_text(
        """---
id: research
title: Validate customer demand
route: LEGACY
kind: research
blocked_by: []
skills: [superqa]
---

## Acceptance criteria

- Customer evidence is recorded.

## Proof

- Evidence review.

## Non-goals

- Unrelated work.
""",
        encoding="utf-8",
    )
    browser = tmp_path / "browser.md"
    browser.write_text(
        """---
id: browser
title: Verify the existing flow
route: LEGACY
kind: qa
browser: false
blocked_by: []
skills: []
---

## Acceptance criteria

- The browser flow passes.

## Proof

- Browser check.

## Non-goals

- Unrelated work.
""",
        encoding="utf-8",
    )

    assert parse_wayfinder_ticket(ui).skills == (
        "supergoal",
        "superdesign",
        "superqa",
    )
    assert parse_wayfinder_ticket(research).skills == (
        "supergoal",
        "superpm",
        "superqa",
    )
    assert parse_wayfinder_ticket(browser).skills == ("supergoal", "superqa")


def test_overlay_metadata_types_and_values_are_validated(tmp_path: Path) -> None:
    invalid_kind = tmp_path / "kind.md"
    invalid_kind.write_text(
        """---
id: invalid-kind
title: Invalid kind
route: LEGACY
kind: backend
---

## Acceptance criteria

- It is rejected.

## Proof

- Parser check.

## Non-goals

- Unrelated work.
""",
        encoding="utf-8",
    )
    invalid_browser = tmp_path / "browser-type.md"
    invalid_browser.write_text(
        """---
id: invalid-browser
title: Invalid browser marker
route: LEGACY
browser: "yes"
---

## Acceptance criteria

- It is rejected.

## Proof

- Parser check.

## Non-goals

- Unrelated work.
""",
        encoding="utf-8",
    )

    for path, message in (
        (invalid_kind, "kind must be one of"),
        (invalid_browser, "browser must be a boolean"),
    ):
        try:
            parse_wayfinder_ticket(path)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"invalid metadata was accepted: {path}")


def test_delivery_ticket_rejects_non_delivery_supergoal_route(tmp_path: Path) -> None:
    path = tmp_path / "review.md"
    path.write_text(
        """---
id: review
title: Review only
route: REVIEW-ONLY
---

## Acceptance criteria

- The review is written.

## Proof

- Read the report.

## Non-goals

- Product code.
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported Route"):
        parse_wayfinder_ticket(path)
