from __future__ import annotations

from backend.core.note_access import note_to_response_dict
from backend.core.yuque_html import looks_like_legacy_yuque_lake_html, normalize_legacy_yuque_lake_html
from backend.models import NoteNode


def test_normalize_legacy_yuque_lake_html_removes_lake_shell_and_keeps_content() -> None:
    source = (
        '<!DOCTYPE lake><meta content="1" name="doc-version"/>'
        '<h1 data-lake-id="a" class="lake-fontsize-32 keep">标题</h1>'
        '<p id="x" fid="y" data-lake-indent="1">正文 <a href="/file.zip">附件</a></p>'
        '<img data-lake-id="img1" src="/static/attachments/a.png" alt="图"/>'
    )

    result = normalize_legacy_yuque_lake_html(source)

    assert "<!DOCTYPE" not in result
    assert "<meta" not in result
    assert "data-lake" not in result
    assert "lake-fontsize" not in result
    assert "fid=" not in result
    assert "keep" in result
    assert "标题" in result
    assert '<a href="/file.zip">附件</a>' in result
    assert 'src="/static/attachments/a.png"' in result


def test_normalize_legacy_yuque_lake_html_leaves_normal_html_unchanged() -> None:
    source = '<p class="note">普通 HTML</p>'

    assert not looks_like_legacy_yuque_lake_html(source)
    assert normalize_legacy_yuque_lake_html(source) == source


def test_note_response_sanitizes_legacy_yuque_content() -> None:
    note = NoteNode(
        id="note-1",
        user_id=1,
        title="旧语雀",
        content='<!DOCTYPE lake><meta name="doc-version" content="1"/><p data-lake-id="p1">正文</p>',
        start_at=0,
    )

    payload = note_to_response_dict(note, None)

    assert payload["content"] == "<p>正文</p>"
