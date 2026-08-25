from __future__ import annotations

"""Run one fixed spirit-artifact snapshot inside the game's main Lua state."""

import hashlib
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _agent_source,
    _asset_path,
    _lua_addresses,
    _remote_sha256,
    _verify_game_binaries,
)
from backend.core.fanxiu.instrumentation.runtime_memory import MumuProcessMemory
from backend.core.fanxiu.instrumentation.policy import reject_active_instrumentation
from backend.core.temp_paths import codeyun_temp_root


_ORIGINAL_CHUNK = b"local M=require('GameSystem.Game.Redbag.Mgr.RedbagMgr');M.Inst_get()"
_ORIGINAL_SONAME = b"libcodeyun_fanxiu_redbag_bridge.so"
_SPIRIT_ARTIFACT_SONAME = b"libcodeyun_fanxiu_spirit_bridge.so"
_REMOTE_BRIDGE_PATH = "/data/local/tmp/codeyun-spirit-artifact-bridge.so"
_REMOTE_SCRIPT_PATH = "/data/local/tmp/cy-spirit-artifact.lua"
_REMOTE_RESULT_PATH = (
    "/sdcard/Android/data/com.frxxcrjpwssc3.ggws/files/"
    "codeyun-spirit-artifact.tsv"
)
_SCRIPT_CHUNK = f"dofile('{_REMOTE_SCRIPT_PATH}')".encode("ascii")

_LUA_SNAPSHOT_SCRIPT = f'''
local DBMgr=require("GameSystem.Game.LuaConfig.Manager.DBMgr")
local ConfigName=require("GameSystem.Game.LuaConfig.Const.ConfigName")
local SpiritwareMgr=require("GameSystem.Game.Spiritware.Mgr.SpiritwareMgr")
local BackpackMgr=require("GameSystem.Game.BackPack.Mgr.BackpackMgr")
local SpiritwareData=require("GameSystem.Game.Spiritware.Model.SpiritwareData")
local resultPath="{_REMOTE_RESULT_PATH}"

local function text(value)
  local s=tostring(value or "")
  s=string.gsub(s,"[\\t\\r\\n]"," ")
  return s
end

local function longText(value)
  if value and value.ToString then return value:ToString() end
  return text(value)
end

local function writeSnapshot()
  local f=io.open(resultPath,"w")
  if not f then return end
  local count=0
  local mgr=SpiritwareMgr.Inst_get()
  local dic=mgr.Model.data:GetWareDic()
  for wareId,ware in Kpairs(dic) do
    local putUpSet=ware:GetCurPutList()
    if putUpSet then
      for _,uid in Cipairs(putUpSet) do
        local item=BackpackMgr.Inst_get().Model.BackpackData:GetSpiritWareItemByLongId(uid)
        if item then
          local ext=item.ext or {{}}
          local partCfg=DBMgr.Inst_get():GetConfigTableById(
            ConfigName.SpiritWare_SpiritWareItem,item.baseId
          )
          local part=partCfg and partCfg.parts or 0
          local artifactName=ware.GetName and ware:GetName() or ""
          local partName=mgr.GetPartName and mgr:GetPartName(item.baseId) or ""
          count=count+1
          f:write("part\\t",text(wareId),"\\t",text(part),"\\t",longText(uid),"\\t",
            text(item.baseId),"\\t",text(ext.grade),"\\t",text(ext.pinLevel),"\\t",
            text(ext.refineNum),"\\t",text(ext.isBreak),"\\t",text(artifactName),"\\t",
            text(partName),"\\n")
          for rawId,effect in pairs(ext.attrMap or {{}}) do
            local cleanseId=effect.cleanseId or rawId or 0
            local name,attrCfg,cleanseCfg=mgr:GetCleanseName(cleanseId)
            f:write("effect\\t",text(wareId),"\\t",text(part),"\\t",longText(uid),"\\t",
              text(cleanseId),"\\t",text(effect.value),"\\t",text(effect.baseValue),"\\t",
              text(effect.addValue),"\\t",text(effect.quality),"\\t",text(effect.isLock),"\\t",
              text(cleanseCfg and cleanseCfg.code),"\\t",text(name),"\\t",
              text(cleanseCfg and cleanseCfg.selfName),"\\t",text(cleanseCfg and cleanseCfg.type),"\\t",
              text(cleanseCfg and cleanseCfg.scoreValue),"\\t",text(attrCfg and attrCfg.id),"\\t",
              text(attrCfg and attrCfg.name),"\\n")
          end
        end
      end
    end
  end
  f:write("complete\\t1\\t",text(count),"\\n")
  f:close()
end

if not SpiritwareData.__codeyunSnapshotOriginal then
  SpiritwareData.__codeyunSnapshotOriginal=SpiritwareData.SyncWareInfo
  SpiritwareData.SyncWareInfo=function(self,msg)
    local result=SpiritwareData.__codeyunSnapshotOriginal(self,msg)
    pcall(writeSnapshot)
    return result
  end
end
pcall(writeSnapshot)
SpiritwareMgr.Inst_get().NetLogic.CM_SyncSpiritWareInfoFun()
'''.strip()


