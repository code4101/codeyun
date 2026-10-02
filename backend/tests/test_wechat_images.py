"""Image-provider regression: full validation, segment lengths and retry recovery."""
from io import BytesIO
from pathlib import Path
import hashlib
import shutil
import struct
import subprocess

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from PIL import Image
import pytest

from pyxllib.autogui import wechat_db as provider


def image_bytes(fmt="PNG", size=(48, 32)):
    output = BytesIO()
    Image.new("RGB", size, "#2156ab").save(output, format=fmt)
    return output.getvalue()


def encrypted_image(plain, key, aes_size, xor_size, xor=156, version=b"V2"):
    return (b"\x07\x08" + version + b"\x08\x07" + struct.pack("<II", aes_size, xor_size) + b"\x01"
            + AES.new(key, AES.MODE_ECB).encrypt(pad(plain[:aes_size], 16))
            + plain[aes_size:len(plain) - xor_size if xor_size else len(plain)]
            + (bytes(x ^ xor for x in plain[-xor_size:]) if xor_size else b""))


@pytest.mark.parametrize("aes_size,xor_size", [(48, 0), (64, 20), (70000, 12345)])
def test_decode_obeys_u32_aes_size_and_actual_xor_tail(tmp_path, aes_size, xor_size):
    plain = image_bytes("BMP", (256, 160))
    key = b"0123456789abcdef"
    source = tmp_path / "image.dat"
    source.write_bytes(encrypted_image(plain, key, aes_size, xor_size))
    output = provider._decode_wechat_v4_image_dat(source, tmp_path / "out", "image", 156, key)
    assert output is not None and output.read_bytes() == plain
    assert provider._readable_image(output)
    # An invalid prior export of the same size must be overwritten as well.
    output.write_bytes(b"x" * len(plain))
    assert provider._decode_wechat_v4_image_dat(source, tmp_path / "out", "image", 156, key) == output
    assert output.read_bytes() == plain


def test_v1_fixed_key_and_corruption_never_publish_half_image(tmp_path):
    plain, key = image_bytes(), b"cfcd208495d565ef"
    source = tmp_path / "v1.dat"
    data = encrypted_image(plain, key, 48, 12, version=b"V1")
    source.write_bytes(data)
    output = provider._decode_wechat_v4_image_dat(source, tmp_path / "out", "image", 156)
    assert output and output.read_bytes() == plain
    source.write_bytes(data[:70])
    assert provider._decode_wechat_v4_image_dat(source, tmp_path / "out", "image", 156) is None
    assert output.read_bytes() == plain
    damaged = bytearray(data)
    damaged[15 + 63] ^= 1  # Last AES block contains PKCS7 padding.
    source.write_bytes(damaged)
    assert provider._decode_wechat_v4_image_dat(source, tmp_path / "out", "image", 156) is None
    assert output.read_bytes() == plain


def test_wrong_aes_key_bmp_magic_is_rejected():
    key = b"0123456789abcdef"
    block = AES.new(key, AES.MODE_ECB).encrypt(b"BM" + b"random garbage")
    assert not provider._verify_wechat_v4_image_aes_key(key, [block])


@pytest.mark.parametrize("xor", [0, 73, 255])
def test_legacy_xor_images_decode_without_account_key(tmp_path, xor):
    plain = image_bytes()
    source = tmp_path / "legacy.dat"
    source.write_bytes(bytes(value ^ xor for value in plain))
    output = provider._decode_wechat_v4_image_dat(source, tmp_path / "out", "legacy", 0)
    assert output and output.read_bytes() == plain


