from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest
from sqlalchemy import inspect
from sqlmodel import Session, create_engine, select

from backend.plugins.modules.media_sync import models
from backend.plugins.modules.media_sync import sources


@pytest.fixture(autouse=True)
def no_pixiv_request_delay(tmp_path, monkeypatch):
    circuit_path = tmp_path / "pixiv-risk-circuit.json"
    monkeypatch.setattr(sources, "PIXIV_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(sources, "PIXIV_REQUEST_JITTER_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(sources, "_pixiv_last_request_at", 0.0)
    monkeypatch.setattr(sources, "pixiv_risk_circuit_path", lambda: circuit_path)
    return circuit_path


@pytest.fixture
def isolated_pixiv_risk_circuit(no_pixiv_request_delay):
    return no_pixiv_request_delay


def test_pixiv_remote_budget_stops_before_operation_over_limit():
    with pytest.raises(sources.PixivRemoteOperationBudgetExceeded):
        with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=2) as audit:
            sources.wait_for_pixiv_request_slot("page_navigation")
            sources.wait_for_pixiv_request_slot("detail_api")
            sources.wait_for_pixiv_request_slot("download")

    snapshot = audit.snapshot()
    assert snapshot["remote_operations_total"] == 2
    assert snapshot["operation_counts"] == {"detail_api": 1, "page_navigation": 1}
    assert snapshot["stop_reason"] == "remote_operation_budget_exhausted"
    assert "2/2" in snapshot["error"]


def test_pixiv_remote_budget_is_shared_with_worker_threads():
    errors: list[Exception] = []

    def authorize_download() -> None:
        try:
            sources.wait_for_pixiv_request_slot("download")
        except Exception as exc:  # pragma: no cover - assertion checks the concrete error below
            errors.append(exc)

    with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=1) as audit:
        first = threading.Thread(target=authorize_download)
        second = threading.Thread(target=authorize_download)
        first.start()
        first.join()
        second.start()
        second.join()

    assert audit.snapshot()["remote_operations_total"] == 1
    assert len(errors) == 1
    assert isinstance(errors[0], sources.PixivRemoteOperationBudgetExceeded)


@pytest.mark.parametrize("budget", [0, 501])
def test_pixiv_remote_budget_rejects_unsafe_limits(budget):
    with pytest.raises(ValueError):
        with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=budget):
            pass


def test_pixiv_risk_circuit_persists_and_blocks_before_next_operation(isolated_pixiv_risk_circuit):
    with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=2) as audit:
        sources.wait_for_pixiv_request_slot("detail_api")
        sources.trip_pixiv_risk_circuit(
            reason="http_rate_limited",
            signal="HTTP 429",
            operation_kind="detail_api",
            status_code=429,
            url="https://www.pixiv.net/ajax/illust/1",
        )
        with pytest.raises(sources.PixivRiskCircuitOpen):
            sources.wait_for_pixiv_request_slot("download")

    snapshot = audit.snapshot()
    assert isolated_pixiv_risk_circuit.exists()
    assert snapshot["remote_operations_total"] == 1
    assert snapshot["stop_reason"] == "risk_circuit_tripped"
    assert snapshot["risk_circuit"]["reason"] == "http_rate_limited"


@dataclass
class FakeResponse:
    status_code: int
    url: str
    text: str = ""


@dataclass
class FakeBody:
    text: str


@dataclass
class FakeTab:
    url: str
    body_text: str

    def ele(self, selector, timeout=None):
        del timeout
        return FakeBody(self.body_text) if selector == "tag:body" else None


def test_pixiv_http_429_trips_persistent_circuit(isolated_pixiv_risk_circuit):
    response = FakeResponse(status_code=429, url="https://www.pixiv.net/ajax/top/illust")

    with pytest.raises(sources.PixivRiskCircuitOpen):
        sources.raise_if_pixiv_http_risk(response, operation_kind="recommend_api", inspect_body=True)

    state = sources.read_pixiv_risk_circuit()
    assert state is not None
    assert state["reason"] == "http_rate_limited"
    assert state["status_code"] == 429


