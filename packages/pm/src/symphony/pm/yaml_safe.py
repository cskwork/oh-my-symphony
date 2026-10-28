"""Safe YAML loading with duplicate-key rejection and C acceleration."""

from __future__ import annotations

from typing import Any

import yaml
from yaml.constructor import ConstructorError

_LOADERS: dict[type[Any], type[Any]] = {}


def _unique_key_loader() -> type[Any]:
    base: type[Any] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    cached = _LOADERS.get(base)
    if cached is not None:
        return cached
    loader: type[Any] = type("_UniqueKeyLoader", (base,), {})
    loader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _construct_unique_mapping,
    )
    _LOADERS[base] = loader
    return loader


def _construct_unique_mapping(
    loader: Any, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


def load_unique_yaml(source: str) -> Any:
    return yaml.load(source, Loader=_unique_key_loader())
