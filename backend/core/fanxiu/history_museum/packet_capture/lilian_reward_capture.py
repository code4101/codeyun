"""Retired Lilian reward packet/socket capture implementation.

Manual historical use only. Production code must use Runtime and must not import this module.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _agent_source,
    _asset_path,
    _remote_sha256,
)
from backend.core.fanxiu.instrumentation.policy import reject_active_instrumentation
from backend.core.temp_paths import codeyun_temp_root

LILIAN_SUCCESS_ITEM_ID = 17003
LILIAN_REWARD_REASON = "103206"
LILIAN_CHOOSE_RESPONSE_PRO_ID = 89504
_ORIGINAL_CHUNK = b"local M=require('GameSystem.Game.Redbag.Mgr.RedbagMgr');M.Inst_get()"
_REMOTE_BRIDGE_PATH = "/data/local/tmp/codeyun-lilian-reward-bridge.so"
_REMOTE_SCRIPT_PATH = "/data/local/tmp/codeyun-lilian-reward.lua"
_REMOTE_RESULT_PATH = (
    "/sdcard/Android/data/com.frxxcrjpwssc3.ggws/files/"
    "codeyun-lilian-reward.tsv"
)

_SCRIPT_CHUNK = f"dofile('{_REMOTE_SCRIPT_PATH}')".encode("ascii")
_LUA_CAPTURE_SCRIPT = f"""
local H=require("GameSystem.Game.Explore.Model.ExploreTravelHandler")
local resultPath="{_REMOTE_RESULT_PATH}"
local function writeResult(msg)
  local f=io.open(resultPath,"w")
  if not f then return end
  f:write("complete\\t1\\n")
  f:write("finishedEventIndexId\\t",tostring(msg and msg.finishedEventIndexId or ""), "\\n")
  f:write("gaveNormalReward\\t",tostring(msg and msg.gaveNormalReward or false), "\\n")
  local rewards=msg and msg.rewardResults
  if rewards then
    for _,v in Cipairs(rewards) do
      local amount=0
      if v.amount then amount=v.amount:ToNum() end
      f:write("reward\\t",tostring(v.code or 0),"\\t",tostring(amount),"\\n")
    end
  end
  f:close()
end
if not H.__codeyunLilianOriginal then
  H.__codeyunLilianOriginal=H.OnFinishExploreEvent
  H.OnFinishExploreEvent=function(self,msg)
    writeResult(msg)
    return H.__codeyunLilianOriginal(self,msg)
  end
end
local armed=io.open(resultPath,"w")
if armed then armed:write("armed\\t1\\n");armed:close() end
""".strip()

_SOCKET_CAPTURE_AGENT = r"""
const pending = new Map();
const MAX_PENDING_BYTES = 2 * 1024 * 1024;
const MAX_PENDING_FDS = 64;

function concatBytes(left, right) {
  const merged = new Uint8Array(left.length + right.length);
  merged.set(left, 0);
  merged.set(right, left.length);
  return merged;
}

function readZigzag(bytes, start, end) {
  let value = 0;
  let shift = 0;
  let pos = start;
  for (let index = 0; index < 10 && pos < end; index++, pos++) {
    const byte = bytes[pos];
    value += (byte & 0x7f) * Math.pow(2, shift);
    if ((byte & 0x80) === 0) {
      const decoded = Math.floor(value / 2) ^ -(value & 1);
      return { value: decoded, pos: pos + 1 };
    }
    shift += 7;
  }
  return null;
}