class FanxiuSpiritArtifactRuntimeLoadError(RuntimeError):
    pass


def _patched_bridge_bytes(source: bytes) -> bytes:
    if len(_SCRIPT_CHUNK) > len(_ORIGINAL_CHUNK):
        raise FanxiuSpiritArtifactRuntimeLoadError("灵器快照加载路径超过适配器固定容量")
    offset = source.find(_ORIGINAL_CHUNK)
    if offset < 0 or source.find(_ORIGINAL_CHUNK, offset + 1) >= 0:
        raise FanxiuSpiritArtifactRuntimeLoadError("灵器快照适配器模板指纹不唯一")
    replacement = _SCRIPT_CHUNK + b"\0" * (len(_ORIGINAL_CHUNK) - len(_SCRIPT_CHUNK))
    patched = source[:offset] + replacement + source[offset + len(_ORIGINAL_CHUNK):]
    soname_count = patched.count(_ORIGINAL_SONAME)
    if soname_count <= 0 or len(_ORIGINAL_SONAME) != len(_SPIRIT_ARTIFACT_SONAME):
        raise FanxiuSpiritArtifactRuntimeLoadError("灵器快照适配器 SONAME 指纹无效")
    return patched.replace(_ORIGINAL_SONAME, _SPIRIT_ARTIFACT_SONAME)


def _local_assets() -> tuple[Path, Path]:
    root = codeyun_temp_root("fanxiu-spirit-artifact-runtime")
    bridge_path = root / "codeyun-spirit-artifact-bridge.so"
    script_path = root / "cy-spirit-artifact.lua"
    bridge_path.write_bytes(
        _patched_bridge_bytes(_asset_path("redbag_runtime_bridge.arm64.bin").read_bytes())
    )
    script_path.write_text(_LUA_SNAPSHOT_SCRIPT, encoding="utf-8")
    return bridge_path, script_path


def _deploy(service: Any, device_id: str) -> Path:
    reject_active_instrumentation("向设备部署灵器 Runtime 桥接文件")
    bridge_path, script_path = _local_assets()
    for local_path, remote_path in (
        (bridge_path, _REMOTE_BRIDGE_PATH),
        (script_path, _REMOTE_SCRIPT_PATH),
    ):
        local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
        if _remote_sha256(service, device_id, remote_path) != local_hash:
            service._run_adb(["-s", device_id, "push", str(local_path), remote_path], timeout=20.0)
            service._shell(device_id, "chmod", "755", remote_path, timeout=5.0)
    return bridge_path


