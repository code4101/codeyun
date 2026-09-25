"""远端游戏窗口 HTTP 客户端。

负责设备认证、直连、传输超时、响应校验及连接释放；不加载本机 MuMu
控制器。JSON、图片及错误解析的公共入口供路由和画面流适配复用。
"""
from __future__ import annotations

from typing import Any
import requests
from fastapi import HTTPException
from fastapi.responses import Response
from backend.core.devices.http_proxy import REMOTE_DEVICE_DIRECT_PROXIES
from backend.models import UserDevice


def remote_entry_base_url(entry: UserDevice) -> str:
    if entry.mode != "remote" or not entry.server_url:
        raise HTTPException(status_code=400, detail="远程设备入口未配置后端地址")
    return entry.server_url.rstrip("/")


def remote_entry_headers(entry: UserDevice) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
    }


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


def request_remote_game_window2_json(
    entry: UserDevice,
    service_path: str,
    payload: dict[str, Any] | None,
    action: str,
    *,
    method: str = "post",
    read_timeout: float = 12.0,
) -> dict[str, Any]:
    """调用窗口服务并校验 JSON 对象；统一认证、直连、错误翻译和连接释放。

    操作适配器仅选择服务路径、业务名称和读取超时；响应消费后必定关闭。
    """
    target_url = f"{remote_entry_base_url(entry)}/api/fanxiu/game-window2/{service_path.lstrip('/')}"
    try:
        response = requests.request(
            method, target_url, headers=remote_entry_headers(entry), json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(), timeout=(5.0, read_timeout),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务不可达：{exc}") from exc
    try:
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=extract_stream_error(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"远程游戏{action}服务响应不是 JSON") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail=f"远程游戏{action}服务响应格式不支持")
        return data
    finally:
        response.close()


def post_remote_game_window2_json(entry: UserDevice, service_path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
    return request_remote_game_window2_json(entry, service_path, payload, action)


def click_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return post_remote_game_window2_json(entry, "service-input/click", payload, "操作")


def drag_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return post_remote_game_window2_json(entry, "service-input/drag", payload, "拖拽")


def keyevent_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return post_remote_game_window2_json(entry, "service-input/keyevent", payload, "按键")


def text_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return post_remote_game_window2_json(entry, "service-input/text", payload, "文本输入")


def match_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return request_remote_game_window2_json(entry, "service-match", payload, "匹配", read_timeout=30.0)


def request_remote_game_window2_image(
    entry: UserDevice,
    service_path: str,
    *,
    action: str,
    params: dict[str, Any] | None = None,
    read_timeout: float = 30.0,
    default_media_type: str = "image/jpeg",
    cache_control: str = "no-store",
) -> Response:
    """读取窗口服务的图片；保留服务端类型，并在构建响应后释放上游连接。"""
    target_url = f"{remote_entry_base_url(entry)}/api/fanxiu/game-window2/{service_path.lstrip('/')}"
    try:
        response = requests.get(
            target_url, headers=remote_entry_headers(entry), params=params,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(), timeout=(5.0, read_timeout),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务不可达：{exc}") from exc
    try:
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=extract_stream_error(response))
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type") or default_media_type,
            headers={"Cache-Control": cache_control},
        )
    finally:
        response.close()


def remote_game_window2_screencap(entry: UserDevice) -> Response:
    return request_remote_game_window2_image(
        entry, "service-screencap", action=" ADB 截图",
        read_timeout=20.0, default_media_type="image/png",
    )
