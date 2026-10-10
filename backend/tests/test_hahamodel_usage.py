import datetime as dt

from backend.core import hahamodel_usage as haha

TZ = dt.timezone(dt.timedelta(hours=8))


def at(day, hour=0):
    # 2026-09-28 is a Monday.
    return dt.datetime(2026, 9, day, hour, tzinfo=TZ) if day <= 30 else dt.datetime(2026, 10, day - 30, hour, tzinfo=TZ)


def parsed(points):
    return [(dt.datetime.fromisoformat(p["at"]), p["value"]) for p in points]


def hours(*items):
    return {str(int(moment.timestamp())): usd for moment, usd in items}


def test_week_starts_on_local_monday():
    assert haha.week_start_of(at(30, 15)) == at(28)
    assert haha.week_start_of(at(28)) == at(28)
    assert haha.week_start_of(at(27, 23)) == at(21)


def test_running_week_quota_is_scaled_by_elapsed_share():
    # Previous week spent $50 in total; this week $30 over the first 3 days.
    history = hours((at(22, 10), 20.0), (at(25, 9), 30.0), (at(29, 10), 10.0), (at(30, 10), 20.0))
    window = haha.build_hahamodel_weekly_window(history, now=at(31))
    previous, running = window["weeks"]
    assert (previous["used"], previous["quota"], previous["complete"]) == (50.0, 50.0, True)
    assert running["used"] == 30.0 and running["quota"] == 70.0 and not running["complete"]
    assert window["max"] == 70.0
    assert dt.datetime.fromisoformat(window["window_start"]) == at(21)
    assert dt.datetime.fromisoformat(window["window_end"]) == at(35)
    assert [p["start_value"] for p in window["periods"]] == [50.0, 70.0]


def test_points_drop_within_the_spend_hour_and_break_between_weeks():
    history = hours((at(22, 10), 20.0), (at(25, 9), 30.0), (at(29, 10), 30.0))
    window = haha.build_hahamodel_weekly_window(history, now=at(31))
    points = window["points"]
    breaks = [i for i, p in enumerate(points) if p["value"] is None]
    assert len(breaks) == 1
    first_week, second_week = parsed(points[:breaks[0]]), parsed(points[breaks[0] + 1:])
    assert first_week[0] == (at(21), 50.0)
    assert (at(22, 10), 50.0) in first_week
    assert (at(22, 11), 30.0) in first_week
    assert first_week[-1] == (at(28), 0.0)
    # The running week ends exactly on its even-burn line at "now".
    assert second_week[-1] == (at(31), 40.0)


def test_axis_max_covers_older_heavier_weeks():
    history = hours((at(8, 10), 100.0), (at(29, 10), 10.0))
    window = haha.build_hahamodel_weekly_window(history, now=at(31))
    assert window["max"] == 100.0


def test_merge_replaces_fetched_range_only(tmp_path):
    path = tmp_path / "history.json"
    haha.merge_hahamodel_history(hours((at(1), 1.0), (at(20), 2.0)), start=at(1), end=at(21), path=path)
    merged = haha.merge_hahamodel_history(hours((at(21, 5), 3.0)), start=at(14), end=at(28), path=path)
    assert merged == hours((at(1), 1.0), (at(21, 5), 3.0))


def test_hourly_rows_sum_across_models():
    rows = [
        {"created_at": 7200, "quota": 250000, "model_name": "a"},
        {"created_at": 7200, "quota": 250000, "model_name": "b"},
        {"created_at": 3600, "quota": 500000},
    ]
    assert haha.sum_hourly_usd(rows, 500000.0) == {"7200": 1.0, "3600": 1.0}


def test_missing_credentials_report_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv(haha.HAHAMODEL_USERNAME_ENV, raising=False)
    monkeypatch.delenv(haha.HAHAMODEL_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(haha, "ROOT_DIR", tmp_path)
    payload = haha.read_hahamodel_usage(now=at(31))
    assert payload["configured"] is False and not payload["available"]


def test_credentials_fall_back_to_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv(haha.HAHAMODEL_USERNAME_ENV, raising=False)
    monkeypatch.delenv(haha.HAHAMODEL_PASSWORD_ENV, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('HAHAMODEL_USERNAME="u"\nHAHAMODEL_PASSWORD="p\\"w"\n', encoding="utf-8")
    assert haha.read_hahamodel_credentials(env_file=env_file) == ("u", 'p"w')