function consume(fd, raw) {
  const previous = pending.get(fd) || new Uint8Array(0);
  const bytes = concatBytes(previous, new Uint8Array(raw));
  let pos = 0;
  let earliestIncomplete = -1;
  while (pos + 6 <= bytes.length) {
    const length = (
      bytes[pos] * 0x1000000
      + bytes[pos + 1] * 0x10000
      + bytes[pos + 2] * 0x100
      + bytes[pos + 3]
    );
    const end = pos + 4 + length;
    if (length < 2 || length > MAX_PENDING_BYTES) {
      pos += 1;
      continue;
    }
    if (end > bytes.length) {
      if (earliestIncomplete < 0) earliestIncomplete = pos;
      pos += 1;
      continue;
    }
    const sn = readZigzag(bytes, pos + 4, end);
    const packet = sn === null ? null : readZigzag(bytes, sn.pos, end);
    if (
      packet === null
      || packet.value <= 0
      || packet.value > 200000
    ) {
      pos += 1;
      continue;
    }
    earliestIncomplete = -1;
    if (packet.value === 10062 || packet.value === 89504) {
      const frame = bytes.slice(pos, end);
      send(
        { kind: "fanxiu-recv", fd: fd, count: frame.length, proId: packet.value },
        frame.buffer
      );
    }
    pos = end;
  }
  const keepFrom = earliestIncomplete >= 0
    ? earliestIncomplete
    : Math.max(pos, bytes.length - 16);
  const boundedFrom = Math.max(keepFrom, bytes.length - MAX_PENDING_BYTES);
  pending.delete(fd);
  pending.set(fd, bytes.slice(boundedFrom));
  while (pending.size > MAX_PENDING_FDS) {
    pending.delete(pending.keys().next().value);
  }
}

function attachReceive(name) {
  const address = Module.findGlobalExportByName(name);
  if (address === null) return false;
  Interceptor.attach(address, {
    onEnter(args) {
      this.fd = args[0].toInt32();
      this.buffer = args[1];
    },
    onLeave(result) {
      const count = result.toInt32();
      if (count <= 0 || count > 1048576) return;
      try {
        consume(this.fd, this.buffer.readByteArray(count));
      } catch (_) {
      }
    }
  });
  return true;
}
const hooks = {
  recv: attachReceive("recv"),
  recvfrom: attachReceive("recvfrom"),
  read: attachReceive("read")
};
send({ kind: "fanxiu-recv-armed", hooks: hooks });
"""

_MAX_SOCKET_CAPTURE_BYTES = 8 * 1024 * 1024
_socket_capture: "_LilianSocketCapture | None" = None
_socket_capture_lock = threading.RLock()
_pcap_capture_boundary: dict[str, Any] | None = None


class FanxiuLilianRewardCaptureError(RuntimeError):
    pass


class _LilianSocketCapture:
    """Long-lived, bounded receive capture owned by the real Jupyter kernel."""

    def __init__(self, session: Any, script: Any) -> None:
        self.session = session
        self.script = script
        self.lock = threading.RLock()
        self.armed = False
        self.hooks: dict[str, bool] = {}
        self.chunks: dict[int, bytearray] = {}
        self.total_bytes = 0

    def on_message(self, message: dict[str, Any], data: bytes | None) -> None:
        payload = message.get("payload") if message.get("type") == "send" else None
        if not isinstance(payload, dict):
            return
        kind = str(payload.get("kind") or "")
        if kind == "fanxiu-recv-armed":
            with self.lock:
                self.hooks = {
                    str(key): bool(value)
                    for key, value in (payload.get("hooks") or {}).items()
                }
                self.armed = any(self.hooks.values())
            return
        if kind != "fanxiu-recv" or not data:
            return
        try:
            fd = int(payload.get("fd"))
        except (TypeError, ValueError):
            return
        raw = bytes(data)
        with self.lock:
            target = self.chunks.setdefault(fd, bytearray())
            target.extend(raw)
            self.total_bytes += len(raw)
            overflow = self.total_bytes - _MAX_SOCKET_CAPTURE_BYTES
            if overflow > 0:
                for old_fd in list(self.chunks):
                    old = self.chunks[old_fd]
                    cut = min(len(old), overflow)
                    del old[:cut]
                    self.total_bytes -= cut
                    overflow -= cut
                    if not old:
                        self.chunks.pop(old_fd, None)
                    if overflow <= 0:
                        break

    def snapshot(self) -> dict[int, bytes]:
        with self.lock:
            return {
                fd: bytes(payload)
                for fd, payload in self.chunks.items()
                if payload
            }

    def close(self) -> None:
        try:
            self.script.unload()
        except Exception:
            pass
        try:
            self.session.detach()
        except Exception:
            pass


def _inherited_field(payload: Any, name: str) -> Any:
    """Read one field through the game's nested ``_super`` VO chain."""

    current = payload
    while isinstance(current, dict):
        if name in current:
            return current.get(name)
        current = current.get("_super")
    return None


