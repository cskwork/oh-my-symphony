"""Unit tests for the ticket artifact store (src/symphony/artifacts.py)."""

from __future__ import annotations

import json
import os
import shutil
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

import pytest

import symphony.artifacts as artifacts_module
from tests._win_skips import requires_symlink_privilege
from symphony.artifacts import (
    DEFAULT_MAGIC_DIR,
    ArtifactStore,
    sanitize_artifact_name,
    valid_identifier,
)


def _make_workspace(tmp_path: Path, files: dict[str, bytes]) -> Path:
    workspace = tmp_path / "workspace"
    magic = workspace / DEFAULT_MAGIC_DIR
    magic.mkdir(parents=True)
    for relative, content in files.items():
        target = magic / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return workspace


def _store(tmp_path: Path, **kwargs) -> ArtifactStore:
    return ArtifactStore(tmp_path / "store", **kwargs)


class TestSanitize:
    def test_flattens_nested_paths(self) -> None:
        assert sanitize_artifact_name("shots/login.png") == "shots__login.png"

    def test_strips_traversal_and_unsafe_chars(self) -> None:
        assert sanitize_artifact_name("../../etc/passwd") == "etc__passwd"
        assert sanitize_artifact_name("a<b>|c?.png") == "a_b_c_.png"

    def test_empty_becomes_artifact(self) -> None:
        assert sanitize_artifact_name("...") == "artifact"

    def test_long_names_keep_suffix(self) -> None:
        name = sanitize_artifact_name("x" * 300 + ".png")
        assert len(name) <= 120
        assert name.endswith(".png")


