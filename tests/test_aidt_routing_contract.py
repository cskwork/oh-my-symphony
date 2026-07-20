"""Closed configuration contract for AIDT routing."""

import math
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from symphony.aidt_routing.contract import (
    CATALOG_SCHEMA,
    MAX_ALIASES_PER_SERVICE,
    MAX_ANCHORS_PER_CATEGORY,
    MAX_CHILDREN,
    MAX_COORDINATORS,
    MAX_EVIDENCE_RECORDS,
    MAX_GIT_PATHS_PER_SERVICE,
    MAX_ID_BYTES,
    MAX_OBSERVATION_BYTES,
    MAX_ROUTE_BATCH_BYTES,
    MAX_SERVICE_OBJECT_BYTES,
    MAX_SERVICES,
    MAX_VALUE_BYTES,
    AidtRoutingFailure,
    AidtRoutingResult,
    _FAILURE_CATEGORIES,
    canonical_fingerprint,
    load_routing_settings,
)

from tests.aidt_routing_support import (
    routing_config,
    service_config,
    service_definition,
)


class _IntSubclass(int):
    pass


class _StringSubclass(str):
    pass


class _UnhashableString(str):
    __hash__ = None  # type: ignore[assignment]


class _FrozenSetSubclass(frozenset[str]):
    pass


class _HostileValue:
    def __init__(self) -> None:
        self.repr_calls = 0

    def __repr__(self) -> str:
        self.repr_calls += 1
        return "TOP-SECRET-OBJECT-/private/payload"


def _card_key(byte_count: int) -> str:
    return f"{'A' * (byte_count - 2)}-1"


def _result(**overrides: Any) -> AidtRoutingResult:
    fields: dict[str, Any] = {
        "enabled": True,
        "global_allow_dispatch": True,
        "blocked_identifiers": frozenset(),
        "routed_count": 0,
        "review_count": 0,
        "child_count": 0,
        "failure_count": 0,
        "status": "success",
        "error_category": None,
        "error_ref": None,
    }
    fields.update(overrides)
    return AidtRoutingResult(**fields)


def _assert_canonical_failure(result: AidtRoutingResult) -> None:
    assert result.enabled is True
    assert result.global_allow_dispatch is False
    assert type(result.blocked_identifiers) is frozenset
    assert result.blocked_identifiers == frozenset()
    assert result.routed_count == 0
    assert result.review_count == 0
    assert result.child_count == 0
    assert result.failure_count == 1
    assert result.status == "failure"
    assert result.error_category == "internal_error"
    assert result.error_ref is None


def test_absent_and_disabled_routing_ignore_untrusted_siblings(tmp_path: Path) -> None:
    board = tmp_path / "board"
    board.mkdir()

    assert load_routing_settings(service_config(board, {})) is None
    raw = {"aidt_routing": {"enabled": False, "untrusted": object()}}
    assert load_routing_settings(service_config(board, raw)) is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: {**raw, "unknown": True},
        lambda raw: {**raw, "minimum_confidence": True},
        lambda raw: {**raw, "source_mode": "body"},
        lambda raw: {**raw, "states": {**raw["states"], "extra": "state"}},
        lambda raw: {
            **raw,
            "services": [{**raw["services"][0], "unknown": True}],
        },
    ],
)
def test_enabled_catalog_is_recursively_closed(
    tmp_path: Path, mutation: Any
) -> None:
    board = tmp_path / "board"
    board.mkdir()
    raw = routing_config(tmp_path, [service_definition()])["aidt_routing"]

    with pytest.raises(AidtRoutingFailure, match="config_invalid"):
        load_routing_settings(service_config(board, {"aidt_routing": mutation(raw)}))


@pytest.mark.parametrize(
    "services",
    [
        [service_definition(), service_definition("other", checkout="viewer-api")],
        [service_definition(), service_definition("other", aliases=["VIEWER-API"])],
        [service_definition("caf-e", aliases=["café", "café"])],
        [service_definition("Upper")],
    ],
)
def test_catalog_rejects_identity_and_casefold_collisions(
    tmp_path: Path, services: list[dict[str, Any]]
) -> None:
    board = tmp_path / "board"
    board.mkdir()

    with pytest.raises(AidtRoutingFailure, match="config_invalid"):
        load_routing_settings(service_config(board, routing_config(tmp_path, services)))