def _reward_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("items")
    return [dict(item) for item in (raw_items or []) if isinstance(item, dict)]


def _lilian_outcome_from_frame(frame: dict[str, Any]) -> dict[str, Any] | None:
    """Decode the authoritative event-choice result, with legacy fallback."""

    pro_id = int(frame.get("pro_id") or 0)
    parsed = frame.get("parsed") or {}
    if pro_id == LILIAN_CHOOSE_RESPONSE_PRO_ID:
        event = parsed.get("event") if isinstance(parsed, dict) else None
        result = _inherited_field(event, "result")
        if not isinstance(result, dict) or "win" not in result:
            return None
        rewards = _reward_items(result.get("rewardResult"))
        return {
            "rewards": rewards,
            "success": bool(result.get("win")),
            "meta": {
                "pro_id": str(pro_id),
                "event_id": str(_inherited_field(event, "eventId") or ""),
                "choose_id": str(_inherited_field(event, "chooseId") or ""),
                "travel_id": str(_inherited_field(event, "travelId") or ""),
            },
        }
    if pro_id == 10062 and str(parsed.get("reason") or "") == LILIAN_REWARD_REASON:
        rewards = _reward_items(parsed.get("rewards"))
        return {
            "rewards": rewards,
            "success": lilian_reward_is_success(rewards),
            "meta": {
                "pro_id": str(pro_id),
                "reason": str(parsed.get("reason") or ""),
            },
        }
    return None


def _decode_socket_reward_capture(
    streams: dict[int, bytes],
) -> dict[str, Any]:
    """Resynchronise captured recv chunks and return the latest finish packet."""

    from backend.core.fanxiu.packet.tcp_flow import (
        LuaPacketSchemaIndex,
        VarintBinaryReader,
        _decode_lusuo_frames_tolerant,
        _patch_fanxiu_schema_long_list,
        _resolve_fanxiu_message_text_assets,
    )

    text_assets = _resolve_fanxiu_message_text_assets(None)
    schema = _patch_fanxiu_schema_long_list(
        LuaPacketSchemaIndex(text_assets)
    )
    matches: list[dict[str, Any]] = []
    for fd, payload in streams.items():
        pos = 0
        total = len(payload)
        framed = bytearray()
        while pos + 4 <= total:
            length = int.from_bytes(payload[pos:pos + 4], "big")
            end = pos + 4 + length
            if 2 <= length <= 16 * 1024 * 1024 and end <= total:
                body = payload[pos + 4:end]
                try:
                    reader = VarintBinaryReader(body)
                    reader.read_int()
                    packet_id = reader.read_int()
                except Exception:
                    packet_id = 0
                if packet_id in schema.protocol_names:
                    framed.extend(payload[pos:end])
                    pos = end
                    continue
            pos += 1
        frames, _warnings = _decode_lusuo_frames_tolerant(
            bytes(framed),
            schema,
        )
        for frame in frames:
            if int(frame.get("pro_id") or 0) in {
                10062,
                LILIAN_CHOOSE_RESPONSE_PRO_ID,
            }:
                matches.append({"fd": fd, **frame})
    if not matches:
        return {
            "ok": False,
            "armed": True,
            "complete": False,
            "rewards": [],
            "success": False,
            "reason": "尚未收到历练结算包",
        }
    frame: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    for candidate in matches:
        decoded = _lilian_outcome_from_frame(candidate)
        if decoded is not None:
            frame = candidate
            outcome = decoded
    if frame is None or outcome is None:
        return {
            "ok": False,
            "armed": True,
            "complete": False,
            "rewards": [],
            "success": False,
            "reason": "尚未收到本轮历练奖励包",
        }
    return {
        "ok": True,
        "armed": True,
        "complete": True,
        "rewards": outcome["rewards"],
        "meta": {
            **dict(outcome.get("meta") or {}),
            "fd": str(frame.get("fd") or ""),
        },
        "success": bool(outcome["success"]),
        "source": "realtime_socket_packet",
    }