def test_pixiv_browser_account_warning_trips_persistent_circuit(
    isolated_pixiv_risk_circuit,
    monkeypatch,
):
    tab = FakeTab(url="https://www.pixiv.net/", body_text="Suspicious activity detected")
    monkeypatch.setattr(sources, "import_system_chrome_debug_cookies_to_tab", lambda *_args, **_kwargs: False)

    with pytest.raises(sources.BrowserActionRequiredError):
        sources.raise_if_browser_action_required(tab, context="Pixiv 首页")

    state = sources.read_pixiv_risk_circuit()
    assert state is not None
    assert state["reason"] == "account_warning"


def test_pixiv_home_collection_stops_immediately_after_circuit_trip(monkeypatch):
    calls: list[str] = []

    def fake_fetch(_session, *, artwork_id, lang):
        del lang
        calls.append(artwork_id)
        raise sources.PixivRiskCircuitOpen("blocked")

    monkeypatch.setattr(sources, "fetch_pixiv_illust_detail", fake_fetch)

    with pytest.raises(sources.PixivRiskCircuitOpen):
        sources.collect_pixiv_home_items(
            object(),
            artwork_urls=[
                "https://www.pixiv.net/artworks/100",
                "https://www.pixiv.net/artworks/200",
            ],
            source_kind="home_recommend",
            log=lambda _message: None,
        )

    assert calls == ["100"]


def test_pixiv_home_collection_skips_known_ids_before_detail_request(monkeypatch):
    calls: list[str] = []
    stats: dict[str, int] = {}

    def fake_fetch(_session, *, artwork_id, lang):
        del lang
        calls.append(artwork_id)
        return {
            "id": artwork_id,
            "title": f"artwork-{artwork_id}",
            "userId": "9",
            "userName": "tester",
            "pageCount": 1,
        }

    monkeypatch.setattr(sources, "fetch_pixiv_illust_detail", fake_fetch)
    known_ids = {"100", "200"}

    items = sources.collect_pixiv_home_items(
        object(),
        artwork_urls=[
            "https://www.pixiv.net/artworks/100",
            "https://www.pixiv.net/artworks/200",
            "https://www.pixiv.net/artworks/300",
        ],
        source_kind="home_recommend",
        log=lambda _message: None,
        known_artwork_ids=known_ids,
        stats=stats,
    )

    assert calls == ["300"]
    assert [item["artwork_id"] for item in items] == ["300"]
    assert known_ids == {"100", "200", "300"}
    assert stats == {
        "discovered_artwork_ids": 3,
        "historical_hits": 2,
        "detail_requests": 1,
        "detail_failures": 0,
    }


def test_pixiv_home_collection_limits_new_detail_requests(monkeypatch):
    calls: list[str] = []

    def fake_fetch(_session, *, artwork_id, lang):
        del lang
        calls.append(artwork_id)
        return {
            "id": artwork_id,
            "title": f"artwork-{artwork_id}",
            "userId": "9",
            "userName": "tester",
            "pageCount": 1,
        }

    monkeypatch.setattr(sources, "fetch_pixiv_illust_detail", fake_fetch)

    items = sources.collect_pixiv_home_items(
        object(),
        artwork_urls=[
            "https://www.pixiv.net/artworks/100",
            "https://www.pixiv.net/artworks/200",
            "https://www.pixiv.net/artworks/300",
        ],
        source_kind="home_following",
        log=lambda _message: None,
        max_new_items=2,
    )

    assert calls == ["100", "200"]
    assert [item["artwork_id"] for item in items] == ["100", "200"]


def test_pixiv_author_catalog_uses_one_budgeted_request():
    class AuthorCatalogResponse:
        status_code = 200
        url = "https://www.pixiv.net/ajax/user/9/profile/all"
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "error": False,
                "body": {
                    "illusts": {"300": None, "100": None},
                    "manga": {"200": None},
                },
            }

    class AuthorCatalogSession:
        def get(self, *_args, **_kwargs):
            return AuthorCatalogResponse()

    with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=2) as audit:
        artwork_ids = sources.fetch_pixiv_author_work_ids(AuthorCatalogSession(), author_id="9")

    assert artwork_ids == ["100", "200", "300"]
    assert audit.snapshot()["operation_counts"] == {"author_catalog_api": 1}


