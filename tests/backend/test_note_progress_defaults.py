from backend.core.notes.progress import (
    get_completion_progress_expr,
    is_default_full_completion_progress_expr,
    resolve_completion_progress,
)
from backend.api.notes import _prepare_note_update_data
from backend.models import NoteNode


def test_done_without_progress_expression_defaults_to_full_completion():
    assert resolve_completion_progress("done", None) == 1.0
    assert resolve_completion_progress("predone", "") == 1.0
    assert resolve_completion_progress("doing", None) is None


def test_explicit_progress_overrides_done_default():
    assert resolve_completion_progress("done", "24/392") == 24 / 392


def test_only_literal_full_progress_is_redundant():
    assert is_default_full_completion_progress_expr("1") is True
    assert is_default_full_completion_progress_expr("1.00") is True
    assert is_default_full_completion_progress_expr("100%") is True
    assert is_default_full_completion_progress_expr("24/24") is False
    assert is_default_full_completion_progress_expr("24/392") is False


def test_selecting_done_drops_only_redundant_full_progress():
    redundant = NoteNode(
        user_id=1,
        lifecycle_stage="doing",
        custom_fields=[["__completion_progress_expr", "string", "1"]],
    )
    informative = NoteNode(
        user_id=1,
        lifecycle_stage="doing",
        custom_fields=[["__completion_progress_expr", "string", "24/392"]],
    )

    redundant_update = _prepare_note_update_data(redundant, {"lifecycle_stage": "done"})
    informative_update = _prepare_note_update_data(informative, {"lifecycle_stage": "done"})

    assert get_completion_progress_expr(redundant_update["custom_fields"]) is None
    assert get_completion_progress_expr(informative_update.get("custom_fields", informative.custom_fields)) == "24/392"