@pytest.mark.parametrize(
    "path",
    ["/absolute", "../escape", "nested/../escape", "nested//file", "a\\b", "bad\npath", "café.txt"],
)
def test_catalog_git_paths_are_ascii_unambiguous_relatives(
    tmp_path: Path, path: str
) -> None:
    board = tmp_path / "board"
    board.mkdir()
    service = service_definition()
    service["markers"] = [path]

    with pytest.raises(AidtRoutingFailure, match="config_invalid"):
        load_routing_settings(
            service_config(board, routing_config(tmp_path, [service]))
        )


def test_catalog_and_fingerprint_ignore_mapping_and_list_order(tmp_path: Path) -> None:
    board = tmp_path / "board"
    board.mkdir()
    first = service_definition("alpha", aliases=["A", "B"])
    second = service_definition("beta")
    one = load_routing_settings(
        service_config(board, routing_config(tmp_path, [first, second]))
    )
    first["component_aliases"] = ["B", "A"]
    two = load_routing_settings(
        service_config(board, routing_config(tmp_path, [second, first]))
    )

    assert one is not None and two is not None
    assert one.catalog_revision == two.catalog_revision
    assert CATALOG_SCHEMA == "aidt-catalog-object-v2"
    assert canonical_fingerprint("v1", {"b": 2, "a": 1}) == canonical_fingerprint(
        "v1", {"a": 1, "b": 2}
    )
    with pytest.raises(ValueError):
        canonical_fingerprint("v1", {"invalid": math.nan})


def test_named_contract_caps_accept_boundary_and_reject_boundary_plus_one(
    tmp_path: Path,
) -> None:
    board = tmp_path / "board"
    board.mkdir()
    services = [service_definition(f"s-{index}") for index in range(MAX_SERVICES)]
    assert load_routing_settings(
        service_config(board, routing_config(tmp_path, services))
    ) is not None

    with pytest.raises(AidtRoutingFailure, match="config_invalid"):
        load_routing_settings(
            service_config(
                board,
                routing_config(tmp_path, [*services, service_definition("overflow")]),
            )
        )


def test_named_value_anchor_and_alias_caps_are_binding(tmp_path: Path) -> None:
    board = tmp_path / "board"
    board.mkdir()
    service = service_definition(
        aliases=[f"alias-{index}" for index in range(MAX_ALIASES_PER_SERVICE)]
    )
    service["context_anchors"] = [
        {"id": f"a-{index}", "file": "pom.xml", "literal": f"v-{index}"}
        for index in range(MAX_ANCHORS_PER_CATEGORY)
    ]
    assert load_routing_settings(
        service_config(board, routing_config(tmp_path, [service]))
    ) is not None

    overflow = deepcopy(service)
    overflow["component_aliases"] = [
        f"a-{index}" for index in range(MAX_ALIASES_PER_SERVICE + 1)
    ]
    with pytest.raises(AidtRoutingFailure, match="config_invalid"):
        load_routing_settings(
            service_config(board, routing_config(tmp_path, [overflow]))
        )

    overflow = deepcopy(service)
    overflow["context_anchors"].append(
        {"id": "overflow", "file": "pom.xml", "literal": "overflow"}
    )
    with pytest.raises(AidtRoutingFailure, match="config_invalid"):
        load_routing_settings(
            service_config(board, routing_config(tmp_path, [overflow]))
        )


