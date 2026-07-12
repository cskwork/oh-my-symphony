"""Scenario model + YAML IO.

A scenario is a human-editable YAML file. Non-devs read/edit it, the engine runs it.
Selectors accept either a plain string (CSS, or "text=...") or a dict with a
fallback chain: {testid, css, role, name, text}.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_ACTIONS = {
    "goto", "click", "dblclick", "fill", "press", "select", "check", "uncheck",
    "hover", "wait", "expect_visible", "expect_text", "expect_url",
    "screenshot", "switch_tab", "close_tab", "scroll", "login",
}

DEFAULT_HOME = Path.home() / ".superqa"


def safe_name(name: str) -> str:
    return re.sub(r"[^\w\-가-힣]+", "-", name).strip("-")[:60] or "run"


def superqa_home() -> Path:
    import os
    home = Path(os.environ.get("SUPERQA_HOME", DEFAULT_HOME))
    for sub in ("scenarios", "reports", "sites", "schedules"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home


@dataclass
class Step:
    action: str
    selector: Any = None          # str | dict | None
    value: str | None = None      # fill text / press key / select option / expect text
    url: str | None = None        # goto / expect_url (substring match)
    description: str = ""         # human-readable, user's language
    timeout_ms: int = 10000
    optional: bool = False        # optional step failure does not fail the scenario
    expect_popup: bool = False    # click is expected to open a new tab/popup
    retry: int = 0                # extra attempts on failure (flaky UIs)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"action": self.action}
        for key in ("selector", "value", "url"):
            v = getattr(self, key)
            if v is not None:
                d[key] = v
        if self.description:
            d["description"] = self.description
        if self.timeout_ms != 10000:
            d["timeout_ms"] = self.timeout_ms
        if self.optional:
            d["optional"] = True
        if self.expect_popup:
            d["expect_popup"] = True
        if self.retry:
            d["retry"] = self.retry
        return d

    @staticmethod
    def from_dict(d: dict) -> "Step":
        action = str(d.get("action", "")).strip()
        if action not in VALID_ACTIONS:
            raise ValueError(f"unknown action: {action!r}")
        return Step(
            action=action,
            selector=d.get("selector"),
            value=None if d.get("value") is None else str(d.get("value")),
            url=d.get("url"),
            description=str(d.get("description", "")),
            timeout_ms=int(d.get("timeout_ms", 10000)),
            optional=bool(d.get("optional", False)),
            expect_popup=bool(d.get("expect_popup", False)),
            retry=max(0, int(d.get("retry", 0))),
        )


@dataclass
class Policy:
    dialogs: str = "accept"       # accept | dismiss | fail
    popups: str = "follow"        # follow (switch to new tab) | ignore | fail
    fail_on_console_error: bool = False
    fail_on_http_error: bool = False
    ignore_effects: list[str] = field(default_factory=list)  # substrings -> noise
    visual_threshold: float = 1.0  # % pixels changed vs baseline before flagging

    def to_dict(self) -> dict:
        d = {
            "dialogs": self.dialogs,
            "popups": self.popups,
            "fail_on_console_error": self.fail_on_console_error,
            "fail_on_http_error": self.fail_on_http_error,
        }
        if self.ignore_effects:
            d["ignore_effects"] = list(self.ignore_effects)
        if self.visual_threshold != 1.0:
            d["visual_threshold"] = self.visual_threshold
        return d

    @staticmethod
    def from_dict(d: dict | None) -> "Policy":
        d = d or {}
        return Policy(
            dialogs=str(d.get("dialogs", "accept")),
            popups=str(d.get("popups", "follow")),
            fail_on_console_error=bool(d.get("fail_on_console_error", False)),
            fail_on_http_error=bool(d.get("fail_on_http_error", False)),
            ignore_effects=[str(x) for x in (d.get("ignore_effects") or [])],
            visual_threshold=float(d.get("visual_threshold", 1.0)),
        )


def site_ignore_patterns(site: str, home: Path | None = None) -> list[str]:
    """Noise substrings from ~/.superqa/sites/<site>/ignore.yaml (a plain list)."""
    p = (home or superqa_home()) / "sites" / site / "ignore.yaml"
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


@dataclass
class Scenario:
    name: str
    site: str = "default"
    base_url: str = ""
    language: str = "ko"
    tags: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    policy: Policy = field(default_factory=Policy)
    path: Path | None = None      # where it was loaded from / saved to

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "site": self.site,
            "base_url": self.base_url,
            "language": self.language,
            "tags": list(self.tags),
            "policy": self.policy.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
        }

    @staticmethod
    def from_dict(d: dict, path: Path | None = None) -> "Scenario":
        steps = [Step.from_dict(s) for s in (d.get("steps") or [])]
        return Scenario(
            name=str(d.get("name", path.stem if path else "scenario")),
            site=str(d.get("site", "default")),
            base_url=str(d.get("base_url", "")),
            language=str(d.get("language", "ko")),
            tags=[str(t) for t in (d.get("tags") or [])],
            steps=steps,
            policy=Policy.from_dict(d.get("policy")),
            path=path,
        )

    def save(self, path: Path | None = None) -> Path:
        target = path or self.path
        if target is None:
            safe = re.sub(r"[^\w\-가-힣]+", "-", self.name).strip("-") or "scenario"
            target = superqa_home() / "scenarios" / self.site / f"{safe}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.path = target
        return target


def load_scenario(path: Path) -> Scenario:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a scenario file")
    return Scenario.from_dict(data, path=path)


def list_scenarios(home: Path | None = None) -> list[Scenario]:
    root = (home or superqa_home()) / "scenarios"
    out: list[Scenario] = []
    for p in sorted(root.rglob("*.yaml")):
        try:
            out.append(load_scenario(p))
        except Exception:
            continue  # surfaced separately via broken_scenarios()
    return out


def broken_scenarios(home: Path | None = None) -> list[tuple[Path, str]]:
    """Scenario files that fail to load - shown as warnings so they never vanish silently."""
    root = (home or superqa_home()) / "scenarios"
    out: list[tuple[Path, str]] = []
    for p in sorted(root.rglob("*.yaml")):
        try:
            load_scenario(p)
        except Exception as e:
            out.append((p, str(e).splitlines()[0][:120]))
    return out


def find_scenario(name_or_path: str, home: Path | None = None) -> Scenario:
    p = Path(name_or_path).expanduser()
    if p.exists():
        return load_scenario(p)
    for sc in list_scenarios(home):
        if sc.name == name_or_path or (sc.path and sc.path.stem == name_or_path):
            return sc
    raise FileNotFoundError(f"scenario not found: {name_or_path}")
