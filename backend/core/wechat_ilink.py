from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import random
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from backend.core.ai_chat import (
    AiProviderConfig,
    CODEX_CLI_DEFAULT_COMMAND,
    CODEX_CLI_DEFAULT_MODEL,
    chat_with_provider,
)
from backend.core.note_semantics import (
    NOTE_CATEGORY_DEFAULT,
    NOTE_FORM_DOCUMENT,
    NOTE_LIFECYCLE_STAGE_DEFAULT,
    NOTE_SCENE_DEFAULT,
    derive_legacy_semantics_from_taxonomy,
)
from backend.core.settings import ROOT_DIR, get_settings
from backend.models import NoteNode, User


class WechatIlinkError(RuntimeError):
    """Raised when the WeChat iLink client cannot complete an operation."""


DEFAULT_API_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_BOT_TYPE = "3"
CHANNEL_VERSION = "2.1.7"
ILINK_APP_ID = "bot"
LOGIN_TTL_SECONDS = 5 * 60
DEFAULT_API_TIMEOUT_SECONDS = 15.0
DEFAULT_LONG_POLL_TIMEOUT_SECONDS = 35.0
DEFAULT_MEDIA_TIMEOUT_SECONDS = 30.0
STORE_VERSION = 1
CODEX_BRIDGE_DEFAULT_TIMEOUT_SECONDS = 600.0
CODEX_BRIDGE_MAX_REPLY_CHARS = 3500
CODEX_BRIDGE_LEGACY_DEFAULT_MODELS = {"gpt-5.4"}
CODEX_BRIDGE_TIMEZONE = "Asia/Shanghai"
CODEX_BRIDGE_QUICK_NOTE_TEXT = "-"
CODEX_BRIDGE_QUICK_NOTE_PRIVATE_LEVEL = 1
CODEX_BRIDGE_IMAGE_DIRECTIVE_RE = re.compile(
    r"^\s*CODECLAW_(?:SEND_)?IMAGE\s*:\s*(?P<path>.+?)\s*$",
    re.IGNORECASE,
)
MEDIA_INLINE_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
MEDIA_UPLOAD_MAX_BYTES = 20 * 1024 * 1024
MESSAGE_TYPE_USER = 1
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
MESSAGE_ITEM_TEXT = 1
MESSAGE_ITEM_IMAGE = 2
UPLOAD_MEDIA_TYPE_IMAGE = 1


@dataclass
class LoginSession:
    session_key: str
    qrcode: str
    qrcode_url: str
    started_at: float
    current_base_url: str = DEFAULT_API_BASE_URL
    status: str = "wait"


@dataclass
class CodexBridgeWorker:
    account_id: str
    stop_event: threading.Event
    thread: threading.Thread
    started_at: float
    model: str
    command: str
    handled_count: int = 0
    last_poll_at: float | None = None
    last_message_at: float | None = None
    last_reply_at: float | None = None
    last_error: str = ""


_login_sessions: dict[str, LoginSession] = {}
_login_lock = threading.RLock()
_codex_bridge_workers: dict[str, CodexBridgeWorker] = {}
_codex_bridge_lock = threading.RLock()


def _store_path() -> Path:
    return get_settings().data_dir / "wechat-ilink" / "accounts.json"


def _media_dir() -> Path:
    return _store_path().parent / "media"


def _normalize_media_id(value: str) -> str:
    media_id = (value or "").strip()
    if not media_id:
        raise WechatIlinkError("媒体标识不能为空")
    if "/" in media_id or "\\" in media_id or media_id in {".", ".."}:
        raise WechatIlinkError("媒体标识非法")
    return media_id


def resolve_media_file(media_id: str) -> tuple[Path, str]:
    normalized_media_id = _normalize_media_id(media_id)
    path = _media_dir() / normalized_media_id
    if not path.exists() or not path.is_file():
        raise WechatIlinkError("媒体文件不存在")
    mime_type, _ = mimetypes.guess_type(path.name)
    return path, mime_type or "application/octet-stream"


def _now() -> float:
    return time.time()


def _build_client_version(version: str) -> int:
    parts = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    major = parts[0] if len(parts) > 0 else 0
    minor = parts[1] if len(parts) > 1 else 0
    patch = parts[2] if len(parts) > 2 else 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


def _build_base_info() -> dict[str, Any]:
    return {"channel_version": CHANNEL_VERSION}


def _random_wechat_uin() -> str:
    value = str(random.getrandbits(32)).encode("utf-8")
    return base64.b64encode(value).decode("ascii")


