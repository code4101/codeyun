from backend.private_modules.media_sync.sources import build_pixiv_related_snapshot, create_pixiv_related_store


def test_build_pixiv_related_snapshot_distinguishes_empty_review_dir_from_pending_download_queue(tmp_path):
    root_dir = tmp_path / "media-root"
    store = create_pixiv_related_store(root_dir)
    conn = store.connect_db()
    try:
        conn.execute(
            """
            INSERT INTO artworks (
                artwork_id, artwork_url, title, user_id, user_name,
                first_seen_at, last_seen_at, last_synced_at, download_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "a1",
                "https://www.pixiv.net/artworks/a1",
                "Done",
                "u1",
                "Author",
                "2026-03-27T00:00:00+08:00",
                "2026-03-27T00:00:00+08:00",
                "2026-03-27T00:00:00+08:00",
                "done",
            ),
        )
        conn.execute(
            """
            INSERT INTO artworks (
                artwork_id, artwork_url, title, user_id, user_name,
                first_seen_at, last_seen_at, last_synced_at, download_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "a2",
                "https://www.pixiv.net/artworks/a2",
                "Pending",
                "u1",
                "Author",
                "2026-03-27T00:00:00+08:00",
                "2026-03-27T00:00:00+08:00",
                "2026-03-27T00:00:00+08:00",
                "pending",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    snapshot = build_pixiv_related_snapshot(root_dir=str(root_dir), user_id=1, enabled=True)

    assert snapshot["counts"]["done"] == 1
    assert snapshot["counts"]["pending"] == 1
    assert snapshot["review_file_count"] == 0
    assert "候选目录已清空" in str(snapshot["message"] or "")
    assert "推荐队列未下载" in str(snapshot["message"] or "")
