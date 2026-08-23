"""Ticket artifact store — collect worker file deliverables from workspaces.

Workers drop deliverable files (screenshots, reports, PDFs) into a magic
directory inside their workspace (default ``.symphony-artifacts/``). After
each completed worker turn the orchestrator collects new files into the
host-owned store at ``<workflow>/.symphony/artifacts/<TICKET-ID>/`` so
deliverables stay visible on the web board while the run is live and after
the workspace is removed.

Unlike the git-tracked document artefacts under ``docs/<TICKET-ID>/`` that
the Done contract already checks, this store holds files that do not belong
in git history. It is host-owned operational data, like ``stats.jsonl``.

Store layout (one directory per ticket)::

    <root>/<TICKET-ID>/index.json      # metadata, atomic replace
    <root>/<TICKET-ID>/files/<name>    # sanitized file names

An optional ``manifest.json`` in the workspace magic directory carries
titles and summaries::

    {"artifacts": [{"file": "shot.png", "title": "Login page", "summary": "…"}]}

Files absent from the manifest are still collected with the file name as
title. Collection is idempotent per content hash, so scanning after every
turn never duplicates entries.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logging import get_logger

log = get_logger()

DEFAULT_MAGIC_DIR = ".symphony-artifacts"
MANIFEST_NAME = "manifest.json"
INDEX_NAME = "index.json"
FILES_DIR = "files"

# Ticket identifiers as the file tracker mints them (TASK-001, SMA-32…).
# No leading dot, no path separators — safe as a single path component.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Word characters (Unicode-aware, so `로그인.png` survives), dot, dash, space.
# Traversal safety comes from the path-part filter in `sanitize_artifact_name`,
# not from this class — separators are simply not word characters.
_UNSAFE_NAME_CHARS = re.compile(r"[^\w.\- ]+", re.UNICODE)
_MAX_NAME_LEN = 120

# Worker-authored manifest text is interpolated into the ticket Markdown,
# which carries HTML-comment section markers. A title that contained one
# would split the orchestrator-owned block; newlines would break out of the
# list and could forge a `## Heading` that stage contracts read as evidence.
_MANIFEST_TEXT_STRIP = re.compile(r"<!--|-->")
_WHITESPACE_RUN = re.compile(r"\s+")
_MAX_MANIFEST_TEXT_LEN = 300


def _clean_manifest_text(raw: Any) -> str:
    """Flatten worker-supplied title/summary to one safe single-line string."""
    if not isinstance(raw, str):
        return ""
    collapsed = _WHITESPACE_RUN.sub(" ", _MANIFEST_TEXT_STRIP.sub("", raw)).strip()
    return collapsed[:_MAX_MANIFEST_TEXT_LEN]


def _utcnow_str(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_identifier(identifier: str) -> bool:
    return bool(_SAFE_IDENTIFIER.match(identifier or ""))


def sanitize_artifact_name(raw: str) -> str:
    """Flatten `raw` (possibly a relative path) to one safe file name.

    Path separators become ``__`` so nested workspace layouts stay
    distinguishable; anything outside a conservative charset becomes ``_``.
    """
    # `pathlib` renders the root separator of a Windows path as "\\" in
    # `parts` even when the input used "/", so filter both separators:
    # a surviving root part would be rewritten to a stray "_" instead of
    # being dropped (POSIX parts never contain a bare "\\").
    parts = [
        p for p in Path(raw).parts if p not in ("", ".", "..", "/", "\\")
    ]
    flat = "__".join(parts) if parts else ""
    # NFC so the name written to disk, stored in the index, and asked for over
    # HTTP compare equal on byte-exact filesystems (macOS hands back NFD).
    flat = unicodedata.normalize("NFC", flat)
    flat = _UNSAFE_NAME_CHARS.sub("_", flat).strip().lstrip(". -")
    if not flat:
        flat = "artifact"
    if len(flat) > _MAX_NAME_LEN:
        stem, dot, suffix = flat.rpartition(".")
        if dot and 0 < len(suffix) <= 16:
            keep = _MAX_NAME_LEN - len(suffix) - 1
            flat = f"{stem[:keep]}.{suffix}"
        else:
            flat = flat[:_MAX_NAME_LEN]
    return flat


def format_bytes(size: int) -> str:
    """Short human-readable size for board notes (`12 KB`, `3.4 MB`)."""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            rendered = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{rendered} {unit}"
    return f"{size} B"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactRecord:
    """One collected deliverable inside a ticket's artifact directory."""

    name: str
    title: str
    summary: str
    content_type: str
    byte_size: int
    sha256: str
    collected_at: str
    run_id: str | None = None
    turn: int | None = None


