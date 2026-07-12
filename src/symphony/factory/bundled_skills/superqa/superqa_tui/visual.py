"""Visual regression: pixel-diff step screenshots against an accepted baseline.

Zero-config: when a baseline exists for a scenario, every run is compared and
changes above the threshold surface as `visual_change` side effects with a red
diff overlay image. Accept a new baseline with `superqa baseline <scenario>`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .scenario import Scenario, safe_name, superqa_home

PIXEL_DELTA = 24  # per-channel difference below this is "same" (antialiasing)


def baseline_dir(site: str, scenario_name: str, home: Path | None = None) -> Path:
    return (home or superqa_home()) / "baselines" / site / safe_name(scenario_name)


def compare_images(baseline: Path, current: Path, diff_out: Path | None = None) -> float:
    """Return percentage of changed pixels; write a red-overlay diff image if given."""
    from PIL import Image, ImageChops

    a = Image.open(baseline).convert("RGB")
    b = Image.open(current).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b).convert("L")
    mask = diff.point(lambda p: 255 if p > PIXEL_DELTA else 0)
    changed = mask.histogram()[255]
    total = a.size[0] * a.size[1]
    pct = (changed / total) * 100 if total else 0.0
    if diff_out is not None and pct > 0:
        base = b.convert("L").convert("RGB")
        red = Image.new("RGB", a.size, (220, 38, 38))
        Image.composite(red, base, mask).save(diff_out)
    return pct


def run_visual_checks(sc: Scenario, step_results, run_dir: Path) -> list[dict]:
    """Compare this run's screenshots to the baseline. Returns finding dicts."""
    bdir = baseline_dir(sc.site, sc.name)
    if not bdir.exists():
        return []
    findings: list[dict] = []
    threshold = sc.policy.visual_threshold
    for sr in step_results:
        if not sr.screenshot:
            continue
        base = bdir / sr.screenshot
        cur = run_dir / sr.screenshot
        if not base.exists() or not cur.exists():
            continue
        diff_name = sr.screenshot.replace(".png", "-diff.png")
        try:
            pct = compare_images(base, cur, run_dir / diff_name)
        except Exception:
            continue  # unreadable image must not fail the run
        if pct > threshold:
            findings.append({
                "step_index": sr.index,
                "pct": pct,
                "message": f"{pct:.1f}% pixel change vs baseline -> {diff_name}",
            })
    return findings


def accept_baseline(sc_site: str, sc_name: str, run_dir: Path) -> int:
    """Copy a run's step screenshots as the new baseline. Returns file count."""
    bdir = baseline_dir(sc_site, sc_name)
    if bdir.exists():
        shutil.rmtree(bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for png in sorted(run_dir.glob("step-*.png")):
        if png.name.endswith("-diff.png"):
            continue
        shutil.copy2(png, bdir / png.name)
        n += 1
    return n
