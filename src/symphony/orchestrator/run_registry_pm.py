"""PM workspace coordination and dispatch authority for the run registry."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .run_registry_base import _RunRegistryBase
from .run_registry_records import (
    DEFAULT_LEASE_TTL,
    PmWorkspaceRecord,
    PmManagedTicketRecord,
    PmDispatchPermitRecord,
    _utc,
    _iso,
    _pm_workspace,
    _pm_managed_ticket,
    _pm_receipt_matches_policy,
    _pm_dispatch_permit,
)


class _PmRegistry(_RunRegistryBase):
    """PM workspaces, handoffs, receipts, and dispatch permits."""

    # ------------------------------------------------------------------
    # PM workspace coordination and dispatch authority
    # ------------------------------------------------------------------

    def claim_pm_workspace(
        self,
        *,
        project_id: str,
        change_id: str,
        workspace_path: Path,
        base_git_sha: str,
        owner: str,
        replace_existing: bool = False,
        now: datetime | None = None,
    ) -> PmWorkspaceRecord:
        conn = self._connect()
        stamp = _iso(_utc(now))
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT * FROM pm_workspaces
                WHERE project_id = ? AND change_id = ?
                """,
                (project_id, change_id),
            ).fetchone()
            if row is not None and str(row["state"]) == "RELEASED":
                raise RuntimeError("planning change identity is retired")
            if row is not None:
                same_claim = (
                    str(row["owner"]) == owner
                    and Path(str(row["workspace_path"])) == workspace_path
                    and str(row["base_git_sha"]) == base_git_sha
                )
                if same_claim:
                    conn.execute("COMMIT")
                    record = self.get_pm_workspace(project_id, change_id)
                    assert record is not None
                    return record
                if not replace_existing:
                    raise RuntimeError("planning workspace is already claimed")
            fence = (int(row["fence"]) + 1) if row is not None else 1
            conn.execute(
                """
                INSERT INTO pm_workspaces (
                    project_id, change_id, workspace_path, base_git_sha,
                    owner, fence, state, observed_hashes_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', '{}', ?)
                ON CONFLICT(project_id, change_id) DO UPDATE SET
                    workspace_path = excluded.workspace_path,
                    base_git_sha = excluded.base_git_sha,
                    owner = excluded.owner,
                    fence = excluded.fence,
                    state = 'ACTIVE',
                    observed_hashes_json = '{}',
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    change_id,
                    str(workspace_path),
                    base_git_sha,
                    owner,
                    fence,
                    stamp,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        record = self.get_pm_workspace(project_id, change_id)
        assert record is not None
        return record

    def get_pm_workspace(
        self, project_id: str, change_id: str
    ) -> PmWorkspaceRecord | None:
        row = self._connect().execute(
            """
            SELECT * FROM pm_workspaces
            WHERE project_id = ? AND change_id = ?
            """,
            (project_id, change_id),
        ).fetchone()
        return _pm_workspace(row) if row is not None else None

    def set_pm_workspace_state(
        self,
        project_id: str,
        change_id: str,
        *,
        fence: int,
        state: str,
        observed_hashes: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> bool:
        if state not in {
            "ACTIVE",
            "EXTERNAL_EDIT",
            "REPAIR_REQUIRED",
            "RELEASED",
        }:
            raise ValueError("invalid PM workspace state")
        updated = self._connect().execute(
            """
            UPDATE pm_workspaces
            SET state = ?, observed_hashes_json = ?, updated_at = ?
            WHERE project_id = ? AND change_id = ? AND fence = ?
            """,
            (
                state,
                json.dumps(dict(observed_hashes or {}), sort_keys=True),
                _iso(_utc(now)),
                project_id,
                change_id,
                fence,
            ),
        )
        return updated.rowcount == 1

    def create_pm_operation(
        self,
        *,
        operation_id: str,
        project_id: str,
        change_id: str,
        kind: str,
        expected_revision: str | None,
        input_hash: str = "",
        owner_token: str = "",
        now: datetime | None = None,
    ) -> tuple[str, bool]:
        stamp = _iso(_utc(now))
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO pm_operations (
                    operation_id, project_id, change_id, kind,
                    expected_revision, input_hash, state, owner_token,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    operation_id,
                    project_id,
                    change_id,
                    kind,
                    expected_revision,
                    input_hash,
                    owner_token,
                    stamp,
                    stamp,
                ),
            )
            return operation_id, True
        except sqlite3.IntegrityError:
            row = conn.execute(
                """
                SELECT project_id, change_id, kind, expected_revision, input_hash
                FROM pm_operations WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None or (
                str(row["project_id"]),
                str(row["change_id"]),
                str(row["kind"]),
                row["expected_revision"],
                str(row["input_hash"]),
            ) != (
                project_id,
                change_id,
                kind,
                expected_revision,
                input_hash,
            ):
                raise ValueError("operation id reused for different input") from None
            return operation_id, False

    def finish_pm_operation(
        self,
        operation_id: str,
        *,
        state: str,
        result: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        if state not in {"completed", "failed", "cancelled", "reconcile"}:
            raise ValueError("invalid terminal operation state")
        updated = self._connect().execute(
            """
            UPDATE pm_operations
            SET state = ?, result_json = ?, error_code = ?, updated_at = ?
            WHERE operation_id = ?
              AND state IN ('pending','running','reconcile')
            """,
            (
                state,
                json.dumps(dict(result or {}), sort_keys=True),
                error_code,
                _iso(_utc(now)),
                operation_id,
            ),
        )
        return updated.rowcount == 1

    def prepare_pm_operation(
        self,
        operation_id: str,
        *,
        result: Mapping[str, Any],
        now: datetime | None = None,
    ) -> bool:
        encoded = json.dumps(dict(result), sort_keys=True)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, result_json FROM pm_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown PM operation")
            if str(row["state"]) == "pending":
                conn.execute(
                    "UPDATE pm_operations SET state = 'running', result_json = ?, updated_at = ? WHERE operation_id = ? AND state = 'pending'",
                    (encoded, _iso(_utc(now)), operation_id),
                )
                conn.execute("COMMIT")
                return True
            if str(row["state"]) in {"running", "reconcile"} and str(row["result_json"] or "") == encoded:
                conn.execute("COMMIT")
                return False
            raise ValueError("PM operation preparation conflicts with durable state")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def register_pm_managed_ticket(
        self,
        *,
        project_id: str,
        ticket_identifier: str,
        change_id: str,
        task_id: str,
        task_revision: str,
        handoff_id: str,
        state: str,
        now: datetime | None = None,
    ) -> PmManagedTicketRecord:
        stamp = _iso(_utc(now))
        self._connect().execute(
            """
            INSERT INTO pm_managed_tickets (
                project_id, ticket_identifier, change_id, task_id,
                task_revision, handoff_id, state, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, ticket_identifier) DO UPDATE SET
                state = excluded.state,
                updated_at = excluded.updated_at
            WHERE pm_managed_tickets.change_id = excluded.change_id
              AND pm_managed_tickets.task_id = excluded.task_id
              AND pm_managed_tickets.task_revision = excluded.task_revision
              AND pm_managed_tickets.handoff_id = excluded.handoff_id
            """,
            (
                project_id,
                ticket_identifier,
                change_id,
                task_id,
                task_revision,
                handoff_id,
                state,
                stamp,
            ),
        )
        record = self.get_pm_managed_ticket(project_id, ticket_identifier)
        if record is None or (
            record.change_id,
            record.task_id,
            record.task_revision,
            record.handoff_id,
        ) != (change_id, task_id, task_revision, handoff_id):
            raise ValueError("managed ticket identifier collision")
        return record

    def record_pm_approval(
        self,
        *,
        approval_id: str,
        project_id: str,
        change_id: str,
        scope_hash: str,
        revision_hash: str,
        manifest_ref: str,
        reviewer_label: str,
        approved_at: str,
        manifest_hash: str | None = None,
        snapshot_commit: str = "",
    ) -> None:
        if manifest_hash is None:
            manifest_hash = "sha256:" + __import__("hashlib").sha256(
                Path(manifest_ref).read_bytes()
            ).hexdigest()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                UPDATE pm_approvals
                SET superseded_by = ?
                WHERE project_id = ? AND change_id = ? AND superseded_by IS NULL
                """,
                (approval_id, project_id, change_id),
            )
            conn.execute(
                """
                INSERT INTO pm_approvals (
                    approval_id, project_id, change_id, scope_hash,
                    revision_hash, manifest_ref, manifest_hash, snapshot_commit,
                    reviewer_label, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    project_id,
                    change_id,
                    scope_hash,
                    revision_hash,
                    manifest_ref, manifest_hash, snapshot_commit,
                    reviewer_label,
                    approved_at,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_active_pm_approval(
        self, project_id: str, change_id: str
    ) -> dict[str, Any] | None:
        row = self._connect().execute(
            """
            SELECT * FROM pm_approvals
            WHERE project_id = ? AND change_id = ? AND superseded_by IS NULL
            ORDER BY approved_at DESC LIMIT 1
            """,
            (project_id, change_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_pm_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM pm_approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def create_pm_handoff_batch(
        self,
        *,
        handoff_id: str,
        operation_id: str,
        project_id: str,
        change_id: str,
        scope_hash: str,
        approval_id: str,
        snapshot_id: str,
        items: Sequence[Mapping[str, str]],
        now: datetime | None = None,
    ) -> bool:
        stamp = _iso(_utc(now))
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT * FROM pm_handoff_batches WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                same = (
                    str(existing["project_id"]),
                    str(existing["change_id"]),
                    str(existing["scope_hash"]),
                    str(existing["approval_id"]),
                ) == (project_id, change_id, scope_hash, approval_id)
                if not same:
                    raise ValueError("handoff operation id reused for different scope")
                existing_items = conn.execute(
                    "SELECT * FROM pm_handoff_items WHERE handoff_id = ?",
                    (str(existing["handoff_id"]),),
                ).fetchall()
                self._merge_pm_fallback(project_id, {
                    str(item["deterministic_ticket_id"]): {
                        "change_id": str(item["change_id"]),
                        "task_id": str(item["task_id"]),
                        "task_revision": str(item["task_revision"]),
                        "handoff_id": str(item["handoff_id"]),
                        "state": str(item["state"]),
                    }
                    for item in existing_items
                })
                conn.execute("COMMIT")
                return False
            conn.execute(
                """
                INSERT INTO pm_handoff_batches (
                    handoff_id, operation_id, project_id, change_id, scope_hash,
                    approval_id, snapshot_id, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'STAGING', ?)
                """,
                (
                    handoff_id,
                    operation_id,
                    project_id,
                    change_id,
                    scope_hash,
                    approval_id,
                    snapshot_id,
                    stamp,
                ),
            )
            for item in items:
                conn.execute(
                    """
                    INSERT INTO pm_handoff_items (
                        handoff_id, project_id, change_id, task_id,
                        task_revision, deterministic_ticket_id, state, content_hash,
                        input_hash, input_json, required_procedures_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 'STAGING', ?, ?, ?, ?)
                    """,
                    (
                        handoff_id,
                        project_id,
                        change_id,
                        item["task_id"],
                        item["task_revision"],
                        item["deterministic_ticket_id"],
                        item["content_hash"],
                        item.get("input_hash", ""),
                        item.get("input_json", "{}"),
                        item.get("required_procedures_json", "{}"),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO pm_managed_tickets (
                        project_id, ticket_identifier, change_id, task_id,
                        task_revision, handoff_id, state, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'STAGING', ?)
                    """,
                    (
                        project_id, item["deterministic_ticket_id"], change_id,
                        item["task_id"], item["task_revision"], handoff_id, stamp,
                    ),
                )
            self._merge_pm_fallback(
                project_id,
                {
                    str(item["deterministic_ticket_id"]): {
                        "change_id": change_id,
                        "task_id": str(item["task_id"]),
                        "task_revision": str(item["task_revision"]),
                        "handoff_id": handoff_id,
                        "state": "STAGING",
                    }
                    for item in items
                },
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def publish_pm_handoff(
        self,
        handoff_id: str,
        *,
        ticket_identifiers: Mapping[str, str],
        now: datetime | None = None,
    ) -> bool:
        stamp = _iso(_utc(now))
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            batch = conn.execute(
                "SELECT * FROM pm_handoff_batches WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            if batch is None:
                raise ValueError("unknown handoff")
            if str(batch["state"]) == "PUBLISHED":
                conn.execute("COMMIT")
                return False
            if str(batch["state"]) != "STAGING":
                raise ValueError("handoff is not publishable")
            items = conn.execute(
                "SELECT * FROM pm_handoff_items WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchall()
            if set(ticket_identifiers) != {str(item["task_id"]) for item in items}:
                raise ValueError("published ticket set does not match handoff")
            for item in items:
                task_id = str(item["task_id"])
                ticket = ticket_identifiers[task_id]
                conn.execute(
                    """
                    UPDATE pm_handoff_items
                    SET ticket_identifier = ?, state = 'PUBLISHED'
                    WHERE handoff_id = ? AND task_id = ?
                    """,
                    (ticket, handoff_id, task_id),
                )
                updated = conn.execute(
                    """
                    UPDATE pm_managed_tickets
                    SET state = 'PUBLISHED', updated_at = ?
                    WHERE project_id = ? AND ticket_identifier = ?
                      AND change_id = ? AND task_id = ?
                      AND task_revision = ? AND handoff_id = ?
                    """,
                    (
                        stamp, str(batch["project_id"]), ticket,
                        str(batch["change_id"]), task_id,
                        str(item["task_revision"]), handoff_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise ValueError("staged managed-ticket identity is missing")
            conn.execute(
                """
                UPDATE pm_handoff_batches
                SET state = 'PUBLISHED', published_at = ?
                WHERE handoff_id = ?
                """,
                (stamp, handoff_id),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_pm_handoff_batch(self, handoff_id: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM pm_handoff_batches WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_pm_handoff_items(self, handoff_id: str) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            "SELECT * FROM pm_handoff_items WHERE handoff_id = ? ORDER BY task_id",
            (handoff_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_pm_handoff_by_operation(self, operation_id: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM pm_handoff_batches WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_pm_managed_ticket(
        self, project_id: str, ticket_identifier: str
    ) -> PmManagedTicketRecord | None:
        row = self._connect().execute(
            """
            SELECT * FROM pm_managed_tickets
            WHERE project_id = ? AND ticket_identifier = ?
            """,
            (project_id, ticket_identifier),
        ).fetchone()
        return _pm_managed_ticket(row) if row is not None else None

    def get_pm_fallback_ticket(
        self, project_id: str, ticket_identifier: str
    ) -> dict[str, Any] | None:
        path = self._pm_fallback_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("PM fallback authority index is unreadable or corrupt") from exc
        if not isinstance(payload, dict):
            raise ValueError("PM fallback authority index is corrupt")
        project = payload.get(project_id) if isinstance(payload, dict) else None
        if project is not None and not isinstance(project, dict):
            raise ValueError("PM fallback project index is corrupt")
        row = project.get(ticket_identifier) if isinstance(project, dict) else None
        if row is not None and not isinstance(row, dict):
            raise ValueError("PM fallback ticket identity is corrupt")
        return dict(row) if isinstance(row, dict) else None

    def pm_ticket_identifier_known(self, ticket_identifier: str) -> bool:
        """Whether any project's PM records mention this ticket.

        Used when the host project identity is unavailable. An unreadable
        fallback index counts as known so callers fail closed.
        """
        row = self._connect().execute(
            "SELECT 1 FROM pm_managed_tickets WHERE ticket_identifier = ? LIMIT 1",
            (ticket_identifier,),
        ).fetchone()
        if row is not None:
            return True
        path = self._pm_fallback_path()
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        if not isinstance(payload, dict):
            return True
        return any(
            not isinstance(project, dict) or ticket_identifier in project
            for project in payload.values()
        )

    def _pm_fallback_path(self) -> Path:
        return self._path.with_name(self._path.name + ".pm-managed.json")

    def _merge_pm_fallback(
        self, project_id: str, rows: Mapping[str, Mapping[str, str]]
    ) -> None:
        path = self._pm_fallback_path()
        if not path.exists():
            payload = {}
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "refusing to merge into corrupt PM fallback authority index"
                ) from exc
        if not isinstance(payload, dict):
            raise ValueError("refusing to merge into corrupt PM fallback authority index")
        project = payload.setdefault(project_id, {})
        if not isinstance(project, dict):
            project = {}
            payload[project_id] = project
        project.update({key: dict(value) for key, value in rows.items()})
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def get_pm_managed_ticket_scope(
        self, project_id: str, ticket_identifier: str
    ) -> dict[str, Any] | None:
        row = self._connect().execute(
            """
            SELECT i.*, b.scope_hash, b.approval_id, b.snapshot_id,
                   b.state AS batch_state
            FROM pm_managed_tickets m
            JOIN pm_handoff_items i
              ON i.handoff_id = m.handoff_id
             AND i.task_id = m.task_id
             AND i.task_revision = m.task_revision
            JOIN pm_handoff_batches b ON b.handoff_id = m.handoff_id
            WHERE m.project_id = ? AND m.ticket_identifier = ?
            """,
            (project_id, ticket_identifier),
        ).fetchone()
        return dict(row) if row is not None else None

    def has_active_pm_dispatch(self, project_id: str, ticket_identifier: str) -> bool:
        row = self._connect().execute(
            """SELECT 1 FROM pm_dispatch_permits
            WHERE project_id=? AND ticket_identifier=?
              AND state IN ('RESERVED','STARTING','RUNNING') LIMIT 1""",
            (project_id, ticket_identifier),
        ).fetchone()
        return row is not None

    def list_pm_managed_tickets(self, project_id: str) -> list[PmManagedTicketRecord]:
        rows = self._connect().execute(
            """
            SELECT * FROM pm_managed_tickets
            WHERE project_id = ? ORDER BY ticket_identifier
            """,
            (project_id,),
        ).fetchall()
        return [_pm_managed_ticket(row) for row in rows]

    def reserve_pm_dispatch_permit(
        self,
        *,
        project_id: str,
        ticket_identifier: str,
        handoff_id: str,
        task_revision: str,
        issue_hash: str,
        scope_hash: str,
        ttl: timedelta = DEFAULT_LEASE_TTL,
        now: datetime | None = None,
    ) -> PmDispatchPermitRecord:
        stamp_dt = _utc(now)
        stamp = _iso(stamp_dt)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                UPDATE pm_dispatch_permits
                SET state = 'REVOKED', updated_at = ?
                WHERE project_id = ? AND ticket_identifier = ?
                  AND state IN ('RESERVED','STARTING')
                  AND lease_expires_at <= ?
                """,
                (stamp, project_id, ticket_identifier, stamp),
            )
            mapping = conn.execute(
                """
                SELECT state, handoff_id, task_revision, change_id
                FROM pm_managed_tickets
                WHERE project_id = ? AND ticket_identifier = ?
                """,
                (project_id, ticket_identifier),
            ).fetchone()
            if mapping is None:
                raise RuntimeError("PM managed-ticket mapping is missing")
            batch = conn.execute(
                """
                SELECT state, scope_hash FROM pm_handoff_batches
                WHERE handoff_id = ? AND project_id = ?
                """,
                (handoff_id, project_id),
            ).fetchone()
            if (
                str(mapping["state"]) != "PUBLISHED"
                or str(mapping["handoff_id"]) != handoff_id
                or str(mapping["task_revision"]) != task_revision
                or batch is None
                or str(batch["state"]) != "PUBLISHED"
                or str(batch["scope_hash"]) != scope_hash
            ):
                raise RuntimeError("PM dispatch authority is not published")
            revoked = conn.execute(
                """
                SELECT 1 FROM pm_authority_revocations
                WHERE project_id = ? AND change_id = ? AND handoff_id = ?
                  AND task_revision = ?
                """,
                (
                    project_id,
                    str(mapping["change_id"]),
                    handoff_id,
                    task_revision,
                ),
            ).fetchone()
            if revoked is not None:
                raise RuntimeError("PM dispatch authority was durably revoked")
            previous = conn.execute(
                """
                SELECT MAX(fence) AS fence FROM pm_dispatch_permits
                WHERE project_id = ? AND ticket_identifier = ?
                """,
                (project_id, ticket_identifier),
            ).fetchone()
            fence = int(previous["fence"] or 0) + 1
            permit_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO pm_dispatch_permits (
                    permit_id, project_id, ticket_identifier, handoff_id,
                    task_revision, issue_hash, scope_hash, fence, state,
                    lease_expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)
                """,
                (
                    permit_id,
                    project_id,
                    ticket_identifier,
                    handoff_id,
                    task_revision,
                    issue_hash,
                    scope_hash,
                    fence,
                    _iso(stamp_dt + ttl),
                    stamp,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        record = self.get_pm_dispatch_permit(permit_id)
        assert record is not None
        return record

    def transition_pm_dispatch_permit(
        self,
        permit_id: str,
        *,
        fence: int,
        from_states: tuple[str, ...],
        to_state: str,
        process_pid: int | None = None,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        placeholders = ",".join("?" for _ in from_states)
        stamp = _iso(_utc(now))
        params: list[Any] = [
            to_state,
            process_pid,
            run_id,
            stamp,
            permit_id,
            fence,
            stamp,
            *from_states,
        ]
        updated = self._connect().execute(
            f"""
            UPDATE pm_dispatch_permits
            SET state = ?, process_pid = COALESCE(?, process_pid),
                run_id = COALESCE(?, run_id), updated_at = ?
            WHERE permit_id = ? AND fence = ? AND lease_expires_at > ?
              AND state IN ({placeholders})
            """,
            params,
        )
        return updated.rowcount == 1

    def revoke_pm_dispatch(
        self,
        project_id: str,
        ticket_identifier: str,
        *,
        now: datetime | None = None,
    ) -> int:
        updated = self._connect().execute(
            """
            UPDATE pm_dispatch_permits
            SET state = 'REVOKED', updated_at = ?
            WHERE project_id = ? AND ticket_identifier = ?
              AND state IN ('RESERVED','STARTING','RUNNING')
            """,
            (_iso(_utc(now)), project_id, ticket_identifier),
        )
        return updated.rowcount

    def consume_pm_dispatch_permit(
        self,
        permit_id: str,
        *,
        fence: int,
        run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Consume the exact RUNNING permit after its owning run is terminal."""
        stamp = _iso(_utc(now))
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            consumed = self._consume_pm_dispatch_permit_locked(
                conn, permit_id, fence=fence, run_id=run_id, stamp=stamp
            )
            if not consumed:
                conn.execute("ROLLBACK")
                return False
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _consume_pm_dispatch_permit_locked(
        conn: sqlite3.Connection,
        permit_id: str,
        *,
        fence: int,
        run_id: str,
        stamp: str,
    ) -> bool:
        permit = conn.execute(
            """SELECT ticket_identifier, run_id, state, fence
               FROM pm_dispatch_permits WHERE permit_id = ?""",
            (permit_id,),
        ).fetchone()
        run = conn.execute(
            """SELECT identifier, status FROM runs WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
        if (
            permit is None
            or run is None
            or int(permit["fence"]) != fence
            or str(permit["run_id"] or "") != run_id
            or str(run["identifier"]) != str(permit["ticket_identifier"])
            or str(run["status"]) in {"active", "reclaiming"}
        ):
            return False
        if str(permit["state"]) == "CONSUMED":
            return True
        if str(permit["state"]) != "RUNNING":
            return False
        updated = conn.execute(
            """UPDATE pm_dispatch_permits
               SET state = 'CONSUMED', updated_at = ?
               WHERE permit_id = ? AND fence = ?
                 AND run_id = ? AND state = 'RUNNING'""",
            (stamp, permit_id, fence, run_id),
        )
        return updated.rowcount == 1

    def revoke_pm_authority(
        self,
        project_id: str,
        ticket_identifier: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> int:
        stamp = _iso(_utc(now))
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            mapping = conn.execute(
                "SELECT * FROM pm_managed_tickets WHERE project_id = ? AND ticket_identifier = ?",
                (project_id, ticket_identifier),
            ).fetchone()
            if mapping is None:
                conn.execute("COMMIT")
                return 0
            conn.execute(
                """
                INSERT OR IGNORE INTO pm_authority_revocations (
                    project_id, change_id, handoff_id, task_revision, reason, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, str(mapping["change_id"]), str(mapping["handoff_id"]),
                    str(mapping["task_revision"]), reason, stamp,
                ),
            )
            conn.execute(
                "UPDATE pm_managed_tickets SET state = 'REVOKED', updated_at = ? WHERE project_id = ? AND ticket_identifier = ?",
                (stamp, project_id, ticket_identifier),
            )
            updated = conn.execute(
                """
                UPDATE pm_dispatch_permits SET state = 'REVOKED', updated_at = ?
                WHERE project_id = ? AND ticket_identifier = ?
                  AND state IN ('RESERVED','STARTING','RUNNING')
                """,
                (stamp, project_id, ticket_identifier),
            ).rowcount
            conn.execute("COMMIT")
            return max(1, updated)
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def record_pm_verification(
        self, *, receipt_id: str, project_id: str, ticket_identifier: str,
        task_revision: str, operation_id: str, run_id: str, procedure_id: str,
        command_digest: str, tested_commit: str, return_code: int | None,
        timed_out: bool, cancelled: bool, stdout_hash: str, stderr_hash: str,
        started_at: str, completed_at: str, artifact_refs: Sequence[str],
        result: str,
    ) -> None:
        self._connect().execute(
            """
            INSERT OR REPLACE INTO pm_verification_records (
                receipt_id, project_id, ticket_identifier, task_revision,
                operation_id, run_id, procedure_id, command_digest, tested_commit,
                return_code, timed_out, cancelled, stdout_hash, stderr_hash,
                started_at, completed_at, artifact_refs_json, result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id, project_id, ticket_identifier, task_revision,
                operation_id, run_id, procedure_id, command_digest, tested_commit,
                return_code, int(timed_out), int(cancelled), stdout_hash,
                stderr_hash, started_at, completed_at,
                json.dumps(list(artifact_refs)), result,
            ),
        )

    def get_pm_verification_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM pm_verification_records WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if row is not None:
            return dict(row)
        manual = self.get_pm_manual_verification_receipt(receipt_id)
        return ({**manual, "provenance_kind": "manual", "run_id": None} if manual else None)

    def get_verified_pm_commit(
        self, project_id: str, ticket_identifier: str
    ) -> str | None:
        mapping = self.get_pm_managed_ticket(project_id, ticket_identifier)
        if mapping is None:
            return None
        required = self.get_pm_required_procedures(project_id, ticket_identifier)
        if not required:
            return None
        rows = self._connect().execute(
            """
            SELECT tested_commit, MAX(completed_at) AS completed_at FROM (
              SELECT tested_commit, completed_at FROM pm_verification_records
              WHERE project_id=? AND ticket_identifier=? AND task_revision=?
              UNION ALL
              SELECT tested_commit, completed_at FROM pm_manual_verification_records
              WHERE project_id=? AND ticket_identifier=? AND task_revision=?
            ) GROUP BY tested_commit ORDER BY completed_at DESC
            """,
            (project_id, ticket_identifier, mapping.task_revision, project_id, ticket_identifier, mapping.task_revision),
        ).fetchall()
        for candidate in rows:
            commit = str(candidate["tested_commit"])
            receipts = self.list_pm_verification_receipts(
                project_id, ticket_identifier, mapping.task_revision, commit
            )
            qualified = {
                str(receipt["procedure_id"])
                for receipt in receipts
                if _pm_receipt_matches_policy(
                    receipt, required.get(str(receipt["procedure_id"]))
                )
            }
            if qualified == set(required):
                return commit
        return None

    def get_pm_required_procedures(
        self, project_id: str, ticket_identifier: str
    ) -> dict[str, Any]:
        row = self.get_pm_managed_ticket_scope(project_id, ticket_identifier)
        if row is None:
            return {}
        try:
            value = json.loads(str(row["required_procedures_json"]))
        except (KeyError, json.JSONDecodeError):
            return {}
        return {
            str(key): digest
            for key, digest in value.items()
            if isinstance(key, str) and isinstance(digest, (str, dict))
        } if isinstance(value, dict) else {}

    def list_pm_verification_receipts(
        self, project_id: str, ticket_identifier: str, task_revision: str,
        tested_commit: str,
    ) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            """
            SELECT * FROM pm_verification_records
            WHERE project_id = ? AND ticket_identifier = ?
              AND task_revision = ? AND tested_commit = ?
            ORDER BY completed_at, receipt_id
            """,
            (project_id, ticket_identifier, task_revision, tested_commit),
        ).fetchall()
        result = [dict(row) for row in rows]
        manual = self._connect().execute(
            """
            SELECT * FROM pm_manual_verification_records
            WHERE project_id = ? AND ticket_identifier = ?
              AND task_revision = ? AND tested_commit = ?
            ORDER BY completed_at, receipt_id
            """,
            (project_id, ticket_identifier, task_revision, tested_commit),
        ).fetchall()
        result.extend({**dict(row), "provenance_kind": "manual", "run_id": None} for row in manual)
        return sorted(result, key=lambda row: (str(row["completed_at"]), str(row["receipt_id"])))

    def record_pm_manual_verification(self, record: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT * FROM pm_manual_verification_records WHERE operation_id = ?",
                (record["operation_id"],),
            ).fetchone()
            if existing is not None:
                if str(existing["input_hash"]) != str(record["input_hash"]):
                    raise ValueError("manual evidence operation reused for another input")
                conn.execute("COMMIT")
                return dict(existing), False
            columns = (
                "receipt_id", "operation_id", "project_id", "change_id",
                "ticket_identifier", "task_id", "task_revision", "procedure_id",
                "procedure_kind", "definition_digest", "tested_commit",
                "environment_summary", "return_code", "timed_out", "cancelled",
                "stdout_hash", "stderr_hash", "started_at", "completed_at",
                "submitted_at", "submitted_by", "report_ref_json",
                "artifact_refs_json", "result", "input_hash",
            )
            conn.execute(
                f"INSERT INTO pm_manual_verification_records ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(record.get(column) for column in columns),
            )
            row = conn.execute(
                "SELECT * FROM pm_manual_verification_records WHERE receipt_id = ?",
                (record["receipt_id"],),
            ).fetchone()
            conn.execute("COMMIT")
            assert row is not None
            return dict(row), True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    def get_pm_manual_verification_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM pm_manual_verification_records WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def create_pm_coverage_waiver(self, record: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT * FROM pm_coverage_waivers WHERE operation_id = ?",
                (record["operation_id"],),
            ).fetchone()
            if existing is not None:
                same = all(str(existing[key]) == str(record[key]) for key in (
                    "project_id", "change_id", "semantic_id", "reason", "revision", "scope_hash"
                ))
                if not same:
                    raise ValueError("waiver operation reused for another scope")
                conn.execute("COMMIT")
                return dict(existing), False
            conn.execute(
                """INSERT INTO pm_coverage_waivers
                (waiver_id,operation_id,project_id,change_id,semantic_id,reason,reviewer,revision,scope_hash,created_at,state)
                VALUES (?,?,?,?,?,?,?,?,?,?,'ACTIVE')""",
                tuple(record[key] for key in (
                    "waiver_id", "operation_id", "project_id", "change_id", "semantic_id",
                    "reason", "reviewer", "revision", "scope_hash", "created_at",
                )),
            )
            row = conn.execute("SELECT * FROM pm_coverage_waivers WHERE waiver_id = ?", (record["waiver_id"],)).fetchone()
            conn.execute("COMMIT")
            assert row is not None
            return dict(row), True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    def list_pm_coverage_waivers(self, project_id: str, change_id: str) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            "SELECT * FROM pm_coverage_waivers WHERE project_id = ? AND change_id = ? ORDER BY created_at, waiver_id",
            (project_id, change_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def revoke_pm_coverage_waiver(
        self, waiver_id: str, *, project_id: str, change_id: str,
        revision: str, operation_id: str, reviewer: str, reason: str,
        revoked_at: str,
    ) -> tuple[dict[str, Any], bool] | None:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM pm_coverage_waivers WHERE waiver_id=? "
                "AND project_id=? AND change_id=?",
                (waiver_id, project_id, change_id),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            if row["revoke_operation_id"]:
                if str(row["revoke_operation_id"]) != operation_id:
                    raise ValueError("coverage waiver is already revoked")
                conn.execute("COMMIT")
                return dict(row), False
            if str(row["state"]) != "ACTIVE" or str(row["revision"]) != revision:
                raise ValueError("coverage waiver revision is stale")
            conn.execute(
                "UPDATE pm_coverage_waivers SET state='REVOKED',revoked_at=?,"
                "revoked_by=?,revoke_reason=?,revoke_operation_id=? "
                "WHERE waiver_id=? AND state='ACTIVE'",
                (revoked_at, reviewer, reason, operation_id, waiver_id),
            )
            updated = conn.execute(
                "SELECT * FROM pm_coverage_waivers WHERE waiver_id=?", (waiver_id,)
            ).fetchone()
            conn.execute("COMMIT")
            assert updated is not None
            return dict(updated), True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    def record_pm_acceptance(
        self, *, acceptance_id: str, project_id: str, ticket_identifier: str,
        task_revision: str, tested_commit: str, receipt_ids: Sequence[str],
        receipt_set_hash: str, accepted_by: str, accepted_at: str,
    ) -> None:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                UPDATE pm_acceptances SET state = 'SUPERSEDED'
                WHERE project_id = ? AND ticket_identifier = ?
                  AND task_revision = ? AND state = 'ACCEPTED'
                """,
                (project_id, ticket_identifier, task_revision),
            )
            conn.execute(
                """
                INSERT INTO pm_acceptances (
                    acceptance_id, project_id, ticket_identifier, task_revision,
                    tested_commit, receipt_ids_json, receipt_set_hash,
                    accepted_by, accepted_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACCEPTED')
                """,
                (
                    acceptance_id, project_id, ticket_identifier, task_revision,
                    tested_commit, json.dumps(sorted(receipt_ids)), receipt_set_hash,
                    accepted_by, accepted_at,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_active_pm_acceptance(
        self, project_id: str, ticket_identifier: str, task_revision: str,
    ) -> dict[str, Any] | None:
        row = self._connect().execute(
            """
            SELECT * FROM pm_acceptances
            WHERE project_id = ? AND ticket_identifier = ?
              AND task_revision = ? AND state = 'ACCEPTED'
            ORDER BY accepted_at DESC LIMIT 1
            """,
            (project_id, ticket_identifier, task_revision),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_accepted_pm_commit(
        self, project_id: str, ticket_identifier: str
    ) -> str | None:
        mapping = self.get_pm_managed_ticket(project_id, ticket_identifier)
        if mapping is None:
            return None
        row = self.get_active_pm_acceptance(
            project_id, ticket_identifier, mapping.task_revision
        )
        if row is None:
            return None
        required = self.get_pm_required_procedures(project_id, ticket_identifier)
        if not required:
            return None
        try:
            receipt_ids = json.loads(str(row["receipt_ids_json"]))
        except json.JSONDecodeError:
            return None
        if not isinstance(receipt_ids, list):
            return None
        observed: set[str] = set()
        for receipt_id in receipt_ids:
            receipt = self.get_pm_verification_receipt(str(receipt_id))
            if receipt is None:
                return None
            procedure_id = str(receipt["procedure_id"])
            policy = required.get(procedure_id)
            qualifies = _pm_receipt_matches_policy(receipt, policy)
            if (
                str(receipt["project_id"]) != project_id
                or str(receipt["ticket_identifier"]) != ticket_identifier
                or str(receipt["task_revision"]) != mapping.task_revision
                or str(receipt["tested_commit"]) != str(row["tested_commit"])
                or str(receipt["result"]) != "passed"
                or bool(receipt["timed_out"])
                or bool(receipt["cancelled"])
                or not qualifies
            ):
                return None
            observed.add(procedure_id)
        if observed != set(required):
            return None
        # PM receipts exist only when the optional PM package wrote them.
        from ..pm.models import canonical_receipt_set_hash  # pyright: ignore[reportMissingImports]

        expected_set_hash = canonical_receipt_set_hash(
            tuple(str(item) for item in receipt_ids)
        )
        if expected_set_hash != str(row["receipt_set_hash"]):
            return None
        return str(row["tested_commit"])

    def reserve_pm_legacy_conversion(
        self, *, project_id: str, change_id: str, source_ticket_identifier: str,
        workspace_path: str, locator: str, now: datetime | None = None,
    ) -> dict[str, Any]:
        stamp = _iso(_utc(now))
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(_utc(now))
            active = self._active_identifier_locked(source_ticket_identifier, _utc(now))
            if active is not None:
                raise ValueError("legacy source has an active run")
            conn.execute(
                """
            INSERT INTO pm_legacy_conversions (
                project_id, change_id, source_ticket_identifier,
                workspace_path, locator, state, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'RESERVED', ?)
            ON CONFLICT(project_id, change_id) DO NOTHING
                """,
                (project_id, change_id, source_ticket_identifier, workspace_path, locator, stamp),
            )
            row = conn.execute(
                "SELECT * FROM pm_legacy_conversions WHERE project_id = ? AND change_id = ?",
                (project_id, change_id),
            ).fetchone()
            if row is None or (
                str(row["source_ticket_identifier"]), str(row["workspace_path"]), str(row["locator"])
            ) != (source_ticket_identifier, workspace_path, locator):
                raise ValueError("legacy conversion identity conflict")
            conn.execute("COMMIT")
            return dict(row)
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def set_pm_legacy_conversion_state(
        self, project_id: str, change_id: str, state: str,
        *, now: datetime | None = None,
    ) -> bool:
        updated = self._connect().execute(
            "UPDATE pm_legacy_conversions SET state = ?, updated_at = ? WHERE project_id = ? AND change_id = ?",
            (state, _iso(_utc(now)), project_id, change_id),
        )
        return updated.rowcount == 1

    def get_pm_legacy_conversion_by_source(
        self, project_id: str, source_ticket_identifier: str
    ) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM pm_legacy_conversions WHERE project_id = ? AND source_ticket_identifier = ?",
            (project_id, source_ticket_identifier),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_pm_dispatch_permit(
        self, permit_id: str
    ) -> PmDispatchPermitRecord | None:
        row = self._connect().execute(
            "SELECT * FROM pm_dispatch_permits WHERE permit_id = ?",
            (permit_id,),
        ).fetchone()
        return _pm_dispatch_permit(row) if row is not None else None