def _decode_lilian_reward_frames_from_pcap(
    path: Path,
) -> list[dict[str, Any]]:
    """Decode all candidate reward packets from one live pcap."""

    from backend.core.fanxiu.packet.tcp_flow import (
        DEFAULT_FANXIU_SERVER_HOST,
        LuaPacketSchemaIndex,
        _decode_lusuo_frames_tolerant,
        _patch_fanxiu_schema_long_list,
        _resolve_fanxiu_message_text_assets,
        extract_tcp_stream_payloads_with_tshark,
        list_tcp_streams_with_tshark,
    )

    schema = _patch_fanxiu_schema_long_list(
        LuaPacketSchemaIndex(_resolve_fanxiu_message_text_assets(None))
    )
    rows: list[dict[str, Any]] = []
    for stream_row in list_tcp_streams_with_tshark(path):
        stream = int(stream_row.get("stream") or 0)
        _c2s, s2c = extract_tcp_stream_payloads_with_tshark(
            path,
            stream,
            server_host=DEFAULT_FANXIU_SERVER_HOST,
        )
        frames, _warnings = _decode_lusuo_frames_tolerant(s2c, schema)
        for frame in frames:
            outcome = _lilian_outcome_from_frame(frame)
            if outcome is None:
                continue
            rows.append({
                "stream": stream,
                "offset": int(frame.get("offset") or 0),
                "rewards": list(outcome["rewards"]),
                "success": bool(outcome["success"]),
                "meta": dict(outcome.get("meta") or {}),
            })
    return rows


def _recent_live_pcaps(live_dir: Path, *, limit: int) -> list[Path]:
    rows: list[tuple[float, Path]] = []
    for path in live_dir.glob("*.pcap"):
        try:
            rows.append((float(path.stat().st_mtime), path))
        except OSError:
            continue
    rows.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in rows[:limit]]


def mark_lilian_reward_pcap_boundary() -> dict[str, Any]:
    """Record decoded stream offsets immediately before claiming a reward."""

    from backend.core.fanxiu.packet.tcp_flow import (
        resolve_fanxiu_tcp_live_capture_dir,
    )

    live_dir = resolve_fanxiu_tcp_live_capture_dir()
    files: dict[str, Any] = {}
    candidates = _recent_live_pcaps(live_dir, limit=3)
    for path in candidates:
        try:
            rows = _decode_lilian_reward_frames_from_pcap(path)
            stat = path.stat()
        except Exception:
            continue
        offsets: dict[str, int] = {}
        for row in rows:
            stream_key = str(int(row.get("stream") or 0))
            offsets[stream_key] = max(
                offsets.get(stream_key, -1),
                int(row.get("offset") or 0),
            )
        files[str(path.resolve())] = {
            "size": int(stat.st_size),
            "mtime": float(stat.st_mtime),
            "offsets": offsets,
        }
    return {
        "captured_at": time.time(),
        "live_dir": str(live_dir),
        "files": files,
    }


def read_lilian_reward_from_pcap_boundary(
    boundary: dict[str, Any],
    *,
    timeout_seconds: float = 0.0,
    poll_seconds: float = 1.0,
) -> dict[str, Any]:
    """Return the first correlated reward strictly after a pcap boundary."""

    deadline = time.monotonic() + max(0.0, float(timeout_seconds or 0.0))
    live_dir = Path(str(boundary.get("live_dir") or ""))
    captured_at = float(boundary.get("captured_at") or 0.0)
    baseline_files = boundary.get("files") or {}
    while True:
        matches: list[dict[str, Any]] = []
        candidates = _recent_live_pcaps(live_dir, limit=5)
        for path in candidates:
            resolved = str(path.resolve())
            baseline = baseline_files.get(resolved) or {}
            try:
                stat = path.stat()
            except OSError:
                continue
            if (
                not baseline
                and float(stat.st_mtime) + 1.0 < captured_at
            ):
                continue
            if (
                baseline
                and int(stat.st_size) <= int(baseline.get("size") or 0)
            ):
                continue
            try:
                rows = _decode_lilian_reward_frames_from_pcap(path)
            except Exception:
                continue
            offsets = baseline.get("offsets") or {}
            for row in rows:
                stream_key = str(int(row.get("stream") or 0))
                if int(row.get("offset") or 0) <= int(
                    offsets.get(stream_key, -1)
                ):
                    continue
                matches.append({
                    **row,
                    "pcap": resolved,
                    "pcap_name": path.name,
                    "pcap_mtime": float(stat.st_mtime),
                })
        if matches:
            frame = min(
                matches,
                key=lambda item: (
                    0
                    if str((item.get("meta") or {}).get("pro_id") or "")
                    == str(LILIAN_CHOOSE_RESPONSE_PRO_ID)
                    else 1,
                    str(item.get("pcap_name") or ""),
                    int(item.get("stream") or 0),
                    int(item.get("offset") or 0),
                ),
            )
            rewards = list(frame.get("rewards") or [])
            return {
                "ok": True,
                "armed": True,
                "complete": True,
                "rewards": rewards,
                "meta": {
                    **dict(frame.get("meta") or {}),
                    "pcap": str(frame.get("pcap") or ""),
                    "stream": str(frame.get("stream", "")),
                    "offset": str(frame.get("offset", "")),
                },
                "success": (
                    bool(frame.get("success"))
                    if "success" in frame
                    else lilian_reward_is_success(rewards)
                ),
                "source": "pcap_boundary",
            }
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "armed": True,
                "complete": False,
                "rewards": [],
                "success": False,
                "reason": "抓包边界后尚未收到历练奖励包",
                "source": "pcap_boundary",
            }
        time.sleep(max(0.2, float(poll_seconds or 1.0)))