def test_value_and_identifier_byte_caps_reject_boundary_plus_one(
    tmp_path: Path,
) -> None:
    board = tmp_path / "board"
    board.mkdir()
    service = service_definition("x" * 48, aliases=["x" * MAX_VALUE_BYTES])
    assert load_routing_settings(
        service_config(board, routing_config(tmp_path, [service]))
    ) is not None

    too_long_value = service_definition(aliases=["x" * (MAX_VALUE_BYTES + 1)])
    with pytest.raises(AidtRoutingFailure, match="config_invalid"):
        load_routing_settings(
            service_config(board, routing_config(tmp_path, [too_long_value]))
        )
    too_long_id = service_definition("x" * 49)
    with pytest.raises(AidtRoutingFailure, match="config_invalid"):
        load_routing_settings(
            service_config(board, routing_config(tmp_path, [too_long_id]))
        )


def test_64_configured_git_paths_are_derived_from_category_boundaries(
    tmp_path: Path,
) -> None:
    board = tmp_path / "board"
    board.mkdir()
    service = service_definition()
    service["markers"] = [f"marker-{index}" for index in range(16)]
    service["context_anchors"] = [
        {"id": f"c-{index}", "file": f"context-{index}", "literal": "x"}
        for index in range(16)
    ]
    service["route_anchors"] = [
        {
            "id": f"r-{index}",
            "file": f"route-{index}",
            "method": "GET",
            "endpoint": f"/route/{index}",
            "symbols": ["x"],
        }
        for index in range(16)
    ]
    service["domain_anchors"] = [
        {"id": f"d-{index}", "file": f"domain-{index}", "terms": ["x"]}
        for index in range(16)
    ]

    assert load_routing_settings(
        service_config(board, routing_config(tmp_path, [service]))
    ) is not None
    assert sum(len(service[key]) for key in ("markers", "context_anchors", "route_anchors", "domain_anchors")) == MAX_GIT_PATHS_PER_SERVICE


def test_all_cross_layer_caps_have_frozen_names_and_values() -> None:
    assert MAX_EVIDENCE_RECORDS == 32
    assert MAX_GIT_PATHS_PER_SERVICE == 64
    assert MAX_SERVICE_OBJECT_BYTES == 4_194_304
    assert MAX_OBSERVATION_BYTES == 16_777_216
    assert MAX_COORDINATORS == 500
    assert MAX_CHILDREN == 2_000
    assert MAX_ROUTE_BATCH_BYTES == 10_485_760


def test_public_failures_and_results_sanitize_repr_and_refs() -> None:
    allowed = AidtRoutingFailure("git_command_failed", "service:viewer-api")
    denied = AidtRoutingFailure("config_invalid", "service:secret")
    malformed = AidtRoutingFailure("git_command_failed", "service:../../secret")
    result = AidtRoutingResult(
        True,
        False,
        frozenset({"SECRET-CARD"}),
        0,
        0,
        0,
        1,
        "failure",
        allowed.category,
        allowed.identifier,
    )

    assert allowed.identifier == "service:viewer-api"
    assert denied.identifier is None and malformed.identifier is None
    assert "SECRET-CARD" not in repr(result)
    malicious = AidtRoutingResult(
        True,
        False,
        frozenset(),
        0,
        0,
        0,
        1,
        "failure",
        "SECRET-CATEGORY",
        "service:SECRET-PATH",
    )
    assert malicious.error_category == "internal_error"
    assert malicious.error_ref is None
    assert "SECRET" not in repr(malicious)


def test_public_result_normalizes_hostile_status_before_repr() -> None:
    for status in ("disabled", "success", "review", "failure"):
        allowed = AidtRoutingResult(
            status != "disabled",
            status != "failure",
            frozenset(),
            0,
            0,
            0,
            0,
            status,
        )
        assert allowed.status == status
    result = AidtRoutingResult(
        True,
        False,
        frozenset(),
        0,
        0,
        0,
        1,
        "SECRET-SOURCE-/private/path",
    )

    assert result.status == "failure"
    assert "SECRET" not in repr(result)
    assert "/private/path" not in repr(result)


