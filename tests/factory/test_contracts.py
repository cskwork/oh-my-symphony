from symphony.orchestrator.contracts import evaluate_contract


def test_factory_build_contract_requires_each_supergoal_ledger_pass() -> None:
    result = evaluate_contract(
        "Build", "## Implementation\n\nchanged it", "TASK-1", profile="factory"
    )
    assert result.passed is False
    assert result.missing == [
        "## Full Spec Review",
        "## Edge Case Review",
        "## Adversarial Review",
        "## Test Evidence",
    ]


def test_factory_build_contract_accepts_nonempty_compact_supergoal_ledger() -> None:
    body = """## Implementation

- Added the bounded behavior and regression test.

## Full Spec Review

- Re-read every acceptance criterion; no gap remains.

## Edge Case Review

- Covered the empty-input boundary.

## Adversarial Review

- Tried to disprove completion; no unresolved finding.

## Test Evidence

- `pytest tests/test_api.py -q`: pass.
"""

    assert evaluate_contract("Build", body, "TASK-1", profile="factory").passed


def test_factory_verify_contract_rejects_bare_claimed_pass() -> None:
    result = evaluate_contract(
        "Verify",
        "## Verification\n\n- `pytest`: pass\n- Acceptance criteria: pass",
        "TASK-1",
        profile="factory",
    )
    assert result.passed is False
    assert any("criterion | command | result" in item for item in result.missing)


def test_factory_verify_contract_requires_structured_passing_command_evidence() -> None:
    body = """## Acceptance criteria

- API returns the saved item

## Verification

| criterion | command | result |
| --- | --- | --- |
| API returns the saved item | `pytest tests/test_api.py -q` | pass |
"""

    assert evaluate_contract("Verify", body, "TASK-1", profile="factory").passed


def test_factory_verify_contract_rejects_missing_command_or_failed_result() -> None:
    body = """## Acceptance criteria

- API returns the saved item
- Existing API tests remain green

## Verification

| criterion | command | result |
| --- | --- | --- |
| API returns the saved item | not run | pass |
| Existing API tests remain green | `pytest tests/test_api.py -q` | fail |
"""

    result = evaluate_contract("Verify", body, "TASK-1", profile="factory")

    assert result.passed is False
    assert any("row 1" in item and "command" in item for item in result.missing)
    assert any("row 2" in item and "result" in item for item in result.missing)


def test_factory_verify_contract_requires_every_acceptance_criterion() -> None:
    body = """## Acceptance criteria

- API returns the saved item.
2. Existing API tests remain green.

## Verification

| criterion | command | result |
| --- | --- | --- |
| **API** returns the `saved item`. | `pytest tests/test_api.py -q` | pass |
"""

    result = evaluate_contract("Verify", body, "TASK-1", profile="factory")

    assert result.passed is False
    assert any("Existing API tests remain green." in item for item in result.missing)


def test_factory_verify_contract_matches_normalized_markdown_in_distinct_rows() -> None:
    body = """## Acceptance criteria

- [ ] **API** returns the `saved item`.
2) Existing [API tests](tests/test_api.py) remain green.

## Verification

| criterion | command | result |
| --- | --- | --- |
| API returns the saved item. | `pytest tests/test_api.py -q` | pass |
| Existing API tests remain green. | `pytest tests/test_api.py -q` | pass |
"""

    assert evaluate_contract("Verify", body, "TASK-1", profile="factory").passed


def test_factory_verify_contract_requires_literal_pass_and_no_extra_rows() -> None:
    body = """## Acceptance criteria

- API returns the saved item.

## Verification

| criterion | command | result |
| --- | --- | --- |
| API returns the saved item. | `pytest tests/test_api.py -q` | green |
| An undeclared behavior. | `pytest tests/test_api.py -q` | pass |
"""

    result = evaluate_contract("Verify", body, "TASK-1", profile="factory")

    assert result.passed is False
    assert any("result is not exactly `pass`" in item for item in result.missing)
    assert any("undeclared criterion" in item for item in result.missing)


def test_factory_verify_contract_joins_wrapped_acceptance_criterion() -> None:
    body = """## Acceptance criteria

- `python -m unittest -q test_factory_probe.py` exits 0 and proves
  `factory_value()` returns exactly `factory-ok`.

## Verification

| criterion | command | result |
| --- | --- | --- |
| `python -m unittest -q test_factory_probe.py` exits 0 and proves `factory_value()` returns exactly `factory-ok`. | `python -m unittest -q test_factory_probe.py` | pass |
"""

    assert evaluate_contract("Verify", body, "TASK-1", profile="factory").passed


def test_factory_verify_contract_rejects_missing_or_empty_acceptance_criteria() -> None:
    verification = """## Verification

| criterion | command | result |
| --- | --- | --- |
| API returns the saved item | `pytest tests/test_api.py -q` | pass |
"""

    missing = evaluate_contract("Verify", verification, "TASK-1", profile="factory")
    empty = evaluate_contract(
        "Verify",
        "## Acceptance criteria\n\n## Verification\n\n"
        "| criterion | command | result |\n"
        "| --- | --- | --- |\n"
        "| API returns the saved item | `pytest tests/test_api.py -q` | pass |",
        "TASK-1",
        profile="factory",
    )

    assert missing.passed is False
    assert empty.passed is False
    assert "## Acceptance criteria" in missing.missing
    assert "## Acceptance criteria" in empty.missing


def test_factory_verify_contract_does_not_reuse_one_row_for_duplicate_criteria() -> None:
    body = """## Acceptance criteria

- Same behavior is proven.
- Same behavior is proven.

## Verification

| criterion | command | result |
| --- | --- | --- |
| Same behavior is proven. | `pytest tests/test_api.py -q` | pass |
"""

    result = evaluate_contract("Verify", body, "TASK-1", profile="factory")

    assert result.passed is False
    assert any("distinct Verification row" in item for item in result.missing)


def test_advanced_contract_remains_unchanged() -> None:
    result = evaluate_contract(
        "Verify", "## Verification\n\npass", "TASK-1", profile="advanced"
    )
    assert result.passed is False
    assert "## QA Evidence" in result.missing