def test_pixiv_author_scan_keeps_history_and_new_edge_independent():
    first_state = models.MediaSyncPixivAuthorState(user_id=2, pixiv_author_id="9")

    first_plan = sources.plan_pixiv_author_catalog_scan(
        first_state,
        catalog_artwork_ids=["300", "100", "200"],
        known_artwork_ids={"100"},
        limit=1,
    )

    assert first_plan["history_catalog_ceiling_artwork_id"] == "300"
    assert first_plan["incremental_ids"] == []
    assert first_plan["history_ids"] == ["200"]
    assert first_plan["history_cursor_after_success"] == "200"
    assert first_plan["history_exhausted_after_success"] is False

    next_state = models.MediaSyncPixivAuthorState(
        user_id=2,
        pixiv_author_id="9",
        history_catalog_ceiling_artwork_id="300",
        history_cursor_artwork_id="200",
        latest_catalog_artwork_id="300",
    )
    next_plan = sources.plan_pixiv_author_catalog_scan(
        next_state,
        catalog_artwork_ids=["100", "200", "300", "400", "500"],
        known_artwork_ids={"100", "200", "400"},
        limit=2,
    )

    assert next_plan["incremental_ids"] == ["500"]
    assert next_plan["history_ids"] == ["300"]
    assert next_plan["history_cursor_after_success"] == "300"
    assert next_plan["history_exhausted_after_success"] is True


def test_pixiv_candidate_registration_also_discovers_author(engine, monkeypatch):
    monkeypatch.setattr(sources, "engine", engine)

    sources.upsert_pixiv_candidate_source_items(
        user_id=999,
        source_kind="home_recommend",
        collection_url=sources.PIXIV_HOME_RECOMMEND_COLLECTION_URL,
        artwork_rows=[
            {
                "artwork_id": "100",
                "artwork_url": "https://www.pixiv.net/artworks/100",
                "user_id": "9",
                "user_name": "tester",
                "create_date": "2020-01-01T00:00:00+00:00",
            }
        ],
    )

    with Session(engine) as session:
        author = session.exec(
            select(models.MediaSyncPixivAuthorState).where(
                models.MediaSyncPixivAuthorState.user_id == 999,
                models.MediaSyncPixivAuthorState.pixiv_author_id == "9",
            )
        ).one()
        item = session.exec(
            select(models.MediaSyncSourceItem).where(
                models.MediaSyncSourceItem.user_id == 999,
                models.MediaSyncSourceItem.remote_id == "100",
            )
        ).first()

    assert author.author_name == "tester"
    assert author.priority == 20
    assert item is None


def test_pixiv_author_state_can_be_loaded_and_saved_across_sessions(engine, monkeypatch):
    monkeypatch.setattr(sources, "engine", engine)
    sources.register_pixiv_authors(
        user_id=999,
        artwork_rows=[{"user_id": "9", "user_name": "followed"}],
        source_kind="home_following",
        seen_at=100.0,
    )
    sources.register_pixiv_authors(
        user_id=999,
        artwork_rows=[{"user_id": "10", "user_name": "recommended"}],
        source_kind="home_recommend",
        seen_at=100.0,
    )

    due = sources.load_due_pixiv_author_states(user_id=999, limit=1, at=200.0)
    assert [state.pixiv_author_id for state in due] == ["9"]

    state = due[0]
    state.history_catalog_ceiling_artwork_id = "300"
    state.history_cursor_artwork_id = "100"
    state.last_catalog_scan_at = 200.0
    state.next_catalog_scan_at = 300.0
    sources.save_pixiv_author_state(state)

    assert state.author_name == "followed"
    assert sources.load_due_pixiv_author_states(user_id=999, limit=2, at=250.0)[0].pixiv_author_id == "10"
    with Session(engine) as session:
        saved = session.exec(
            select(models.MediaSyncPixivAuthorState).where(
                models.MediaSyncPixivAuthorState.user_id == 999,
                models.MediaSyncPixivAuthorState.pixiv_author_id == "9",
            )
        ).one()
    assert saved.history_catalog_ceiling_artwork_id == "300"
    assert saved.history_cursor_artwork_id == "100"


