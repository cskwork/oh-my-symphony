"""JUnit XML output - lets CI systems (Jenkins, GitHub Actions, GitLab) render
SuperQA runs as native test results. One testsuite per scenario, one testcase
per step.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .engine import RunResult


def write_junit(results: list[RunResult], path: Path) -> Path:
    suites = ET.Element("testsuites")
    total_tests = total_failures = 0
    for r in results:
        sc = r.scenario
        suite = ET.SubElement(suites, "testsuite", {
            "name": f"{sc.site}.{sc.name}",
            "tests": str(len(r.step_results)),
            "failures": str(r.failed),
            "skipped": str(sum(1 for s in r.step_results if s.status == "skipped")),
            "time": f"{max(0.0, r.finished_at - r.started_at):.2f}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S",
                                       time.localtime(r.started_at)),
        })
        for sr in r.step_results:
            case = ET.SubElement(suite, "testcase", {
                "name": f"{sr.index + 1}. {sr.step.description or sr.step.action}",
                "classname": f"{sc.site}.{sc.name}",
                "time": f"{sr.duration_ms / 1000:.2f}",
            })
            if sr.status == "fail":
                ET.SubElement(case, "failure", {"message": sr.error or "failed"})
            elif sr.status == "skipped":
                ET.SubElement(case, "skipped")
        # side effects worth CI eyes: attach as suite-level stdout
        visible = r.visible_effects
        if visible:
            out = ET.SubElement(suite, "system-out")
            out.text = "\n".join(
                f"[{e.severity}] {e.type} x{e.count}: {e.message}" for e in visible)
        total_tests += len(r.step_results)
        total_failures += r.failed
    suites.set("tests", str(total_tests))
    suites.set("failures", str(total_failures))
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suites).write(path, encoding="utf-8", xml_declaration=True)
    return path