def _parse_result(text: str) -> dict[str, Any]:
    parts: dict[str, dict[str, Any]] = {}
    complete = False
    declared_count = 0
    for raw_line in str(text or "").splitlines():
        columns = raw_line.split("\t")
        if not columns:
            continue
        if columns[0] == "complete" and len(columns) >= 3:
            complete = columns[1] == "1"
            declared_count = int(columns[2] or 0)
        elif columns[0] == "part" and len(columns) >= 9:
            uid = columns[3]
            parts[uid] = {
                "ware_id": int(columns[1] or 0),
                "part": int(columns[2] or 0),
                "item_id": uid,
                "base_id": int(columns[4] or 0),
                "grade": int(float(columns[5] or 0)),
                "realm": int(float(columns[6] or 0)),
                "refine_num": int(float(columns[7] or 0)),
                "is_break": columns[8].lower() == "true",
                "artifact_name": columns[9] if len(columns) >= 10 else "",
                "part_name": columns[10] if len(columns) >= 11 else "",
                "effects": [],
            }
        elif columns[0] == "effect" and len(columns) >= 17:
            uid = columns[3]
            if uid not in parts:
                continue
            parts[uid]["effects"].append(
                {
                    "cleanse_id": int(float(columns[4] or 0)),
                    "value": int(float(columns[5] or 0)),
                    "base_value": int(float(columns[6] or 0)),
                    "add_value": int(float(columns[7] or 0)),
                    "quality": int(float(columns[8] or 0)),
                    "locked": columns[9].lower() == "true",
                    "code": columns[10],
                    "name": columns[11],
                    "self_name": columns[12],
                    "type": int(float(columns[13] or 0)),
                    "score_value": columns[14],
                    "attribute_id": columns[15],
                    "attribute_name": columns[16],
                }
            )
    return {
        "complete": complete and declared_count == len(parts),
        "declared_count": declared_count,
        "parts": list(parts.values()),
    }


def refresh_spirit_artifact_runtime(*, timeout_seconds: float = 12.0) -> dict[str, Any]:
    """Explicitly request the server snapshot, then return exact equipped references."""

    reject_active_instrumentation("改写灵器 Runtime 并请求服务器同步")

    from backend.core.fanxiu.instrumentation.service import (
        fanxiu_instrumentation_service as service,
    )

    memory = MumuProcessMemory.discover()
    device_id = memory.adb_serial
    _verify_game_binaries(service, device_id, memory)
    addresses = _lua_addresses(memory)
    bridge_path = _deploy(service, device_id)
    service._shell(device_id, "rm", "-f", _REMOTE_RESULT_PATH, timeout=5.0)
    service.ensure_server(device_id=device_id)
    frida = service._frida_loader()
    device = service._frida_device(frida, device_id, 8.0)
    session = None
    script = None
    try:
        session = device.attach(memory.pid)
        script = session.create_script(_agent_source())
        script.load()
        probe = script.exports_sync.probe(_REMOTE_BRIDGE_PATH)
        if not probe.get("ok"):
            raise FanxiuSpiritArtifactRuntimeLoadError("灵器 ARM64 适配器 ABI 探针失败")
        result = script.exports_sync.ensure(_REMOTE_BRIDGE_PATH, addresses)
        if not result.get("ok"):
            raise FanxiuSpiritArtifactRuntimeLoadError(
                f"灵器快照 Lua 状态码：{result.get('status')}"
            )
    finally:
        if script is not None:
            try:
                script.unload()
            except Exception:
                pass
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    latest: dict[str, Any] = {"complete": False, "parts": []}
    while time.monotonic() < deadline:
        output = service._shell(device_id, "cat", _REMOTE_RESULT_PATH, timeout=5.0, check=False)
        latest = _parse_result(output)
        part_count = len(latest.get("parts") or [])
        if latest.get("complete") and part_count > 0 and part_count % 6 == 0:
            return {
                **latest,
                "source": "lua_main_state_server_sync",
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "bridge_sha256": hashlib.sha256(bridge_path.read_bytes()).hexdigest(),
            }
        time.sleep(0.25)
    raise FanxiuSpiritArtifactRuntimeLoadError(
        f"灵器服务器装配同步未返回完整的六部位编组：当前 {len(latest.get('parts') or [])} 件"
    )
