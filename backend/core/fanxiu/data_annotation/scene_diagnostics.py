from __future__ import annotations

import io
import json
import math
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from backend.core.temp_paths import codeyun_temp_root, prune_temp_files
from pyxllib.autogui import View


_DEFAULT_MAX_BYTES = 1 * 1024 * 1024 * 1024
_DEFAULT_MAX_FILES = 2000
_DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _retention_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _diagnostic_root() -> Path:
    return codeyun_temp_root("fanxiu_scene_diagnostics")


def _prune_diagnostics() -> dict[str, int]:
    max_bytes = _retention_int("CODEYUN_FANXIU_SCENE_DIAGNOSTIC_MAX_BYTES", _DEFAULT_MAX_BYTES)
    max_files = _retention_int("CODEYUN_FANXIU_SCENE_DIAGNOSTIC_MAX_FILES", _DEFAULT_MAX_FILES)
    max_age_seconds = _retention_int(
        "CODEYUN_FANXIU_SCENE_DIAGNOSTIC_MAX_AGE_SECONDS",
        _DEFAULT_MAX_AGE_SECONDS,
    )
    return prune_temp_files(
        _diagnostic_root(),
        patterns=("*.png", "*.json"),
        recursive=True,
        max_files=max_files,
        target_files=max(0, int(max_files * 0.9)),
        max_bytes=max_bytes,
        target_bytes=max(0, int(max_bytes * 0.9)),
        max_age_seconds=max_age_seconds,
    )


def _safe_stem(value: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in str(value or ""))
    return normalized.strip("_")[:48] or "scene"


def _artifact_paths(kind: str, label: str) -> tuple[Path, Path]:
    directory = _diagnostic_root() / _safe_stem(kind) / time.strftime("%Y%m%d")
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}_{_safe_stem(label)}"
    return directory / f"{stem}.png", directory / f"{stem}.json"


def _frame_bytes(runner: Any, source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, Path):
        return source.read_bytes()
    text = str(source or "")
    if text.startswith("data:image"):
        return runner._decode_frame_data_url(text)
    path = Path(text)
    if path.is_file():
        return path.read_bytes()
    raise ValueError("诊断画面必须是 image data URL、图片路径或图片字节")


def _frame_data_url(runner: Any, source: str | Path | bytes) -> str:
    if isinstance(source, str) and source.startswith("data:image"):
        return source
    return runner._data_url(_frame_bytes(runner, source))


def _load_image(runner: Any, source: str | Path | bytes):
    from PIL import Image

    return Image.open(io.BytesIO(_frame_bytes(runner, source))).convert("RGB")


def _font(size: int):
    from PIL import ImageFont

    for path in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ):
        try:
            if path.is_file():
                return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scene_id(runner: Any, image: dict[str, Any]) -> int | None:
    try:
        value = runner._image_number(image)
    except Exception:
        value = image.get("id") or image.get("number")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _scene_reference_data_url(runner: Any, ctx: dict[str, Any], image: dict[str, Any]) -> str:
    return runner._scene_frame_data_url_from_reference(ctx, image)