def test_public_result_preserves_every_valid_boundary_field_identically() -> None:
    service = "s" * MAX_ID_BYTES
    child = f"{_card_key(MAX_VALUE_BYTES - 2 - len(service))}--{service}"
    blocked = frozenset({_card_key(MAX_VALUE_BYTES), child})
    status = "".join(("suc", "cess"))
    category = "".join(("repository_", "invalid"))
    ref = f"service:{service}"
    result = _result(
        blocked_identifiers=blocked,
        routed_count=MAX_COORDINATORS,
        review_count=MAX_COORDINATORS,
        child_count=MAX_CHILDREN,
        failure_count=MAX_COORDINATORS,
        status=status,
        error_category=category,
        error_ref=ref,
    )

    assert result.blocked_identifiers is blocked
    assert result.status is status
    assert result.error_category is category
    assert result.error_ref is ref
    assert result.routed_count == MAX_COORDINATORS
    assert result.review_count == MAX_COORDINATORS
    assert result.child_count == MAX_CHILDREN
    assert result.failure_count == MAX_COORDINATORS


def test_public_result_accepts_status_error_and_blocked_cardinality_boundaries() -> None:
    blocked = frozenset(f"A-{index}" for index in range(1, MAX_COORDINATORS + MAX_CHILDREN + 1))
    for status in ("disabled", "success", "review", "failure"):
        assert _result(status=status, blocked_identifiers=blocked).status == status
    for category in _FAILURE_CATEGORIES:
        result = _result(status="failure", error_category=category)
        assert result.error_category is category
        assert result.error_ref is None

    card_ref = f"card:{_card_key(MAX_VALUE_BYTES)}"
    card = _result(status="failure", error_category="source_invalid", error_ref=card_ref)
    assert card.error_ref is card_ref
    assert len(card.blocked_identifiers) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enabled", 1),
        ("global_allow_dispatch", 1),
        ("routed_count", True),
        ("routed_count", _IntSubclass(1)),
        ("routed_count", -1),
        ("routed_count", MAX_COORDINATORS + 1),
        ("review_count", True),
        ("review_count", _IntSubclass(1)),
        ("review_count", -1),
        ("review_count", MAX_COORDINATORS + 1),
        ("child_count", True),
        ("child_count", _IntSubclass(1)),
        ("child_count", -1),
        ("child_count", MAX_CHILDREN + 1),
        ("failure_count", True),
        ("failure_count", _IntSubclass(1)),
        ("failure_count", -1),
        ("failure_count", MAX_COORDINATORS + 1),
    ],
)
def test_public_result_normalizes_non_exact_or_out_of_range_scalars(
    field: str,
    value: object,
) -> None:
    _assert_canonical_failure(_result(**{field: value}))


@pytest.mark.parametrize(
    "status",
    [
        "unknown",
        _StringSubclass("success"),
        _UnhashableString("success"),
        ["success"],
        {"status": "success"},
    ],
)
def test_public_result_normalizes_malformed_status_without_raising(status: object) -> None:
    _assert_canonical_failure(_result(status=status))


@pytest.mark.parametrize(
    "blocked",
    [
        {"A-1"},
        ["A-1"],
        _FrozenSetSubclass({"A-1"}),
        frozenset(f"A-{index}" for index in range(1, MAX_COORDINATORS + MAX_CHILDREN + 2)),
        frozenset({1}),
        frozenset({_StringSubclass("A-1")}),
        frozenset({"a-1"}),
        frozenset({"A-0"}),
        frozenset({"../A-1"}),
        frozenset({"A-1\n"}),
        frozenset({"Á-1"}),
        frozenset({"A-1viewer-api"}),
        frozenset({"A-1--viewer-api--extra"}),
        frozenset({"A-1--"}),
        frozenset({"A-1--VIEWER"}),
        frozenset({_card_key(MAX_VALUE_BYTES + 1)}),
        frozenset({f"A-1--{'s' * (MAX_ID_BYTES + 1)}"}),
        frozenset({f"{_card_key(MAX_VALUE_BYTES - MAX_ID_BYTES - 1)}--{'s' * MAX_ID_BYTES}"}),
    ],
)
def test_public_result_normalizes_malformed_blocked_identifiers(
    blocked: object,
) -> None:
    _assert_canonical_failure(_result(blocked_identifiers=blocked))