def _build_qrcode_data_url(value: str) -> str:
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - opencv is a backend dependency.
        raise WechatIlinkError("当前环境缺少 OpenCV，无法生成二维码图片") from exc

    encoder = cv2.QRCodeEncoder_create()
    image = encoder.encode(value)
    if image is None:
        raise WechatIlinkError("二维码图片生成失败")
    image = cv2.copyMakeBorder(image, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    image = cv2.resize(
        image,
        (image.shape[1] * 8, image.shape[0] * 8),
        interpolation=cv2.INTER_NEAREST,
    )
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise WechatIlinkError("二维码图片编码失败")
    encoded = base64.b64encode(buffer.tobytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _build_common_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(_build_client_version(CHANNEL_VERSION)),
    }


def _build_post_headers(token: str | None, body: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
        **_build_common_headers(),
    }
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _parse_json_response(response: requests.Response, label: str) -> dict[str, Any]:
    text = response.text
    if not response.ok:
        raise WechatIlinkError(f"{label} {response.status_code}: {text}")
    if not text.strip():
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise WechatIlinkError(f"{label} 返回了非 JSON 响应") from exc
    if not isinstance(payload, dict):
        raise WechatIlinkError(f"{label} 响应格式不是对象")
    return payload


def _get_json(base_url: str, endpoint: str, *, label: str, timeout_seconds: float | None = None) -> dict[str, Any]:
    try:
        response = requests.get(
            _join_url(base_url, endpoint),
            headers=_build_common_headers(),
            timeout=timeout_seconds,
        )
    except requests.Timeout:
        raise
    except requests.RequestException as exc:
        raise WechatIlinkError(f"{label} 请求失败：{exc}") from exc
    return _parse_json_response(response, label)


def _post_json(
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    *,
    token: str | None,
    label: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        response = requests.post(
            _join_url(base_url, endpoint),
            headers=_build_post_headers(token, body),
            data=body.encode("utf-8"),
            timeout=timeout_seconds,
        )
    except requests.Timeout:
        raise
    except requests.RequestException as exc:
        raise WechatIlinkError(f"{label} 请求失败：{exc}") from exc
    return _parse_json_response(response, label)


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _aes_ecb_padded_size(plaintext_size: int) -> int:
    return ((max(0, plaintext_size) + 16) // 16) * 16


def _parse_cdn_aes_key(aes_key_base64: str, label: str) -> bytes:
    try:
        decoded = base64.b64decode(aes_key_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WechatIlinkError(f"{label} AES key 不是有效 base64") from exc
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if re.fullmatch(r"[0-9a-fA-F]{32}", text):
            return bytes.fromhex(text)
    raise WechatIlinkError(f"{label} AES key 长度不正确")


def _build_cdn_download_url(encrypted_query_param: str) -> str:
    return f"{DEFAULT_CDN_BASE_URL}/download?encrypted_query_param={quote(encrypted_query_param)}"


def _build_cdn_upload_url(upload_param: str, filekey: str) -> str:
    return (
        f"{DEFAULT_CDN_BASE_URL}/upload?"
        f"encrypted_query_param={quote(upload_param)}&filekey={quote(filekey)}"
    )


def _download_cdn_bytes(*, encrypted_query_param: str, full_url: str = "", label: str) -> bytes:
    url = full_url.strip() or _build_cdn_download_url(encrypted_query_param)
    try:
        response = requests.get(url, timeout=DEFAULT_MEDIA_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise WechatIlinkError(f"{label} 下载失败：{exc}") from exc
    if not response.ok:
        raise WechatIlinkError(f"{label} 下载失败 {response.status_code}: {response.text}")
    return response.content


def _infer_image_mime(image_bytes: bytes, filename: str = "", *, fallback_jpeg: bool = True) -> str:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes.startswith(b"BM"):
        return "image/bmp"
    guessed, _ = mimetypes.guess_type(filename)
    if guessed and guessed.startswith("image/"):
        return guessed
    return "image/jpeg" if fallback_jpeg else ""


def _image_extension(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }.get(mime_type.lower(), ".jpg")


def _build_media_data_url(image_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _save_inbound_image(image_bytes: bytes, *, mime_type: str) -> dict[str, Any]:
    media_id = f"{uuid.uuid4().hex}{_image_extension(mime_type)}"
    media_path = _media_dir() / media_id
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(image_bytes)
    summary: dict[str, Any] = {
        "id": media_id,
        "mime_type": mime_type,
        "size": len(image_bytes),
        "download_url": f"/api/wechat-ilink/media/{quote(media_id)}",
        "path": str(media_path),
    }
    if len(image_bytes) <= MEDIA_INLINE_PREVIEW_MAX_BYTES:
        summary["data_url"] = _build_media_data_url(image_bytes, mime_type)
    return summary


def _download_image_item(image_item: dict[str, Any], *, label: str) -> dict[str, Any]:
    media = image_item.get("media")
    if not isinstance(media, dict):
        media = {}
    encrypted_query_param = str(media.get("encrypt_query_param") or "").strip()
    full_url = str(media.get("full_url") or image_item.get("url") or "").strip()
    if not encrypted_query_param and not full_url:
        raise WechatIlinkError(f"{label} 缺少图片 CDN 地址")

    encrypted_bytes = _download_cdn_bytes(
        encrypted_query_param=encrypted_query_param,
        full_url=full_url,
        label=label,
    )
    aeskey_hex = str(image_item.get("aeskey") or "").strip()
    aes_key_base64 = str(media.get("aes_key") or "").strip()
    if aeskey_hex:
        try:
            aes_key = bytes.fromhex(aeskey_hex)
        except ValueError as exc:
            raise WechatIlinkError(f"{label} AES key 不是有效 hex") from exc
        image_bytes = _aes_ecb_decrypt(encrypted_bytes, aes_key)
    elif aes_key_base64:
        image_bytes = _aes_ecb_decrypt(encrypted_bytes, _parse_cdn_aes_key(aes_key_base64, label))
    else:
        image_bytes = encrypted_bytes

    mime_type = _infer_image_mime(image_bytes)
    return _save_inbound_image(image_bytes, mime_type=mime_type)


def _extract_image_summaries(items: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    images: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or item.get("type") != MESSAGE_ITEM_IMAGE:
            continue
        image_item = item.get("image_item")
        if not isinstance(image_item, dict):
            images.append(
                {
                    "id": f"image-{index}",
                    "mime_type": "image/*",
                    "size": 0,
                    "download_error": "图片消息缺少 image_item",
                }
            )
            continue
        try:
            images.append(_download_image_item(image_item, label=f"{label} image#{index}"))
        except Exception as exc:
            images.append(
                {
                    "id": f"image-{index}",
                    "mime_type": "image/*",
                    "size": 0,
                    "download_error": str(exc),
                }
            )
    return images


def _upload_image_to_cdn(
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    image_bytes: bytes,
    timeout_seconds: float,
) -> dict[str, Any]:
    rawsize = len(image_bytes)
    rawfilemd5 = hashlib.md5(image_bytes).hexdigest()
    filekey = secrets.token_hex(16)
    aes_key = secrets.token_bytes(16)
    upload_response = _post_json(
        base_url,
        "ilink/bot/getuploadurl",
        {
            "filekey": filekey,
            "media_type": UPLOAD_MEDIA_TYPE_IMAGE,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": _aes_ecb_padded_size(rawsize),
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
            "base_info": _build_base_info(),
        },
        token=token,
        label="getuploadurl",
        timeout_seconds=max(1.0, timeout_seconds),
    )
    upload_full_url = str(upload_response.get("upload_full_url") or "").strip()
    upload_param = str(upload_response.get("upload_param") or "").strip()
    if not upload_full_url and not upload_param:
        raise WechatIlinkError("微信没有返回图片上传地址")

    upload_url = upload_full_url or _build_cdn_upload_url(upload_param, filekey)
    encrypted_bytes = _aes_ecb_encrypt(image_bytes, aes_key)
    try:
        response = requests.post(
            upload_url,
            headers={"Content-Type": "application/octet-stream"},
            data=encrypted_bytes,
            timeout=max(1.0, timeout_seconds),
        )
    except requests.RequestException as exc:
        raise WechatIlinkError(f"图片上传 CDN 失败：{exc}") from exc
    if response.status_code != 200:
        error_text = response.headers.get("x-error-message") or response.text
        raise WechatIlinkError(f"图片上传 CDN 失败 {response.status_code}: {error_text}")
    download_param = response.headers.get("x-encrypted-param", "").strip()
    if not download_param:
        raise WechatIlinkError("图片上传 CDN 后没有返回下载参数")
    return {
        "filekey": filekey,
        "download_encrypted_query_param": download_param,
        "aeskey_hex": aes_key.hex(),
        "file_size": rawsize,
        "file_size_ciphertext": len(encrypted_bytes),
    }


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    secret_key = get_settings().secret_key.encode("utf-8")
    digest = hashlib.sha256(secret_key).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_secret(value: str) -> str:
    payload = value.strip()
    if not payload:
        return ""
    return _get_fernet().encrypt(payload.encode("utf-8")).decode("utf-8")


def _decrypt_secret(value: str) -> str:
    payload = value.strip()
    if not payload:
        return ""
    try:
        return _get_fernet().decrypt(payload.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise WechatIlinkError("已保存的微信 token 无法解密，请重新扫码连接") from exc


def _mask_secret(value: str) -> str:
    payload = value.strip()
    if not payload:
        return ""
    if len(payload) <= 10:
        return f"{payload[:2]}***{payload[-2:]}" if len(payload) > 4 else "*" * len(payload)
    return f"{payload[:5]}...{payload[-5:]}"


def _read_store() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {"version": STORE_VERSION, "accounts": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WechatIlinkError("微信接入配置文件无法读取") from exc
    if not isinstance(payload, dict):
        return {"version": STORE_VERSION, "accounts": {}}
    accounts = payload.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {}
    return {"version": STORE_VERSION, "accounts": accounts}


def _write_store(payload: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": STORE_VERSION,
        "accounts": payload.get("accounts") if isinstance(payload.get("accounts"), dict) else {},
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _normalize_base_url(value: str | None, fallback: str = DEFAULT_API_BASE_URL) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        text = f"https://{text}"
    return text.rstrip("/")


def _normalize_account_id(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        raise WechatIlinkError("账号标识不能为空")
    if "/" in text or "\\" in text:
        raise WechatIlinkError("账号标识不能包含路径分隔符")
    return text


def _purge_expired_logins() -> None:
    now = _now()
    expired = [
        key for key, session in _login_sessions.items()
        if now - session.started_at > LOGIN_TTL_SECONDS
    ]
    for key in expired:
        _login_sessions.pop(key, None)


def start_login(*, account_id: str | None = None, force: bool = False, bot_type: str = DEFAULT_BOT_TYPE) -> dict[str, Any]:
    session_key = (account_id or "").strip() or str(uuid.uuid4())
    with _login_lock:
        _purge_expired_logins()
        existing = _login_sessions.get(session_key)
        if existing and not force:
            return {
                "session_key": existing.session_key,
                "qrcode_url": existing.qrcode_url,
                "status": existing.status,
                "message": "二维码已就绪，请使用微信扫描。",
            }

        payload = _get_json(
            DEFAULT_API_BASE_URL,
            f"ilink/bot/get_bot_qrcode?bot_type={quote(bot_type)}",
            label="get_bot_qrcode",
            timeout_seconds=DEFAULT_API_TIMEOUT_SECONDS,
        )
        qrcode = str(payload.get("qrcode") or "").strip()
        qrcode_login_url = str(payload.get("qrcode_img_content") or "").strip()
        if not qrcode or not qrcode_login_url:
            raise WechatIlinkError("微信没有返回有效二维码")

        session = LoginSession(
            session_key=session_key,
            qrcode=qrcode,
            qrcode_url=_build_qrcode_data_url(qrcode_login_url),
            started_at=_now(),
        )
        _login_sessions[session_key] = session
        return {
            "session_key": session.session_key,
            "qrcode_url": session.qrcode_url,
            "status": session.status,
            "message": "使用微信扫描二维码并在手机上确认。",
        }


def wait_login(
    *,
    session_key: str,
    timeout_seconds: float = DEFAULT_LONG_POLL_TIMEOUT_SECONDS,
    owner_user_id: int | None = None,
) -> dict[str, Any]:
    deadline = _now() + max(1.0, timeout_seconds)
    while _now() < deadline:
        with _login_lock:
            session = _login_sessions.get(session_key)
            if session is None:
                return {"connected": False, "status": "missing", "message": "当前没有进行中的登录。"}
            if _now() - session.started_at > LOGIN_TTL_SECONDS:
                _login_sessions.pop(session_key, None)
                return {"connected": False, "status": "expired", "message": "二维码已过期，请重新生成。"}
            current_base_url = session.current_base_url
            qrcode = session.qrcode

        remaining = max(1.0, deadline - _now())
        try:
            payload = _get_json(
                current_base_url,
                f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode)}",
                label="get_qrcode_status",
                timeout_seconds=min(DEFAULT_LONG_POLL_TIMEOUT_SECONDS, remaining),
            )
        except WechatIlinkError:
            raise
        except requests.Timeout:
            payload = {"status": "wait"}

        status = str(payload.get("status") or "wait").strip() or "wait"
        with _login_lock:
            session = _login_sessions.get(session_key)
            if session is not None:
                session.status = status

        if status == "scaned_but_redirect":
            redirect_host = str(payload.get("redirect_host") or "").strip()
            if redirect_host:
                with _login_lock:
                    session = _login_sessions.get(session_key)
                    if session is not None:
                        session.current_base_url = _normalize_base_url(redirect_host)
            continue

        if status == "confirmed":
            account_id = _normalize_account_id(str(payload.get("ilink_bot_id") or ""))
            bot_token = str(payload.get("bot_token") or "").strip()
            if not bot_token:
                raise WechatIlinkError("登录已确认，但微信没有返回 bot_token")
            user_id = str(payload.get("ilink_user_id") or "").strip()
            base_url = _normalize_base_url(str(payload.get("baseurl") or current_base_url))
            summary = save_account(
                account_id=account_id,
                token=bot_token,
                user_id=user_id,
                base_url=base_url,
                owner_user_id=owner_user_id,
            )
            with _login_lock:
                _login_sessions.pop(session_key, None)
            return {
                "connected": True,
                "status": status,
                "account": summary,
                "message": "已连接微信。",
            }

        if status == "expired":
            with _login_lock:
                _login_sessions.pop(session_key, None)
            return {"connected": False, "status": status, "message": "二维码已过期，请重新生成。"}

        if status in {"scaned", "wait"}:
            if status == "scaned":
                return {"connected": False, "status": status, "message": "已扫码，请在微信继续确认。"}
            return {"connected": False, "status": status, "message": "等待扫码。"}

        return {"connected": False, "status": status, "message": f"微信返回状态：{status}"}

    return {"connected": False, "status": "wait", "message": "等待扫码。"}


def save_account(
    *,
    account_id: str,
    token: str,
    user_id: str = "",
    base_url: str = DEFAULT_API_BASE_URL,
    owner_user_id: int | None = None,
) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    store = _read_store()
    accounts = store.setdefault("accounts", {})
    existing = accounts.get(normalized_account_id)
    now = _now()
    account = existing if isinstance(existing, dict) else {}
    account.update(
        {
            "account_id": normalized_account_id,
            "user_id": user_id.strip(),
            "base_url": _normalize_base_url(base_url),
            "token_cipher": _encrypt_secret(token),
            "token_masked": _mask_secret(token),
            "updated_at": now,
        }
    )
    if owner_user_id is not None:
        account["owner_user_id"] = int(owner_user_id)
    account.setdefault("created_at", now)
    account.setdefault("get_updates_buf", "")
    account.setdefault("context_tokens", {})
    accounts[normalized_account_id] = account
    _write_store(store)
    return _serialize_account(account)


def _serialize_account(account: dict[str, Any]) -> dict[str, Any]:
    context_tokens = account.get("context_tokens")
    if not isinstance(context_tokens, dict):
        context_tokens = {}
    account_id = str(account.get("account_id") or "")
    bridge_config = account.get("codex_bridge")
    if not isinstance(bridge_config, dict):
        bridge_config = {}
    bridge_status = _get_codex_bridge_status(account_id, bridge_config=bridge_config)
    return {
        "account_id": account_id,
        "user_id": str(account.get("user_id") or ""),
        "base_url": str(account.get("base_url") or DEFAULT_API_BASE_URL),
        "token_masked": str(account.get("token_masked") or ""),
        "created_at": account.get("created_at"),
        "updated_at": account.get("updated_at"),
        "last_poll_at": account.get("last_poll_at"),
        "last_message_at": account.get("last_message_at"),
        "has_cursor": bool(account.get("get_updates_buf")),
        "context_user_count": len(context_tokens),
        "codex_bridge": bridge_status,
    }


def list_accounts() -> list[dict[str, Any]]:
    store = _read_store()
    accounts = store.get("accounts", {})
    if not isinstance(accounts, dict):
        return []
    summaries = [
        _serialize_account(account)
        for account in accounts.values()
        if isinstance(account, dict)
    ]
    return sorted(summaries, key=lambda item: float(item.get("updated_at") or 0), reverse=True)


def delete_account(account_id: str) -> None:
    normalized_account_id = _normalize_account_id(account_id)
    store = _read_store()
    accounts = store.setdefault("accounts", {})
    if normalized_account_id not in accounts:
        raise WechatIlinkError("微信账号不存在")
    accounts.pop(normalized_account_id, None)
    _write_store(store)


def _load_account(store: dict[str, Any], account_id: str) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    accounts = store.get("accounts")
    if not isinstance(accounts, dict):
        raise WechatIlinkError("微信账号不存在")
    account = accounts.get(normalized_account_id)
    if not isinstance(account, dict):
        raise WechatIlinkError("微信账号不存在")
    return account


def _decrypt_account_token(account: dict[str, Any]) -> str:
    token = _decrypt_secret(str(account.get("token_cipher") or ""))
    if not token:
        raise WechatIlinkError("微信账号缺少 token，请重新扫码连接")
    return token


def _message_text_from_items(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == MESSAGE_ITEM_TEXT:
            text_item = item.get("text_item")
            if isinstance(text_item, dict):
                text = str(text_item.get("text") or "").strip()
                if text:
                    parts.append(text)
        elif item_type == 3:
            voice_item = item.get("voice_item")
            if isinstance(voice_item, dict):
                text = str(voice_item.get("text") or "").strip()
                if text:
                    parts.append(text)
    return "\n".join(parts)


def _message_item_types(items: Any) -> list[int]:
    if not isinstance(items, list):
        return []
    result: list[int] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("type"), int):
            result.append(int(item["type"]))
    return result


def _simplify_message(message: dict[str, Any], *, media_label: str) -> dict[str, Any]:
    items = message.get("item_list")
    return {
        "seq": message.get("seq"),
        "message_id": message.get("message_id"),
        "from_user_id": str(message.get("from_user_id") or ""),
        "to_user_id": str(message.get("to_user_id") or ""),
        "create_time_ms": message.get("create_time_ms"),
        "session_id": str(message.get("session_id") or ""),
        "message_type": message.get("message_type"),
        "message_state": message.get("message_state"),
        "context_token": str(message.get("context_token") or ""),
        "text": _message_text_from_items(items),
        "images": _extract_image_summaries(items, label=media_label),
        "item_types": _message_item_types(items),
        "raw": message,
    }


def get_updates(account_id: str, *, timeout_seconds: float = DEFAULT_LONG_POLL_TIMEOUT_SECONDS) -> dict[str, Any]:
    store = _read_store()
    account = _load_account(store, account_id)
    token = _decrypt_account_token(account)
    base_url = _normalize_base_url(str(account.get("base_url") or ""))
    payload = {
        "get_updates_buf": str(account.get("get_updates_buf") or ""),
        "base_info": _build_base_info(),
    }
    try:
        response = _post_json(
            base_url,
            "ilink/bot/getupdates",
            payload,
            token=token,
            label="getupdates",
            timeout_seconds=max(1.0, timeout_seconds),
        )
    except requests.Timeout:
        return {
            "ret": 0,
            "errcode": None,
            "errmsg": None,
            "messages": [],
            "timed_out": True,
            "longpolling_timeout_ms": int(DEFAULT_LONG_POLL_TIMEOUT_SECONDS * 1000),
        }

    raw_messages = response.get("msgs")
    if not isinstance(raw_messages, list):
        raw_messages = []
    messages = [
        _simplify_message(
            message,
            media_label=f"{account_id}:{message.get('message_id') or message.get('seq') or 'message'}",
        )
        for message in raw_messages
        if isinstance(message, dict)
    ]

    if response.get("ret") in {0, None}:
        account["get_updates_buf"] = str(response.get("get_updates_buf") or account.get("get_updates_buf") or "")
        account["last_poll_at"] = _now()
        context_tokens = account.get("context_tokens")
        if not isinstance(context_tokens, dict):
            context_tokens = {}
        for message in messages:
            from_user_id = message.get("from_user_id")
            context_token = message.get("context_token")
            if isinstance(from_user_id, str) and from_user_id and isinstance(context_token, str) and context_token:
                context_tokens[from_user_id] = context_token
            create_time_ms = message.get("create_time_ms")
            if isinstance(create_time_ms, (int, float)):
                account["last_message_at"] = max(float(account.get("last_message_at") or 0), float(create_time_ms))
        account["context_tokens"] = context_tokens
        _write_store(store)

    return {
        "ret": response.get("ret", 0),
        "errcode": response.get("errcode"),
        "errmsg": response.get("errmsg"),
        "messages": messages,
        "timed_out": False,
        "longpolling_timeout_ms": response.get("longpolling_timeout_ms"),
    }


def _normalize_codex_bridge_model(value: str | None) -> str:
    model = (value or CODEX_CLI_DEFAULT_MODEL).strip() or CODEX_CLI_DEFAULT_MODEL
    if model in CODEX_BRIDGE_LEGACY_DEFAULT_MODELS:
        return CODEX_CLI_DEFAULT_MODEL
    return model


def _normalize_codex_bridge_command(value: str | None) -> str:
    return (value or CODEX_CLI_DEFAULT_COMMAND).strip() or CODEX_CLI_DEFAULT_COMMAND


def _build_codex_bridge_system_prompt(extra_prompt: str | None = None) -> str:
    sections = [
        "你是 CodeClaw：CodeYun 的微信入口。微信只负责收发消息，实际任务交给本机 Codex CLI。",
        f"当前工作目录：{ROOT_DIR}",
        "请用中文回复，适合微信阅读，默认简洁但把关键结果说清楚。",
        "如需让微信发送图片，请在最终回复中单独写一行：CODECLAW_IMAGE: 图片文件路径",
    ]
    if extra_prompt and extra_prompt.strip():
        sections.extend(["", extra_prompt.strip()])
    return "\n".join(sections)


def _build_codex_bridge_provider(*, model: str, command: str, session_id: str = "") -> AiProviderConfig:
    return AiProviderConfig(
        id="wechat-codex-cli",
        label="Codex CLI",
        kind="codex_cli",
        base_url=command,
        default_model=model,
        timeout_seconds=CODEX_BRIDGE_DEFAULT_TIMEOUT_SECONDS,
        api_key="",
        supports_stream=False,
        supports_vision=True,
        requires_api_key=False,
        configured=True,
        models=(model,),
        is_custom=False,
        workspace_dir=str(ROOT_DIR),
        session_id=session_id,
    )


def _message_images(message: dict[str, Any]) -> list[dict[str, Any]]:
    images = message.get("images")
    if not isinstance(images, list):
        return []
    return [image for image in images if isinstance(image, dict)]


def _is_user_bridge_message(message: dict[str, Any]) -> bool:
    text = str(message.get("text") or "").strip()
    images = _message_images(message)
    from_user_id = str(message.get("from_user_id") or "").strip()
    if not from_user_id or (not text and not images):
        return False
    message_type = message.get("message_type")
    return message_type in {None, 0, MESSAGE_TYPE_USER}


def _build_bridge_user_prompt(message: dict[str, Any]) -> str:
    sender = str(message.get("from_user_id") or "").strip()
    text = str(message.get("text") or "").strip()
    images = _message_images(message)
    if not text and images:
        text = "用户发送了图片，请读取图片内容并结合上下文回复。"
    image_note = f"\n\n图片：{len(images)} 张，已作为附件传给 Codex CLI。" if images else ""
    return "\n".join(
        [
            f"微信发送方：{sender}",
            "",
            _build_bridge_time_context(message),
            "",
            "用户消息：",
            f"{text}{image_note}",
        ]
    ).strip()


def _bridge_timezone() -> ZoneInfo:
    return ZoneInfo(CODEX_BRIDGE_TIMEZONE)


def _datetime_from_message_time(value: Any, timezone: ZoneInfo) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    timestamp = float(value)
    if timestamp <= 0:
        return None
    if abs(timestamp) > 100_000_000_000:
        timestamp = timestamp / 1000.0
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone)
    except (OSError, OverflowError, ValueError):
        return None


def _format_bridge_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _format_bridge_date_range(start_date: date, end_date: date) -> str:
    return f"{start_date.isoformat()}~{end_date.isoformat()}"


def _build_bridge_time_context(message: dict[str, Any]) -> str:
    timezone = _bridge_timezone()
    raw_message_time = message.get("create_time_ms")
    message_at = _datetime_from_message_time(raw_message_time, timezone)
    if message_at is None:
        return f"时间上下文（{CODEX_BRIDGE_TIMEZONE}）：消息未提供固定时间戳。"

    today = message_at.date()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    last_week_start = week_start - timedelta(days=7)
    last_week_end = week_start - timedelta(days=1)
    return (
        f"时间上下文（{CODEX_BRIDGE_TIMEZONE}）："
        f"消息时间戳={raw_message_time}；"
        f"消息时间={_format_bridge_datetime(message_at)}；"
        f"今天={today.isoformat()}；昨天={yesterday.isoformat()}；"
        f"本周={_format_bridge_date_range(week_start, week_end)}；"
        f"上周={_format_bridge_date_range(last_week_start, last_week_end)}。"
    )


def _build_bridge_image_payloads(message: dict[str, Any]) -> list[str]:
    payloads: list[str] = []
    for image in _message_images(message):
        image_path = Path(str(image.get("path") or ""))
        if not image_path.exists() or not image_path.is_file():
            continue
        try:
            image_bytes = image_path.read_bytes()
        except OSError:
            continue
        mime_type = str(image.get("mime_type") or _infer_image_mime(image_bytes, image_path.name))
        payloads.append(_build_media_data_url(image_bytes, mime_type))
    return payloads


def _get_database_engine():
    from backend.db import engine

    return engine


def _normalize_owner_user_id(value: Any) -> int | None:
    try:
        user_id = int(value)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def _load_codex_bridge_owner_user_id(account_id: str) -> int | None:
    store = _read_store()
    account = _load_account(store, account_id)
    bridge_config = account.get("codex_bridge")
    if not isinstance(bridge_config, dict):
        bridge_config = {}
    for value in (bridge_config.get("owner_user_id"), account.get("owner_user_id")):
        user_id = _normalize_owner_user_id(value)
        if user_id is not None:
            return user_id
    return None


def _resolve_quick_note_owner(session: Any, owner_user_id: int | None) -> User:
    from sqlmodel import select

    if owner_user_id is not None:
        user = session.get(User, owner_user_id)
        if user is not None and user.is_active:
            return user

    bootstrap_username = get_settings().bootstrap_admin_username
    if bootstrap_username:
        user = session.exec(
            select(User).where(User.username == bootstrap_username, User.is_active == True)  # noqa: E712
        ).first()
        if user is not None:
            return user

    superusers = session.exec(
        select(User)
        .where(User.is_active == True, User.is_superuser == True)  # noqa: E712
        .order_by(User.id)
    ).all()
    if len(superusers) == 1:
        return superusers[0]

    users = session.exec(
        select(User)
        .where(User.is_active == True)  # noqa: E712
        .order_by(User.id)
    ).all()
    if len(users) == 1:
        return users[0]

    raise WechatIlinkError("CodeClaw 尚未绑定星图笔记用户，请在 CodeYun 页面重新开启 CodeClaw。")


def _create_codex_bridge_quick_dash_note(account_id: str) -> dict[str, Any]:
    from sqlmodel import Session

    owner_user_id = _load_codex_bridge_owner_user_id(account_id)
    taxonomy = derive_legacy_semantics_from_taxonomy(
        [{"key": NOTE_CATEGORY_DEFAULT, "weight": 100}],
        primary_category=NOTE_CATEGORY_DEFAULT,
        note_form=NOTE_FORM_DOCUMENT,
        note_scene=NOTE_SCENE_DEFAULT,
        lifecycle_stage=NOTE_LIFECYCLE_STAGE_DEFAULT,
    )
    now = _now()
    with Session(_get_database_engine()) as session:
        owner = _resolve_quick_note_owner(session, owner_user_id)
        note = NoteNode(
            id=str(uuid.uuid4()),
            user_id=int(owner.id),
            title=CODEX_BRIDGE_QUICK_NOTE_TEXT,
            content="",
            weight=0,
            node_type=taxonomy["node_type"],
            note_types=taxonomy["note_types"],
            note_categories=taxonomy["note_categories"],
            primary_category=taxonomy["primary_category"],
            note_form=taxonomy["note_form"],
            note_kind=taxonomy["note_kind"],
            note_scene=taxonomy["note_scene"],
            node_status=taxonomy["node_status"],
            lifecycle_stage=taxonomy["lifecycle_stage"],
            color=None,
            weight_mode=None,
            private_level=CODEX_BRIDGE_QUICK_NOTE_PRIVATE_LEVEL,
            custom_fields=[],
            created_at=now,
            updated_at=now,
            start_at=now,
            history=[],
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        return {
            "id": str(note.id),
            "user_id": int(note.user_id),
            "title": str(note.title or ""),
            "private_level": int(note.private_level),
            "note_form": str(note.note_form or ""),
            "start_at": float(note.start_at),
        }


def _is_codex_bridge_quick_dash_note_message(message: dict[str, Any]) -> bool:
    text = str(message.get("text") or "").strip()
    return text == CODEX_BRIDGE_QUICK_NOTE_TEXT and not _message_images(message)


def _handle_codex_bridge_quick_dash_note(account_id: str, message: dict[str, Any]) -> dict[str, Any]:
    sender_id = str(message.get("from_user_id") or "").strip()
    note = _create_codex_bridge_quick_dash_note(account_id)
    note_time = _format_bridge_datetime(datetime.fromtimestamp(note["start_at"], tz=_bridge_timezone()))
    sent = send_text_message(
        account_id,
        to_user_id=sender_id,
        text=f"已记录星图笔记：-（{note_time}）",
        context_token=str(message.get("context_token") or ""),
    )
    return {
        "message_id": str(sent.get("message_id") or ""),
        "to_user_id": sender_id,
        "text": sent,
        "images": [],
        "errors": [],
        "note": note,
        "handled_without_ai": True,
    }


def _clip_wechat_reply(value: str) -> str:
    text = value.strip()
    if len(text) <= CODEX_BRIDGE_MAX_REPLY_CHARS:
        return text
    return f"{text[:CODEX_BRIDGE_MAX_REPLY_CHARS - 20].rstrip()}\n\n[回复过长，已截断]"


def _strip_path_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _resolve_codex_bridge_image_path(value: str) -> Path | None:
    raw_path = _strip_path_quotes(value)
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    return path


def _extract_codex_bridge_reply_images(content: str) -> tuple[str, list[Path]]:
    text_lines: list[str] = []
    image_paths: list[Path] = []
    for line in content.splitlines():
        match = CODEX_BRIDGE_IMAGE_DIRECTIVE_RE.match(line)
        if match:
            image_path = _resolve_codex_bridge_image_path(match.group("path"))
            if image_path is not None:
                image_paths.append(image_path)
            continue
        text_lines.append(line)
    return "\n".join(text_lines).strip(), image_paths


def _normalize_codex_bridge_session_id(value: Any) -> str:
    session_id = str(value or "").strip()
    if not session_id:
        return ""
    try:
        uuid.UUID(session_id)
    except ValueError:
        return ""
    return session_id


def _load_codex_bridge_session_id(account_id: str, sender_id: str) -> str:
    try:
        bridge_config = _load_codex_bridge_config(account_id)
    except WechatIlinkError:
        return ""
    sessions = bridge_config.get("sessions")
    if not isinstance(sessions, dict):
        return ""
    return _normalize_codex_bridge_session_id(sessions.get(sender_id))


def _save_codex_bridge_session_id(account_id: str, sender_id: str, session_id: str) -> None:
    normalized_session_id = _normalize_codex_bridge_session_id(session_id)
    if not sender_id or not normalized_session_id:
        return

    store = _read_store()
    try:
        account = _load_account(store, account_id)
    except WechatIlinkError:
        return
    bridge_config = account.get("codex_bridge")
    bridge_config = dict(bridge_config) if isinstance(bridge_config, dict) else {}
    sessions = bridge_config.get("sessions")
    sessions = dict(sessions) if isinstance(sessions, dict) else {}
    sessions[sender_id] = normalized_session_id
    bridge_config["sessions"] = sessions
    account["codex_bridge"] = bridge_config
    account["updated_at"] = _now()
    _write_store(store)


def handle_codex_bridge_message(
    account_id: str,
    message: dict[str, Any],
    *,
    model: str | None = None,
    command: str | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any] | None:
    if not _is_user_bridge_message(message):
        return None

    if _is_codex_bridge_quick_dash_note_message(message):
        return _handle_codex_bridge_quick_dash_note(account_id, message)

    resolved_model = _normalize_codex_bridge_model(model)
    resolved_command = _normalize_codex_bridge_command(command)
    sender_id = str(message.get("from_user_id") or "").strip()
    session_id = _load_codex_bridge_session_id(account_id, sender_id)
    image_payloads = _build_bridge_image_payloads(message)
    provider = _build_codex_bridge_provider(
        model=resolved_model,
        command=resolved_command,
        session_id=session_id,
    )
    response = chat_with_provider(
        provider_id=provider.id,
        messages=[
            {
                "role": "user",
                "content": _build_bridge_user_prompt(message),
                "images": image_payloads,
            }
        ],
        model=resolved_model,
        system_prompt=_build_codex_bridge_system_prompt(system_prompt),
        timeout_seconds=CODEX_BRIDGE_DEFAULT_TIMEOUT_SECONDS,
        extra_providers=(provider,),
    )
    returned_session_id = _normalize_codex_bridge_session_id(response.get("session_id"))
    if returned_session_id:
        _save_codex_bridge_session_id(account_id, sender_id, returned_session_id)
    reply_text, reply_image_paths = _extract_codex_bridge_reply_images(str(response.get("content") or ""))
    reply_text = _clip_wechat_reply(reply_text)
    context_token = str(message.get("context_token") or "")
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    if reply_text:
        results.append(
            send_text_message(
                account_id,
                to_user_id=sender_id,
                text=reply_text,
                context_token=context_token,
            )
        )

    for image_path in reply_image_paths:
        if not image_path.exists() or not image_path.is_file():
            errors.append(f"图片不存在：{image_path}")
            continue
        try:
            image_bytes = image_path.read_bytes()
        except OSError as exc:
            errors.append(f"读取图片失败：{image_path}（{exc}）")
            continue
        try:
            results.append(
                send_image_message(
                    account_id,
                    to_user_id=sender_id,
                    image_bytes=image_bytes,
                    filename=image_path.name,
                    mime_type=_infer_image_mime(image_bytes, image_path.name, fallback_jpeg=False),
                    context_token=context_token,
                    timeout_seconds=max(DEFAULT_MEDIA_TIMEOUT_SECONDS, 60.0),
                )
            )
        except WechatIlinkError as exc:
            errors.append(f"发送图片失败：{image_path}（{exc}）")

    if not results:
        fallback_text = "Codex CLI 没有返回有效内容。"
        if errors:
            fallback_text = "Codex CLI 返回了图片发送请求，但发送失败：\n" + "\n".join(errors)
        results.append(
            send_text_message(
                account_id,
                to_user_id=sender_id,
                text=_clip_wechat_reply(fallback_text),
                context_token=context_token,
            )
        )
    elif errors:
        results.append(
            send_text_message(
                account_id,
                to_user_id=sender_id,
                text=_clip_wechat_reply("部分图片发送失败：\n" + "\n".join(errors)),
                context_token=context_token,
            )
        )

    return {
        "message_id": str(results[-1].get("message_id") or ""),
        "to_user_id": sender_id,
        "text": results[0] if reply_text else None,
        "images": [result for result in results if isinstance(result.get("image"), dict)],
        "errors": errors,
    }


def _load_codex_bridge_config(account_id: str) -> dict[str, Any]:
    store = _read_store()
    account = _load_account(store, account_id)
    bridge_config = account.get("codex_bridge")
    return dict(bridge_config) if isinstance(bridge_config, dict) else {}


def _save_codex_bridge_config(account_id: str, bridge_config: dict[str, Any]) -> dict[str, Any]:
    store = _read_store()
    account = _load_account(store, account_id)
    account["codex_bridge"] = dict(bridge_config)
    account["updated_at"] = _now()
    _write_store(store)
    return _serialize_account(account)


def _codex_bridge_loop(account_id: str) -> None:
    while True:
        with _codex_bridge_lock:
            worker = _codex_bridge_workers.get(account_id)
        if worker is None or worker.stop_event.is_set():
            return

        try:
            bridge_config = _load_codex_bridge_config(account_id)
            if not bool(bridge_config.get("enabled", False)):
                return
            payload = get_updates(account_id, timeout_seconds=DEFAULT_LONG_POLL_TIMEOUT_SECONDS)
            worker.last_poll_at = _now()
            if payload.get("ret") not in {0, None}:
                worker.last_error = str(payload.get("errmsg") or payload.get("errcode") or "微信拉取消息失败")
                worker.stop_event.wait(5)
                continue

            for message in payload.get("messages") or []:
                if worker.stop_event.is_set():
                    return
                if not isinstance(message, dict) or not _is_user_bridge_message(message):
                    continue
                worker.last_message_at = _now()
                try:
                    result = handle_codex_bridge_message(
                        account_id,
                        message,
                        model=str(bridge_config.get("model") or worker.model),
                        command=str(bridge_config.get("command") or worker.command),
                        system_prompt=str(bridge_config.get("system_prompt") or ""),
                    )
                    if result is not None:
                        worker.handled_count += 1
                        worker.last_reply_at = _now()
                        worker.last_error = ""
                except Exception as exc:  # pragma: no cover - worker keeps listening after one bad message.
                    worker.last_error = str(exc)
                    try:
                        send_text_message(
                            account_id,
                            to_user_id=str(message.get("from_user_id") or ""),
                            text=f"CodeClaw 处理失败：{exc}",
                            context_token=str(message.get("context_token") or ""),
                        )
                    except Exception:
                        pass
            worker.stop_event.wait(1)
        except Exception as exc:  # pragma: no cover - worker should survive transient backend errors.
            worker.last_error = str(exc)
            worker.stop_event.wait(5)


def _get_codex_bridge_status(account_id: str, *, bridge_config: dict[str, Any] | None = None) -> dict[str, Any]:
    with _codex_bridge_lock:
        worker = _codex_bridge_workers.get(account_id)
        running = bool(worker and worker.thread.is_alive() and not worker.stop_event.is_set())
        worker_payload = {
            "started_at": worker.started_at if worker else None,
            "handled_count": worker.handled_count if worker else 0,
            "last_poll_at": worker.last_poll_at if worker else None,
            "last_message_at": worker.last_message_at if worker else None,
            "last_reply_at": worker.last_reply_at if worker else None,
            "last_error": worker.last_error if worker else "",
        }

    config = dict(bridge_config or {})
    return {
        "enabled": bool(config.get("enabled", False)),
        "running": running,
        "model": _normalize_codex_bridge_model(str(config.get("model") or "")),
        "command": _normalize_codex_bridge_command(str(config.get("command") or "")),
        **worker_payload,
    }


def start_codex_bridge(
    account_id: str,
    *,
    model: str | None = None,
    command: str | None = None,
    system_prompt: str | None = None,
    owner_user_id: int | None = None,
) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    resolved_model = _normalize_codex_bridge_model(model)
    resolved_command = _normalize_codex_bridge_command(command)
    existing_config = _load_codex_bridge_config(normalized_account_id)
    existing_sessions = existing_config.get("sessions")
    bridge_config = {
        "enabled": True,
        "model": resolved_model,
        "command": resolved_command,
        "system_prompt": (system_prompt or "").strip(),
        "updated_at": _now(),
    }
    if isinstance(existing_sessions, dict):
        bridge_config["sessions"] = dict(existing_sessions)
    if owner_user_id is not None:
        bridge_config["owner_user_id"] = int(owner_user_id)
    elif _normalize_owner_user_id(existing_config.get("owner_user_id")) is not None:
        bridge_config["owner_user_id"] = int(existing_config["owner_user_id"])
    summary = _save_codex_bridge_config(normalized_account_id, bridge_config)

    with _codex_bridge_lock:
        existing = _codex_bridge_workers.get(normalized_account_id)
        if existing and existing.thread.is_alive() and not existing.stop_event.is_set():
            existing.model = resolved_model
            existing.command = resolved_command
            return summary

        stop_event = threading.Event()
        thread = threading.Thread(
            target=_codex_bridge_loop,
            args=(normalized_account_id,),
            name=f"codeyun-wechat-codex-{normalized_account_id}",
            daemon=True,
        )
        worker = CodexBridgeWorker(
            account_id=normalized_account_id,
            stop_event=stop_event,
            thread=thread,
            started_at=_now(),
            model=resolved_model,
            command=resolved_command,
        )
        _codex_bridge_workers[normalized_account_id] = worker
        thread.start()
    return _save_codex_bridge_config(normalized_account_id, bridge_config)


def start_enabled_codex_bridges() -> list[dict[str, Any]]:
    store = _read_store()
    accounts = store.get("accounts", {})
    if not isinstance(accounts, dict):
        return []

    started: list[dict[str, Any]] = []
    for account in accounts.values():
        if not isinstance(account, dict):
            continue
        account_id = str(account.get("account_id") or "").strip()
        bridge_config = account.get("codex_bridge")
        if not account_id or not isinstance(bridge_config, dict):
            continue
        if not bool(bridge_config.get("enabled", False)):
            continue

        with _codex_bridge_lock:
            existing = _codex_bridge_workers.get(account_id)
            if existing and existing.thread.is_alive() and not existing.stop_event.is_set():
                continue

        started.append(
            start_codex_bridge(
                account_id,
                model=str(bridge_config.get("model") or ""),
                command=str(bridge_config.get("command") or ""),
                system_prompt=str(bridge_config.get("system_prompt") or ""),
            )
        )
    return started


def shutdown_codex_bridges(*, join_timeout: float = 2.0) -> None:
    with _codex_bridge_lock:
        workers = list(_codex_bridge_workers.values())
        _codex_bridge_workers.clear()

    for worker in workers:
        worker.stop_event.set()

    for worker in workers:
        if worker.thread.is_alive():
            worker.thread.join(timeout=max(0.0, join_timeout))


def stop_codex_bridge(account_id: str) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    bridge_config = _load_codex_bridge_config(normalized_account_id)
    bridge_config["enabled"] = False
    bridge_config["updated_at"] = _now()
    with _codex_bridge_lock:
        worker = _codex_bridge_workers.pop(normalized_account_id, None)
        if worker is not None:
            worker.stop_event.set()
    return _save_codex_bridge_config(normalized_account_id, bridge_config)


def _send_message_items(
    account_id: str,
    *,
    to_user_id: str,
    items: list[dict[str, Any]],
    context_token: str | None = None,
    timeout_seconds: float = DEFAULT_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    target_user = to_user_id.strip()
    if not target_user:
        raise WechatIlinkError("接收方不能为空")
    if not items:
        raise WechatIlinkError("消息内容不能为空")

    store = _read_store()
    account = _load_account(store, account_id)
    token = _decrypt_account_token(account)
    context_tokens = account.get("context_tokens")
    if not isinstance(context_tokens, dict):
        context_tokens = {}
    effective_context_token = (context_token or "").strip() or str(context_tokens.get(target_user) or "")
    client_id = ""
    for item in items:
        client_id = f"codeyun-wechat-{uuid.uuid4().hex}"
        message_payload: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": target_user,
            "client_id": client_id,
            "message_type": MESSAGE_TYPE_BOT,
            "message_state": MESSAGE_STATE_FINISH,
            "item_list": [item],
        }
        if effective_context_token:
            message_payload["context_token"] = effective_context_token
        payload = {
            "msg": message_payload,
            "base_info": _build_base_info(),
        }
        _post_json(
            _normalize_base_url(str(account.get("base_url") or "")),
            "ilink/bot/sendmessage",
            payload,
            token=token,
            label="sendmessage",
            timeout_seconds=max(1.0, timeout_seconds),
        )
    account["updated_at"] = _now()
    _write_store(store)
    return {
        "message_id": client_id,
        "to_user_id": target_user,
        "used_context_token": bool(effective_context_token),
    }


def send_text_message(
    account_id: str,
    *,
    to_user_id: str,
    text: str,
    context_token: str | None = None,
    timeout_seconds: float = DEFAULT_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    message_text = text.strip()
    if not message_text:
        raise WechatIlinkError("消息内容不能为空")
    return _send_message_items(
        account_id,
        to_user_id=to_user_id,
        items=[
            {
                "type": MESSAGE_ITEM_TEXT,
                "text_item": {"text": message_text},
            }
        ],
        context_token=context_token,
        timeout_seconds=timeout_seconds,
    )


def send_image_message(
    account_id: str,
    *,
    to_user_id: str,
    image_bytes: bytes,
    filename: str = "",
    mime_type: str = "",
    text: str = "",
    context_token: str | None = None,
    timeout_seconds: float = DEFAULT_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    target_user = to_user_id.strip()
    if not target_user:
        raise WechatIlinkError("接收方不能为空")
    if not image_bytes:
        raise WechatIlinkError("图片内容不能为空")
    if len(image_bytes) > MEDIA_UPLOAD_MAX_BYTES:
        raise WechatIlinkError(f"图片不能超过 {MEDIA_UPLOAD_MAX_BYTES // 1024 // 1024}MB")
    resolved_mime_type = (mime_type or "").strip().lower()
    if not resolved_mime_type.startswith("image/"):
        resolved_mime_type = _infer_image_mime(image_bytes, filename, fallback_jpeg=False)
    if not resolved_mime_type.startswith("image/"):
        raise WechatIlinkError("只能发送图片文件")

    store = _read_store()
    account = _load_account(store, account_id)
    token = _decrypt_account_token(account)
    base_url = _normalize_base_url(str(account.get("base_url") or ""))
    uploaded = _upload_image_to_cdn(
        base_url=base_url,
        token=token,
        to_user_id=target_user,
        image_bytes=image_bytes,
        timeout_seconds=timeout_seconds,
    )
    image_item = {
        "type": MESSAGE_ITEM_IMAGE,
        "image_item": {
            "media": {
                "encrypt_query_param": uploaded["download_encrypted_query_param"],
                "aes_key": base64.b64encode(uploaded["aeskey_hex"].encode("ascii")).decode("ascii"),
                "encrypt_type": 1,
            },
            "mid_size": uploaded["file_size_ciphertext"],
        },
    }
    items: list[dict[str, Any]] = []
    if text.strip():
        items.append({"type": MESSAGE_ITEM_TEXT, "text_item": {"text": text.strip()}})
    items.append(image_item)
    sent = _send_message_items(
        account_id,
        to_user_id=target_user,
        items=items,
        context_token=context_token,
        timeout_seconds=timeout_seconds,
    )
    sent["image"] = {
        "id": uploaded["filekey"],
        "mime_type": resolved_mime_type,
        "size": len(image_bytes),
    }
    return sent


def get_runtime_status() -> dict[str, Any]:
    with _login_lock:
        _purge_expired_logins()
        active_logins = [
            {
                "session_key": session.session_key,
                "qrcode_url": session.qrcode_url,
                "status": session.status,
                "started_at": session.started_at,
            }
            for session in _login_sessions.values()
        ]
    return {
        "base_url": DEFAULT_API_BASE_URL,
        "bot_type": DEFAULT_BOT_TYPE,
        "channel_version": CHANNEL_VERSION,
        "accounts": list_accounts(),
        "active_logins": active_logins,
    }
