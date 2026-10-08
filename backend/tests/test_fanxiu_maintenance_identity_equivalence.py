"""Unresolved terminal maintenance identities share a blocking meaning."""
import pytest

from backend.core.fanxiu.data_annotation.maintenance import resolve_maintenance_identity_ambiguity


@pytest.mark.parametrize('ids,expected', [
    ([415, 546], 546), ([546, 415, 546], 546),
    ([], None), ([546], None), ([415, 546, 760], None), ([18, 34], None),
])
def test_maintenance_identity_equivalence(ids, expected):
    assert resolve_maintenance_identity_ambiguity(iter(ids)) == expected
