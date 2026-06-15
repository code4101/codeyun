from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import requests
from fastapi import HTTPException
from fastapi.responses import Response

from backend.core.fanxiu.runtime.mumu_control import (
    capture_mumu_window_frame,
    click_mumu_window_processed_point,
    drag_mumu_window_processed_points,
    keyevent_mumu_adb,
    keyevents_mumu_adb,
    match_fanxiu_screenshot_box_frame,
    screencap_mumu_adb_cached_png,
    screencap_mumu_adb_png,
    text_mumu_adb,
)
from backend.models import UserDevice

REMOTE_DEVICE_DIRECT_PROXIES = {"http": "", "https": "", "all": "", "no_proxy": "*"}


def remote_entry_base_url(entry: UserDevice) -> str:
    if entry.mode != "remote" or not entry.server_url:
        raise HTTPException(status_code=400, detail="远程设备入口未配置后端地址")
    return entry.server_url.rstrip("/")


def remote_entry_headers(entry: UserDevice) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
    }


def normalize_game_window2_title(title: str | None) -> str | None:
    value = (title or "").strip()
    if not value:
        return title
    if "mumu" in value.lower():
        return "MuMu"
    return title


def game_window2_desktop_title(title: str | None) -> str:
    return normalize_game_window2_title(title) or "MuMu"


def extract_stream_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return response.text.strip() or f"画面流服务返回 HTTP {response.status_code}"


_GAME_WINDOW2_MATCH_CACHE_TTL = 8.0
_GAME_WINDOW2_MATCH_CACHE_MAX_SIZE = 64
_game_window2_match_cache_lock = threading.Lock()
_game_window2_match_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_game_window2_match_inflight: dict[str, threading.Event] = {}


