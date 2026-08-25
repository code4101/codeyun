"""Shared optimistic-write helpers for resource mutation endpoints.

Whole-resource versions remain useful for ordering and cache invalidation, but a
stale version is not itself a conflict. A mutation only conflicts when one of
the fields it intends to replace no longer has the value the editor observed.
"""

from __future__ import annotations

from typing import Any, Mapping


MUTATION_CONTEXT_FIELDS = {"base_version", "expected_fields", "mutation_id", "client_instance_id"}


def changed_fields_from_request(raw_request: Mapping[str, Any]) -> dict[str, Any]:
    """Return domain fields, excluding transport/concurrency metadata."""

    return {key: value for key, value in raw_request.items() if key not in MUTATION_CONTEXT_FIELDS}


def stale_field_conflicts(
    resource: object,
    updates: Mapping[str, Any],
    expected_fields: Mapping[str, Any] | None,
) -> list[str]:
    """Return fields that cannot be safely replayed on the latest resource."""

    expected = dict(expected_fields or {})
    conflicts: list[str] = []
    for field_name in updates:
        if field_name not in expected or not hasattr(resource, field_name):
            conflicts.append(field_name)
            continue
        if getattr(resource, field_name) != expected[field_name]:
            conflicts.append(field_name)
    return conflicts