def _patched_bridge_bytes(source: bytes) -> bytes:
    """Replace the bridge's one bounded chunk with the Lilian hook loader."""

    if len(_SCRIPT_CHUNK) > len(_ORIGINAL_CHUNK):
        raise FanxiuLilianRewardCaptureError("历练奖励脚本入口超过桥接器固定容量")
    offset = source.find(_ORIGINAL_CHUNK)
    if offset < 0 or source.find(_ORIGINAL_CHUNK, offset + 1) >= 0:
        raise FanxiuLilianRewardCaptureError("历练奖励桥接器模板签名不唯一")
    replacement = _SCRIPT_CHUNK + b"\0" * (
        len(_ORIGINAL_CHUNK) - len(_SCRIPT_CHUNK)
    )
    return source[:offset] + replacement + source[offset + len(_ORIGINAL_CHUNK):]


def _prepare_local_assets() -> tuple[Path, Path]:
    root = codeyun_temp_root("fanxiu-lilian-reward-capture")
    root.mkdir(parents=True, exist_ok=True)
    bridge_path = root / "codeyun-lilian-reward-bridge.so"
    script_path = root / "codeyun-lilian-reward.lua"
    bridge_path.write_bytes(
        _patched_bridge_bytes(
            _asset_path("redbag_runtime_bridge.arm64.bin").read_bytes()
        )
    )
    script_path.write_text(_LUA_CAPTURE_SCRIPT, encoding="utf-8")
    return bridge_path, script_path


def _deploy_file(
    service: Any,
    device_id: str,
    local_path: Path,
    remote_path: str,
) -> None:
    reject_active_instrumentation("向设备部署历练动态插桩文件")
    local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
    if _remote_sha256(service, device_id, remote_path) != local_hash:
        service._run_adb(
            ["-s", device_id, "push", str(local_path), remote_path],
            timeout=20.0,
        )
    service._shell(
        device_id,
        "chmod",
        "755" if local_path.suffix == ".so" else "644",
        remote_path,
        timeout=5.0,
    )


def arm_lilian_reward_capture() -> dict[str, Any]:
    """Arm a passive pcap boundary without attaching to the game process."""

    global _pcap_capture_boundary, _socket_capture
    boundary = mark_lilian_reward_pcap_boundary()
    _pcap_capture_boundary = boundary
    with _socket_capture_lock:
        previous = _socket_capture
        _socket_capture = None
    if previous is not None:
        previous.close()
    return {
        "ok": True,
        "armed": True,
        "source": "pcap_boundary",
        "pcap_armed": True,
        "socket_armed": False,
        "boundary": boundary,
        "policy": "strict-read-only",
    }


