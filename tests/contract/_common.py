"""Shared helpers for contract tests (tests/contract/test_*.py).

Each contract test loads its corresponding JSON Schema file from
`specs/001-orchestrator-kernel/contracts/` and validates BOTH:
  1. the pydantic mirror accepts/rejects instances as expected (runtime-facing);
  2. the raw JSON Schema accepts/rejects the same instances (wire-facing).

Round-trip equivalence between the two (pydantic's `model_json_schema()` vs
`contracts/*.schema.json`) is checked by T025 in `test_round_trip.py`.

The helpers below avoid any dependency on the kernel package, so they work
even if the pydantic mirrors have not been written yet (RED phase).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "001-orchestrator-kernel"
    / "contracts"
)


def load_schema(name: str) -> dict[str, Any]:
    """Load a single schema JSON by filename (e.g. 'entry-event.schema.json')."""
    return json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _registry() -> Registry:
    """Build a referencing Registry that resolves `./*.schema.json` relative refs."""
    reg = Registry()
    base_uri = CONTRACTS_DIR.resolve().as_uri() + "/"
    for path in sorted(CONTRACTS_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource(contents=schema, specification=DRAFT202012)
        reg = reg.with_resource(uri=base_uri + path.name, resource=resource)
        if "$id" in schema:
            reg = reg.with_resource(uri=schema["$id"], resource=resource)
    return reg


def validator(name: str) -> Draft202012Validator:
    """Return a Draft202012Validator for the named schema, with cross-file $ref support."""
    schema = load_schema(name)
    base_uri = CONTRACTS_DIR.resolve().as_uri() + "/" + name
    resource = Resource(contents=schema, specification=DRAFT202012)
    registry = _registry().with_resource(uri=base_uri, resource=resource)
    return Draft202012Validator(schema, registry=registry)


def assert_json_schema_accepts(schema_name: str, instance: dict[str, Any]) -> None:
    """Fail loudly if the raw JSON Schema rejects a supposedly-valid instance."""
    errors = sorted(validator(schema_name).iter_errors(instance), key=lambda e: e.path)
    if errors:
        bullets = "\n".join(f"  - {list(e.path)}: {e.message}" for e in errors)
        raise AssertionError(
            f"Schema {schema_name} unexpectedly rejected valid instance:\n{bullets}"
        )


def assert_json_schema_rejects(
    schema_name: str, instance: dict[str, Any], *, reason_contains: str | None = None
) -> None:
    """Fail if the raw JSON Schema wrongly accepts an instance."""
    errors = list(validator(schema_name).iter_errors(instance))
    if not errors:
        raise AssertionError(
            f"Schema {schema_name} accepted an instance it should have rejected: {instance!r}"
        )
    if reason_contains:
        joined = " || ".join(e.message for e in errors)
        if reason_contains not in joined:
            raise AssertionError(
                f"Schema {schema_name} rejected the instance but not for the "
                f"expected reason {reason_contains!r}. Actual errors: {joined}"
            )
