from __future__ import annotations

import json
import hashlib
import base64
import os
import time
from importlib.metadata import PathDistribution
from types import SimpleNamespace

from symphony.orchestrator.run_registry import RunRegistry
from symphony.pm.application import PmApplication
import symphony.pm.application as application_module
from symphony.pm.models import PmError
import pytest


@pytest.fixture(autouse=True)
def _reset_package_binding_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application_module, "_PACKAGE_BINDING_CACHE", None)


def _application(
    tmp_path, *, development_verification: bool = False,
    flags: dict[str, bool] | None = None,
):
    repository = SimpleNamespace(
        project_id="p1", registry=RunRegistry(tmp_path / "state.db")
    )
    return PmApplication(
        repository, None, None, None, None,  # type: ignore[arg-type]
        project_root=tmp_path,
        flags=flags or {
            "authoring": True, "exchange": True,
            "assistance": True, "handoff": True,
        },
        development_verification=development_verification,
    )


def _row(digest: str, gate: str, status: str = "passed") -> dict:
    return {
        "status": status,
        "evidence_ids": [f"receipt:{gate}"],
        "evaluated_at": "2026-10-04T15:00:00Z",
        "source_digest": digest,
        "reasons": [] if status == "passed" else ["failed verification"],
    }


def _receipt(tmp_path, receipt_id: str, digest: str, *, passed: bool = True) -> dict:
    value = {
        "version": 1, "source_digest": digest,
        "command": ["pytest", "-q"], "return_code": 0 if passed else 1,
        "result": "passed" if passed else "failed",
        "completed_at": "2026-10-04T14:59:00Z",
    }
    raw = json.dumps(value, sort_keys=True).encode()
    root = tmp_path / ".symphony" / "pm" / "release-receipts"
    root.mkdir(parents=True, exist_ok=True)
    name = f"{receipt_id}.json"
    (root / name).write_bytes(raw)
    return {"file": name, "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}


def test_flags_do_not_claim_release_gates_without_bound_host_receipt(tmp_path) -> None:
    capabilities = _application(tmp_path).capabilities()
    assert capabilities["flags"] == {
        "authoring": True, "exchange": True, "assistance": True, "handoff": True,
    }
    assert capabilities["highest_passed_gate"] is None
    assert all(not row["passed"] for row in capabilities["gates"].values())
    assert {row["status"] for row in capabilities["gates"].values()} == {"not_evaluated"}
    assert capabilities["release_evidence"]["status"] == "not_evaluated"
    assert capabilities["effective_flags"] == {
        "authoring": False, "exchange": False,
        "assistance": False, "handoff": False,
    }
    assert capabilities["capabilities"]["authoring"]["requested"] is True
    assert capabilities["capabilities"]["authoring"]["effective"] is False
    assert capabilities["capabilities"]["authoring"]["missing_gates"] == ["G0", "G1"]
    with pytest.raises(PmError) as exc:
        _application(tmp_path)._require("authoring")
    assert exc.value.code == "capability_not_ready"
    assert exc.value.details["missing_gates"] == ["G0", "G1"]


def test_host_only_development_verification_does_not_claim_passed_gates(tmp_path) -> None:
    app = _application(tmp_path, development_verification=True)
    app._require("authoring")
    capabilities = app.capabilities()
    assert capabilities["health"]["development_verification"] is True
    assert capabilities["highest_passed_gate"] is None
    assert all(not row["passed"] for row in capabilities["gates"].values())
    assert all(capabilities["effective_flags"].values())
    assert capabilities["capabilities"]["authoring"]["development_verification"] is True
    assert "host development verification context" in capabilities["capabilities"]["authoring"]["reasons"][0]


def test_development_require_does_not_hash_release_binding(
    tmp_path, monkeypatch,
) -> None:
    def unexpected():
        raise AssertionError("development mutation must not hash release evidence")

    monkeypatch.setattr(application_module, "_installed_package_binding", unexpected)
    app = _application(tmp_path, development_verification=True)
    app._require("authoring")
    app._require("exchange")


def test_release_binding_is_verified_once_per_application(
    tmp_path, monkeypatch,
) -> None:
    calls = 0
    binding = {
        "package_version": "1", "package_digest": "sha256:package",
        "verified_file_count": "10",
    }

    def installed():
        nonlocal calls
        calls += 1
        return binding, None

    monkeypatch.setattr(application_module, "_installed_package_binding", installed)
    monkeypatch.setattr(
        application_module, "_installed_package_fingerprint", lambda: (("stable", 1, 1, 1, 1, 1, 1),)
    )
    monkeypatch.setattr(application_module, "_PACKAGE_BINDING_CACHE", None)
    app = _application(tmp_path)
    app.capabilities()
    app.capabilities()
    with pytest.raises(PmError):
        app._require("authoring")
    assert calls == 1


def test_cached_binding_rehashes_same_size_tampered_code_static_and_metadata(
    tmp_path, monkeypatch,
) -> None:
    site = tmp_path / "site"
    code = site / "symphony" / "pm" / "models.py"
    asset = site / "symphony" / "pm" / "static" / "index.js"
    code.parent.mkdir(parents=True)
    asset.parent.mkdir(parents=True)
    code.write_bytes(b"trusted-code\n")
    asset.write_bytes(b"trusted-static\n")
    dist_info = site / "oh_my_symphony-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: oh-my-symphony\nVersion: 1.0\n",
        encoding="utf-8",
    )

    def record_row(path, data: bytes) -> str:
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        return f"{path},sha256={digest},{len(data)}"

    (dist_info / "RECORD").write_text(
        "\n".join((
            record_row("symphony/pm/models.py", code.read_bytes()),
            record_row("symphony/pm/static/index.js", asset.read_bytes()),
            record_row(
                "oh_my_symphony-1.0.dist-info/METADATA",
                (dist_info / "METADATA").read_bytes(),
            ),
            "oh_my_symphony-1.0.dist-info/RECORD,,",
        )) + "\n",
        encoding="utf-8",
    )
    distribution = PathDistribution(dist_info)
    monkeypatch.setattr(
        application_module.metadata, "distribution", lambda _name: distribution
    )
    monkeypatch.setattr(application_module, "_PACKAGE_BINDING_CACHE", None)
    app = _application(tmp_path / "project")
    warm = app.capabilities()
    assert warm["release_evidence"]["binding"] is not None

    for target in (code, asset, dist_info / "METADATA"):
        original = target.read_bytes()
        before = target.stat()
        time.sleep(0.001)
        target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert target.stat().st_size == before.st_size
        assert target.stat().st_mtime_ns == before.st_mtime_ns
        if os.name != "nt":
            assert target.stat().st_ctime_ns != before.st_ctime_ns
        tampered = app.capabilities()
        assert tampered["release_evidence"]["binding"] is None
        assert "differs from RECORD" in tampered["release_evidence"]["reason"]
        time.sleep(0.001)
        target.write_bytes(original)
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        restored = app.capabilities()
        assert restored["release_evidence"]["binding"] is not None