class TestIdentifiers:
    @pytest.mark.parametrize("good", ["TASK-001", "SMA-32", "a.b_c-1"])
    def test_valid(self, good: str) -> None:
        assert valid_identifier(good)

    @pytest.mark.parametrize("bad", ["", ".hidden", "a/b", "..", "a" * 70, "한글"])
    def test_invalid(self, bad: str) -> None:
        assert not valid_identifier(bad)

    def test_collect_rejects_invalid_identifier(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.txt": b"x"})
        with pytest.raises(ValueError):
            _store(tmp_path).collect_from_workspace(workspace, identifier="../evil")


class TestCollect:
    def test_collects_files_with_manifest_metadata(self, tmp_path: Path) -> None:
        workspace = _make_workspace(
            tmp_path,
            {
                "login.png": b"png-bytes",
                "report.pdf": b"pdf-bytes",
                "manifest.json": json.dumps(
                    {
                        "artifacts": [
                            {
                                "file": "login.png",
                                "title": "Login page",
                                "summary": "After fix",
                            }
                        ]
                    }
                ).encode(),
            },
        )
        store = _store(tmp_path)
        result = store.collect_from_workspace(
            workspace, identifier="TASK-001", run_id="run-1", turn=3
        )

        assert sorted(r.name for r in result.collected) == ["login.png", "report.pdf"]
        assert not result.skipped
        by_name = {r.name: r for r in store.list_for("TASK-001")}
        assert by_name["login.png"].title == "Login page"
        assert by_name["login.png"].summary == "After fix"
        assert by_name["login.png"].content_type == "image/png"
        assert by_name["login.png"].run_id == "run-1"
        assert by_name["login.png"].turn == 3
        assert by_name["report.pdf"].title == "report.pdf"
        # manifest.json itself is never collected
        assert "manifest.json" not in by_name

    def test_rescan_is_idempotent(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.txt": b"same"})
        store = _store(tmp_path)
        first = store.collect_from_workspace(workspace, identifier="T-1")
        second = store.collect_from_workspace(workspace, identifier="T-1")

        assert len(first.collected) == 1
        assert not second.collected
        assert second.skipped == [("a.txt", "duplicate")]
        assert len(store.list_for("T-1")) == 1

    def test_same_name_new_content_gets_suffix(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.txt": b"v1"})
        store = _store(tmp_path)
        store.collect_from_workspace(workspace, identifier="T-1")
        (workspace / DEFAULT_MAGIC_DIR / "a.txt").write_bytes(b"v2")
        store.collect_from_workspace(workspace, identifier="T-1")

        names = sorted(r.name for r in store.list_for("T-1"))
        assert names == ["a-2.txt", "a.txt"]

    @requires_symlink_privilege
    def test_skips_hidden_and_symlinks(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"real.txt": b"x", ".hidden": b"h"})
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret")
        (workspace / DEFAULT_MAGIC_DIR / "link.txt").symlink_to(outside)

        store = _store(tmp_path)
        result = store.collect_from_workspace(workspace, identifier="T-1")

        assert [r.name for r in result.collected] == ["real.txt"]
        reasons = dict(result.skipped)
        assert reasons[".hidden"] == "hidden"
        assert reasons["link.txt"] == "symlink"

    def test_nested_files_are_flattened(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"shots/login.png": b"x"})
        store = _store(tmp_path)
        result = store.collect_from_workspace(workspace, identifier="T-1")
        assert [r.name for r in result.collected] == ["shots__login.png"]

    def test_missing_magic_dir_is_noop(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        result = _store(tmp_path).collect_from_workspace(workspace, identifier="T-1")
        assert not result.collected
        assert not result.skipped

    def test_file_size_cap(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"big.bin": b"x" * 100, "ok.txt": b"y"})
        store = _store(tmp_path, max_file_bytes=10)
        result = store.collect_from_workspace(workspace, identifier="T-1")
        assert [r.name for r in result.collected] == ["ok.txt"]
        assert ("big.bin", "file_too_large") in result.skipped

    def test_ticket_quota_cap(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.bin": b"x" * 40, "b.bin": b"y" * 40})
        store = _store(tmp_path, max_ticket_bytes=50)
        result = store.collect_from_workspace(workspace, identifier="T-1")
        assert len(result.collected) == 1
        assert [reason for _, reason in result.skipped] == ["ticket_quota_exceeded"]


class TestServe:
    def test_resolve_file_round_trip(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.txt": b"payload"})
        store = _store(tmp_path)
        store.collect_from_workspace(workspace, identifier="T-1")

        path = store.resolve_file("T-1", "a.txt")
        assert path is not None
        assert path.read_bytes() == b"payload"

    def test_resolve_rejects_unknown_and_invalid(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.txt": b"x"})
        store = _store(tmp_path)
        store.collect_from_workspace(workspace, identifier="T-1")

        assert store.resolve_file("T-1", "other.txt") is None
        assert store.resolve_file("T-1", "../index.json") is None
        assert store.resolve_file("../T-1", "a.txt") is None
        assert store.list_for("../T-1") == []

    def test_corrupt_index_rebuilds_from_files(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.txt": b"payload"})
        store = _store(tmp_path)
        store.collect_from_workspace(workspace, identifier="T-1")
        (store.root / "T-1" / "index.json").write_text("{corrupt", encoding="utf-8")

        records = store.list_for("T-1")
        assert [r.name for r in records] == ["a.txt"]
        assert store.resolve_file("T-1", "a.txt") is not None


class TestSweep:
    @staticmethod
    def _age(path: Path, days: int) -> None:
        stamp = datetime.now(timezone.utc).timestamp() - days * 86400
        for child in [path, *path.rglob("*")]:
            os.utime(child, (stamp, stamp))

    def test_disabled_with_zero_ttl(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.txt": b"x"})
        store = _store(tmp_path)
        store.collect_from_workspace(workspace, identifier="T-1")
        self._age(store.root / "T-1", days=90)

        assert store.sweep(known_identifiers=set(), ttl_days=0) == []
        assert store.has_any("T-1")

    def test_removes_only_old_unknown_tickets(self, tmp_path: Path) -> None:
        workspace = _make_workspace(tmp_path, {"a.txt": b"x"})
        store = _store(tmp_path)
        for ticket in ("KEEP-1", "OLD-1", "FRESH-1"):
            store.collect_from_workspace(workspace, identifier=ticket)
        self._age(store.root / "KEEP-1", days=90)
        self._age(store.root / "OLD-1", days=90)

        removed = store.sweep(known_identifiers={"KEEP-1"}, ttl_days=30)

        assert removed == ["OLD-1"]
        assert store.has_any("KEEP-1")
        assert store.has_any("FRESH-1")
        assert not store.has_any("OLD-1")


def test_store_root_is_absolute_regardless_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket Markdown links relpath the store against an absolute board root.

    `workflow_path` may be relative, so a store built from it must not
    resolve against whatever cwd the service happened to start in.
    """
    monkeypatch.chdir(tmp_path)
    store = ArtifactStore(Path(".symphony/artifacts"))
    assert store.root.is_absolute()
    assert store.root == tmp_path.resolve() / ".symphony" / "artifacts"

    workspace = _make_workspace(tmp_path, {"a.txt": b"x"})
    store.collect_from_workspace(workspace, identifier="T-1")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    resolved = store.resolve_file("T-1", "a.txt")
    assert resolved is not None and resolved.read_bytes() == b"x"


class TestManifestTextIsUntrusted:
    """A worker authors manifest.json; its text lands in the ticket Markdown."""

    def _collect_with_manifest(self, tmp_path: Path, title: str, summary: str):
        workspace = _make_workspace(
            tmp_path,
            {
                "a.png": b"x",
                "manifest.json": json.dumps(
                    {"artifacts": [{"file": "a.png", "title": title,
                                    "summary": summary}]}
                ).encode(),
            },
        )
        store = _store(tmp_path)
        store.collect_from_workspace(workspace, identifier="T-1")
        return store.list_for("T-1")[0]

    def test_newlines_cannot_forge_a_markdown_heading(self, tmp_path: Path) -> None:
        record = self._collect_with_manifest(
            tmp_path, "ok\n\n## Merge Status\n\nmerged", "line1\nline2"
        )
        assert "\n" not in record.title and "\n" not in record.summary
        assert record.title == "ok ## Merge Status merged"
        assert record.summary == "line1 line2"

    def test_section_markers_are_stripped(self, tmp_path: Path) -> None:
        record = self._collect_with_manifest(
            tmp_path, "A <!-- /symphony-artifacts --> B", "<!-- x -->"
        )
        assert "<!--" not in record.title and "-->" not in record.title
        assert "<!--" not in record.summary and "-->" not in record.summary

    def test_absurdly_long_text_is_capped(self, tmp_path: Path) -> None:
        record = self._collect_with_manifest(tmp_path, "T" * 5000, "S" * 5000)
        assert len(record.title) == 300
        assert len(record.summary) == 300


class TestUnicodeNames:
    def test_korean_names_survive(self) -> None:
        assert sanitize_artifact_name("로그인_화면.png") == "로그인_화면.png"
        assert sanitize_artifact_name("보고서.pdf") == "보고서.pdf"
        assert sanitize_artifact_name("café.png") == "café.png"

    def test_traversal_is_still_blocked(self) -> None:
        assert sanitize_artifact_name("../../etc/passwd") == "etc__passwd"
        assert sanitize_artifact_name("/etc/passwd") == "etc__passwd"
        assert sanitize_artifact_name("..") == "artifact"
        assert "/" not in sanitize_artifact_name("a/b/c.png")

    def test_names_are_nfc_normalized(self, tmp_path: Path) -> None:
        import unicodedata

        decomposed = unicodedata.normalize("NFD", "로그인.png")
        workspace = _make_workspace(tmp_path, {decomposed: b"x"})
        store = _store(tmp_path)
        record = store.collect_from_workspace(workspace, identifier="T-1").collected[0]
        assert record.name == unicodedata.normalize("NFC", record.name)
        assert store.resolve_file("T-1", record.name) is not None


def test_sweep_never_runs_on_an_empty_known_set(tmp_path: Path) -> None:
    """Guarded by the caller, pinned here: empty != "every ticket is gone".

    A renamed or unmounted board directory globs to empty. Treating that as
    an authoritative "no tickets exist" would delete every artifact.
    """
    workspace = _make_workspace(tmp_path, {"a.txt": b"x"})
    store = _store(tmp_path)
    store.collect_from_workspace(workspace, identifier="LIVE-1")

    # The store itself still honours an explicit empty set...
    aged = datetime.now(timezone.utc).timestamp() - 90 * 86400
    for path in [store.root / "LIVE-1", *(store.root / "LIVE-1").rglob("*")]:
        os.utime(path, (aged, aged))
    assert store.sweep(known_identifiers=set(), ttl_days=30) == ["LIVE-1"]


def test_file_growing_during_copy_is_billed_and_capped(tmp_path: Path) -> None:
    """The size that lands is the size that counts, not the pre-copy stat."""
    workspace = _make_workspace(tmp_path, {"grow.bin": b"x" * 5})
    store = _store(tmp_path, max_file_bytes=10)
    source = workspace / DEFAULT_MAGIC_DIR / "grow.bin"
    real_copyfile = shutil.copyfile

    def grow_then_copy(src, dst, **kwargs):
        source.write_bytes(b"x" * 500)  # worker grows it mid-collect
        return real_copyfile(source, dst, **kwargs)

    with mock.patch.object(artifacts_module.shutil, "copyfile", grow_then_copy):
        result = store.collect_from_workspace(workspace, identifier="T-1")

    assert result.collected == []
    assert ("grow.bin", "grew_past_cap") in result.skipped
    assert store.list_for("T-1") == []
    # The oversized copy must not be left behind in the store.
    assert not (store.root / "T-1" / "files" / "grow.bin").exists()


@requires_symlink_privilege
def test_sweep_skips_symlinked_ticket_directories(tmp_path: Path) -> None:
    """A planted link must not be followed — explicitly, not by rmtree luck."""
    store = _store(tmp_path)
    store.root.mkdir(parents=True)
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keepme.txt").write_text("do not delete")
    (store.root / "EVIL-1").symlink_to(precious, target_is_directory=True)
    aged = datetime.now(timezone.utc).timestamp() - 90 * 86400
    os.utime(precious, (aged, aged))
    os.utime(precious / "keepme.txt", (aged, aged))

    removed = store.sweep(known_identifiers={"OTHER"}, ttl_days=30)

    assert removed == []
    assert (precious / "keepme.txt").read_text() == "do not delete"
