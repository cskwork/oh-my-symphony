"""Run-to-run comparison: what changed since the last run of the same scenario.

The regression verdict a developer actually wants after shipping a feature:
new failures and newly appeared side-effect types - not the absolute counts.
"""
from __future__ import annotations

import json

from .i18n import t


def compute_diff(prev_summary_json: str | None, current: dict) -> dict:
    """Compare a run summary (engine.RunResult.summary_dict) against the previous one."""
    if not prev_summary_json:
        return {"first_run": True}
    try:
        prev = json.loads(prev_summary_json)
    except Exception:
        return {"first_run": True}
    prev_failed = set(prev.get("failed_steps") or [])
    cur_failed = set(current.get("failed_steps") or [])
    prev_eff = set(prev.get("effect_digests") or [])
    cur_eff = set(current.get("effect_digests") or [])
    return {
        "first_run": False,
        "status_change": (prev.get("status"), current.get("status")),
        "new_failed_steps": sorted(cur_failed - prev_failed),
        "resolved_steps": sorted(prev_failed - cur_failed),
        "new_effects": sorted(cur_eff - prev_eff),
        "gone_effects": sorted(prev_eff - cur_eff),
    }


def is_clean(diff: dict) -> bool:
    return not diff.get("first_run") and not any(
        diff.get(k) for k in ("new_failed_steps", "resolved_steps",
                              "new_effects", "gone_effects"))


def format_diff_lines(diff: dict, lang: str = "ko") -> list[str]:
    """Human lines for CLI/TUI/report. Empty-change runs get one calm line."""
    if diff.get("first_run"):
        return [t("diff_first_run", lang)]
    lines: list[str] = []
    prev_s, cur_s = diff.get("status_change", (None, None))
    if prev_s != cur_s and prev_s and cur_s:
        word = {"pass": t("status_pass", lang), "fail": t("status_fail", lang)}
        lines.append(f"{t('diff_status_change', lang)}: "
                     f"{word.get(prev_s, prev_s)} -> {word.get(cur_s, cur_s)}")
    for key, label in (("new_failed_steps", "diff_new_failures"),
                       ("resolved_steps", "diff_resolved"),
                       ("new_effects", "diff_new_effects"),
                       ("gone_effects", "diff_gone_effects")):
        items = diff.get(key) or []
        if items:
            lines.append(f"{t(label, lang)} ({len(items)}):")
            lines.extend(f"  - {it}" for it in items[:8])
            if len(items) > 8:
                lines.append(f"  ... 외 {len(items) - 8}건" if lang == "ko"
                             else f"  ... and {len(items) - 8} more")
    if not lines:
        lines.append(t("diff_same", lang))
    return lines
