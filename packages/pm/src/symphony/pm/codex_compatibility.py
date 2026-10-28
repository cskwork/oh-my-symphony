"""Installed-binary admission for the restricted PM app-server profile."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import PmError

REQUIRED_CONTROLS = "restricted app-server controls"
_VERSION = re.compile(r"codex-cli (\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?)")
_CACHE: dict[str, tuple[tuple[int, ...], str]] = {}


@dataclass(frozen=True)
class CodexAdmission:
    executable: str | None
    found: str | None
    reason: str | None


def _identity(executable: str) -> tuple[int, ...]:
    stat = Path(executable).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def isolated_environment(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(home)
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        env.pop(key, None)
    return env


async def _stop_probe(owner: Any) -> None:
    # A live asyncio child handle proves ownership even when PATH lacks ps.
    process = owner.process
    if process is not None and process.returncode is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 1)
        except asyncio.TimeoutError:
            pass
    await owner.stop()


async def _run(command: list[str], home: Path, deadline: float) -> str:
    from symphony._shell import process_identity
    from .planning import PmPlanningClient, _remaining

    owner = PmPlanningClient()
    process = await asyncio.create_subprocess_exec(
        *command, cwd=home, env=isolated_environment(home),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix", limit=2 * 1024 * 1024,
    )
    owner.process = process
    owner._process_identity = process_identity(process.pid)
    owner._owned_process_identities = (
        frozenset({owner._process_identity}) if owner._process_identity else frozenset()
    )

    async def bounded_read(stream: asyncio.StreamReader | None) -> bytes:
        assert stream is not None
        chunks = []
        size = 0
        while chunk := await stream.read(65536):
            size += len(chunk)
            if size > 2 * 1024 * 1024:
                raise ValueError("probe output exceeds limit")
            chunks.append(chunk)
        return b"".join(chunks)

    readers = [asyncio.create_task(bounded_read(process.stdout)),
               asyncio.create_task(bounded_read(process.stderr))]
    try:
        stdout, _, _ = await asyncio.wait_for(
            asyncio.gather(readers[0], readers[1], process.wait()), _remaining(deadline)
        )
        if process.returncode != 0:
            raise ValueError("command failed")
        return stdout.decode(errors="replace").strip()
    finally:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await _stop_probe(owner)


def _accepts(schema: dict[str, Any], value: Any, root: dict[str, Any]) -> bool:
    """Check the literal control values against the binary's JSON-schema branches."""
    # Unknown schema combinators/conditions cannot establish the control contract.
    if set(schema) & {"not", "if", "then", "else", "dependentSchemas", "unevaluatedProperties"}:
        return False
    if "allOf" in schema and not all(_accepts(branch, value, root) for branch in schema["allOf"]):
        return False
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/definitions/"):
            return False
        target = root.get("definitions", {}).get(ref.removeprefix("#/definitions/"))
        return isinstance(target, dict) and _accepts(target, value, root)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = sum(_accepts(branch, value, root) for branch in schema[keyword])
            return matches == 1 if keyword == "oneOf" else matches > 0
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "const" in schema and value != schema["const"]:
        return False
    value_type = ("boolean" if isinstance(value, bool) else "object" if isinstance(value, dict)
                  else "array" if isinstance(value, list) else "string" if isinstance(value, str)
                  else "null" if value is None else "number")
    types = schema.get("type")
    if types is not None and value_type not in ([types] if isinstance(types, str) else types):
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not all(key in value for key in schema.get("required", [])):
            return False
        for key, item in value.items():
            child = properties.get(key, schema.get("additionalProperties", {}))
            if child is False or (isinstance(child, dict) and not _accepts(child, item, root)):
                return False
        # Security controls must be explicit, not silently accepted as unknown fields.
        if value.get("type") == "readOnly" and properties.get("networkAccess", {}).get("type") != "boolean":
            return False
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return all(_accepts(schema["items"], item, root) for item in value)
    return True


def _check_schemas(root: Path) -> None:
    from .planning import DEVELOPER_INSTRUCTIONS, EFFORT, MODEL, OUTPUT_SCHEMA

    controls = {
        "ThreadStartParams": {"model": MODEL, "allowProviderModelFallback": False,
            "ephemeral": True, "approvalPolicy": "never", "sandbox": "read-only",
            "developerInstructions": DEVELOPER_INSTRUCTIONS, "dynamicTools": [], "environments": []},
        "TurnStartParams": {"model": MODEL, "effort": EFFORT, "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            "environments": [], "disabledPluginIds": [], "outputSchema": OUTPUT_SCHEMA,
            "additionalContext": {"planning_snapshot": {"kind": "untrusted", "value": "probe"}}},
    }
    for name, values in controls.items():
        path = root / "v2" / f"{name}.json"
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError(f"{name} schema exceeds limit")
        schema = json.loads(path.read_text(encoding="utf-8"))
        for key, value in values.items():
            field = schema.get("properties", {}).get(key)
            if (not isinstance(field, dict) or (not field and key != "outputSchema")
                    or not _accepts(field, value, schema)):
                raise ValueError(f"{name}.{key} required control is missing or incompatible")


async def admit_codex(executable: str = "codex", *, deadline: float | None = None) -> CodexAdmission:
    from .planning import PmPlanningClient

    resolved = shutil.which(executable)
    if resolved is None:
        return CodexAdmission(None, None, "Codex is missing on PATH (found: none). Install a compatible Codex CLI.")
    resolved = str(Path(resolved).resolve())
    found = None
    stage = "version"
    deadline = min(deadline or float("inf"), asyncio.get_running_loop().time() + 5)
    try:
        identity = _identity(resolved)
        with tempfile.TemporaryDirectory(prefix="symphony-pm-codex-probe-") as temporary:
            home = Path(temporary)
            output = await _run([resolved, "--version"], home, deadline)
            match = _VERSION.fullmatch(output)
            if match is None:
                raise ValueError("unrecognized successful version output")
            found = match[1]
            if _identity(resolved) != identity:
                raise ValueError("executable changed during version probe")
            # npm launchers delegate to separately replaceable native packages.
            cacheable = Path(resolved).suffix != ".js"
            if cacheable and _CACHE.get(resolved) == (identity, found):
                return CodexAdmission(resolved, found, None)
            _CACHE.pop(resolved, None)
            stage = "schema controls"
            schema_root = home / "schema"
            await _run([resolved, "app-server", "generate-json-schema", "--experimental", "--out", str(schema_root)], home, deadline)
            _check_schemas(schema_root)
            stage = "strict startup/initialize"
            client = PmPlanningClient(codex_executable=resolved)
            try:
                await client._start_runtime(home, deadline, authenticated=False)
                if client.server_request is not None:
                    raise ValueError(f"unexpected server request {client.server_request}")
            finally:
                await _stop_probe(client)
            if _identity(resolved) != identity:
                raise ValueError("executable changed during compatibility probe")
            if cacheable:
                _CACHE[resolved] = (identity, found)
            return CodexAdmission(resolved, found, None)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, asyncio.TimeoutError, PmError) as exc:
        _CACHE.pop(resolved, None)
        detail = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)
        return CodexAdmission(resolved, found,
            f"Codex {stage} compatibility failed (found: {found or 'none'}): {detail}. "
            "Install a compatible Codex CLI supporting restricted app-server controls, then retry. No fallback is used.")
