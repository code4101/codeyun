from pathlib import Path

import pytest
from PIL import Image

from backend.core.messaging.wechat_media_delivery import (
    MAX_FILE_BYTES, MAX_IMAGE_BYTES, clipboard_file_payload,
    reply_attachments, reply_media, same_image, same_file,
)


def test_clipboard_file_list_preserves_unicode_and_multiple_paths():
    import struct
    paths = [Path("C:/临时/图片😀.png"), Path("C:/临时/附件.txt")]
    data = clipboard_file_payload(paths)
    assert struct.unpack("<IiiII", data[:20]) == (20, 0, 0, 0, 1)
    assert data[20:].decode("utf-16-le") == "\0".join(map(str, paths)) + "\0\0"


def test_image_directive_is_validated_removed_and_deduplicated(tmp_path):
    image = tmp_path / "result.png"
    Image.new("RGB", (100, 50), "white").save(image)
    text, paths = reply_media(f"处理结果\nCODECLAW_IMAGE: {image}\nCODECLAW_IMAGE: {image}")
    assert text == "处理结果" and paths == [image]


def test_quoted_code_is_not_a_send_instruction(tmp_path):
    text = "示例：\n```\nCODECLAW_IMAGE: missing.png\n```"
    assert reply_media(text) == (text, [])
    with pytest.raises(ValueError, match="绝对路径"):
        reply_media("CODECLAW_IMAGE: relative.png")


def test_corrupt_attachment_is_not_sent(tmp_path):
    image = tmp_path / "fake.png"
    image.write_text("not an image")
    with pytest.raises(Exception):
        reply_media(f"CODECLAW_IMAGE: {image}")


def test_file_directive_is_distinct_from_picture_and_deduplicated(tmp_path):
    file = tmp_path / "result.txt"
    file.write_text("result", encoding="utf-8")
    body, attachments = reply_attachments(f"报告\nCODECLAW_FILE: {file}\nCODECLAW_FILE: {file}")
    assert body == "报告" and attachments == [("file", file)]
    example = f"```\nCODECLAW_FILE: {file}\n```"
    assert reply_attachments(example) == (example, [])


def test_verification_rejects_different_pixels_or_thumbnail(tmp_path):
    left, right, thumb = [tmp_path / f"{name}.png" for name in ("left", "right", "thumb")]
    Image.new("RGB", (100, 50), "white").save(left)
    Image.new("RGB", (100, 50), "black").save(right)
    Image.new("RGB", (20, 10), "white").save(thumb)
    assert same_image(left, left)
    assert not same_image(left, right) and not same_image(left, thumb)


@pytest.mark.parametrize('size,accepted', [
    (MAX_IMAGE_BYTES + 1, True), (1018855829, True),
    (MAX_FILE_BYTES, True), (MAX_FILE_BYTES + 1, False),
])
def test_file_directive_large_attachment_boundary(tmp_path, monkeypatch, size, accepted):
    import os
    file = tmp_path / 'dataset.zip'
    file.write_bytes(b'PK')
    original_stat = Path.stat

    def reported_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == file:
            values = list(result)
            values[6] = size
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, 'stat', reported_stat)
    if accepted:
        assert reply_attachments(f'CODECLAW_FILE: {file}')[1] == [('file', file)]
        with pytest.raises(ValueError, match='50MiB'):
            reply_media(f'CODECLAW_IMAGE: {file}')
    else:
        with pytest.raises(ValueError, match='1024MiB'):
            reply_attachments(f'CODECLAW_FILE: {file}')


def test_file_echo_verification_streams_and_rejects_changed_payload(tmp_path, monkeypatch):
    left, right = tmp_path / 'left.zip', tmp_path / 'right.zip'
    left.write_bytes(b'a' * (1024 * 1024 + 1))
    right.write_bytes(b'a' * (1024 * 1024 + 1))
    def forbid_read_bytes(*args, **kwargs):
        raise AssertionError('Attachment verification must stream')
    monkeypatch.setattr(Path, 'read_bytes', forbid_read_bytes)
    assert same_file(left, right)
    with right.open('r+b') as stream:
        stream.seek(-1, 2)
        stream.write(b'b')
    assert not same_file(left, right)
