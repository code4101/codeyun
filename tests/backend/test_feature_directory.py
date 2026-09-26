from types import SimpleNamespace

import pytest

from backend.app import app
from backend.core.access.auth import get_current_active_superuser
from backend.core.access import feature_access as access
from backend.models import User


@pytest.fixture(autouse=True)
def isolated_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(access, 'get_settings', lambda: SimpleNamespace(data_dir=tmp_path))
    access.clear_feature_access_registry_cache()
    yield
    access.clear_feature_access_registry_cache()


def test_move_persists_and_recalculates_inheritance(session):
    key = 'tools.password-generator'
    access.move_feature_directory_node(key=key, target_key='ai-tools', position='inside')
    access.clear_feature_access_registry_cache()
    context = access.build_feature_access_subject_context(session, current_user=None)
    assert context['flat_items'][key]['parent_key'] == 'ai-tools'
    assert context['flat_items'][key]['effective_value'] is False
    assert context['flat_items'][key]['source'] == 'ancestor_denied'
    access.move_feature_directory_node(key=key, target_key='home', position='before')
    registry = access.load_feature_access_registry()
    assert registry.root_keys[0] == key
    assert access.build_feature_access_subject_context(session, current_user=None)['flat_items'][key]['effective_value'] is True


def test_move_subtree_and_order():
    access.move_feature_directory_node(key='tools', target_key='ai-tools', position='inside')
    registry = access.load_feature_access_registry()
    assert registry.node_map['tools.password-generator'].parent_key == 'tools'
    assert registry.node_map['tools'].parent_key == 'ai-tools'
    access.move_feature_directory_node(key='tools', target_key='home', position='after')
    registry = access.load_feature_access_registry()
    assert registry.root_keys.index('tools') == registry.root_keys.index('home') + 1


def test_project_graph_replaces_notes_canvas():
    registry = access.load_feature_access_registry()
    assert 'notes.infinite-canvas' not in registry.node_map
    assert registry.node_map['plugins.project-graph'].parent_key == 'note-tools'
    assert registry.node_map['plugins.project-graph'].sort_order == 50


def test_empty_page_container_accepts_children_again():
    registry = access.load_feature_access_registry()
    parent = 'notes.center'
    children = registry.children_map[parent]
    for child in children:
        access.move_feature_directory_node(key=child, target_key='home', position='after')
    access.clear_feature_access_registry_cache()
    assert access.load_feature_access_registry().node_map[parent].can_have_children
    access.move_feature_directory_node(key=children[0], target_key=parent, position='inside')
    assert access.load_feature_access_registry().node_map[children[0]].parent_key == parent


@pytest.mark.parametrize('key,target,position', [
    ('tools', 'tools.password-generator', 'before'),
    ('tools', 'tools', 'inside'),
    ('missing', 'tools', 'inside'),
])
def test_invalid_moves_leave_registry_unchanged(key, target, position):
    previous = access.serialize_feature_access_registry()
    with pytest.raises(ValueError):
        access.move_feature_directory_node(key=key, target_key=target, position=position)
    assert access.serialize_feature_access_registry() == previous


def test_move_endpoint_requires_admin(client):
    response = client.post('/api/admin/feature-access/directory/move', json={
        'key': 'home', 'target_key': 'tools', 'position': 'after',
    })
    assert response.status_code in (401, 403)


def test_nest_under_leaf_then_promote_subtree(session):
    access.move_feature_directory_node(key='tools', target_key='home', position='inside')
    access.clear_feature_access_registry_cache()
    context = access.build_feature_access_subject_context(session, current_user=None)
    assert context['flat_items']['tools']['parent_key'] == 'home'
    assert context['flat_items']['tools.password-generator']['parent_key'] == 'tools'
    access.move_feature_directory_node(key='tools.password-generator', target_key='tools', position='after')
    registry = access.load_feature_access_registry()
    assert registry.node_map['tools.password-generator'].parent_key == 'home'
    access.move_feature_directory_node(key='tools', target_key='home', position='after')
    assert access.load_feature_access_registry().node_map['tools'].parent_key is None


def test_move_endpoint_and_context(client):
    admin = User(id=999, username='admin', hashed_password='pw', is_superuser=True)
    app.dependency_overrides[get_current_active_superuser] = lambda: admin
    try:
        response = client.post('/api/admin/feature-access/directory/move', json={
            'key': 'home', 'target_key': 'tools', 'position': 'inside',
        })
        assert response.status_code == 200
        context = client.get('/api/admin/feature-access/subjects/anonymous').json()
        assert context['flat_items']['home']['parent_key'] == 'tools'
        response = client.post('/api/admin/feature-access/directory/move', json={
            'key': 'tools', 'target_key': 'home', 'position': 'inside',
        })
        assert response.status_code == 400
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)
