from __future__ import annotations

from backend.core.media_sync_worker import _worker_lanes_conflict, media_sync_worker_lane


def lane(*sources: str) -> str:
    return media_sync_worker_lane({"requested_sources": list(sources)})


def test_platform_curation_lanes_are_independent() -> None:
    pinterest = lane("pinterest_curate")
    pixiv = lane("pixiv_curate")

    assert pinterest == "curation:pinterest"
    assert pixiv == "curation:pixiv"
    assert not _worker_lanes_conflict(pinterest, pixiv)


def test_platform_wide_pinterest_download_does_not_block_pixiv() -> None:
    pinterest = lane("pinterest_download")
    pixiv_curation = lane("pixiv_curate")
    pixiv_discovery = lane("pixiv_download")

    assert pinterest == "platform:pinterest"
    assert not _worker_lanes_conflict(pinterest, pixiv_curation)
    assert not _worker_lanes_conflict(pinterest, pixiv_discovery)


def test_platform_wide_work_conflicts_with_same_platform_lanes() -> None:
    pinterest = lane("pinterest_download")

    assert _worker_lanes_conflict(pinterest, lane("pinterest_curate"))
    assert _worker_lanes_conflict(pinterest, lane("pinterest_collect_ids"))


def test_mixed_platform_work_remains_globally_exclusive() -> None:
    mixed = lane("pixiv_curate", "pinterest_curate")

    assert mixed == "exclusive"
    assert _worker_lanes_conflict(mixed, lane("video_curate"))
