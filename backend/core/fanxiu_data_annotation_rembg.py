from __future__ import annotations

import base64
import io

from PIL import Image, UnidentifiedImageError

from backend.core.fanxiu_game_window_models import FanxiuDataAnnotationRemoveBackgroundResponse


def _decode_image_data_url(data_url: str) -> bytes:
    text = str(data_url or "").strip()
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode("".join(text.split()), validate=False)
    except Exception as exc:
        raise ValueError("图片不是有效的 base64 data URL") from exc


def _png_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _alpha_mask_from_rgba(image: Image.Image) -> Image.Image:
    alpha = image.convert("RGBA").getchannel("A")
    return Image.merge("RGBA", (alpha, alpha, alpha, Image.new("L", alpha.size, 255)))


def remove_fanxiu_data_annotation_background(
    image_data_url: str,
    *,
    model: str = "isnet-general-use",
    alpha_matting: bool = False,
    post_process_mask: bool = True,
) -> FanxiuDataAnnotationRemoveBackgroundResponse:
    image_bytes = _decode_image_data_url(image_data_url)
    if not image_bytes:
        raise ValueError("图片数据为空")

    normalized_model = (model or "isnet-general-use").strip() or "isnet-general-use"
    try:
        import rembg
    except ImportError as exc:
        raise RuntimeError("rembg 未安装，请先执行 uv sync") from exc

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            source_rgba = source.convert("RGBA")
    except UnidentifiedImageError as exc:
        raise ValueError("无法解析图片数据") from exc

    try:
        session = rembg.new_session(normalized_model)
        result = rembg.remove(
            source_rgba,
            session=session,
            alpha_matting=alpha_matting,
            post_process_mask=post_process_mask,
        )
    except Exception as exc:
        raise RuntimeError(f"rembg 抠图失败：{exc}") from exc

    if isinstance(result, Image.Image):
        result_rgba = result.convert("RGBA")
    elif isinstance(result, (bytes, bytearray)):
        with Image.open(io.BytesIO(result)) as result_image:
            result_rgba = result_image.convert("RGBA")
    else:
        raise RuntimeError("rembg 返回了不支持的结果类型")

    alpha_mask = _alpha_mask_from_rgba(result_rgba)
    return FanxiuDataAnnotationRemoveBackgroundResponse(
        ok=True,
        model=normalized_model,
        width=result_rgba.width,
        height=result_rgba.height,
        alpha_mask_data_url=_png_data_url(alpha_mask),
        result_data_url=_png_data_url(result_rgba),
    )