def test_pixiv_author_default_scan_batch_covers_twelve_authors(engine, monkeypatch):
    monkeypatch.setattr(sources, "engine", engine)
    sources.register_pixiv_authors(
        user_id=999,
        artwork_rows=[
            {"user_id": str(author_id), "user_name": f"author-{author_id}"}
            for author_id in range(1, 14)
        ],
        source_kind="home_following",
        seen_at=100.0,
    )

    due = sources.load_due_pixiv_author_states(user_id=999, at=200.0)

    assert len(due) == 12


def test_pixiv_author_queue_ignores_placeholder_author_ids(engine, monkeypatch):
    monkeypatch.setattr(sources, "engine", engine)

    result = sources.register_pixiv_authors(
        user_id=999,
        artwork_rows=[
            {"user_id": "0", "user_name": "-----"},
            {"user_id": "unknown", "user_name": "unknown"},
            {"user_id": "9", "user_name": "valid"},
        ],
        source_kind="bookmark",
        seen_at=100.0,
    )

    assert result == {"added": 1, "updated": 0}
    assert [state.pixiv_author_id for state in sources.load_due_pixiv_author_states(user_id=999, at=200.0)] == ["9"]


@pytest.mark.parametrize(
    ("target_count", "author_count", "expected"),
    [
        (190, 12, 16),
        (20, 12, 2),
        (200, 1, 200),
        (0, 12, 0),
        (200, 0, 0),
    ],
)
def test_pixiv_author_candidates_are_balanced_across_the_selected_authors(
    target_count: int,
    author_count: int,
    expected: int,
):
    assert sources.pixiv_author_candidate_limit(
        target_count=target_count,
        author_count=author_count,
    ) == expected


