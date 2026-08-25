from __future__ import annotations

from types import SimpleNamespace

from backend.plugins.modules.media_sync import sources


def test_clean_weight_preflight_does_not_reconcile_shared_index(tmp_path, monkeypatch) -> None:
    review_root = tmp_path / "2、pinterest"
    review_root.mkdir()
    (review_root / "one.jpg").write_bytes(b"image")

    monkeypatch.setattr(sources, "media_review_root", lambda *_args, **_kwargs: review_root)
    monkeypatch.setattr(sources, "get_device_id", lambda: "device")

    class FakeResult:
        def all(self):
            return [SimpleNamespace(absolute_path=str(review_root / "one.jpg"), weight=1)]

    class FakeSession:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def exec(self, _statement):
            return FakeResult()

    monkeypatch.setattr(sources, "Session", FakeSession)
    monkeypatch.setattr(
        sources,
        "reconcile_local_media_directory_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("preflight must stay read-only")),
    )

    result = sources.candidate_review_weight_summary(
        root_dir=str(tmp_path),
        platform="pinterest",
    )

    assert result == {"review_count": 1, "positive_weight_count": 1}
