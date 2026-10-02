from pathlib import Path

import pytest
from PIL import Image

from backend.core.messaging.wechat_media_delivery import clipboard_file_payload, reply_attachments, reply_media, same_image


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