def parse_lilian_reward_capture(text: str) -> dict[str, Any]:
    rewards: list[dict[str, int]] = []
    meta: dict[str, str] = {}
    armed = False
    complete = False
    for raw_line in str(text or "").splitlines():
        parts = raw_line.rstrip("\r").split("\t")
        if parts[:2] == ["armed", "1"]:
            armed = True
        elif parts[:2] == ["complete", "1"]:
            complete = True
        elif len(parts) == 3 and parts[0] == "reward":
            try:
                rewards.append({
                    "code": int(parts[1]),
                    "amount": int(float(parts[2])),
                })
            except ValueError:
                continue
        elif len(parts) >= 2:
            meta[parts[0]] = parts[1]
    return {
        "ok": complete,
        "armed": armed,
        "complete": complete,
        "rewards": rewards,
        "meta": meta,
        "success": (
            complete
            and any(
                item["code"] == LILIAN_SUCCESS_ITEM_ID
                and item["amount"] > 0
                for item in rewards
            )
        ),
    }


def read_lilian_reward_capture(
    *,
    device_id: str = "",
    boundary: dict[str, Any] | None = None,
    timeout_seconds: float = 0.0,
) -> dict[str, Any]:
    with _socket_capture_lock:
        capture = _socket_capture
    if capture is not None and capture.armed:
        socket_result = _decode_socket_reward_capture(capture.snapshot())
        if socket_result.get("complete"):
            return socket_result

    selected_boundary = boundary or _pcap_capture_boundary
    if selected_boundary:
        return read_lilian_reward_from_pcap_boundary(
            selected_boundary,
            timeout_seconds=timeout_seconds,
        )

    from backend.core.fanxiu.instrumentation.service import (
        fanxiu_instrumentation_service as service,
    )

    selected = device_id or service.choose_device()
    text = service._shell(
        selected,
        "cat",
        _REMOTE_RESULT_PATH,
        timeout=5.0,
        check=False,
    )
    if not text.strip():
        return {
            "ok": False,
            "armed": False,
            "complete": False,
            "rewards": [],
            "success": False,
            "reason": "尚无历练奖励捕获结果",
        }
    return parse_lilian_reward_capture(text)


def lilian_reward_is_success(
    rewards: Iterable[dict[str, Any]],
) -> bool:
    return any(
        int(item.get("code") or 0) == LILIAN_SUCCESS_ITEM_ID
        and int(item.get("amount") or 0) > 0
        for item in rewards
    )


_DB_METHODS = frozenset(
    {"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"}
)
_LILIAN_CONFIG_TABLES = {
    "event": "XianLvTravel.PartnerTrainEvent",
    "plot": "XianLvTravel.PartnerTrainEventPlot",
    "reward": "XianLvTravel.PartnerTrainReward",
    "check": "XianLvTravel.PartnerTrainCheck",
    "item": "Item.Item",
}
_LILIAN_CONFIG_FIELD_INDEXES = {
    "event": {
        "id": 1, "eventName": 2, "eventType": 3, "eventGroupId": 4,
        "areaIds": 6, "condition": 7, "spEventCondition": 8,
        "spEventConditionDes": 9, "spReward": 10,
    },
    "plot": {
        "id": 1, "eventGroupId": 2, "eventPlotType": 3,
        "checkGroupId": 4, "eventDes": 5, "winDes": 6,
        "winReward": 7, "loseDes": 8, "loseReward": 9,
    },
    "reward": {
        "id": 1, "rewardGroupId": 2, "condition": 3, "reward": 4,
    },
    "check": {
        "id": 1, "checkGroupId": 2, "checkCondition": 3,
        "battleScoreShow": 4,
    },
    "item": {"id": 1, "name": 2, "quality": 13},
}

_PARTNER_EXPLORE_METHODS = frozenset(
    {"LuaPartnerExploreMgr", "OpenExploreDispatchView"}
)
_PARTNER_CONFIG_FIELD_INDEXES = {
    "id": 1,
    "name": 2,
    "npcId": 3,
    "xianLvCareer": 27,
}
_NPC_CONFIG_FIELD_INDEXES = {"id": 1, "name": 2, "sex": 4}
_CAPTAIN_CAREER_TO_PARTNER_CAREER = {
    1: 4,  # 剑修
    2: 3,  # 法修
    3: 2,  # 魔修
    4: 1,  # 体修
}
_CAPTAIN_CAREER_LABELS = {1: "剑修", 2: "法修", 3: "魔修", 4: "体修"}
_SEX_LABELS = {1: "男性", 2: "女性"}