def test_binding_cache_rehashes_when_files_change_during_hash(
    monkeypatch,
) -> None:
    first = (("file", 1, 1, 1, 1, 1, 1),)
    changed = (("file", 1, 1, 1, 1, 1, 2),)
    fingerprints = iter((first, first, changed, changed))
    results = iter((
        ({"package_digest": "sha256:stale"}, None),
        (None, "installed package file differs from RECORD"),
    ))
    monkeypatch.setattr(
        application_module, "_installed_package_fingerprint",
        lambda: next(fingerprints),
    )
    monkeypatch.setattr(
        application_module, "_installed_package_binding", lambda: next(results)
    )
    monkeypatch.setattr(application_module, "_PACKAGE_BINDING_CACHE", None)
    binding, error = application_module._cached_installed_package_binding()
    assert binding is None
    assert error == "installed package file differs from RECORD"


def test_binding_cache_falls_back_to_full_hash_without_safe_fingerprint(
    monkeypatch,
) -> None:
    calls = 0

    def installed():
        nonlocal calls
        calls += 1
        return {"package_digest": f"sha256:{calls}"}, None

    monkeypatch.setattr(
        application_module, "_installed_package_fingerprint", lambda: None
    )
    monkeypatch.setattr(application_module, "_installed_package_binding", installed)
    monkeypatch.setattr(application_module, "_PACKAGE_BINDING_CACHE", None)
    assert application_module._cached_installed_package_binding()[0] == {
        "package_digest": "sha256:1"
    }
    assert application_module._cached_installed_package_binding()[0] == {
        "package_digest": "sha256:2"
    }