@dataclass
class CollectResult:
    collected: list[ArtifactRecord] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)


def _parse_manifest(magic_dir: Path) -> dict[str, dict[str, str]]:
    """Best-effort read of manifest.json → {file name: {title, summary}}."""
    manifest_path = magic_dir / MANIFEST_NAME
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    entries = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {}
    result: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_name = entry.get("file")
        if not isinstance(file_name, str) or not file_name.strip():
            continue
        result[file_name.strip()] = {
            "title": _clean_manifest_text(entry.get("title")),
            "summary": _clean_manifest_text(entry.get("summary")),
        }
    return result


class ArtifactStore:
    """Host-owned, per-ticket artifact directories with an index.json each.

    All methods are synchronous and internally locked; call them via
    ``asyncio.to_thread`` from async code.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = 25 * 1024 * 1024,
        max_ticket_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        # Absolute from here on: `workflow_path` may be relative while
        # `tracker.board_root` is always resolved, and the two are mixed in
        # an `os.path.relpath` to build the ticket's Markdown links. A
        # relative root would silently resolve against the process cwd.
        self._root = Path(os.path.abspath(root))
        self._max_file_bytes = max_file_bytes
        self._max_ticket_bytes = max_ticket_bytes
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    # -- read side -----------------------------------------------------

    def list_for(self, identifier: str) -> list[ArtifactRecord]:
        if not valid_identifier(identifier):
            return []
        with self._lock:
            return self._load_index(identifier)

    def has_any(self, identifier: str) -> bool:
        return bool(self.list_for(identifier))

    def resolve_file(self, identifier: str, name: str) -> Path | None:
        """Return the on-disk path for a listed artifact, or None.

        Only names present in the index resolve; the result is re-checked
        for containment under the store root so a corrupted index can never
        direct the caller outside the store.
        """
        if not valid_identifier(identifier):
            return None
        records = self.list_for(identifier)
        if not any(record.name == name for record in records):
            return None
        candidate = self._files_dir(identifier) / name
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root.resolve())
        except (OSError, ValueError):
            return None
        if not resolved.is_file():
            return None
        return resolved

    def record_for(self, identifier: str, name: str) -> ArtifactRecord | None:
        for record in self.list_for(identifier):
            if record.name == name:
                return record
        return None

    # -- write side ----------------------------------------------------

    def collect_from_workspace(
        self,
        workspace_path: Path,
        *,
        identifier: str,
        magic_dir_name: str = DEFAULT_MAGIC_DIR,
        run_id: str | None = None,
        turn: int | None = None,
        now: datetime | None = None,
    ) -> CollectResult:
        """Copy new files from the workspace magic directory into the store.

        Idempotent: a file whose sha256 already exists for the ticket is
        skipped, so per-turn rescans of an unchanged directory are no-ops.
        Files stay in the workspace (collection copies, never moves).
        """
        result = CollectResult()
        if not valid_identifier(identifier):
            raise ValueError(f"invalid ticket identifier: {identifier!r}")
        magic_dir = workspace_path / magic_dir_name
        if not magic_dir.is_dir() or magic_dir.is_symlink():
            return result
        manifest = _parse_manifest(magic_dir)

        sources: list[tuple[str, Path]] = []
        for path in sorted(magic_dir.rglob("*")):
            relative = path.relative_to(magic_dir).as_posix()
            if path.is_symlink():
                result.skipped.append((relative, "symlink"))
                continue
            if not path.is_file():
                continue
            if relative == MANIFEST_NAME:
                continue
            if any(part.startswith(".") for part in Path(relative).parts):
                result.skipped.append((relative, "hidden"))
                continue
            sources.append((relative, path))

        if not sources:
            return result

        with self._lock:
            records = self._load_index(identifier)
            known_hashes = {record.sha256 for record in records}
            taken_names = {record.name for record in records}
            total_bytes = sum(record.byte_size for record in records)

            for relative, path in sources:
                try:
                    byte_size = path.stat().st_size
                except OSError:
                    result.skipped.append((relative, "unreadable"))
                    continue
                if byte_size > self._max_file_bytes:
                    result.skipped.append((relative, "file_too_large"))
                    continue
                if total_bytes + byte_size > self._max_ticket_bytes:
                    result.skipped.append((relative, "ticket_quota_exceeded"))
                    continue
                try:
                    digest = _sha256_file(path)
                except OSError:
                    result.skipped.append((relative, "unreadable"))
                    continue
                if digest in known_hashes:
                    result.skipped.append((relative, "duplicate"))
                    continue

                name = self._unique_name(sanitize_artifact_name(relative), taken_names)
                meta = manifest.get(relative, {})
                record = ArtifactRecord(
                    name=name,
                    title=meta.get("title") or name,
                    summary=meta.get("summary") or "",
                    content_type=mimetypes.guess_type(name)[0]
                    or "application/octet-stream",
                    byte_size=byte_size,
                    sha256=digest,
                    collected_at=_utcnow_str(now),
                    run_id=run_id,
                    turn=turn,
                )
                try:
                    # Re-measure what actually landed: the worker may have
                    # grown the file between the stat above and the copy,
                    # which would put both caps and the running total below
                    # reality. `copied` is the number we record and bill.
                    byte_size = self._copy_into_store(identifier, path, name)
                    if byte_size > self._max_file_bytes or (
                        total_bytes + byte_size > self._max_ticket_bytes
                    ):
                        self._discard_from_store(identifier, name)
                        result.skipped.append((relative, "grew_past_cap"))
                        continue
                except OSError as exc:
                    log.warning(
                        "artifact_copy_failed",
                        identifier=identifier,
                        name=relative,
                        error=str(exc),
                    )
                    result.skipped.append((relative, "copy_failed"))
                    continue
                records.append(record)
                known_hashes.add(digest)
                taken_names.add(name)
                total_bytes += byte_size
                result.collected.append(record)
                log.info(
                    "artifact_collected",
                    identifier=identifier,
                    name=name,
                    bytes=byte_size,
                    turn=turn,
                )

            if result.collected:
                self._write_index(identifier, records)
        return result

    def sweep(
        self,
        *,
        known_identifiers: set[str],
        ttl_days: int,
        now: datetime | None = None,
    ) -> list[str]:
        """Remove artifact directories for tickets no longer on the board.

        A directory is removed only when its ticket is not in
        `known_identifiers` (i.e. archived or deleted) AND nothing in it was
        touched for `ttl_days`. `ttl_days <= 0` disables the sweep.
        """
        if ttl_days <= 0:
            return []
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=ttl_days)
        cutoff_ts = cutoff.timestamp()
        removed: list[str] = []
        with self._lock:
            try:
                children = sorted(self._root.iterdir())
            except (FileNotFoundError, OSError):
                return []
            for child in children:
                # `is_dir()` follows symlinks, so a worker-planted link into
                # a home directory would look sweepable. `rmtree` refuses a
                # symlink itself, but relying on that makes the safety
                # accidental; skip links explicitly instead.
                if child.is_symlink() or not child.is_dir():
                    continue
                if child.name in known_identifiers:
                    continue
                if not valid_identifier(child.name):
                    continue
                if self._newest_mtime(child) >= cutoff_ts:
                    continue
                try:
                    shutil.rmtree(child)
                    removed.append(child.name)
                except OSError as exc:
                    log.warning(
                        "artifact_sweep_failed",
                        identifier=child.name,
                        error=str(exc),
                    )
        if removed:
            log.info("artifact_sweep_removed", identifiers=removed)
        return removed

    # -- internals -------------------------------------------------------

    def _ticket_dir(self, identifier: str) -> Path:
        return self._root / identifier

    def _files_dir(self, identifier: str) -> Path:
        return self._ticket_dir(identifier) / FILES_DIR

    def _index_path(self, identifier: str) -> Path:
        return self._ticket_dir(identifier) / INDEX_NAME

    @staticmethod
    def _unique_name(name: str, taken: set[str]) -> str:
        if name not in taken:
            return name
        stem, dot, suffix = name.rpartition(".")
        base, ext = (stem, f".{suffix}") if dot else (name, "")
        for counter in range(2, 1000):
            candidate = f"{base}-{counter}{ext}"
            if candidate not in taken:
                return candidate
        raise OSError(f"could not find a free artifact name for {name!r}")

    def _copy_into_store(self, identifier: str, source: Path, name: str) -> int:
        """Copy `source` into the ticket store; return the bytes written."""
        files_dir = self._files_dir(identifier)
        files_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-artifact-", dir=files_dir)
        os.close(fd)
        try:
            shutil.copyfile(source, tmp)
            written = os.stat(tmp).st_size
            os.replace(tmp, files_dir / name)
            return written
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _discard_from_store(self, identifier: str, name: str) -> None:
        try:
            (self._files_dir(identifier) / name).unlink()
        except OSError:
            pass

    def _load_index(self, identifier: str) -> list[ArtifactRecord]:
        index_path = self._index_path(identifier)
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._rebuild_index_from_files(identifier, log_missing=False)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return self._rebuild_index_from_files(identifier, log_missing=True)
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return self._rebuild_index_from_files(identifier, log_missing=True)
        records: list[ArtifactRecord] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                records.append(
                    ArtifactRecord(
                        name=str(entry["name"]),
                        title=str(entry.get("title") or entry["name"]),
                        summary=str(entry.get("summary") or ""),
                        content_type=str(
                            entry.get("content_type") or "application/octet-stream"
                        ),
                        byte_size=int(entry.get("byte_size") or 0),
                        sha256=str(entry.get("sha256") or ""),
                        collected_at=str(entry.get("collected_at") or ""),
                        run_id=entry.get("run_id"),
                        turn=entry.get("turn"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return records

    def _rebuild_index_from_files(
        self, identifier: str, *, log_missing: bool
    ) -> list[ArtifactRecord]:
        """Recover listings from files/ when index.json is absent or corrupt.

        Serving must not go dark because one JSON write was interrupted;
        titles and provenance are lost but the files stay reachable.
        """
        files_dir = self._files_dir(identifier)
        if not files_dir.is_dir():
            return []
        if log_missing:
            log.warning("artifact_index_rebuilt", identifier=identifier)
        records: list[ArtifactRecord] = []
        for path in sorted(files_dir.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                stat = path.stat()
                digest = _sha256_file(path)
            except OSError:
                continue
            records.append(
                ArtifactRecord(
                    name=path.name,
                    title=path.name,
                    summary="",
                    content_type=mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                    byte_size=stat.st_size,
                    sha256=digest,
                    collected_at=_utcnow_str(
                        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                    ),
                )
            )
        if records and log_missing:
            self._write_index(identifier, records)
        return records

    def _write_index(self, identifier: str, records: list[ArtifactRecord]) -> None:
        index_path = self._index_path(identifier)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": [asdict(record) for record in records]}
        fd, tmp = tempfile.mkstemp(prefix=".tmp-artifact-index-", dir=index_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, index_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _newest_mtime(directory: Path) -> float:
        newest = 0.0
        try:
            newest = directory.stat().st_mtime
        except OSError:
            pass
        for path in directory.rglob("*"):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
        return newest
