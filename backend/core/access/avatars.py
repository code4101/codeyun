"""Account avatars are keyed by immutable user ID, independent of login names.

Only normalized, metadata-free 256px WebP images are persisted. Defaults are
rendered by the shared frontend avatar component and require no stored image.
"""
from io import BytesIO
from pathlib import Path
import os
import tempfile

from PIL import Image, ImageOps, UnidentifiedImageError
from backend.core.settings import get_settings

MAX_AVATAR_BYTES = 5 * 1024 * 1024


def avatar_path(user_id: int) -> Path:
    if user_id <= 0:
        raise ValueError('Invalid user ID')
    return get_settings().data_dir / 'avatars' / f'{user_id}.webp'


def avatar_url(user_id: int) -> str | None:
    path = avatar_path(user_id)
    try:
        version = path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    return f'/api/auth/avatars/{user_id}?v={version}'


def save_avatar(user_id: int, content: bytes) -> None:
    """Validate decoded content, center-crop and atomically replace this user's image."""
    if len(content) > MAX_AVATAR_BYTES:
        raise ValueError('头像不能超过 5 MB')
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise ValueError('请选择 JPG、PNG 或 WebP 图片')
            if source.width * source.height > 20_000_000:
                raise ValueError('图片像素过大，请使用 2000 万像素以内的图片')
            normalized = ImageOps.fit(ImageOps.exif_transpose(source).convert('RGBA'), (256, 256), method=Image.Resampling.LANCZOS)
            # Rebuild pixels so EXIF, ICC and other source metadata are never retained.
            clean = Image.frombytes('RGBA', normalized.size, normalized.tobytes())
            output = BytesIO()
            clean.save(output, format='WEBP', quality=85)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('图片无效或无法读取') from exc
    path = avatar_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(output.getvalue())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reset_avatar(user_id: int) -> None:
    avatar_path(user_id).unlink(missing_ok=True)
