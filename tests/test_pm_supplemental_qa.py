from __future__ import annotations

import io
import zipfile

from packages.pm.scripts.pm_workspace_supplemental_qa import _padded_workbook, _record


def test_padded_workbook_preserves_parts_and_controls_compression() -> None:
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", b"workbook")
    compressed = _padded_workbook(source.getvalue(), 100_000, compressible=True)
    incompressible = _padded_workbook(source.getvalue(), 100_000, compressible=False)
    with zipfile.ZipFile(io.BytesIO(compressed)) as archive:
        assert archive.read("xl/workbook.xml") == b"workbook"
        assert len(archive.read("qa/padding.bin")) == 100_000
    assert len(incompressible) > len(compressed) + 90_000


def test_evidence_status_distinguishes_executed_pass_and_failure() -> None:
    receipts: dict = {}
    _record(receipts, "O18", True, status_code=200)
    _record(receipts, "O35", False, status_code=403)
    assert receipts["O18"] == {"status": "executed_pass", "status_code": 200}
    assert receipts["O35"] == {"status": "executed_fail", "status_code": 403}
