from __future__ import annotations

from types import SimpleNamespace

from backend.api import note_sheets


def test_registration_run_state_survives_memory_cache_reset(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        note_sheets,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    run = {
        "run_id": "registration123",
        "sheet_id": 17,
        "action": note_sheets.NOTE_SHEET_CELL_ACTION_REGISTRATION_COMPOSITE_UPDATE,
        "status": "running",
        "queued_at": 10.0,
        "cancel_requested": False,
        "updated_count": 3,
    }
    note_sheets._write_note_sheet_run_state("registration-match", run)
    note_sheets._REGISTRATION_MATCH_RUNS.clear()
    note_sheets._REGISTRATION_MATCH_ACTIVE_BY_KEY.clear()

    restored = note_sheets._get_registration_match_run_snapshot("registration123")
    active = note_sheets._get_active_registration_match_run_snapshot(17)

    assert restored == run
    assert active == run


def test_clockin_run_updates_are_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        note_sheets,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    run = {
        "run_id": "clockin123",
        "sheet_id": 18,
        "status": "pending",
        "queued_at": 11.0,
        "cancel_requested": False,
    }
    note_sheets._write_note_sheet_run_state("clockin-link-detection", run)

    note_sheets._update_clockin_link_detection_run(
        "clockin123",
        status="running",
        processed_count=4,
    )
    note_sheets._CLOCKIN_LINK_DETECTION_RUNS.clear()

    restored = note_sheets._get_clockin_link_detection_run_snapshot("clockin123")
    assert restored is not None
    assert restored["status"] == "running"
    assert restored["processed_count"] == 4