def _clone_json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _game_window2_match_cache_key(payload: dict[str, Any]) -> str:
    frame_data_url = str(payload.get("current_frame_data_url") or "")
    cache_payload = dict(payload)
    if frame_data_url:
        cache_payload["current_frame_data_url"] = hashlib.sha256(frame_data_url.encode("utf-8")).hexdigest()
    else:
        return ""
    raw = json.dumps(cache_payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_game_window2_match_cache(cache_key: str) -> dict[str, Any] | None:
    if not cache_key:
        return None
    now = time.monotonic()
    with _game_window2_match_cache_lock:
        cached = _game_window2_match_cache.get(cache_key)
        if cached is None:
            return None
        cached_at, result = cached
        if now - cached_at > _GAME_WINDOW2_MATCH_CACHE_TTL:
            _game_window2_match_cache.pop(cache_key, None)
            return None
        return _clone_json_dict(result)


def _set_game_window2_match_cache(cache_key: str, result: dict[str, Any]) -> None:
    if not cache_key:
        return
    now = time.monotonic()
    cloned = _clone_json_dict(result)
    with _game_window2_match_cache_lock:
        expired_keys = [
            key for key, (cached_at, _) in _game_window2_match_cache.items()
            if now - cached_at > _GAME_WINDOW2_MATCH_CACHE_TTL
        ]
        for key in expired_keys:
            _game_window2_match_cache.pop(key, None)
        while len(_game_window2_match_cache) >= _GAME_WINDOW2_MATCH_CACHE_MAX_SIZE:
            oldest_key = min(_game_window2_match_cache, key=lambda key: _game_window2_match_cache[key][0])
            _game_window2_match_cache.pop(oldest_key, None)
        _game_window2_match_cache[cache_key] = (now, cloned)


def run_game_window2_match_with_cache(payload: dict[str, Any], producer: Any) -> dict[str, Any]:
    cache_key = _game_window2_match_cache_key(payload)
    cached = _get_game_window2_match_cache(cache_key)
    if cached is not None:
        return cached

    owner = False
    inflight: threading.Event | None = None
    if cache_key:
        with _game_window2_match_cache_lock:
            inflight = _game_window2_match_inflight.get(cache_key)
            if inflight is None:
                inflight = threading.Event()
                _game_window2_match_inflight[cache_key] = inflight
                owner = True

    if cache_key and not owner and inflight is not None:
        wait_timeout = 2.0 if payload.get("read_only_cache") else 8.0
        if inflight.wait(timeout=wait_timeout):
            cached = _get_game_window2_match_cache(cache_key)
            if cached is not None:
                return cached

    try:
        result = producer()
        _set_game_window2_match_cache(cache_key, result)
        return result
    finally:
        if cache_key and owner and inflight is not None:
            with _game_window2_match_cache_lock:
                _game_window2_match_inflight.pop(cache_key, None)
            inflight.set()


def click_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return click_mumu_window_processed_point(
            x=float(payload.get("x") or 0),
            y=float(payload.get("y") or 0),
            title=game_window2_desktop_title(payload.get("title")),
            title_match=payload.get("title_match") or "contains",
            mode=payload.get("mode"),
            area=payload.get("area"),
            crop=payload.get("crop"),
            trim_border=payload.get("trim_border"),
            rotate=payload.get("rotate"),
            fixed_width=int(payload.get("fixed_width") or 0),
            fixed_height=int(payload.get("fixed_height") or 0),
            frame_width=int(payload.get("frame_width") or 0) or None,
            frame_height=int(payload.get("frame_height") or 0) or None,
            input_backend=payload.get("input_backend") or "desktop",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def drag_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return drag_mumu_window_processed_points(
            start_x=float(payload.get("start_x") or 0),
            start_y=float(payload.get("start_y") or 0),
            end_x=float(payload.get("end_x") or 0),
            end_y=float(payload.get("end_y") or 0),
            duration_ms=int(payload.get("duration_ms") or 300),
            title=game_window2_desktop_title(payload.get("title")),
            title_match=payload.get("title_match") or "contains",
            mode=payload.get("mode"),
            area=payload.get("area"),
            crop=payload.get("crop"),
            trim_border=payload.get("trim_border"),
            rotate=payload.get("rotate"),
            fixed_width=int(payload.get("fixed_width") or 0),
            fixed_height=int(payload.get("fixed_height") or 0),
            frame_width=int(payload.get("frame_width") or 0) or None,
            frame_height=int(payload.get("frame_height") or 0) or None,
            input_backend=payload.get("input_backend") or "desktop",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def keyevent_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        keys = payload.get("keys")
        if isinstance(keys, list) and keys:
            return keyevents_mumu_adb(keys)
        return keyevent_mumu_adb(str(payload.get("key") or ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def text_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return text_mumu_adb(str(payload.get("text") or ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _encode_bgr_png_response(frame: Any, headers: dict[str, str]) -> Response:
    import cv2

    ok, data = cv2.imencode(".png", frame)
    if not ok:
        raise HTTPException(status_code=500, detail="编码游戏窗口截图失败")
    safe_headers = {key: str(value).encode("ascii", "ignore").decode("ascii") for key, value in headers.items()}
    return Response(content=data.tobytes(), media_type="image/png", headers=safe_headers)


def screencap_game_window2_service(
    *,
    prefer_cached: bool = False,
    cached_only: bool = False,
    allow_window_fallback: bool = False,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
) -> Response:
    title = game_window2_desktop_title(title)
    adb_error = ""
    if prefer_cached or cached_only:
        try:
            data, meta = screencap_mumu_adb_cached_png(cached_only=True)
        except Exception as exc:
            adb_error = str(exc)
            if cached_only:
                raise HTTPException(status_code=400, detail=adb_error) from exc
        else:
            return Response(
                content=data,
                media_type="image/png",
                headers={
                    "Cache-Control": "no-store",
                    "X-CodeYun-Input": str(meta.get("input") or ""),
                    "X-CodeYun-Adb-Serial": str(meta.get("adb_serial") or ""),
                    "X-CodeYun-Adb-Host": str(meta.get("adb_host") or ""),
                    "X-CodeYun-Adb-Port": str(meta.get("adb_port") or ""),
                    "X-CodeYun-Adb-Size": str(meta.get("adb_size") or ""),
                },
            )
    elif not cached_only:
        try:
            data, meta = screencap_mumu_adb_png()
        except Exception as exc:
            adb_error = str(exc)
        else:
            return Response(
                content=data,
                media_type="image/png",
                headers={
                    "Cache-Control": "no-store",
                    "X-CodeYun-Input": str(meta.get("input") or ""),
                    "X-CodeYun-Adb-Serial": str(meta.get("adb_serial") or ""),
                    "X-CodeYun-Adb-Host": str(meta.get("adb_host") or ""),
                    "X-CodeYun-Adb-Port": str(meta.get("adb_port") or ""),
                    "X-CodeYun-Adb-Size": str(meta.get("adb_size") or ""),
                },
            )

    if not allow_window_fallback:
        raise HTTPException(status_code=400, detail=f"ADB截图失败：{adb_error or '未取得 MuMu ADB 截图'}")

    try:
        frame = capture_mumu_window_frame(
            title=title,
            title_match=title_match,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
            prefer_cached=True,
        )
    except Exception as exc:
        detail = str(exc)
        if adb_error:
            detail = f"{detail}；ADB截图失败：{adb_error}"
        raise HTTPException(status_code=400, detail=detail) from exc
    return _encode_bgr_png_response(
        frame,
        {
            "Cache-Control": "no-store",
            "X-CodeYun-Input": "window_capture",
            "X-CodeYun-Adb-Port": "",
            "X-CodeYun-Adb-Size": "",
            "X-CodeYun-Adb-Error": adb_error[:200],
        },
    )


def match_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    title = normalize_game_window2_title(payload.get("title"))
    try:
        return run_game_window2_match_with_cache(
            payload,
            lambda: match_fanxiu_screenshot_box_frame(
                filename=payload["filename"],
                box=payload["box"],
                scan=bool(payload.get("scan")),
                scan_box=payload.get("scan_box"),
                pixel_tolerance=int(payload.get("pixel_tolerance") if payload.get("pixel_tolerance") is not None else 5),
                alpha_mask_data_url=payload.get("alpha_mask_data_url"),
                tolerance_min_data_url=payload.get("tolerance_min_data_url"),
                tolerance_max_data_url=payload.get("tolerance_max_data_url"),
                title=title,
                title_match=payload.get("title_match") or "contains",
                mode=payload.get("mode"),
                area=payload.get("area"),
                crop=payload.get("crop"),
                trim_border=payload.get("trim_border"),
                rotate=payload.get("rotate"),
                fixed_width=int(payload.get("fixed_width") or 0),
                fixed_height=int(payload.get("fixed_height") or 0),
                quality=int(payload.get("quality") or 82),
                current_frame_data_url=payload.get("current_frame_data_url"),
                prefer_cached=bool(payload.get("prefer_cached", True)),
                match_strategy=payload.get("match_strategy") or "auto",
                match_search_radius=payload.get("match_search_radius"),
                ocr_enabled=bool(payload.get("ocr_enabled")),
                ocr_text=payload.get("ocr_text"),
                ocr_match_mode=payload.get("ocr_match_mode") or "contains",
                ocr_min_confidence=float(payload.get("ocr_min_confidence") or 0.0),
                debug_match=bool(payload.get("debug_match")),
                save_match_frame=bool(payload.get("save_match_frame", True)),
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def post_remote_game_window2_json(entry: UserDevice, service_path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
    target_url = f"{remote_entry_base_url(entry)}/api/fanxiu/game-window2/{service_path}"
    try:
        response = requests.post(
            target_url,
            headers=remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 12.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务响应格式不支持")
    return data


def click_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return post_remote_game_window2_json(entry, "service-input/click", payload, "操作")


def drag_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return post_remote_game_window2_json(entry, "service-input/drag", payload, "拖拽")


def keyevent_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return post_remote_game_window2_json(entry, "service-input/keyevent", payload, "按键")


def text_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return post_remote_game_window2_json(entry, "service-input/text", payload, "文本输入")


def match_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    target_url = f"{remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-match"
    try:
        response = requests.post(
            target_url,
            headers=remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 30.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏匹配服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程游戏匹配服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="远程游戏匹配服务响应格式不支持")
    return data


def remote_game_window2_screencap(entry: UserDevice) -> Response:
    target_url = f"{remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-screencap"
    try:
        response = requests.get(
            target_url,
            headers=remote_entry_headers(entry),
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 20.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏 ADB 截图服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=extract_stream_error(response))
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type") or "image/png",
        headers={"Cache-Control": "no-store"},
    )