def test_image_key_recovers_from_account_uin_without_process_scan(tmp_path, monkeypatch):
    uin, wxid = 156, "wxid_fixture"
    suffix = hashlib.md5(str(uin).encode()).hexdigest()[:4]
    account_root = tmp_path / f"{wxid}_{suffix}"
    account_root.mkdir()
    key = hashlib.md5((str(uin) + wxid).encode()).hexdigest()[:16].encode()
    source = account_root / "sample.dat"
    source.write_bytes(encrypted_image(image_bytes(), key, 48, 20))
    storage = provider.WeChatDbStorage(tmp_path / "archive" / "db_storage")
    monkeypatch.setattr(storage, "_wechat_v4_image_xor_key", lambda _: uin)
    monkeypatch.setattr(provider, "_scan_windows_weixin_image_aes_key", lambda *a, **k: pytest.fail("Should recover offline"))
    assert storage._wechat_v4_image_dynamic_aes_key(account_root, source) == key
    # A persisted key from a different account is rejected against ciphertext.
    storage._image_aes_key_cache = b"wrong-accountkey"
    assert storage._wechat_v4_image_dynamic_aes_key(account_root, source) == key


def test_encrypted_manifest_entry_is_retried_and_other_chat_is_excluded(tmp_path, monkeypatch):
    chat = "123@chatroom"
    chat_hash = hashlib.md5(chat.encode()).hexdigest()
    account_root = tmp_path / "wxid_fixture_abcd"
    source = account_root / "msg" / "attach" / chat_hash / "2026-10" / "Img" / "image.dat"
    source.parent.mkdir(parents=True)
    key, plain = b"0123456789abcdef", image_bytes()
    source.write_bytes(encrypted_image(plain, key, 48, 12))
    storage = provider.WeChatDbStorage(tmp_path / "archive" / "db_storage")
    monkeypatch.setattr(storage, "_wechat_account_root", lambda: account_root)
    monkeypatch.setattr(storage, "_hardlink_dirs", lambda: {1: chat_hash, 2: "2026-10", 3: "otherchat"})
    row = {"dir1": 1, "dir2": 2, "file_name": "image.dat", "md5": "a" * 32, "file_size": len(plain)}
    monkeypatch.setattr(storage, "_hardlink_rows", lambda table: [row, {**row, "dir1": 3}] if table.startswith("image") else [])
    monkeypatch.setattr(storage, "_wechat_v4_image_xor_key", lambda _: 156)
    monkeypatch.setattr(storage, "_wechat_v4_image_dynamic_aes_key", lambda *a: key)
    fallback = storage._resource_export_root() / "image" / "old.dat"
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(source.read_bytes())
    entry = {"file_name": "old.dat", "original_file_name": "image.dat", "stored_path": str(fallback),
             "download_name": "image/old.dat", "kind": "image", "md5": "a" * 32, "size": len(plain)}
    storage._write_exported_resource_manifest({"image.dat": entry})
    result = storage._export_resource_files(resource_hints=[{"size": len(plain) + 31, "packed_text": ""}],
                                            chat_username=chat, image_md5="a" * 32)
    decoded = result["a" * 32]
    assert decoded["decoded_from_dat"] and Path(decoded["stored_path"]).read_bytes() == plain
    assert str(account_root / "msg" / "attach" / chat_hash) in decoded["source_path"]
    unknown = storage._export_resource_files(resource_hints=[{"size": len(plain) + 31, "packed_text": ""}],
                                             chat_username=chat, image_md5="b" * 32)
    assert not unknown, "A missing image must not become a different image of equal size"


def test_wxgf_partition_bounds_and_real_hevc_conversion():
    assert provider._convert_wxgf(b"wxgf\xff" + b"\0" * 30) is None
    assert provider._convert_wxgf(b"wxgf\x0f" + b"\0" * 10 + b"\xff" * 4 + b"\0\0\0\1") is None
    executable = shutil.which("ffmpeg")
    if not executable:
        pytest.skip("ffmpeg unavailable")
    result = subprocess.run([executable, "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=0.1",
                             "-frames:v", "1", "-c:v", "libx265", "-x265-params", "log-level=error",
                             "-f", "hevc", "pipe:1"], capture_output=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
    assert result.returncode == 0, result.stderr
    data = b"wxgf\x0f" + b"\0" * 10 + len(result.stdout).to_bytes(4, "big") + result.stdout
    output = provider._convert_wxgf(data)
    assert output and Image.open(BytesIO(output)).size == (32, 32)
