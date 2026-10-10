"""Pure decoded-reference and bounded ancestry contracts, without game state."""
import pytest

from backend.core.fanxiu.catalog.resources import _ui_rect_ancestry, _ui_node_inspection_complete


def rect(parent=0, file_id=0):
    return {'m_Father': {'m_FileID': file_id, 'm_PathID': parent}}


@pytest.mark.parametrize('transforms,start,complete,length', [
    ({1: rect()}, 1, True, 1),
    ({}, 1, False, 0),
    ({1: rect(2)}, 1, False, 1),
    ({1: rect(2, 1)}, 1, False, 1),
    ({1: rect(1)}, 1, False, 1),
    ({1: rect(2), 2: rect(1)}, 1, False, 2),
    ({i: rect(i+1) for i in range(1, 34)}, 1, False, 32),
])
def test_rect_ancestry_never_claims_unresolved_chain_complete(transforms, start, complete, length):
    result = _ui_rect_ancestry(start, transforms, {})
    assert result['complete_local_chain'] is complete
    assert len(result['nodes']) == length


@pytest.mark.parametrize('component,ancestry,errors,missing,complete', [
    ({'type': 'RectTransform'}, {'complete_local_chain': True}, [], [], True),
    ({'type': None}, {'complete_local_chain': True}, [], [], False),
    ({'type': None, 'decode_error': 'external_component_reference'}, {'complete_local_chain': True}, [], [], False),
    ({'type': 'RectTransform'}, None, [], [], False),
    ({'type': 'RectTransform'}, {'complete_local_chain': False}, [], [], False),
    ({'type': 'RectTransform'}, {'complete_local_chain': True}, [{'type': 'RectTransform'}], [], False),
    ({'type': 'RectTransform'}, {'complete_local_chain': True}, [], ['missing'], False),
])
def test_component_completeness_requires_resolved_references_and_ancestry(component, ancestry, errors, missing, complete):
    node = {'components': [component], 'rect_ancestry': ancestry}
    assert _ui_node_inspection_complete([node], errors, missing) is complete
