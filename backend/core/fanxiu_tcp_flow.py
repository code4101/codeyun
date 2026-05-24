from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyxllib.file.packetstream import (
    LuaPacketSchemaIndex,
    decode_lusuo_frames,
    extract_tcp_stream_payloads_with_tshark,
    summarize_decoded_frames,
)

from backend.core.fanxiu_resources import resolve_fanxiu_export_root


DEFAULT_FANXIU_SERVER_HOST = "1.12.44.63"
DEFAULT_TCP_CAPTURE_DIR = Path("tcp_captures")
DEFAULT_TEXT_ASSETS = Path(
    "by_source/lscripts/gamesystem/game/message_bf46a8de9ccefb33ec3f4d0545cc766e/text_assets"
)


def _resolve_export_child(export_root: str | Path | None, path: str | Path) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw = Path(path)
    return raw.expanduser().resolve() if raw.is_absolute() else (root / raw).resolve()


def _trim_value(value: Any, *, max_items: int = 8) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key == "items" and isinstance(item, list) and len(item) > max_items:
                output[key] = [_trim_value(x, max_items=max_items) for x in item[:max_items]]
                output["_truncated_items"] = len(item) - max_items
            else:
                output[key] = _trim_value(item, max_items=max_items)
        return output
    if isinstance(value, list):
        return [_trim_value(x, max_items=max_items) for x in value[:max_items]]
    if isinstance(value, float):
        return round(value, 4)
    return value


def decode_fanxiu_tcp_pcap(
    pcap: str | Path,
    *,
    stream: int = 34,
    server_host: str = DEFAULT_FANXIU_SERVER_HOST,
    export_root: str | Path | None = None,
    text_assets: str | Path = DEFAULT_TEXT_ASSETS,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    pcap_path = _resolve_export_child(export_root, pcap)
    text_assets_path = _resolve_export_child(export_root, text_assets)
    schema = LuaPacketSchemaIndex(text_assets_path)
    c2s_payload, s2c_payload = extract_tcp_stream_payloads_with_tshark(
        pcap_path,
        stream,
        server_host=server_host,
    )

    c2s_frames = decode_lusuo_frames(c2s_payload, schema)
    s2c_frames = decode_lusuo_frames(s2c_payload, schema)
    for item in c2s_frames:
        item["direction"] = "c2s"
    for item in s2c_frames:
        item["direction"] = "s2c"
    frames = [_trim_value(item) for item in c2s_frames + s2c_frames]

    result = {
        "export_root": str(root),
        "pcap": str(pcap_path),
        "stream": stream,
        "server_host": server_host,
        "text_assets": str(text_assets_path),
        "summary": {
            "c2s_bytes": len(c2s_payload),
            "s2c_bytes": len(s2c_payload),
            "c2s_frames": len(c2s_frames),
            "s2c_frames": len(s2c_frames),
            "c2s_protocols": summarize_decoded_frames(c2s_frames),
            "s2c_protocols": summarize_decoded_frames(s2c_frames),
        },
        "frames": frames,
    }

    if output_path is None:
        output = pcap_path.with_suffix(".decoded.json")
    else:
        output = _resolve_export_child(export_root, output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(output)
    return result


def decode_latest_fanxiu_tcp_capture(
    *,
    capture_dir: str | Path = DEFAULT_TCP_CAPTURE_DIR,
    stream: int = 34,
    server_host: str = DEFAULT_FANXIU_SERVER_HOST,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    directory = _resolve_export_child(export_root, capture_dir)
    pcaps = sorted(directory.glob("*.pcapng"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not pcaps:
        raise FileNotFoundError(f"未找到 pcapng：{directory}")
    return decode_fanxiu_tcp_pcap(
        pcaps[0],
        stream=stream,
        server_host=server_host,
        export_root=export_root,
    )