def test_binding_cache_fails_closed_when_both_hash_windows_are_unstable(
    monkeypatch,
) -> None:
    fingerprints = iter((
        (("file", 1, 1, 1, 1, 1, 1),),
        (("file", 1, 1, 1, 1, 1, 1),),
        (("file", 1, 1, 1, 1, 1, 2),),
        (("file", 1, 1, 1, 1, 1, 3),),
    ))
    monkeypatch.setattr(
        application_module, "_installed_package_fingerprint",
        lambda: next(fingerprints),
    )
    monkeypatch.setattr(
        application_module, "_installed_package_binding",
        lambda: ({"package_digest": "sha256:apparently-good"}, None),
    )
    binding, error = application_module._cached_installed_package_binding()
    assert binding is None
    assert error == "installed package changed during verification"
    assert application_module._PACKAGE_BINDING_CACHE is None
    stable = (("file", 1, 1, 1, 1, 1, 4),)
    monkeypatch.setattr(
        application_module, "_installed_package_fingerprint", lambda: stable
    )
    recovered = {"package_digest": "sha256:recovered"}
    monkeypatch.setattr(
        application_module, "_installed_package_binding",
        lambda: (recovered, None),
    )
    assert application_module._cached_installed_package_binding() == (
        recovered, None
    )
    assert application_module._PACKAGE_BINDING_CACHE is not None


def test_disabled_flags_are_requested_and_effective_false_with_reasons(tmp_path) -> None:
    app = _application(
        tmp_path,
        flags={
            "authoring": False, "exchange": False,
            "assistance": False, "handoff": False,
        },
    )
    capabilities = app.capabilities()
    assert not any(capabilities["flags"].values())
    assert not any(capabilities["effective_flags"].values())
    for name, state in capabilities["capabilities"].items():
        assert state["requested"] is False, name
        assert state["effective"] is False, name
        assert state["reasons"] == [f"PM {name} is disabled by host configuration"]


def test_exchange_optional_dependencies_are_required_even_in_development(
    tmp_path, monkeypatch,
) -> None:
    actual_version = application_module.metadata.version

    def version(name: str) -> str:
        if name == "openpyxl":
            raise application_module.metadata.PackageNotFoundError(name)
        return actual_version(name)

    monkeypatch.setattr(application_module.metadata, "version", version)
    app = _application(tmp_path, development_verification=True)
    capabilities = app.capabilities()
    assert capabilities["effective_flags"]["authoring"] is True
    assert capabilities["effective_flags"]["exchange"] is False
    exchange = capabilities["capabilities"]["exchange"]
    assert exchange["requested"] is True
    assert exchange["dependency_reasons"] == [
        "Install the pm-exchange extra; openpyxl 3.1.5 is missing"
    ]
    with pytest.raises(PmError) as exc:
        app._require("exchange")
    assert exc.value.code == "capability_unavailable"


