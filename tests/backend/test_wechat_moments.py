from __future__ import annotations

from pathlib import Path
import sqlite3
import time

from sqlmodel import SQLModel, Session, create_engine, select

from backend.core.jobs import scheduler
from backend.core.wechat_moments import _MediaBudget, _archive_media, ingest_wechat_moments
from backend.models import WeChatMoment


def _write_sns_db(root: Path, *, comment: str = "第一条评论") -> None:
    path = root / "sns" / "sns.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE SnsTopItem_1(tid INTEGER, username TEXT, summary TEXT, create_time INTEGER, last_read_time INTEGER, is_read INTEGER)"
    )
    conn.execute("CREATE TABLE SnsTimeLine(tid INTEGER PRIMARY KEY DESC, user_name TEXT, content TEXT, pack_info_buf TEXT)")
    conn.executemany(
        "INSERT INTO SnsTopItem_1 VALUES (?, ?, ?, ?, ?, ?)",
        [(-1, "wxid_author", "", 1700000000, 1700000100, 1), (-2, "wxid_index_only", "索引摘要", 1690000000, 0, 0)],
    )
    xml = f"""
    <SnsDataItem>
      <TimelineObject>
        <id>18446744073709551615</id><username>wxid_author</username><createTime>1700000000</createTime>
        <contentDesc>正文</contentDesc><location><city>杭州</city></location>
        <ContentObject><type>1</type><title>标题</title><description>描述</description><contentUrl>https://example.invalid/post</contentUrl>
          <mediaList><media><id>m1</id><thumb>https://example.invalid/image.jpg</thumb><width>100</width></media></mediaList>
        </ContentObject>
      </TimelineObject>
      <LocalExtraInfo><nickname>作者昵称</nickname>
        <like_user_list><user_comment><comment_64id>like-1</comment_64id><username>wxid_friend</username><nickname>朋友</nickname></user_comment></like_user_list>
        <comment_user_list><user_comment><comment_64id>comment-1</comment_64id><username>wxid_friend</username><nickname>朋友</nickname><content>{comment}</content></user_comment></comment_user_list>
      </LocalExtraInfo>
    </SnsDataItem>
    """.strip()
    conn.execute("INSERT INTO SnsTimeLine VALUES (?, ?, ?, ?)", (-1, "wxid_author", xml, ""))
    conn.commit()
    conn.close()


def test_ingest_wechat_moments_preserves_detailed_and_index_only_rows(tmp_path: Path) -> None:
    source = tmp_path / "decrypted" / "db_storage"
    _write_sns_db(source)
    db_engine = create_engine(f"sqlite:///{tmp_path / 'codeyun.sqlite'}")
    SQLModel.metadata.create_all(db_engine, tables=[WeChatMoment.__table__])

    first = ingest_wechat_moments(
        db_storage_root=source,
        account_key="wxid_self",
        db_engine=db_engine,
        device_id="device-test",
        download_media=False,
    )

    assert first["inserted"] == 2
    assert first["detailed_records"] == 1
    assert first["parse_error_count"] == 0
    with Session(db_engine) as session:
        rows = session.exec(select(WeChatMoment).order_by(WeChatMoment.published_at.desc())).all()
        assert len(rows) == 2
        assert rows[0].moment_id == "18446744073709551615"
        assert rows[0].content_text == "正文"
        assert rows[0].author_nickname == "作者昵称"
        assert rows[0].location_json == {"city": "杭州"}
        assert rows[0].comments_json[0]["content"] == "第一条评论"
        assert rows[0].source_archive_path.endswith(".xml")
        assert (source.parent / "sns_archive" / rows[0].source_archive_path).exists()
        assert rows[1].content_available is False
        assert rows[1].content_text == "索引摘要"


def test_ingest_wechat_moments_is_idempotent_and_updates_changed_xml(tmp_path: Path) -> None:
    source = tmp_path / "decrypted" / "db_storage"
    _write_sns_db(source)
    db_engine = create_engine(f"sqlite:///{tmp_path / 'codeyun.sqlite'}")
    ingest_wechat_moments(
        db_storage_root=source,
        account_key="wxid_self",
        db_engine=db_engine,
        device_id="device-test",
        download_media=False,
    )
    (source / "sns" / "sns.db").unlink()
    _write_sns_db(source, comment="更新后的评论")

    second = ingest_wechat_moments(
        db_storage_root=source,
        account_key="wxid_self",
        db_engine=db_engine,
        device_id="device-test",
        download_media=False,
    )

    assert second["inserted"] == 0
    assert second["updated"] == 1
    assert second["total_records"] == 2
    with Session(db_engine) as session:
        row = session.exec(select(WeChatMoment).where(WeChatMoment.content_available.is_(True))).one()
        assert row.comments_json[0]["content"] == "更新后的评论"
        versions = list((source.parent / "sns_archive" / "moments" / row.moment_id).glob("*.xml"))
        assert len(versions) == 2


def test_wechat_moments_scheduler_is_six_hour_standard_job() -> None:
    spec = scheduler.get_background_task_spec(scheduler.WECHAT_MOMENTS_INCREMENTAL_SYNC_TASK_KEY)

    assert spec is not None
    assert spec.title == "微信朋友圈增量归档"
    assert spec.category == "微信"
    assert spec.schedule_label == "每 6 小时增量归档"
    policy = scheduler._default_background_task_schedule_policy(scheduler.WECHAT_MOMENTS_INCREMENTAL_SYNC_TASK_KEY)
    assert policy["trigger"] == {"type": "interval", "minutes": 360, "anchor": "last_finish"}


def test_recent_media_failure_uses_cooldown_without_consuming_budget(tmp_path: Path) -> None:
    media = {"id": "m1", "thumb": "https://shmmsns.qpic.cn/example.jpg"}
    previous = {**media, "archive_attempt": {"status": "failed", "attempted_at": time.time()}}
    budget = _MediaBudget(remaining=1)

    result = _archive_media(tmp_path, "moment-1", [media], [previous], budget, object())

    assert result[0]["archive_attempt"]["status"] == "failed"
    assert budget.remaining == 1
    assert budget.failed == 0
