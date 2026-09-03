from backend.core.optimistic_mutation import changed_fields_from_request, stale_field_conflicts


class Resource:
    title = "server title"
    content = "server content"


def test_changed_fields_excludes_mutation_context():
    assert changed_fields_from_request(
        {
            "title": "next",
            "base_version": 2,
            "expected_fields": {"title": "old"},
            "mutation_id": "mutation-1",
            "client_instance_id": "page-1",
        }
    ) == {"title": "next"}


def test_expected_fields_detect_same_field_change_even_if_transport_version_was_refreshed():
    assert stale_field_conflicts(
        Resource(),
        {"title": "local title"},
        {"title": "old title"},
    ) == ["title"]


def test_unrelated_field_change_can_replay():
    assert stale_field_conflicts(
        Resource(),
        {"content": "local content"},
        {"content": "server content"},
    ) == []


def test_already_applied_target_is_idempotent_after_lost_response():
    assert stale_field_conflicts(
        Resource(),
        {"title": "server title"},
        {"title": "old title"},
    ) == []