def test_exact_package_bound_receipts_drive_gates_and_g5_is_derived(
    tmp_path, monkeypatch,
) -> None:
    binding = {"package_version": "1", "package_digest": "sha256:package", "verified_file_count": "10"}
    monkeypatch.setattr(application_module, "_installed_package_binding", lambda: (binding, None))
    digest = binding["package_digest"]
    ids = ["G0", "G1", "G2", "G3", "G4", "integration"]
    receipt = {
        "version": 1,
        "binding": binding,
        "gates": {gate: _row(digest, gate) for gate in ("G0", "G1", "G2", "G3", "G4")},
        "integration": _row(digest, "integration"),
        "receipts": {f"receipt:{item}": _receipt(tmp_path, f"receipt-{item}", digest) for item in ids},
    }
    path = tmp_path / ".symphony" / "pm" / "release-evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    capabilities = _application(tmp_path).capabilities()
    assert all(capabilities["gates"][gate]["passed"] for gate in ("G0", "G1", "G2", "G3", "G4", "G5"))
    assert capabilities["gates"]["G5"]["derived"] is True
    assert capabilities["highest_passed_gate"] == "G5"
    assert capabilities["release_evidence"]["binding"] == binding
    receipt["gates"]["G0"]["evidence_ids"] = ["receipt:forged"]
    path.write_text(json.dumps(receipt), encoding="utf-8")
    forged = _application(tmp_path).capabilities()
    assert forged["gates"]["G0"]["status"] == "not_evaluated"
    assert not forged["gates"]["G5"]["passed"]


def test_stale_source_binding_and_failed_gate_fail_closed(tmp_path, monkeypatch) -> None:
    binding = {"package_version": "1", "package_digest": "sha256:package", "verified_file_count": "10"}
    monkeypatch.setattr(application_module, "_installed_package_binding", lambda: (binding, None))
    path = tmp_path / ".symphony" / "pm" / "release-evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    stale = {"version": 1, "binding": {**binding, "package_digest": "sha256:stale"}, "gates": {}}
    path.write_text(json.dumps(stale), encoding="utf-8")
    capabilities = _application(tmp_path).capabilities()
    assert capabilities["release_evidence"]["status"] == "not_evaluated"
    assert not capabilities["gates"]["G0"]["passed"]

    digest = binding["package_digest"]
    ids = ["G0", "G1", "G2", "G3", "G4", "integration"]
    receipt = {
        "version": 1, "binding": binding,
        "gates": {
            gate: _row(digest, gate, "failed" if gate == "G2" else "passed")
            for gate in ("G0", "G1", "G2", "G3", "G4")
        },
        "integration": _row(digest, "integration"),
        "receipts": {
            f"receipt:{item}": _receipt(
                tmp_path, f"receipt-{item}", digest,
                passed=item != "G2",
            )
            for item in ids
        },
    }
    path.write_text(json.dumps(receipt), encoding="utf-8")
    capabilities = _application(tmp_path).capabilities()
    assert capabilities["gates"]["G2"]["status"] == "failed"
    assert capabilities["gates"]["G5"]["status"] == "failed"
    assert not capabilities["gates"]["G5"]["passed"]


def test_capability_gates_block_only_their_dependent_actions(
    tmp_path, monkeypatch,
) -> None:
    binding = {"package_version": "1", "package_digest": "sha256:package", "verified_file_count": "10"}
    monkeypatch.setattr(application_module, "_installed_package_binding", lambda: (binding, None))
    digest = binding["package_digest"]
    ids = ["G0", "G1", "G2", "G3", "G4", "integration"]
    receipt = {
        "version": 1, "binding": binding,
        "gates": {
            gate: _row(digest, gate, "failed" if gate == "G2" else "passed")
            for gate in ("G0", "G1", "G2", "G3", "G4")
        },
        "integration": _row(digest, "integration", "failed"),
        "receipts": {
            f"receipt:{item}": _receipt(
                tmp_path, f"receipt-{item}", digest, passed=item not in {"G2", "integration"},
            )
            for item in ids
        },
    }
    path = tmp_path / ".symphony" / "pm" / "release-evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    app = _application(tmp_path)
    app._require("authoring")
    app._require("assistance")
    app._require("handoff")
    with pytest.raises(PmError) as exc:
        app._require("exchange")
    assert exc.value.code == "capability_not_ready"
    assert exc.value.details["missing_gates"] == ["G2"]
    capabilities = app.capabilities()
    assert capabilities["gates"]["G5"]["passed"] is False
    assert capabilities["effective_flags"] == {
        "authoring": True, "exchange": False,
        "assistance": True, "handoff": True,
    }
    assert capabilities["capabilities"]["exchange"]["missing_gates"] == ["G2"]