@pytest.mark.parametrize(
    ("category", "ref"),
    [
        ("unknown", None),
        (_StringSubclass("internal_error"), None),
        (_UnhashableString("internal_error"), None),
        (["internal_error"], None),
        ({"category": "internal_error"}, None),
        ({"internal_error"}, None),
        (None, "card:A-1"),
        ("config_invalid", "service:viewer-api"),
        ("source_invalid", "card:../A-1"),
        ("source_invalid", "card:a-1"),
        ("source_invalid", f"card:{_card_key(MAX_VALUE_BYTES + 1)}"),
        ("repository_invalid", "service:../viewer-api"),
        ("repository_invalid", "service:VIEWER"),
        ("repository_invalid", f"service:{'s' * (MAX_ID_BYTES + 1)}"),
        ("repository_invalid", _StringSubclass("service:viewer-api")),
        ("repository_invalid", ["service:viewer-api"]),
        ("repository_invalid", {"ref": "service:viewer-api"}),
        ("repository_invalid", {"service:viewer-api"}),
    ],
)
def test_public_result_normalizes_invalid_error_pairs_without_raising(
    category: object,
    ref: object,
) -> None:
    _assert_canonical_failure(_result(error_category=category, error_ref=ref))


def test_public_result_combined_verifier_payload_is_atomically_sanitized() -> None:
    hostile = _HostileValue()
    result = _result(
        enabled=1,
        global_allow_dispatch=0,
        blocked_identifiers=frozenset({"../../SECRET-CARD"}),
        routed_count="TOP-SECRET-/private/count",
        review_count=-7,
        child_count=hostile,
        failure_count=MAX_COORDINATORS + 1,
        status=_UnhashableString("failure"),
        error_category={"payload": "SECRET"},
        error_ref=["service:../../SECRET"],
    )

    _assert_canonical_failure(result)
    rendered = repr(result)
    assert hash(result)
    assert hostile.repr_calls == 0
    assert "SECRET" not in rendered
    assert "/private" not in rendered


def test_public_result_is_frozen_and_replace_revalidates_the_whole_value() -> None:
    result = _result()
    with pytest.raises(FrozenInstanceError):
        result.status = "failure"  # type: ignore[misc]

    replaced = replace(result, routed_count="TOP-SECRET-/private/count")
    _assert_canonical_failure(replaced)
    assert "SECRET" not in repr(replaced)


@pytest.mark.parametrize(
    ("category", "identifier"),
    [
        ("unknown", "service:viewer-api"),
        (_StringSubclass("git_command_failed"), "service:viewer-api"),
        (_UnhashableString("git_command_failed"), "service:viewer-api"),
        (["git_command_failed"], "service:viewer-api"),
        ({"category": "git_command_failed"}, "service:viewer-api"),
        ({"git_command_failed"}, "service:viewer-api"),
        ("git_command_failed", _StringSubclass("service:viewer-api")),
        ("git_command_failed", ["service:viewer-api"]),
        ("git_command_failed", {"ref": "service:viewer-api"}),
        ("git_command_failed", {"service:viewer-api"}),
    ],
)
def test_public_failure_is_total_for_non_exact_and_unhashable_inputs(
    category: object,
    identifier: object,
) -> None:
    failure = AidtRoutingFailure(category, identifier)  # type: ignore[arg-type]
    expected = (
        category
        if type(category) is str and category != "unknown"
        else "internal_error"
    )

    assert failure.category == expected
    assert failure.identifier is None
    assert failure.args == (failure.category,)
    assert "viewer-api" not in repr(failure)


def test_public_failure_preserves_exact_valid_category_refs() -> None:
    service_ref = f"service:{'s' * MAX_ID_BYTES}"
    card_ref = f"card:{_card_key(MAX_VALUE_BYTES)}"
    service = AidtRoutingFailure("git_command_failed", service_ref)
    card = AidtRoutingFailure("source_invalid", card_ref)

    assert service.category == "git_command_failed"
    assert service.identifier is service_ref
    assert service.args == ("git_command_failed",)
    assert card.category == "source_invalid"
    assert card.identifier is card_ref