def test_pixiv_source_item_schema_migrates_detail_refresh_columns(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy-media-sync.db"
    isolated_engine = create_engine(f"sqlite:///{database_path}")
    with isolated_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE private_media_sync_source_item (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                platform VARCHAR NOT NULL,
                remote_id VARCHAR NOT NULL,
                media_index INTEGER NOT NULL,
                extra_json JSON NOT NULL DEFAULT '{}'
            )
            """
        )

    monkeypatch.setattr(models, "engine", isolated_engine)
    models.ensure_private_media_sync_schema()

    column_names = {
        column["name"]
        for column in inspect(isolated_engine).get_columns("private_media_sync_source_item")
    }
    assert {"last_detail_fetched_at", "next_refresh_at", "last_seen_at"} <= column_names
    assert "private_media_sync_pixiv_author_state" in inspect(isolated_engine).get_table_names()


def test_pixiv_detail_discovery_does_not_create_url_only_source_item(engine, monkeypatch):
    monkeypatch.setattr(sources, "engine", engine)
    monkeypatch.setattr(sources, "get_device_id", lambda: "pytest-device")
    fetched_at = 1_000.0
    next_refresh_at = 2_000.0

    result = sources.upsert_pixiv_candidate_source_items(
        user_id=999,
        source_kind="home_following",
        collection_url=sources.PIXIV_HOME_FOLLOWING_COLLECTION_URL,
        artwork_rows=[
            {
                "artwork_id": "100",
                "artwork_url": "https://www.pixiv.net/artworks/100",
                "thumbnail_url": "https://i.pximg.net/100.jpg",
                "last_detail_fetched_at": fetched_at,
                "next_refresh_at": next_refresh_at,
            }
        ],
    )

    with Session(engine) as session:
        item = session.exec(
            select(models.MediaSyncSourceItem).where(
                models.MediaSyncSourceItem.user_id == 999,
                models.MediaSyncSourceItem.platform == "pixiv",
                models.MediaSyncSourceItem.remote_id == "100",
            )
        ).first()
        legacy_item = models.MediaSyncSourceItem(
            user_id=999,
            platform="pixiv",
            remote_id="200",
            media_index=0,
        )
        secondary_media_item = models.MediaSyncSourceItem(
            user_id=999,
            platform="pixiv",
            remote_id="100",
            media_index=1,
        )
        session.add(legacy_item)
        session.add(secondary_media_item)
        session.commit()
        persisted_detail_fetched_at = item.last_detail_fetched_at if item else None
        persisted_next_refresh_at = item.next_refresh_at if item else None

    assert result == {"added": 0, "updated": 0}
    assert persisted_detail_fetched_at is None
    assert persisted_next_refresh_at is None
    assert sources.get_detail_fresh_remote_ids(user_id=999, platform="pixiv", at=1_500.0) == {"100", "200"}


def test_pixiv_known_artwork_sighting_updates_audit_metadata_without_creating_candidates(
    engine,
    monkeypatch,
):
    monkeypatch.setattr(sources, "engine", engine)
    with Session(engine) as session:
        session.add(
            models.MediaSyncSourceItem(
                user_id=999,
                platform="pixiv",
                source_kind="home_recommend",
                collection_url=sources.PIXIV_HOME_RECOMMEND_COLLECTION_URL,
                remote_id="100",
                media_index=0,
                first_seen_at=100.0,
                last_seen_at=100.0,
                extra_json={"seen_source_kinds": ["home_recommend"]},
            )
        )
        session.commit()

    result = sources.mark_pixiv_artwork_ids_seen(
        user_id=999,
        source_kind="home_following",
        collection_url=sources.PIXIV_HOME_FOLLOWING_COLLECTION_URL,
        artwork_ids=["100", "200", "100"],
        seen_at=200.0,
    )

    with Session(engine) as session:
        rows = session.exec(
            select(models.MediaSyncSourceItem).where(
                models.MediaSyncSourceItem.user_id == 999,
                models.MediaSyncSourceItem.platform == "pixiv",
            )
        ).all()

    assert result == {"seen": 2, "matched": 1}
    assert len(rows) == 1
    assert rows[0].last_seen_at == 200.0
    assert rows[0].extra_json["last_seen_source_kind"] == "home_following"
    assert rows[0].extra_json["last_seen_collection_url"] == sources.PIXIV_HOME_FOLLOWING_COLLECTION_URL
    assert rows[0].extra_json["seen_source_kinds"] == ["home_following", "home_recommend"]


def test_pixiv_home_sync_rejects_collect_only_mode(monkeypatch):
    request_slots: list[str] = []

    class FakeConnection:
        def close(self):
            return None

    class FakeStore:
        target_root = "unused"

        def connect_db(self):
            return FakeConnection()

        def get_status_counts(self):
            return {}

    class FakeBrowser:
        def new_tab(self, _url):
            return object()

    fake_store = FakeStore()
    monkeypatch.setattr(sources, "backfill_pixiv_source_items", lambda *_args, **_kwargs: {"added": 0, "updated": 0})
    monkeypatch.setattr(sources, "create_pixiv_home_recommend_store", lambda _root: fake_store)
    monkeypatch.setattr(sources, "create_pixiv_home_following_store", lambda _root: fake_store)
    monkeypatch.setattr(sources, "get_downloaded_remote_ids", lambda **_kwargs: set())
    monkeypatch.setattr(sources, "get_detail_fresh_remote_ids", lambda **_kwargs: {"100"})
    monkeypatch.setattr(
        sources,
        "mark_pixiv_artwork_ids_seen",
        lambda **_kwargs: {"seen": 1, "matched": 1},
    )
    monkeypatch.setattr(sources, "open_browser", lambda *, headless: FakeBrowser())
    monkeypatch.setattr(sources, "wait_for_pixiv_request_slot", request_slots.append)
    monkeypatch.setattr(sources, "chromium_error_page_message", lambda _tab: None)
    monkeypatch.setattr(sources, "raise_if_browser_action_required", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sources, "create_pixiv_session", lambda _tab: object())
    monkeypatch.setattr(
        sources,
        "extract_pixiv_section_artwork_urls",
        lambda *_args, **_kwargs: ["https://www.pixiv.net/artworks/100"],
    )
    monkeypatch.setattr(sources, "navigate_pixiv_tab", lambda browser, _url, *, tab: (browser, tab))
    monkeypatch.setattr(sources, "extract_pixiv_page_artwork_urls", lambda _tab: [])
    monkeypatch.setattr(sources, "keep_one_domain_tab", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="不再支持仅采集 URL"):
        sources.run_pixiv_home_sync(
            user_id=2,
            root_dir="unused",
            download_limit=1,
            log=lambda _message: None,
            headless=True,
            download_items=False,
        )
    assert request_slots == []


def test_pixiv_download_success_persists_file_facts_without_urls(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "engine", engine)
    monkeypatch.setattr(sources, "get_device_id", lambda: "pytest-device")
    target = tmp_path / "pixiv"
    target.mkdir()
    (target / "100_p0.jpg").write_bytes(b"image")

    sources.upsert_pixiv_source_items(
        user_id=999,
        source_kind="home_recommend",
        collection_url=sources.PIXIV_HOME_RECOMMEND_COLLECTION_URL,
        artwork_id="100",
        artwork_url="https://www.pixiv.net/artworks/100",
        parent_artwork_id=None,
        target_root=target,
        pages=[{"page_index": 0, "relative_path": "100_p0.jpg", "original_url": "https://i.pximg.net/100.jpg"}],
    )

    with Session(engine) as session:
        item = session.exec(select(models.MediaSyncSourceItem)).one()
    assert item.downloaded_at is not None
    assert item.absolute_path == str(target / "100_p0.jpg")
    assert item.content_hash
    assert item.remote_url == ""
    assert item.media_url == ""


def test_pixiv_download_failure_keeps_retryable_id_without_url_queue(engine, monkeypatch):
    monkeypatch.setattr(sources, "engine", engine)
    sources.record_pixiv_download_failure(
        user_id=999,
        artwork_id="100",
        source_kind="home_recommend",
        collection_url=sources.PIXIV_HOME_RECOMMEND_COLLECTION_URL,
        error="temporary",
    )
    with Session(engine) as session:
        item = session.exec(select(models.MediaSyncSourceItem)).one()
    assert item.remote_id == "100"
    assert item.downloaded_at is None
    assert item.absolute_path is None
    assert item.remote_url == ""
    assert item.media_url == ""
    assert item.extra_json["candidate_status"] == "error"
    assert item.extra_json["retryable"] is True


def test_pixiv_discovery_downloads_each_remote_id_once_in_current_flow(monkeypatch):
    downloaded: list[str] = []

    class FakeStore:
        def scrub_artwork_urls(self, _conn, *, artwork_id):
            return None

    def fake_download(_conn, _store, _session, artwork, _log, **kwargs):
        artwork_id = str(artwork["artwork_id"])
        downloaded.append(artwork_id)
        kwargs["downloaded_artwork_ids"].add(artwork_id)

    monkeypatch.setattr(sources, "download_pixiv_artwork", fake_download)
    result = sources.download_discovered_pixiv_items(
        object(),
        FakeStore(),
        object(),
        [{"artwork_id": "100"}, {"artwork_id": "100"}, {"artwork_id": "200"}],
        user_id=999,
        source_kind="home_recommend",
        collection_url=sources.PIXIV_HOME_RECOMMEND_COLLECTION_URL,
        log=lambda _message: None,
        downloaded_artwork_ids=set(),
    )
    assert downloaded == ["100", "200"]
    assert result["downloaded"] == 2
    assert result["duplicate"] == 1