def _shape_box(shape: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int] | None:
    try:
        x = float(shape.get("x") or 0)
        y = float(shape.get("y") or 0)
        w = float(shape.get("w") or 0)
        h = float(shape.get("h") or 0)
    except (TypeError, ValueError):
        return None
    if max(abs(x), abs(y), abs(w), abs(h)) <= 2.0:
        x, y, w, h = x * width, y * height, w * width, h * height
    left = max(0, min(width - 1, round(x)))
    top = max(0, min(height - 1, round(y)))
    right = max(0, min(width - 1, round(x + w)))
    bottom = max(0, min(height - 1, round(y + h)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _draw_scene_shapes(image, scene: dict[str, Any]) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    label_font = _font(max(14, round(image.width / 58)))
    for shape in View(scene).get_shapes(include_groups=False):
        raw = shape.raw
        box = _shape_box(raw, image.width, image.height)
        if box is None:
            continue
        identity = bool(raw.get("isSceneIdentity"))
        color = (220, 0, 0) if identity else (0, 0, 0)
        width = max(2, round(image.width / 300))
        draw.rectangle(box, outline=color, width=width)
        title = str(raw.get("title") or raw.get("id") or "Shape")
        source_scene_id = raw.get("_inheritanceSourceSceneId")
        source = f" #{source_scene_id}" if source_scene_id else ""
        label = f"[{title}]{source}"
        text_box = draw.textbbox((box[0], box[1]), label, font=label_font)
        text_width = max(1, text_box[2] - text_box[0])
        text_height = max(1, text_box[3] - text_box[1])
        label_top = max(0, box[1] - text_height - 4)
        draw.rectangle(
            (box[0], label_top, min(image.width - 1, box[0] + text_width + 6), box[1]),
            fill=color,
        )
        draw.text((box[0] + 3, label_top + 1), label, fill=(255, 255, 255), font=label_font)


def _fit_image(image, size: tuple[int, int]):
    from PIL import Image

    target_width, target_height = size
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    fitted = image.copy()
    fitted.thumbnail((target_width, target_height), resampling)
    canvas = Image.new("RGB", size, (238, 238, 238))
    left = (target_width - fitted.width) // 2
    top = (target_height - fitted.height) // 2
    canvas.paste(fitted, (left, top))
    return canvas


def _ellipsize(draw, text: str, *, font, max_width: int) -> str:
    value = str(text or "")
    if draw.textlength(value, font=font) <= max_width:
        return value
    suffix = "…"
    while value and draw.textlength(value + suffix, font=font) > max_width:
        value = value[:-1]
    return value + suffix


def _write_artifact(image, *, kind: str, label: str, metadata: dict[str, Any]) -> str:
    image_path, metadata_path = _artifact_paths(kind, label)
    image.save(image_path, format="PNG")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _prune_diagnostics()
    return str(image_path)


def save_scene_diagnostic_frame(
    runner: Any,
    frame: str | Path | bytes,
    *,
    kind: str,
    label: str,
    expected_scene_ids: Iterable[int],
    matched_scene_id: int | None = None,
    matched_layer: int | None = None,
) -> str | None:
    """Persist one raw recognition frame with bounded temporary retention."""

    try:
        image_path, metadata_path = _artifact_paths(kind, label)
        image_path.write_bytes(_frame_bytes(runner, frame))
        metadata_path.write_text(
            json.dumps(
                {
                    "kind": str(kind),
                    "label": str(label),
                    "captured_at": time.time(),
                    "expected_scene_ids": list(dict.fromkeys(int(item) for item in expected_scene_ids)),
                    "matched_scene_id": int(matched_scene_id) if matched_scene_id is not None else None,
                    "matched_layer": int(matched_layer) if matched_layer is not None else None,
                    "frame_path": str(image_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _prune_diagnostics()
        return str(image_path)
    except Exception:
        return None


def render_scene_comparison(
    runner: Any,
    ctx: dict[str, Any],
    failure_frame: str | Path | bytes,
    scene_id: int,
) -> str:
    """Render one failure frame beside one scene with all effective Shapes."""

    images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
    tree = ctx.get("asset_tree")
    if isinstance(tree, list):
        try:
            images = runner._shape_inheritance_resolution(tree).images
        except Exception:
            # Some focused diagnostic contexts already contain the effective
            # image projection but not a complete raw asset tree.
            pass
    scene = images.get(int(scene_id))
    if not isinstance(scene, dict):
        raise KeyError(f"场景 #{int(scene_id)} 不存在")
    left = _load_image(runner, failure_frame)
    right = _load_image(runner, _scene_reference_data_url(runner, ctx, scene))
    _draw_scene_shapes(left, scene)
    _draw_scene_shapes(right, scene)

    from PIL import Image, ImageDraw

    tile_width = max(left.width, right.width)
    tile_height = max(left.height, right.height)
    header_height = max(48, round(tile_width * 0.07))
    gap = max(12, round(tile_width * 0.02))
    canvas = Image.new("RGB", (tile_width * 2 + gap, tile_height + header_height), (245, 245, 245))
    canvas.paste(_fit_image(left, (tile_width, tile_height)), (0, header_height))
    canvas.paste(_fit_image(right, (tile_width, tile_height)), (tile_width + gap, header_height))
    draw = ImageDraw.Draw(canvas)
    title_font = _font(max(18, round(tile_width / 42)))
    scene_title = str(scene.get("title") or scene.get("filename") or "")
    draw.text((12, 10), "失败现场", fill=(0, 0, 0), font=title_font)
    draw.text(
        (tile_width + gap + 12, 10),
        _ellipsize(
            draw,
            f"#{int(scene_id)} {scene_title}",
            font=title_font,
            max_width=tile_width - 24,
        ),
        fill=(0, 0, 0),
        font=title_font,
    )
    return _write_artifact(
        canvas,
        kind="comparison",
        label=f"scene_{int(scene_id)}",
        metadata={
            "kind": "scene_comparison",
            "scene_id": int(scene_id),
            "failure_frame": str(failure_frame) if not isinstance(failure_frame, bytes) else "<bytes>",
        },
    )


def render_unknown_scene_overview(
    runner: Any,
    ctx: dict[str, Any],
    failure_frame: str | Path | bytes,
    scenes: list[int],
    *,
    top_k: int = 2,
) -> str:
    """Render a Shape-free contact sheet for Agent scene-identity review."""

    images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
    layer0_ids = list(dict.fromkeys(int(scene_id) for scene_id in scenes))
    frame_data_url = _frame_data_url(runner, failure_frame)
    all_scene_ids = list(dict.fromkeys(
        int(scene_id)
        for image in images.values()
        if isinstance(image, dict)
        and (scene_id := _scene_id(runner, image)) is not None
    ))

    def similarity(scene_id: int) -> tuple[int, float]:
        image = images.get(scene_id)
        if not isinstance(image, dict):
            return scene_id, 0.0
        try:
            score = runner._scene_reference_similarity(ctx, image, frame_data_url)
        except Exception:
            score = None
        return scene_id, float(score or 0.0)

    workers = min(32, max(1, len(all_scene_ids)))
    if len(all_scene_ids) <= 1:
        ranked = [similarity(scene_id) for scene_id in all_scene_ids]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fanxiu-scene-overview") as executor:
            ranked = list(executor.map(similarity, all_scene_ids))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    global_candidates = ranked[: max(0, int(top_k))]

    sources: dict[int, list[str]] = {}
    ordered_ids: list[int] = []
    for scene_id in layer0_ids:
        if scene_id not in ordered_ids:
            ordered_ids.append(scene_id)
        sources.setdefault(scene_id, []).append("Layer 0")
    for index, (scene_id, score) in enumerate(global_candidates, start=1):
        if scene_id not in ordered_ids:
            ordered_ids.append(scene_id)
        sources.setdefault(scene_id, []).append(f"全局相似 {index}（{score:.1f}%）")

    from PIL import Image, ImageDraw

    entries: list[tuple[Any, str, str, int | None]] = [
        (_load_image(runner, failure_frame), "直播画面 x", "", None),
    ]
    for scene_id in ordered_ids:
        scene = images.get(scene_id)
        if not isinstance(scene, dict):
            continue
        try:
            image = _load_image(runner, _scene_reference_data_url(runner, ctx, scene))
        except Exception:
            continue
        title = str(scene.get("title") or scene.get("filename") or "")
        source = " / ".join(sources.get(scene_id) or ["候选"])
        entries.append((image, source, f"#{scene_id} {title}", scene_id))

    tile_width = 360
    tile_height = 640
    header_height = 58
    count = max(1, len(entries))
    columns = min(4, max(1, math.ceil(math.sqrt(count))))
    rows = math.ceil(count / columns)
    canvas = Image.new("RGB", (columns * tile_width, rows * (tile_height + header_height)), (245, 245, 245))
    draw = ImageDraw.Draw(canvas)
    source_font = _font(16)
    title_font = _font(18)
    for index, (image, source, title, _scene_id_value) in enumerate(entries):
        column = index % columns
        row = index // columns
        left = column * tile_width
        top = row * (tile_height + header_height)
        draw.rectangle((left, top, left + tile_width - 1, top + header_height - 1), fill=(230, 230, 230))
        draw.text(
            (left + 8, top + 5),
            _ellipsize(draw, source, font=source_font, max_width=tile_width - 16),
            fill=(0, 0, 0),
            font=source_font,
        )
        if title:
            draw.text(
                (left + 8, top + 29),
                _ellipsize(draw, title, font=title_font, max_width=tile_width - 16),
                fill=(0, 0, 0),
                font=title_font,
            )
        canvas.paste(_fit_image(image, (tile_width, tile_height)), (left, top + header_height))

    return _write_artifact(
        canvas,
        kind="overview",
        label="unknown_scene",
        metadata={
            "kind": "unknown_scene_overview",
            "failure_frame": str(failure_frame) if not isinstance(failure_frame, bytes) else "<bytes>",
            "layer0_scene_ids": layer0_ids,
            "global_candidates": [
                {"scene_id": scene_id, "similarity": round(score, 3)}
                for scene_id, score in global_candidates
            ],
            "displayed_scene_ids": ordered_ids,
            "shapes_rendered": False,
        },
    )
