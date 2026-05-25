from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu_resources import (
    FanxiuResourceError,
    export_fanxiu_unity_text_assets,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)


DEFAULT_LUA_PACKET_DIR = Path("by_source/lscripts/gamesystem/game")
_PACKAGE_RE = re.compile(r"package\.loaded\[[\"']([^\"']+)[\"']\]")
_CLASS_RE = re.compile(r"_M\s*=\s*class\(([^,\)]+)")
_GET_ID_RE = re.compile(r"function\s+_M\.getId\s*\([^)]*\).*?return\s+([0-9]+)", re.S)
_GET_NAME_RE = re.compile(r"function\s+_M\.getName\s*\([^)]*\).*?return\s*[\"']([^\"']+)[\"']", re.S)
_READ_ASSIGN_RE = re.compile(r"self\.([A-Za-z0-9_]+)\s*=.*?self:read([A-Za-z0-9_]+)\(")
_READ_INTO_RE = re.compile(r"self:read([A-Za-z0-9_]+)\(\s*self\.([A-Za-z0-9_]+)")
_WRITE_FROM_RE = re.compile(r"self:write([A-Za-z0-9_]+)\(\s*self\.([A-Za-z0-9_]+)")
_TYPEOF_RE = re.compile(r"typeof\(([A-Za-z0-9_]+)\)")
_REQUIRE_ALIAS_RE = re.compile(r"local\s+(_[A-Za-z0-9_]+)\s*=\s*require[\"']([^\"']+)[\"']")
_REGISTER_RE = re.compile(r"F_Register\(\s*(_[A-Za-z0-9_]+):getId\(\)\s*,\s*typeof\(\s*\1\s*\)\s*(?:,\s*(.*))?")
_HANDLER_REF_RE = re.compile(r"(?:self|_M)[\.:]([A-Za-z0-9_]+)")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_export_dir(path: str | Path | None, default: Path, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path) if path else default
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"目录必须位于导出根目录内：{root}")
    if not resolved.is_dir():
        raise FanxiuResourceError(f"目录不存在：{resolved}")
    return resolved


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_tsv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _direction_for_name(name: str) -> str:
    if name.startswith("CM_"):
        return "client_to_server"
    if name.startswith("SM_"):
        return "server_to_client"
    if name.endswith("VO") or name.endswith("DTO"):
        return "value_object"
    return "other"


def _module_for_package(package_name: str) -> str:
    marker = ".module."
    if marker not in package_name:
        return ""
    tail = package_name.split(marker, 1)[1]
    return tail.rsplit(".packet.", 1)[0] if ".packet." in tail else tail


def _extract_packet_wire_fields(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    access_counts: dict[str, int] = {"read": 0, "write": 0}
    for line_no, line in enumerate(text.splitlines(), start=1):
        field_name = ""
        wire_method = ""
        access = ""
        match = _READ_ASSIGN_RE.search(line)
        if match:
            field_name, wire_method = match.group(1), match.group(2)
            access = "read"
        else:
            match = _READ_INTO_RE.search(line)
            if match:
                wire_method, field_name = match.group(1), match.group(2)
                access = "read"
            else:
                match = _WRITE_FROM_RE.search(line)
                if match:
                    wire_method, field_name = match.group(1), match.group(2)
                    access = "write"
        if not field_name or wire_method == "ing" or not access:
            continue
        type_match = _TYPEOF_RE.search(line)
        type_hint = type_match.group(1) if type_match else ""
        key = (access, field_name, wire_method, type_hint)
        if key in seen:
            continue
        seen.add(key)
        access_counts[access] += 1
        rows.append(
            {
                "access": access,
                "field_index": access_counts[access],
                "field_name": field_name,
                "wire_method": wire_method,
                "read_method": wire_method if access == "read" else "",
                "write_method": wire_method if access == "write" else "",
                "type_hint": type_hint,
                "line": line_no,
            }
        )
    return rows


def _extract_packet_fields(text: str) -> list[dict[str, Any]]:
    return [
        {
            "field_index": row["field_index"],
            "field_name": row["field_name"],
            "read_method": row["wire_method"],
            "type_hint": row["type_hint"],
            "line": row["line"],
        }
        for row in _extract_packet_wire_fields(text)
        if row["access"] == "read"
    ]


def _field_signature(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in rows:
        read_method = str(row.get("wire_method") or row.get("read_method") or "")
        type_hint = str(row.get("type_hint") or "")
        suffix = f"<{type_hint}>" if type_hint else ""
        parts.append(f"{row.get('field_name')}:{read_method}{suffix}")
    return ", ".join(parts)


def _parse_packet_file(path: Path, root: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    package_name = next((match.group(1) for match in _PACKAGE_RE.finditer(text)), "")
    id_match = _GET_ID_RE.search(text)
    name_match = _GET_NAME_RE.search(text)
    message_id = int(id_match.group(1)) if id_match else None
    message_name = name_match.group(1) if name_match else path.stem
    if message_id is None and ".packet." not in package_name and not re.match(r"^(CM|SM)_.+|.+VO$", path.stem):
        return None
    class_match = _CLASS_RE.search(text)
    base_class = class_match.group(1).strip() if class_match else ""
    bundle = path.parent.parent.name if path.parent.name == "text_assets" else path.parent.name
    relative_path = path.relative_to(root).as_posix()
    fields = _extract_packet_fields(text)
    wire_fields = _extract_packet_wire_fields(text)
    return {
        "bundle": bundle,
        "file": path.name,
        "relative_path": relative_path,
        "package": package_name,
        "module": _module_for_package(package_name),
        "name": message_name,
        "id": message_id,
        "direction": _direction_for_name(message_name),
        "base_class": base_class,
        "field_count": len(fields),
        "fields": fields,
        "wire_fields": wire_fields,
    }


def _packet_name_from_package(package_name: str) -> str:
    return package_name.rsplit(".", 1)[-1] if package_name else ""


def _handler_summary(lines: list[str], index: int, handler_expr: str) -> tuple[str, str]:
    expr = handler_expr.strip().rstrip(")")
    if not expr:
        return "none", ""
    if "function" in expr:
        for follow_line in lines[index : min(index + 8, len(lines))]:
            match = _HANDLER_REF_RE.search(follow_line)
            if match:
                return "inline_function", match.group(1)
        return "inline_function", ""
    match = _HANDLER_REF_RE.search(expr)
    if match:
        return "self_method", match.group(1)
    return "expression", expr


def _extract_packet_registrations(source_dir: Path, root: Path, packet_by_name: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(source_dir.rglob("*.lua")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "F_Register(" not in text:
            continue
        lines = text.splitlines()
        aliases = {
            match.group(1): _packet_name_from_package(match.group(2))
            for match in _REQUIRE_ALIAS_RE.finditer(text)
        }
        bundle = path.parent.parent.name if path.parent.name == "text_assets" else path.parent.name
        relative_path = path.relative_to(root).as_posix()
        for index, line in enumerate(lines):
            if "F_Register(" not in line:
                continue
            match = _REGISTER_RE.search(line)
            if not match:
                continue
            packet_var = match.group(1)
            packet_name = aliases.get(packet_var) or packet_var.lstrip("_")
            packet = packet_by_name.get(packet_name, {})
            handler_kind, handler_name = _handler_summary(lines, index, match.group(2) or "")
            rows.append(
                {
                    "packet_id": packet.get("id", ""),
                    "packet_name": packet_name,
                    "packet_direction": packet.get("direction", _direction_for_name(packet_name)),
                    "packet_module": packet.get("module", ""),
                    "handler_kind": handler_kind,
                    "handler_name": handler_name,
                    "logic_name": path.stem,
                    "bundle": bundle,
                    "file": path.name,
                    "relative_path": relative_path,
                    "line": index + 1,
                    "snippet": line.strip(),
                }
            )
    rows.sort(
        key=lambda row: (
            row["packet_id"] == "",
            int(row["packet_id"]) if str(row["packet_id"]).isdigit() else 0,
            str(row["packet_name"]),
            str(row["logic_name"]),
            int(row["line"]),
        )
    )
    return rows


def _canonical_protocol_rows(protocol_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in protocol_rows:
        key = (
            row.get("id"),
            row.get("name"),
            row.get("direction"),
            row.get("module"),
            row.get("field_count"),
            row.get("read_fields"),
            row.get("write_fields"),
            row.get("handler_names"),
            row.get("logic_names"),
        )
        item = grouped.get(key)
        if item is None:
            item = {
                "id": row.get("id"),
                "name": row.get("name"),
                "direction": row.get("direction"),
                "module": row.get("module"),
                "field_count": row.get("field_count"),
                "read_fields": row.get("read_fields"),
                "write_fields": row.get("write_fields"),
                "registration_count": row.get("registration_count"),
                "handler_names": row.get("handler_names"),
                "logic_names": row.get("logic_names"),
                "source_file_count": 0,
                "sample_files": "",
            }
            grouped[key] = item
        item["source_file_count"] = int(item["source_file_count"]) + 1
        files = [value for value in str(item.get("sample_files") or "").split(", ") if value]
        file_name = str(row.get("file") or "")
        if file_name and file_name not in files and len(files) < 8:
            files.append(file_name)
        item["sample_files"] = ", ".join(files)
    rows = list(grouped.values())
    rows.sort(key=lambda row: (row["id"] is None, row["id"] or 0, str(row["name"])))
    return rows


def _protocol_feature_key(row: dict[str, Any]) -> str | None:
    module = str(row.get("module") or "").lower()
    name = str(row.get("name") or "").lower()
    if module == "user.login":
        return "login"
    if module == "player.bluestarsea":
        return "bluestarsea"
    if "blld" in module or "blld" in name:
        return "blld"
    if module == "player.faze":
        return "faze"
    if "gongfa" in module or "gongfa" in name or "gong_fa" in name:
        return "gongfa"
    if module == "scene.fight" or "fight" in name:
        return "fight"
    return None


def _report_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _find_lua_text_asset(export_root: Path, filename: str, required_terms: tuple[str, ...]) -> Path | None:
    lua_root = export_root / "by_source" / "lscripts"
    if not lua_root.is_dir():
        return None
    for path in sorted(lua_root.rglob(filename), key=lambda item: item.as_posix().lower()):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if all(term in text for term in required_terms):
            return path
    return None


def _append_probe_evidence(
    rows: list[dict[str, Any]],
    *,
    path: Path | None,
    export_root: Path,
    stage: str,
    kind: str,
    markers: tuple[str, ...],
) -> None:
    if path is None or not path.is_file():
        for marker in markers:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": "<missing>",
                    "line": "",
                    "marker": marker,
                    "snippet": "missing source file",
                }
            )
        return
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for marker in markers:
        matched = False
        for line_no, line in enumerate(lines, start=1):
            if marker in line:
                rows.append(
                    {
                        "stage": stage,
                        "kind": kind,
                        "source": _report_path(path, export_root),
                        "line": line_no,
                        "marker": marker,
                        "snippet": line.strip(),
                    }
                )
                matched = True
                break
        if not matched:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": _report_path(path, export_root),
                    "line": "",
                    "marker": marker,
                    "snippet": "marker not found",
                }
            )


def _append_probe_evidence_in_section(
    rows: list[dict[str, Any]],
    *,
    path: Path | None,
    export_root: Path,
    stage: str,
    kind: str,
    section_marker: str,
    markers: tuple[str, ...],
    end_markers: tuple[str, ...] = ("function _M.", "function _M:"),
) -> None:
    if path is None or not path.is_file():
        _append_probe_evidence(rows, path=path, export_root=export_root, stage=stage, kind=kind, markers=markers)
        return
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    start_index = next((index for index, line in enumerate(lines) if section_marker in line), None)
    if start_index is None:
        for marker in markers:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": _report_path(path, export_root),
                    "line": "",
                    "marker": marker,
                    "snippet": f"section not found: {section_marker}",
                }
            )
        return
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        if any(marker in lines[index] for marker in end_markers):
            end_index = index
            break
    section_lines = lines[start_index:end_index]
    for marker in markers:
        matched = False
        for offset, line in enumerate(section_lines, start=start_index + 1):
            if marker in line:
                rows.append(
                    {
                        "stage": stage,
                        "kind": kind,
                        "source": _report_path(path, export_root),
                        "line": offset,
                        "marker": marker,
                        "snippet": line.strip(),
                    }
                )
                matched = True
                break
        if not matched:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": _report_path(path, export_root),
                    "line": "",
                    "marker": marker,
                    "snippet": f"marker not found in section: {section_marker}",
                }
            )


def _probe_has(rows: list[dict[str, Any]], stage: str, marker: str) -> bool:
    return any(row.get("stage") == stage and row.get("marker") == marker and row.get("line") not in {"", None} for row in rows)


def _packet_field_rows(export_root: Path, packet_paths: list[Path | None]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in packet_paths:
        if path is None or not path.is_file():
            continue
        item = _parse_packet_file(path, export_root)
        if not item:
            continue
        for field in item["fields"]:
            rows.append(
                {
                    "packet_id": item["id"],
                    "packet_name": item["name"],
                    "direction": item["direction"],
                    "field_index": field["field_index"],
                    "field_name": field["field_name"],
                    "read_method": field["read_method"],
                    "type_hint": field["type_hint"],
                    "source": _report_path(path, export_root),
                    "line": field["line"],
                }
            )
    return rows


def _write_login_socket_send_flow_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login socket send flow report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report traces the local Lua path from socket connection to the first login packets.",
        "- After socket connect, the client sends `CM_ProtoHash(20013)`. `SM_ProtoHash(20014)` returns `hash/version`; when accepted, `EnterGameInfo:ContinueLogin` sends `CM_Login(20001)` or the relogin variant.",
        "- `SocketManager:F_SendMsg` calls `pSendInfo:write()`, then `LuaSocket:F_Send(pid, sn)`, which reaches `SocketBridge.F_Send(pid, isMainSocket, sn)`.",
        "- Packet field order is recovered from the generated Lua packet classes; `CM_Login` writes strings/ints into `BaseMessage -> LusuoStreamWarp -> ProtoBridge`.",
        "",
        "Reconstructed chain:",
        "",
        "```text",
        "SocketConnected / StartEnter_2",
        "  -> LoginNetLogic.CM_ProtoHashFun()",
        "  -> SocketManager.GetMessageFromPools(CM_ProtoHash)",
        "  -> SocketManager.F_SendMsg(CM_ProtoHash)",
        "  -> SM_ProtoHashFun(msg) -> LoginMgr.ContinueLogin(msg)",
        "  -> EnterGameInfo.StartEnter_3(msg) checks hash/version",
        "  -> EnterGameInfo.ContinueLogin()",
        "  -> LoginNetLogic.CM_LoginFun(curServerItem...)",
        "  -> CM_Login.writing(): account/serverId/pid/.../sign",
        "  -> BaseMessage.write -> LusuoStreamWarp -> ProtoBridge",
        "  -> SocketManager.DoSendMsg -> LuaSocket.F_Send(pid, sn)",
        "  -> SocketBridge.F_Send(pid, isMainSocket, sn)",
        "```",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(["", "## Login Packet Fields", "", "| Packet | ID | Direction | # | Field | Wire Method | Source | Line |", "| --- | ---: | --- | ---: | --- | --- | --- | ---: |"])
    for row in field_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('packet_id', '')} | {row.get('direction', '')} | "
            f"{row.get('field_index', '')} | `{row.get('field_name', '')}` | `{row.get('read_method', '')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` | {row.get('line', '')} |"
        )

    lines.extend(["", "## Key Evidence", "", "| Stage | Kind | Source | Line | Marker | Snippet |", "| --- | --- | --- | ---: | --- | --- |"])
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Evidence TSV: `{_report_path(output_dir / 'lua_login_socket_send_flow_evidence.tsv', export_root)}`",
            f"- Packet fields TSV: `{_report_path(output_dir / 'lua_login_socket_packet_fields.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_socket_send_flow_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_socket_send_flow_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Trace CM_ProtoHash/CM_Login packet construction into SocketBridge.F_Send."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    login_net_logic = _find_lua_text_asset(root, "LoginNetLogic.lua", ("CM_LoginFun", "CM_ProtoHashFun", "F_SendMsg"))
    enter_game_info = _find_lua_text_asset(root, "EnterGameInfo.lua", ("StartEnter_2", "StartEnter_3", "ContinueLogin"))
    login_mgr = _find_lua_text_asset(root, "LoginMgr.lua", ("ContinueLogin", "LoginDataBack", "StartEnter_3"))
    socket_manager = _find_lua_text_asset(root, "SocketManager.lua", ("function _M.F_SendMsg", "DoSendMsg", "F_Send"))
    lua_socket = _find_lua_text_asset(root, "LuaSocket.lua", ("function _M.F_Send", "SocketBridge.F_Send"))
    message_pool = _find_lua_text_asset(root, "MessagePool.lua", ("function _M:F_Register", "RegisterLuaPro", "F_GetMessage"))
    base_message = _find_lua_text_asset(root, "BaseMessage.lua", ("function _M:write(buf)", "LusuoStreamWarp.new()", "self:writing()"))
    lusuo_stream = _find_lua_text_asset(root, "LusuoStreamWarp.lua", ("ProtoBridge.WriteInt", "ProtoBridge.WriteBigString"))
    vo_url = _find_lua_text_asset(root, "VO_URL.lua", ("['20001']", "CM_Login", "CM_ProtoHash"))
    cm_login = _find_lua_text_asset(root, "CM_Login.lua", ("return 20001", "self:writeString(self.account)", "self:writeInt(self.signTime)"))
    cm_proto_hash = _find_lua_text_asset(root, "CM_ProtoHash.lua", ("return 20013", "return\"CM_ProtoHash\""))
    sm_proto_hash = _find_lua_text_asset(root, "SM_ProtoHash.lua", ("return 20014", "self.hash=self:readInt()", "self.version=self:readString()"))
    cm_relogin = _find_lua_text_asset(root, "CM_ReLogin.lua", ("return 20009", "self:writeString(self.token)"))

    evidence_rows: list[dict[str, Any]] = []
    _append_probe_evidence(
        evidence_rows,
        path=vo_url,
        export_root=root,
        stage="00-packet-id-map",
        kind="lua",
        markers=(
            "['20001']=setmetatable({'20001','module.user.login.packet.CM_Login',},_o),",
            "['20002']=setmetatable({'20002','module.user.login.packet.SM_Login',},_o),",
            "['20009']=setmetatable({'20009','module.user.login.packet.CM_ReLogin',},_o),",
            "['20010']=setmetatable({'20010','module.user.login.packet.SM_ReLogin',},_o),",
            "['20013']=setmetatable({'20013','module.user.login.packet.CM_ProtoHash',},_o),",
            "['20014']=setmetatable({'20014','module.user.login.packet.SM_ProtoHash',},_o),",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=message_pool,
        export_root=root,
        stage="01-packet-register-bridge",
        kind="lua",
        section_marker="function _M:F_Register(id,messageClass,handlerClass)",
        markers=(
            "self.messages:LuaDic_Add(id,messageClass)",
            "self.handlers:LuaDic_Add(id,handlerClass)",
            "SocketBridge.RegisterLuaPro(id)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_net_logic,
        export_root=root,
        stage="02-login-packet-register",
        kind="lua",
        section_marker="function _M.LoginNetLogic(self)",
        markers=(
            "_MessagePool.Inst_get():F_Register(_CM_Login:getId(),typeof(_CM_Login))",
            "_MessagePool.Inst_get():F_Register(_SM_Login:getId(),typeof(_SM_Login),function(msg)",
            "_MessagePool.Inst_get():F_Register(_CM_ProtoHash:getId(),typeof(_CM_ProtoHash))",
            "_MessagePool.Inst_get():F_Register(_SM_ProtoHash:getId(),typeof(_SM_ProtoHash),function(msg)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="03-after-socket-connect",
        kind="lua",
        section_marker="function _M.StartEnter_2(self)",
        markers=(
            "LoginMgr.Inst_get():SetLoginingState(LoginType.StateType.GetVersion)",
            "LoginMgr.Inst_get().LoginNetLogic.CM_ProtoHashFun()",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_net_logic,
        export_root=root,
        stage="04-send-protohash",
        kind="lua",
        section_marker="function _M.CM_ProtoHashFun()",
        markers=(
            "local CM_ProtoHash=SocketManager.Inst_get():GetMessageFromPools(_CM_ProtoHash)",
            "SocketManager.Inst_get():F_SendMsg(CM_ProtoHash)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_net_logic,
        export_root=root,
        stage="05-protohash-response",
        kind="lua",
        section_marker="function _M.SM_ProtoHashFun(msg)",
        markers=("LoginMgr.Inst_get():ContinueLogin(msg)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_mgr,
        export_root=root,
        stage="06-loginmgr-continue",
        kind="lua",
        section_marker="function _M.ContinueLogin(self,msg)",
        markers=("self.EnterGameInfo:StartEnter_3(msg)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="07-version-check",
        kind="lua",
        section_marker="function _M.StartEnter_3(self,msg)",
        markers=(
            "local hash=msg.hash",
            "local version=LoginMgr.Inst_get().LoginModel.V_version",
            "if hash==version or LuaGlobal.IsWebGL then",
            "self:ContinueLogin()",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="08-send-login",
        kind="lua",
        section_marker="function _M.ContinueLogin(self)",
        markers=(
            "LoginMgr.Inst_get().LoginNetLogic.CM_ReLoginHandler(curServerItem.account,curServerItem.serverId,curServerItem.token,curServerItem.pid,self.V_ChannelPackage)",
            "LoginMgr.Inst_get().LoginNetLogic.CM_LoginFun(curServerItem.serverId,curServerItem.account,curServerItem.pid,self.V_ChannelPackage)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_net_logic,
        export_root=root,
        stage="09-login-fields",
        kind="lua",
        section_marker="function _M.CM_LoginFun(serverId,account,pid,channelPackage)",
        markers=(
            "local CM_Login=SocketManager.Inst_get():GetMessageFromPools(_CM_Login)",
            "CM_Login.serverId=serverId",
            "CM_Login.account=account",
            "CM_Login.pid=pid or\"\"",
            "CM_Login.bundleVersion=PhoneHelper.F_GetPhoneVersion()or\"\"",
            "CM_Login.sign=loginAccount and loginAccount.V_Token or\"\"",
            "CM_Login.signTime=loginAccount and loginAccount.V_Time or 0",
            "CM_Login.cid=(PhoneHelper.GetPid()or\"\")..\"\"",
            "CM_Login.gid=(PhoneHelper.GetGameId()or\"\")..\"\"",
            "CM_Login.device=PhoneHelper.F_GetOSType()or\"\"",
            "CM_Login.bundleId=PhoneHelper.F_GetPackageName()or\"\"",
            "CM_Login.devId=PhoneHelper.F_GetIMEI()or\"\"",
            "CM_Login.pushToken=PhoneHelper.GetPushToken()or\"\"",
            "SocketManager.Inst_get():F_SendMsg(CM_Login)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=cm_login,
        export_root=root,
        stage="10-cm-login-wire-order",
        kind="lua_packet",
        section_marker="function _M.writing(self)",
        markers=(
            "self:writeString(self.account)",
            "self:writeInt(self.serverId)",
            "self:writeString(self.pid)",
            "self:writeString(self.cid)",
            "self:writeString(self.gid)",
            "self:writeString(self.device)",
            "self:writeString(self.devId)",
            "self:writeString(self.pushToken)",
            "self:writeString(self.bundleId)",
            "self:writeString(self.bundleVersion)",
            "self:writeString(self.location)",
            "self:writeInt(self.channelPackage)",
            "self:writeInt(self.signTime)",
            "self:writeString(self.sign)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=socket_manager,
        export_root=root,
        stage="11-sendmsg-core",
        kind="lua",
        section_marker="function _M.F_SendMsg(self,pSendInfo,clientData)",
        markers=(
            "local pid=pSendInfo:getId()",
            "_MessagePool.Inst_get():F_RegClientData(sn,{pid=pid,clientData=clientData})",
            "self:DoSendMsg(so,pSendInfo,pid,sn)",
            "self:Recycle_MessagePools(pSendInfo)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=socket_manager,
        export_root=root,
        stage="12-write-and-send",
        kind="lua",
        section_marker="function _M.DoSendMsg(self,so,pSendInfo,pid,sn)",
        markers=(
            "so:ResetWriteProtoStreamBuffer()",
            "pSendInfo:write()",
            "so:F_Send(pid,sn)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=base_message,
        export_root=root,
        stage="13-base-message-write",
        kind="lua",
        section_marker="function _M:write(buf)",
        markers=(
            "self.m_buf=LusuoStreamWarp.new()",
            "self:writing()",
        ),
    )
    _append_probe_evidence(
        evidence_rows,
        path=lusuo_stream,
        export_root=root,
        stage="14-protobridge-writers",
        kind="lua",
        markers=(
            "ProtoBridge.WriteInt(data)",
            "ProtoBridge.WriteBigString(strOut)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=lua_socket,
        export_root=root,
        stage="15-native-send",
        kind="lua",
        section_marker="function _M.F_Send(self,proId,sn)",
        markers=("return SocketBridge.F_Send(proId,self.isMainSocket,sn)",),
    )

    field_rows = _packet_field_rows(root, [cm_proto_hash, cm_login, sm_proto_hash, cm_relogin])
    field_count_by_name = Counter(str(row["packet_name"]) for row in field_rows)
    checks = {
        "vo_url_login_ids": _probe_has(
            evidence_rows,
            "00-packet-id-map",
            "['20001']=setmetatable({'20001','module.user.login.packet.CM_Login',},_o),",
        )
        and _probe_has(
            evidence_rows,
            "00-packet-id-map",
            "['20013']=setmetatable({'20013','module.user.login.packet.CM_ProtoHash',},_o),",
        ),
        "message_pool_registers_native_protocols": _probe_has(
            evidence_rows,
            "01-packet-register-bridge",
            "SocketBridge.RegisterLuaPro(id)",
        ),
        "login_packets_registered": _probe_has(
            evidence_rows,
            "02-login-packet-register",
            "_MessagePool.Inst_get():F_Register(_CM_Login:getId(),typeof(_CM_Login))",
        )
        and _probe_has(
            evidence_rows,
            "02-login-packet-register",
            "_MessagePool.Inst_get():F_Register(_CM_ProtoHash:getId(),typeof(_CM_ProtoHash))",
        ),
        "socket_connect_requests_protohash": _probe_has(
            evidence_rows,
            "03-after-socket-connect",
            "LoginMgr.Inst_get().LoginNetLogic.CM_ProtoHashFun()",
        ),
        "protohash_packet_sent": _probe_has(
            evidence_rows,
            "04-send-protohash",
            "SocketManager.Inst_get():F_SendMsg(CM_ProtoHash)",
        ),
        "protohash_response_continues_login": _probe_has(
            evidence_rows,
            "05-protohash-response",
            "LoginMgr.Inst_get():ContinueLogin(msg)",
        )
        and _probe_has(evidence_rows, "07-version-check", "self:ContinueLogin()"),
        "normal_login_packet_sent": _probe_has(
            evidence_rows,
            "08-send-login",
            "LoginMgr.Inst_get().LoginNetLogic.CM_LoginFun(curServerItem.serverId,curServerItem.account,curServerItem.pid,self.V_ChannelPackage)",
        )
        and _probe_has(evidence_rows, "09-login-fields", "SocketManager.Inst_get():F_SendMsg(CM_Login)"),
        "cm_login_wire_fields": field_count_by_name.get("CM_Login", 0) >= 14
        and _probe_has(evidence_rows, "10-cm-login-wire-order", "self:writeString(self.account)")
        and _probe_has(evidence_rows, "10-cm-login-wire-order", "self:writeString(self.sign)"),
        "sendmsg_writes_then_sends": _probe_has(evidence_rows, "12-write-and-send", "pSendInfo:write()")
        and _probe_has(evidence_rows, "12-write-and-send", "so:F_Send(pid,sn)"),
        "base_message_uses_lusuo_stream": _probe_has(
            evidence_rows,
            "13-base-message-write",
            "self.m_buf=LusuoStreamWarp.new()",
        ),
        "lusuo_stream_uses_protobridge": _probe_has(evidence_rows, "14-protobridge-writers", "ProtoBridge.WriteInt(data)")
        and _probe_has(evidence_rows, "14-protobridge-writers", "ProtoBridge.WriteBigString(strOut)"),
        "lua_socket_calls_native_send": _probe_has(
            evidence_rows,
            "15-native-send",
            "return SocketBridge.F_Send(proId,self.isMainSocket,sn)",
        ),
    }

    _write_tsv(
        output_dir / "lua_login_socket_send_flow_evidence.tsv",
        evidence_rows,
        ["stage", "kind", "source", "line", "marker", "snippet"],
    )
    _write_tsv(
        output_dir / "lua_login_socket_packet_fields.tsv",
        field_rows,
        ["packet_id", "packet_name", "direction", "field_index", "field_name", "read_method", "type_hint", "source", "line"],
    )
    _write_login_socket_send_flow_markdown(
        output_dir / "lua_login_socket_send_flow_report.md",
        export_root=root,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        field_rows=field_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_socket_send_flow_report.md"),
        "evidence_path": str(output_dir / "lua_login_socket_send_flow_evidence.tsv"),
        "packet_fields_path": str(output_dir / "lua_login_socket_packet_fields.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "evidence": len(evidence_rows),
            "packet_fields": len(field_rows),
            "cm_login_fields": field_count_by_name.get("CM_Login", 0),
        },
        "sources": {
            "login_net_logic": str(login_net_logic) if login_net_logic else "",
            "enter_game_info": str(enter_game_info) if enter_game_info else "",
            "login_mgr": str(login_mgr) if login_mgr else "",
            "socket_manager": str(socket_manager) if socket_manager else "",
            "lua_socket": str(lua_socket) if lua_socket else "",
            "message_pool": str(message_pool) if message_pool else "",
            "base_message": str(base_message) if base_message else "",
            "lusuo_stream": str(lusuo_stream) if lusuo_stream else "",
            "vo_url": str(vo_url) if vo_url else "",
            "cm_login": str(cm_login) if cm_login else "",
            "cm_proto_hash": str(cm_proto_hash) if cm_proto_hash else "",
            "sm_proto_hash": str(sm_proto_hash) if sm_proto_hash else "",
            "cm_relogin": str(cm_relogin) if cm_relogin else "",
        },
    }
    (output_dir / "lua_login_socket_send_flow_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_login_socket_response_flow_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    consumer_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login socket response flow report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report traces `SM_Login(20002)` after the socket receive/dispatch layer reaches `LoginNetLogic.SM_LoginData(msg)`.",
        "- `SM_Login` is a server-to-client login snapshot packet. It carries role, map, wallet, function-open, cross-server, time, token, and channel data used to initialize the local game session.",
        "- Success handling stores the server token back onto the selected server item, initializes core models, then waits for role/time/scene readiness before sending `CM_LoginFinish(20007)`.",
        "",
        "Reconstructed chain:",
        "",
        "```text",
        "Socket receive dispatch",
        "  -> MessagePool handler for SM_Login(20002)",
        "  -> LoginNetLogic.SM_LoginData(msg)",
        "  -> LoginMgr.LoginDataBack(msg)",
        "  -> EnterGameInfo.StartEnter_4(msg)",
        "       token/channel/time/cross/openServer setup",
        "       -> StartEnter_5(msg)",
        "          mapInfo/role/worldLevel/wallet/functionOpen initialization",
        "          -> RoleMgr.InitRole(...) or create-role branch",
        "          -> SceneMgr.SceneNetLogic.CM_RecoverMapFun(false)",
        "  -> GameTime.UpdateSystemTime(...) marks V_IsInfoTime",
        "  -> EnterGameInfo.CheckAndSendFinish()",
        "  -> LoginNetLogic.CM_LoginFinishSent()",
        "  -> CM_LoginFinish(20007)",
        "```",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## SM_Login Fields",
            "",
            "| # | Field | Wire Method | Type Hint | Primary Consumer | Source | Line |",
            "| ---: | --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in field_rows:
        lines.append(
            f"| {row.get('field_index', '')} | `{row.get('field_name', '')}` | `{row.get('read_method', '')}` | "
            f"`{row.get('type_hint', '')}` | {str(row.get('primary_consumer', '')).replace('|', '\\|')} | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` | {row.get('line', '')} |"
        )

    lines.extend(["", "## Field Consumers", "", "| Field | Consumer | Meaning |", "| --- | --- | --- |"])
    for row in consumer_rows:
        lines.append(
            f"| `{row.get('field_name', '')}` | `{str(row.get('consumer', '')).replace('|', '\\|')}` | "
            f"{str(row.get('meaning', '')).replace('|', '\\|')} |"
        )

    lines.extend(["", "## Key Evidence", "", "| Stage | Kind | Source | Line | Marker | Snippet |", "| --- | --- | --- | ---: | --- | --- |"])
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Evidence TSV: `{_report_path(output_dir / 'lua_login_socket_response_flow_evidence.tsv', export_root)}`",
            f"- SM_Login fields TSV: `{_report_path(output_dir / 'lua_login_socket_response_fields.tsv', export_root)}`",
            f"- Field consumers TSV: `{_report_path(output_dir / 'lua_login_socket_response_consumers.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_socket_response_flow_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_socket_response_flow_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Trace SM_Login dispatch into login-session initialization and CM_LoginFinish."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    vo_url = _find_lua_text_asset(root, "VO_URL.lua", ("['20002']", "SM_Login", "SM_LoginFinish"))
    sm_login = _find_lua_text_asset(
        root,
        "SM_Login.lua",
        ("return 20002", "self.accountId=self:readString()", "self.token=self:readString()"),
    )
    cm_login_finish = _find_lua_text_asset(root, "CM_LoginFinish.lua", ("return 20007", "CM_LoginFinish"))
    sm_login_finish = _find_lua_text_asset(root, "SM_LoginFinish.lua", ("return 20008", "SM_LoginFinish"))
    login_net_logic = _find_lua_text_asset(root, "LoginNetLogic.lua", ("SM_LoginData", "CM_LoginFinishSent", "SM_LoginFinishData"))
    login_mgr = _find_lua_text_asset(root, "LoginMgr.lua", ("LoginDataBack", "CheckAndSendFinish", "GetSmLoginData"))
    enter_game_info = _find_lua_text_asset(root, "EnterGameInfo.lua", ("StartEnter_4", "StartEnter_5", "CheckAndSendFinish"))
    game_time = _find_lua_text_asset(root, "GameTime.lua", ("TimeDataInfo", "UpdateSystemTime", "CheckAndSendFinish"))

    evidence_rows: list[dict[str, Any]] = []
    _append_probe_evidence(
        evidence_rows,
        path=vo_url,
        export_root=root,
        stage="00-packet-id-map",
        kind="lua",
        markers=(
            "['20002']=setmetatable({'20002','module.user.login.packet.SM_Login',},_o),",
            "['20007']=setmetatable({'20007','module.user.login.packet.CM_LoginFinish',},_o),",
            "['20008']=setmetatable({'20008','module.user.login.packet.SM_LoginFinish',},_o),",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_net_logic,
        export_root=root,
        stage="01-login-response-register",
        kind="lua",
        section_marker="function _M.LoginNetLogic(self)",
        markers=(
            "_MessagePool.Inst_get():F_Register(_SM_Login:getId(),typeof(_SM_Login),function(msg)",
            "self.SM_LoginData(msg)",
            "_MessagePool.Inst_get():F_Register(_CM_LoginFinish:getId(),typeof(_CM_LoginFinish))",
            "_MessagePool.Inst_get():F_Register(_SM_LoginFinish:getId(),typeof(_SM_LoginFinish),function(msg)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=sm_login,
        export_root=root,
        stage="02-sm-login-wire-order",
        kind="lua_packet",
        section_marker="function _M.reading(self)",
        markers=(
            "self.accountId=self:readString()",
            "self.token=self:readString()",
            "self.role=_AS_(self:readBean(typeof(RoleVO)),RoleVO)",
            "self.mapInfo=_AS_(self:readBean(typeof(MapInfoVO)),MapInfoVO)",
            "self:readMessageList2List(self.wallet)",
            "self.inner=self:readBool()",
            "self.timestamp=self:readLong()",
            "self.timeZone=self:readInt()",
            "self.worldLevel=self:readInt()",
            "self.functionOpen=_AS_(self:readBean(typeof(FunctionOpenVo)),FunctionOpenVo)",
            "self.totalOnlineSecends=self:readLong()",
            "self.totalTimes=self:readInt()",
            "self.openServer=self:readLong()",
            "self.crossVO=_AS_(self:readBean(typeof(CrossVO)),CrossVO)",
            "self.channelPackage=self:readInt()",
            "self.clubVO=_AS_(self:readBean(typeof(SimpleClubVO)),SimpleClubVO)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_net_logic,
        export_root=root,
        stage="03-sm-login-handler",
        kind="lua",
        section_marker="function _M.SM_LoginData(msg)",
        markers=(
            "UIShowMgr.Inst_get():CloseById(Window.LoginWaitingView)",
            "if(msg==nil or msg.code~=0)then",
            "LoginMgr.Inst_get():DisconnectAndBack(msg.code)",
            "PhoneHelper.F_UploadThinkingLaunchProcess(SdkLaunchEventType.Recv_Login_Data)",
            "LoginMgr.Inst_get():LoginDataBack(msg)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_mgr,
        export_root=root,
        stage="04-loginmgr-forward",
        kind="lua",
        section_marker="function _M.LoginDataBack(self,msg)",
        markers=("self.EnterGameInfo:StartEnter_4(msg)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="05-start-enter-4-session",
        kind="lua",
        section_marker="function _M.StartEnter_4(self,msg)",
        markers=(
            "self.V_SM_Login=msg",
            "serverItem.token=msg.token",
            "self:ChangeChannelPackage(msg and msg.channelPackage)",
            "self:FinishChekDataInfo()",
            "LoginMgr.Inst_get().GameTime:TimeDataInfo(msg.timestamp,msg.timeZone)",
            "LoginMgr.Inst_get().SdkCustomEvent:SmLoginInfo(msg)",
            "self:UpdateCrossData(msg and msg.crossVO)",
            "self.openServer=msg.openServer",
            "self:StartEnter_5(msg)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="06-cross-data",
        kind="lua",
        section_marker="function _M.UpdateCrossData(self,crossVO)",
        markers=(
            "self.V_CrossVO=crossVO",
            "self.V_ServerList=self.V_CrossVO and self.V_CrossVO.serverList",
            "self.V_PrepareServerList=self.V_CrossVO and self.V_CrossVO.prepareServerList",
            "self.V_CrossServerDic=dic",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="07-start-enter-5-models",
        kind="lua",
        section_marker="function _M.StartEnter_5(self,msg)",
        markers=(
            "self.V_EnterGameMapInfo=msg.mapInfo",
            "TaskMgr.Inst_get().Model.TaskData:OnLevelUpdate(msg.role.level)",
            "WorldlevelMgr.Inst_get().Model:SetWorldLevel(msg.worldLevel)",
            "WalletMgr.Inst_get().Model.WalletData:SetWalletInfo(msg.wallet)",
            "FunctionMgr.Inst_get().Model:DataInfo(msg.functionOpen)",
            "PhoneHelper.SetUserName(msg.role.name,msg.role.roleId:ToString())",
            "RoleMgr.Inst_get():InitRole(msg.role)",
            "SceneMgr.Inst_get().SceneNetLogic.CM_RecoverMapFun(false)",
            "self:GetBaseModelData(msg)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=game_time,
        export_root=root,
        stage="08-time-ready",
        kind="lua",
        section_marker="function _M.TimeDataInfo(self,timestamp,timeZone)",
        markers=(
            "self.timeZone=timeZone",
            "self:UpdateSystemTime(timestamp)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=game_time,
        export_root=root,
        stage="09-time-finish-gate",
        kind="lua",
        section_marker="function _M.UpdateSystemTime(self,timestamp)",
        markers=(
            "LoginMgr.Inst_get().EnterGameInfo.V_IsInfoTime=true",
            "LoginMgr.Inst_get().EnterGameInfo:CheckAndSendFinish()",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="10-finish-gate",
        kind="lua",
        section_marker="function _M.CheckAndSendFinish(self)",
        markers=(
            "self.V_InfoAllFinish=self.V_IsInfoRole and self.V_IsInfoTime and self.V_IsEnterScence",
            "LoginMgr.Inst_get().LoginNetLogic.CM_LoginFinishSent()",
            "LoginMgr.Inst_get().LoginModel:RaiseEvent(LoginType.EventType.LoginSucceed)",
            "self.LoginFinishGetServerData()",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_net_logic,
        export_root=root,
        stage="11-send-login-finish",
        kind="lua",
        section_marker="function _M.CM_LoginFinishSent()",
        markers=(
            "local CM_LoginFinish=SocketManager.Inst_get():GetMessageFromPools(_CM_LoginFinish)",
            "SocketManager.Inst_get():F_SendMsg(CM_LoginFinish)",
        ),
    )

    consumer_by_field = {
        "accountId": ("SM_Login retained on EnterGameInfo.V_SM_Login", "account identifier included in the login snapshot"),
        "token": ("EnterGameInfo.StartEnter_4 -> curServerItem.token", "server token retained for later reconnect/login state"),
        "role": ("StartEnter_5 -> RoleMgr.InitRole / create-role branch", "main role payload and SDK role reporting source"),
        "mapInfo": ("StartEnter_5 -> V_EnterGameMapInfo", "map restore data for scene recovery"),
        "wallet": ("StartEnter_5 -> WalletData.SetWalletInfo", "currency wallet snapshot"),
        "inner": ("SM_Login field retained", "internal/login-state flag; no direct consumer pinned in this probe"),
        "timestamp": ("GameTime.TimeDataInfo -> UpdateSystemTime", "server time calibration and login finish gate"),
        "timeZone": ("GameTime.TimeDataInfo", "server timezone cache"),
        "worldLevel": ("StartEnter_5 -> WorldlevelMgr.Model.SetWorldLevel", "world-level model state"),
        "functionOpen": ("StartEnter_5 -> FunctionMgr.Model.DataInfo", "feature-open switch snapshot"),
        "totalOnlineSecends": ("SM_Login retained on V_SM_Login", "online-history counter; not directly consumed in this probe"),
        "totalTimes": ("StartEnter_4/5 WebGL SDK report branch", "login count used for first-login reporting"),
        "openServer": ("StartEnter_4 -> self.openServer", "open-server timestamp/state accessor"),
        "crossVO": ("StartEnter_4 -> UpdateCrossData", "cross-server lists and cross-group data"),
        "channelPackage": ("StartEnter_4 -> ChangeChannelPackage", "channel package selection for later login context"),
        "clubVO": ("StartEnter_4/5 SDK report partyName", "simple club/faction info used in SDK role reporting"),
    }
    field_rows = _packet_field_rows(root, [sm_login])
    for row in field_rows:
        consumer, meaning = consumer_by_field.get(str(row.get("field_name") or ""), ("", ""))
        row["primary_consumer"] = consumer
        row["meaning"] = meaning
    consumer_rows = [
        {"field_name": field_name, "consumer": values[0], "meaning": values[1]}
        for field_name, values in consumer_by_field.items()
    ]

    sm_login_field_names = {str(row.get("field_name") or "") for row in field_rows if row.get("packet_name") == "SM_Login"}
    checks = {
        "vo_url_response_ids": _probe_has(
            evidence_rows,
            "00-packet-id-map",
            "['20002']=setmetatable({'20002','module.user.login.packet.SM_Login',},_o),",
        )
        and _probe_has(
            evidence_rows,
            "00-packet-id-map",
            "['20007']=setmetatable({'20007','module.user.login.packet.CM_LoginFinish',},_o),",
        ),
        "sm_login_registered": _probe_has(
            evidence_rows,
            "01-login-response-register",
            "_MessagePool.Inst_get():F_Register(_SM_Login:getId(),typeof(_SM_Login),function(msg)",
        )
        and _probe_has(evidence_rows, "01-login-response-register", "self.SM_LoginData(msg)"),
        "sm_login_wire_fields": len(sm_login_field_names) >= 16
        and {"token", "role", "mapInfo", "wallet", "timestamp", "timeZone", "functionOpen", "crossVO"}.issubset(sm_login_field_names),
        "sm_login_success_forwards": _probe_has(
            evidence_rows,
            "03-sm-login-handler",
            "LoginMgr.Inst_get():LoginDataBack(msg)",
        )
        and _probe_has(evidence_rows, "04-loginmgr-forward", "self.EnterGameInfo:StartEnter_4(msg)"),
        "start_enter_4_session_fields": _probe_has(evidence_rows, "05-start-enter-4-session", "serverItem.token=msg.token")
        and _probe_has(
            evidence_rows,
            "05-start-enter-4-session",
            "LoginMgr.Inst_get().GameTime:TimeDataInfo(msg.timestamp,msg.timeZone)",
        )
        and _probe_has(evidence_rows, "05-start-enter-4-session", "self:UpdateCrossData(msg and msg.crossVO)"),
        "start_enter_5_model_fields": _probe_has(
            evidence_rows,
            "07-start-enter-5-models",
            "RoleMgr.Inst_get():InitRole(msg.role)",
        )
        and _probe_has(evidence_rows, "07-start-enter-5-models", "WalletMgr.Inst_get().Model.WalletData:SetWalletInfo(msg.wallet)")
        and _probe_has(evidence_rows, "07-start-enter-5-models", "FunctionMgr.Inst_get().Model:DataInfo(msg.functionOpen)"),
        "time_update_enters_finish_gate": _probe_has(
            evidence_rows,
            "09-time-finish-gate",
            "LoginMgr.Inst_get().EnterGameInfo.V_IsInfoTime=true",
        )
        and _probe_has(
            evidence_rows,
            "09-time-finish-gate",
            "LoginMgr.Inst_get().EnterGameInfo:CheckAndSendFinish()",
        ),
        "finish_gate_sends_cm_loginfinish": _probe_has(
            evidence_rows,
            "10-finish-gate",
            "LoginMgr.Inst_get().LoginNetLogic.CM_LoginFinishSent()",
        )
        and _probe_has(
            evidence_rows,
            "11-send-login-finish",
            "SocketManager.Inst_get():F_SendMsg(CM_LoginFinish)",
        ),
    }

    _write_tsv(
        output_dir / "lua_login_socket_response_flow_evidence.tsv",
        evidence_rows,
        ["stage", "kind", "source", "line", "marker", "snippet"],
    )
    _write_tsv(
        output_dir / "lua_login_socket_response_fields.tsv",
        field_rows,
        [
            "packet_id",
            "packet_name",
            "direction",
            "field_index",
            "field_name",
            "read_method",
            "type_hint",
            "primary_consumer",
            "meaning",
            "source",
            "line",
        ],
    )
    _write_tsv(
        output_dir / "lua_login_socket_response_consumers.tsv",
        consumer_rows,
        ["field_name", "consumer", "meaning"],
    )
    _write_login_socket_response_flow_markdown(
        output_dir / "lua_login_socket_response_flow_report.md",
        export_root=root,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        field_rows=field_rows,
        consumer_rows=consumer_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_socket_response_flow_report.md"),
        "evidence_path": str(output_dir / "lua_login_socket_response_flow_evidence.tsv"),
        "fields_path": str(output_dir / "lua_login_socket_response_fields.tsv"),
        "consumers_path": str(output_dir / "lua_login_socket_response_consumers.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "evidence": len(evidence_rows),
            "sm_login_fields": len(sm_login_field_names),
            "field_consumers": len(consumer_rows),
        },
        "sources": {
            "vo_url": str(vo_url) if vo_url else "",
            "sm_login": str(sm_login) if sm_login else "",
            "cm_login_finish": str(cm_login_finish) if cm_login_finish else "",
            "sm_login_finish": str(sm_login_finish) if sm_login_finish else "",
            "login_net_logic": str(login_net_logic) if login_net_logic else "",
            "login_mgr": str(login_mgr) if login_mgr else "",
            "enter_game_info": str(enter_game_info) if enter_game_info else "",
            "game_time": str(game_time) if game_time else "",
        },
    }
    (output_dir / "lua_login_socket_response_flow_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _packet_summary_for_path(root: Path, path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    item = _parse_packet_file(path, root)
    if not item:
        return {}
    return {
        "packet_id": item.get("id", ""),
        "packet_name": item.get("name", ""),
        "direction": item.get("direction", ""),
        "module": item.get("module", ""),
        "field_count": item.get("field_count", 0),
        "fields": _field_signature(item.get("wire_fields", [])),
        "source": _report_path(path, root),
    }


def _write_login_post_sync_handler_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, Any]],
    handler_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    closed_count = sum(1 for row in handler_rows if row.get("status") == "closed_lua_handler")
    schema_only_count = sum(1 for row in handler_rows if row.get("status") == "packet_schema_only")
    closed_lines = [
        f"- `{row.get('entry', '')}`: `{row.get('visible_handler', '')}` -> `{row.get('sink', '')}`"
        for row in handler_rows
        if row.get("status") == "closed_lua_handler"
    ]
    lines = [
        "# Lua login post-sync handler report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report follows the first post-login sync fanout one layer deeper into visible Lua handlers and local model sinks.",
        f"- The current static surface closes `{closed_count}` of `{len(handler_rows)}` post-login branches; `{schema_only_count}` remain packet-schema-only in this export.",
        "",
        "Visible closed branches:",
        "",
        *(closed_lines or ["- none"]),
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Handler Surface",
            "",
            "| Entry | Status | Request | Response | Visible Handler | Sink | Event/Signal | Note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in handler_rows:
        lines.append(
            f"| `{row.get('entry', '')}` | {row.get('status', '')} | `{row.get('request_packet', '')}` | "
            f"`{row.get('response_packet', '')}` | `{row.get('visible_handler', '')}` | "
            f"`{row.get('sink', '')}` | `{row.get('event_or_signal', '')}` | "
            f"{str(row.get('note', '')).replace('|', '\\|')} |"
        )

    lines.extend(["", "## Response Packet Fields", "", "| Packet | ID | # | Field | Wire Method | Type Hint | Source |", "| --- | ---: | ---: | --- | --- | --- | --- |"])
    for row in field_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('packet_id', '')} | {row.get('field_index', '')} | "
            f"`{row.get('field_name', '')}` | `{row.get('read_method', '')}` | `{row.get('type_hint', '')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` |"
        )

    lines.extend(["", "## Key Evidence", "", "| Stage | Kind | Source | Line | Marker | Snippet |", "| --- | --- | --- | ---: | --- | --- |"])
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Evidence TSV: `{_report_path(output_dir / 'lua_login_post_sync_handlers_evidence.tsv', export_root)}`",
            f"- Handler TSV: `{_report_path(output_dir / 'lua_login_post_sync_handlers.tsv', export_root)}`",
            f"- Packet fields TSV: `{_report_path(output_dir / 'lua_login_post_sync_handler_packet_fields.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_post_sync_handlers_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_post_sync_handler_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Trace visible model/event sinks for the first Lua post-login sync fanout."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    enter_game_info = _find_lua_text_asset(root, "EnterGameInfo.lua", ("LoginFinishGetServerData", "SubpackageMgr", "WorldlevelMgr"))
    worldlevel_net_logic = _find_lua_text_asset(root, "WorldlevelNetLogic.lua", ("SM_WorldLevelWorshipInfoSyncFun", "SetWorshipInfo"))
    worldlevel_data = _find_lua_text_asset(root, "WorldlevelData.lua", ("SetWorshipInfo", "UpdateRewardItems"))
    worldlevel_type = _find_lua_text_asset(root, "WorldlevelType.lua", ("WORLD_LEVEL_WORSHIP_INFO_SYNC", "WORLD_LEVEL_REWARD_NOTIFY"))
    worldlevel_view = _find_lua_text_asset(root, "WorldLevelWorshipView.lua", ("OnWorshipInfoSync", "WORLD_LEVEL_WORSHIP_INFO_SYNC"))
    subpackage_mgr = _find_lua_text_asset(root, "SubpackageMgr.lua", ("StartInit", "GetServerData"))
    subpackage_model = _find_lua_text_asset(root, "SubpackageModel.lua", ("CheckLoadingAndStart", "SM_SubpackageSyncFun"))
    subpackage_net_logic = _find_lua_text_asset(root, "SubpackageNetLogic.lua", ("CM_SubpackageSyncFun", "SM_SubpackageSyncFun"))
    event_net_logic = _find_lua_text_asset(root, "EventNetLogic.lua", ("CM_EventSyncAllFun", "SM_EventSyncAllFun"))
    event_data = _find_lua_text_asset(root, "EventData.lua", ("EventInfo", "StartCheckEvent"))
    xianlv_mines_net_logic = _find_lua_text_asset(
        root,
        "XianLvMinesNetLogic.lua",
        ("CM_SyncBattleFieldBuffsFun", "SM_SyncBattleFieldBuffsFun"),
    )
    xianlv_mines_model = _find_lua_text_asset(
        root,
        "XianLvMinesModel.lua",
        ("SetBattleFieldBuffs", "XianLvMinesUpdateBattleFieldBuffs"),
    )
    youlipool_net_logic = _find_lua_text_asset(root, "YoulipoolNetLogic.lua", ("CM_YouliPoolInfoFun", "SM_YouliPoolInfoFun"))
    youlipool_model = _find_lua_text_asset(root, "YoulipoolModel.lua", ("SetInfoMap", "InfoUpdate"))

    packet_paths = {
        "CM_EventSyncAll": _find_lua_text_asset(root, "CM_EventSyncAll.lua", ("return 38005", "CM_EventSyncAll")),
        "SM_EventSyncAll": _find_lua_text_asset(root, "SM_EventSyncAll.lua", ("return 38006", "events")),
        "CM_SyncBattleFieldBuffs": _find_lua_text_asset(root, "CM_SyncBattleFieldBuffs.lua", ("return 87641", "CM_SyncBattleFieldBuffs")),
        "SM_SyncBattleFieldBuffs": _find_lua_text_asset(root, "SM_SyncBattleFieldBuffs.lua", ("return 87642", "activityBaseIdToBuff")),
        "CM_WorldLevelWorshipInfoSync": _find_lua_text_asset(root, "CM_WorldLevelWorshipInfoSync.lua", ("return 10083", "CM_WorldLevelWorshipInfoSync")),
        "SM_WorldLevelWorshipInfoSync": _find_lua_text_asset(root, "SM_WorldLevelWorshipInfoSync.lua", ("return 10084", "worshipTimes")),
        "CM_YouliPoolInfo": _find_lua_text_asset(root, "CM_YouliPoolInfo.lua", ("return 14371", "CM_YouliPoolInfo")),
        "SM_YouliPoolInfo": _find_lua_text_asset(root, "SM_YouliPoolInfo.lua", ("return 14372", "infoMap")),
        "CM_SubpackageSync": _find_lua_text_asset(root, "CM_SubpackageSync.lua", ("return 43203", "CM_SubpackageSync")),
        "SM_SubpackageSync": _find_lua_text_asset(root, "SM_SubpackageSync.lua", ("return 43204", "subpackageIds")),
    }

    evidence_rows: list[dict[str, Any]] = []
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="00-post-login-fanout",
        kind="lua",
        section_marker="function _M.LoginFinishGetServerData()",
        markers=(
            "EventMgr.Inst_get().NetLogic:CM_EventSyncAllFun()",
            "SubpackageMgr.Inst_get():StartInit()",
            "XianLvMinesMgr.Inst_get().NetLogic:CM_SyncBattleFieldBuffsFun()",
            "WorldlevelMgr.Inst_get().NetLogic:CM_WorldLevelWorshipInfoSyncFun()",
            "YoulipoolMgr.Inst_get().NetLogic:CM_YouliPoolInfoFun()",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=worldlevel_net_logic,
        export_root=root,
        stage="01-worldlevel-register",
        kind="lua",
        section_marker="function _M.LuaWorldlevelNetLogic(self)",
        markers=(
            "_MessagePool.Inst_get():F_Register(_SM_WorldLevelWorshipInfoSync:getId(),typeof(_SM_WorldLevelWorshipInfoSync),function(msg)",
            "self.SM_WorldLevelWorshipInfoSyncFun(msg)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=worldlevel_net_logic,
        export_root=root,
        stage="02-worldlevel-send",
        kind="lua",
        section_marker="function _M.CM_WorldLevelWorshipInfoSyncFun(self)",
        markers=(
            "local CM_WorldLevelWorshipInfoSync=SocketManager.Inst_get():GetMessageFromPools(_CM_WorldLevelWorshipInfoSync)",
            "SocketManager.Inst_get():F_SendMsg(CM_WorldLevelWorshipInfoSync)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=worldlevel_net_logic,
        export_root=root,
        stage="03-worldlevel-response",
        kind="lua",
        section_marker="function _M.SM_WorldLevelWorshipInfoSyncFun(msg)",
        markers=(
            'print("SM_WorldLevelWorshipInfoSyncDataIsGet")',
            "WorldlevelMgr.Inst_get().Model.WorldlevelData:SetWorshipInfo(msg)",
            "WorldlevelMgr.Inst_get().Model:RaiseEvent(WorldlevelType.WORLD_LEVEL_WORSHIP_INFO_SYNC,msg)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=worldlevel_data,
        export_root=root,
        stage="04-worldlevel-data",
        kind="lua",
        section_marker="function _M.SetWorshipInfo(self,msg)",
        markers=("self.worshipInfo=msg",),
    )
    _append_probe_evidence(
        evidence_rows,
        path=worldlevel_type,
        export_root=root,
        stage="05-worldlevel-events",
        kind="lua",
        markers=("_M.WORLD_LEVEL_WORSHIP_INFO_SYNC=\"WORLD_LEVEL_WORSHIP_INFO_SYNC\"",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=worldlevel_view,
        export_root=root,
        stage="06-worldlevel-view-event",
        kind="lua",
        section_marker="function _M.OnWorshipInfoSync(self,msg)",
        markers=("self:UpdateView(msg)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=worldlevel_view,
        export_root=root,
        stage="07-worldlevel-view-update",
        kind="lua",
        section_marker="function _M.UpdateView(self,msg)",
        markers=(
            "self.worshipData=WorldlevelMgr.Inst_get().Model.WorldlevelData:GetWorshipInfo()",
            "local firstRankVO=self.worshipData.firstRankVO",
            "self.name:SetText(firstRankVO.name or\"\")",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=subpackage_mgr,
        export_root=root,
        stage="10-subpackage-start",
        kind="lua",
        section_marker="function _M.StartInit(self)",
        markers=("self.Model:CheckLoadingAndStart()",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=subpackage_model,
        export_root=root,
        stage="11-subpackage-model-start",
        kind="lua",
        section_marker="function _M.CheckLoadingAndStart(self)",
        markers=(
            "self:DataInfo()",
            "self:InfoSubcontract()",
            "self:AddEvent()",
            "self.V_LoadingSubId=LuaGameResDownloadBridge.GetDownloadingPackageId()",
            "SubpackageMgr.Inst_get():GetServerData()",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=subpackage_mgr,
        export_root=root,
        stage="12-subpackage-mgr-server",
        kind="lua",
        section_marker="function _M.GetServerData(self)",
        markers=("self.NetLogic:CM_SubpackageSyncFun()",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=subpackage_net_logic,
        export_root=root,
        stage="13-subpackage-send",
        kind="lua",
        section_marker="function _M.CM_SubpackageSyncFun(self)",
        markers=(
            "local CM_SubpackageSync=SocketManager.Inst_get():GetMessageFromPools(_CM_SubpackageSync)",
            "SocketManager.Inst_get():F_SendMsg(CM_SubpackageSync)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=subpackage_net_logic,
        export_root=root,
        stage="14-subpackage-response",
        kind="lua",
        section_marker="function _M.SM_SubpackageSyncFun(msg)",
        markers=("SubpackageMgr.Inst_get().Model:SM_SubpackageSyncFun(msg)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=subpackage_model,
        export_root=root,
        stage="15-subpackage-model-response",
        kind="lua",
        section_marker="function _M.SM_SubpackageSyncFun(self,msg)",
        markers=(
            "self.V_SubPackageRecRewardList=msg.subpackageIds",
            "self.V_SubRecRewardListInfo=true",
            "LuaEventMgr.Inst_get():RaiseEvent(CommonEventType.SUB_PACKAGE_LOADIND_UPDATE)",
            "SubpackageMgr.Inst_get().Model:UpdateMainUiLoadinBtn()",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=event_net_logic,
        export_root=root,
        stage="20-event-send",
        kind="lua",
        section_marker="function _M.CM_EventSyncAllFun",
        markers=("SocketManager.Inst_get():F_SendMsg(CM_EventSyncAll)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=event_net_logic,
        export_root=root,
        stage="21-event-response",
        kind="lua",
        section_marker="function _M.SM_EventSyncAllFun",
        markers=("EventMgr.Inst_get().Model.EventData:EventInfo(msg.events)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=event_data,
        export_root=root,
        stage="22-event-model",
        kind="lua",
        section_marker="function _M.EventInfo",
        markers=("self:AddEventVO(eventvo)", "LocationDetectionMgr.Inst_get():StartCheckEvent()"),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=xianlv_mines_net_logic,
        export_root=root,
        stage="30-xianlv-mines-send",
        kind="lua",
        section_marker="function _M.CM_SyncBattleFieldBuffsFun",
        markers=("SocketManager.Inst_get():F_SendMsg(CM_SyncBattleFieldBuffs)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=xianlv_mines_net_logic,
        export_root=root,
        stage="31-xianlv-mines-response",
        kind="lua",
        section_marker="function _M.SM_SyncBattleFieldBuffsFun",
        markers=("XianLvMinesMgr.Inst_get().Model:SetBattleFieldBuffs(msg)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=xianlv_mines_model,
        export_root=root,
        stage="32-xianlv-mines-model",
        kind="lua",
        section_marker="function _M.SetBattleFieldBuffs",
        markers=(
            "self.Data:SetBattleFieldBuffs(msg and msg.activityBaseIdToBuff)",
            "self:RaiseEvent(XianLvMinesType.EventType.XianLvMinesUpdateBattleFieldBuffs)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=youlipool_net_logic,
        export_root=root,
        stage="40-youlipool-send",
        kind="lua",
        section_marker="function _M.CM_YouliPoolInfoFun",
        markers=("SocketManager.Inst_get():F_SendMsg(CM_YouliPoolInfo)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=youlipool_net_logic,
        export_root=root,
        stage="41-youlipool-response",
        kind="lua",
        section_marker="function _M.SM_YouliPoolInfoFun",
        markers=("YoulipoolMgr.Inst_get().Model:SetInfoMap(msg.infoMap)",),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=youlipool_model,
        export_root=root,
        stage="42-youlipool-model",
        kind="lua",
        section_marker="function _M.SetInfoMap",
        markers=("self.YoulipoolData:SetInfoMap(infoMap)", "self:RaiseEvent(YoulipoolType.EventType.InfoUpdate)"),
    )

    summaries = {name: _packet_summary_for_path(root, path) for name, path in packet_paths.items()}

    def packet_label(name: str) -> str:
        item = summaries.get(name) or {}
        packet_id = item.get("packet_id")
        return f"{name}({packet_id})" if packet_id not in {"", None} else name

    event_closed = (
        _probe_has(evidence_rows, "20-event-send", "SocketManager.Inst_get():F_SendMsg(CM_EventSyncAll)")
        and _probe_has(evidence_rows, "21-event-response", "EventMgr.Inst_get().Model.EventData:EventInfo(msg.events)")
        and _probe_has(evidence_rows, "22-event-model", "LocationDetectionMgr.Inst_get():StartCheckEvent()")
    )
    xianlv_mines_closed = (
        _probe_has(evidence_rows, "30-xianlv-mines-send", "SocketManager.Inst_get():F_SendMsg(CM_SyncBattleFieldBuffs)")
        and _probe_has(evidence_rows, "31-xianlv-mines-response", "XianLvMinesMgr.Inst_get().Model:SetBattleFieldBuffs(msg)")
        and _probe_has(
            evidence_rows,
            "32-xianlv-mines-model",
            "self:RaiseEvent(XianLvMinesType.EventType.XianLvMinesUpdateBattleFieldBuffs)",
        )
    )
    youlipool_closed = (
        _probe_has(evidence_rows, "40-youlipool-send", "SocketManager.Inst_get():F_SendMsg(CM_YouliPoolInfo)")
        and _probe_has(evidence_rows, "41-youlipool-response", "YoulipoolMgr.Inst_get().Model:SetInfoMap(msg.infoMap)")
        and _probe_has(evidence_rows, "42-youlipool-model", "self:RaiseEvent(YoulipoolType.EventType.InfoUpdate)")
    )

    def target_handler_row(
        *,
        entry: str,
        closed: bool,
        request_packet: str,
        response_packet: str,
        visible_handler: str,
        sink: str,
        event_or_signal: str,
        open_note: str,
        closed_note: str,
    ) -> dict[str, Any]:
        return {
            "entry": entry,
            "status": "closed_lua_handler" if closed else "packet_schema_only",
            "request_packet": packet_label(request_packet),
            "response_packet": packet_label(response_packet),
            "visible_handler": visible_handler if closed else "",
            "sink": sink if closed else "",
            "event_or_signal": event_or_signal if closed else "",
            "note": closed_note if closed else open_note,
        }

    handler_rows = [
        target_handler_row(
            entry="event_sync_all",
            closed=event_closed,
            request_packet="CM_EventSyncAll",
            response_packet="SM_EventSyncAll",
            visible_handler="EventNetLogic.SM_EventSyncAllFun",
            sink="EventData.EventInfo(msg.events)",
            event_or_signal="LocationDetectionMgr.StartCheckEvent",
            open_note="Post-login call and packet schema are visible; Event NetLogic handler source is not visible in this export.",
            closed_note="Raw lscript export exposes EventNetLogic and EventData; event sync is stored and can trigger location event checks.",
        ),
        target_handler_row(
            entry="xianlv_mines_battlefield_buffs",
            closed=xianlv_mines_closed,
            request_packet="CM_SyncBattleFieldBuffs",
            response_packet="SM_SyncBattleFieldBuffs",
            visible_handler="XianLvMinesNetLogic.SM_SyncBattleFieldBuffsFun",
            sink="XianLvMinesModel.SetBattleFieldBuffs(msg.activityBaseIdToBuff)",
            event_or_signal="XianLvMinesUpdateBattleFieldBuffs",
            open_note="Post-login call and packet schema are visible; XianLvMines NetLogic handler source is not visible in this export.",
            closed_note="Raw lscript export exposes XianLvMinesNetLogic and the model battlefield buff sink.",
        ),
        {
            "entry": "worldlevel_worship_info",
            "status": "closed_lua_handler",
            "request_packet": packet_label("CM_WorldLevelWorshipInfoSync"),
            "response_packet": packet_label("SM_WorldLevelWorshipInfoSync"),
            "visible_handler": "WorldlevelNetLogic.SM_WorldLevelWorshipInfoSyncFun",
            "sink": "WorldlevelData.SetWorshipInfo(msg)",
            "event_or_signal": "WORLD_LEVEL_WORSHIP_INFO_SYNC",
            "note": "Stores the full response msg as worshipInfo; UI reads firstRankVO, worshipTimes, rewardItems, and recState from that stored object.",
        },
        target_handler_row(
            entry="travel_youlipool_info",
            closed=youlipool_closed,
            request_packet="CM_YouliPoolInfo",
            response_packet="SM_YouliPoolInfo",
            visible_handler="YoulipoolNetLogic.SM_YouliPoolInfoFun",
            sink="YoulipoolModel.SetInfoMap(infoMap)",
            event_or_signal="YoulipoolType.EventType.InfoUpdate",
            open_note="Post-login conditional call and packet schema are visible; Youli pool NetLogic handler source is not visible in this export.",
            closed_note="Raw lscript export exposes YoulipoolNetLogic and the info-map model sink.",
        ),
        {
            "entry": "subpackage_sync",
            "status": "closed_lua_handler",
            "request_packet": packet_label("CM_SubpackageSync"),
            "response_packet": packet_label("SM_SubpackageSync"),
            "visible_handler": "SubpackageNetLogic.SM_SubpackageSyncFun",
            "sink": "SubpackageModel.V_SubPackageRecRewardList = msg.subpackageIds",
            "event_or_signal": "SUB_PACKAGE_LOADIND_UPDATE + RedDotID[120000]",
            "note": "StartInit turns into a real post-login sync for subpackage reward/download UI state.",
        },
    ]

    field_rows = _packet_field_rows(
        root,
        [
            packet_paths["SM_EventSyncAll"],
            packet_paths["SM_SyncBattleFieldBuffs"],
            packet_paths["SM_WorldLevelWorshipInfoSync"],
            packet_paths["SM_YouliPoolInfo"],
            packet_paths["SM_SubpackageSync"],
        ],
    )
    checks = {
        "worldlevel_handler_closed": _probe_has(evidence_rows, "03-worldlevel-response", "WorldlevelMgr.Inst_get().Model.WorldlevelData:SetWorshipInfo(msg)")
        and _probe_has(
            evidence_rows,
            "03-worldlevel-response",
            "WorldlevelMgr.Inst_get().Model:RaiseEvent(WorldlevelType.WORLD_LEVEL_WORSHIP_INFO_SYNC,msg)",
        ),
        "worldlevel_view_consumes_stored_msg": _probe_has(evidence_rows, "06-worldlevel-view-event", "self:UpdateView(msg)")
        and _probe_has(
            evidence_rows,
            "07-worldlevel-view-update",
            "self.worshipData=WorldlevelMgr.Inst_get().Model.WorldlevelData:GetWorshipInfo()",
        )
        and _probe_has(evidence_rows, "07-worldlevel-view-update", "local firstRankVO=self.worshipData.firstRankVO"),
        "subpackage_start_reaches_socket_sync": _probe_has(evidence_rows, "10-subpackage-start", "self.Model:CheckLoadingAndStart()")
        and _probe_has(evidence_rows, "11-subpackage-model-start", "SubpackageMgr.Inst_get():GetServerData()")
        and _probe_has(evidence_rows, "13-subpackage-send", "SocketManager.Inst_get():F_SendMsg(CM_SubpackageSync)"),
        "subpackage_response_updates_model": _probe_has(evidence_rows, "14-subpackage-response", "SubpackageMgr.Inst_get().Model:SM_SubpackageSyncFun(msg)")
        and _probe_has(evidence_rows, "15-subpackage-model-response", "self.V_SubPackageRecRewardList=msg.subpackageIds"),
        "target_handlers_classified": all(
            row["status"] in {"closed_lua_handler", "packet_schema_only"}
            for row in handler_rows
            if row["entry"] in {"event_sync_all", "xianlv_mines_battlefield_buffs", "travel_youlipool_info"}
        ),
        "closed_handler_rows_have_sinks": all(
            row["status"] != "closed_lua_handler" or (row["visible_handler"] and row["sink"]) for row in handler_rows
        ),
        "core_packet_ids_mapped": all(
            summaries.get(name, {}).get("packet_id") not in {"", None}
            for name in (
                "CM_WorldLevelWorshipInfoSync",
                "SM_WorldLevelWorshipInfoSync",
                "CM_SubpackageSync",
                "SM_SubpackageSync",
            )
        ),
    }

    _write_tsv(
        output_dir / "lua_login_post_sync_handlers_evidence.tsv",
        evidence_rows,
        ["stage", "kind", "source", "line", "marker", "snippet"],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_handlers.tsv",
        handler_rows,
        ["entry", "status", "request_packet", "response_packet", "visible_handler", "sink", "event_or_signal", "note"],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_handler_packet_fields.tsv",
        field_rows,
        ["packet_id", "packet_name", "direction", "field_index", "field_name", "read_method", "type_hint", "source", "line"],
    )
    _write_login_post_sync_handler_markdown(
        output_dir / "lua_login_post_sync_handlers_report.md",
        export_root=root,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        handler_rows=handler_rows,
        field_rows=field_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_post_sync_handlers_report.md"),
        "evidence_path": str(output_dir / "lua_login_post_sync_handlers_evidence.tsv"),
        "handlers_path": str(output_dir / "lua_login_post_sync_handlers.tsv"),
        "packet_fields_path": str(output_dir / "lua_login_post_sync_handler_packet_fields.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "evidence": len(evidence_rows),
            "handlers": len(handler_rows),
            "response_fields": len(field_rows),
            "closed_handlers": sum(1 for row in handler_rows if row.get("status") == "closed_lua_handler"),
            "schema_only_handlers": sum(1 for row in handler_rows if row.get("status") == "packet_schema_only"),
        },
        "packet_summaries": summaries,
        "sources": {
            "enter_game_info": str(enter_game_info) if enter_game_info else "",
            "worldlevel_net_logic": str(worldlevel_net_logic) if worldlevel_net_logic else "",
            "worldlevel_data": str(worldlevel_data) if worldlevel_data else "",
            "worldlevel_type": str(worldlevel_type) if worldlevel_type else "",
            "worldlevel_view": str(worldlevel_view) if worldlevel_view else "",
            "subpackage_mgr": str(subpackage_mgr) if subpackage_mgr else "",
            "subpackage_model": str(subpackage_model) if subpackage_model else "",
            "subpackage_net_logic": str(subpackage_net_logic) if subpackage_net_logic else "",
        },
    }
    (output_dir / "lua_login_post_sync_handlers_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _read_protocol_catalog_canonical(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FanxiuResourceError(f"Lua 协议 canonical 目录不存在：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _write_login_post_sync_protocol_families_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    summary_rows: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login post-sync protocol families report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report trims the post-login sync search space from the full canonical protocol catalog to the packet families touched by `LoginFinishGetServerData()`.",
        "- It does not infer hidden handlers. Rows with empty handler fields remain schema-only; rows with handler names are backed by the current rebuilt Lua packet index.",
        "- Use it as the compact index before opening generated packet/VO Lua files by hand.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Families",
            "",
            "| Family | Module | Rows | Client | Server | VO | Registered | Handlers | Request | Response | Note |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in summary_rows:
        lines.append(
            f"| `{row.get('family', '')}` | `{row.get('module', '')}` | {row.get('row_count', '')} | "
            f"{row.get('client_packets', '')} | {row.get('server_packets', '')} | {row.get('value_objects', '')} | "
            f"{row.get('registered_packets', '')} | {row.get('handler_count', '')} | "
            f"`{row.get('post_login_request', '')}` | `{row.get('post_login_response', '')}` | "
            f"{str(row.get('note', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Family Rows",
            "",
            "| Family | ID | Name | Direction | Module | Fields | Handler | Logic | Files |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in family_rows:
        lines.append(
            f"| `{row.get('family', '')}` | {row.get('id', '')} | `{row.get('name', '')}` | "
            f"{row.get('direction', '')} | `{row.get('module', '')}` | "
            f"{str(row.get('read_fields', '')).replace('|', '\\|')} | `{row.get('handler_names', '')}` | "
            f"`{row.get('logic_names', '')}` | `{str(row.get('sample_files', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Families TSV: `{_report_path(output_dir / 'lua_login_post_sync_protocol_families.tsv', export_root)}`",
            f"- Family rows TSV: `{_report_path(output_dir / 'lua_login_post_sync_protocol_family_rows.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_post_sync_protocol_families_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_post_sync_protocol_family_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Create a compact protocol-family index for the first post-login sync fanout."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog_path = root / "parsed_configs" / "lua_packet_index" / "protocol_catalog_canonical.tsv"
    catalog_rows = _read_protocol_catalog_canonical(catalog_path)

    family_specs = [
        {
            "family": "event_sync_all",
            "module": "player.event",
            "request": "CM_EventSyncAll",
            "response": "SM_EventSyncAll",
            "names": None,
            "note": "Event sync family; target response has list field `events`.",
        },
        {
            "family": "xianlv_mines_battlefield_buffs",
            "module": "player.partner",
            "request": "CM_SyncBattleFieldBuffs",
            "response": "SM_SyncBattleFieldBuffs",
            "names": {"CM_SyncBattleFieldBuffs", "SM_SyncBattleFieldBuffs", "BattleFieldBuffVO"},
            "note": "Narrow partner subset for battlefield buffs.",
        },
        {
            "family": "worldlevel_worship_info",
            "module": "world.worldlevel",
            "request": "CM_WorldLevelWorshipInfoSync",
            "response": "SM_WorldLevelWorshipInfoSync",
            "names": None,
            "note": "Visible Lua handler family; included as a closed reference next to schema-only branches.",
        },
        {
            "family": "travel_youlipool_info",
            "module": "player.inner.youlipool",
            "request": "CM_YouliPoolInfo",
            "response": "SM_YouliPoolInfo",
            "names": None,
            "note": "Youli pool family; packet/VO schemas are visible in this compact slice.",
        },
        {
            "family": "subpackage_sync",
            "module": "player.subpackage",
            "request": "CM_SubpackageSync",
            "response": "SM_SubpackageSync",
            "names": None,
            "note": "Visible Lua handler family reached through SubpackageMgr.StartInit.",
        },
    ]

    family_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for spec in family_specs:
        names = spec["names"]
        rows = [
            row
            for row in catalog_rows
            if row.get("module") == spec["module"] and (names is None or row.get("name") in names)
        ]
        rows.sort(key=lambda row: (int(row.get("id") or 0) if str(row.get("id") or "").isdigit() else 0, str(row.get("name") or "")))
        for row in rows:
            family_rows.append(
                {
                    "family": spec["family"],
                    "id": row.get("id", ""),
                    "name": row.get("name", ""),
                    "direction": row.get("direction", ""),
                    "module": row.get("module", ""),
                    "field_count": row.get("field_count", ""),
                    "read_fields": row.get("read_fields", ""),
                    "handler_names": row.get("handler_names", ""),
                    "logic_names": row.get("logic_names", ""),
                    "source_file_count": row.get("source_file_count", ""),
                    "sample_files": row.get("sample_files", ""),
                }
            )
        response_row = next((row for row in rows if row.get("name") == spec["response"]), {})
        note = str(spec["note"])
        if response_row.get("handler_names"):
            note = f"{note} Target response handler visible: {response_row.get('handler_names')}."
        else:
            note = f"{note} Target response remains schema-only in this index."
        summary_rows.append(
            {
                "family": spec["family"],
                "module": spec["module"],
                "row_count": len(rows),
                "client_packets": sum(1 for row in rows if row.get("direction") == "client_to_server"),
                "server_packets": sum(1 for row in rows if row.get("direction") == "server_to_client"),
                "value_objects": sum(1 for row in rows if row.get("direction") == "value_object" or str(row.get("name", "")).endswith("VO")),
                "registered_packets": sum(1 for row in rows if str(row.get("registration_count") or "0").isdigit() and int(row.get("registration_count") or 0) > 0),
                "handler_count": sum(1 for row in rows if row.get("handler_names")),
                "post_login_request": spec["request"],
                "post_login_response": spec["response"],
                "response_handler": response_row.get("handler_names", ""),
                "response_fields": response_row.get("read_fields", ""),
                "note": note,
            }
        )

    families_by_name = {row["family"]: row for row in summary_rows}
    checks = {
        "all_families_present": all(int(row.get("row_count") or 0) > 0 for row in summary_rows),
        "closed_reference_handlers_present": families_by_name["worldlevel_worship_info"].get("response_handler") == "SM_WorldLevelWorshipInfoSyncFun"
        and families_by_name["subpackage_sync"].get("response_handler") == "SM_SubpackageSyncFun",
        "target_response_handlers_classified": all(
            name in families_by_name and "response_handler" in families_by_name[name]
            for name in ("event_sync_all", "xianlv_mines_battlefield_buffs", "travel_youlipool_info")
        ),
        "target_rows_present": all(
            any(row.get("name") == spec["request"] for row in family_rows)
            and any(row.get("name") == spec["response"] for row in family_rows)
            for spec in family_specs
        ),
    }

    _write_tsv(
        output_dir / "lua_login_post_sync_protocol_families.tsv",
        summary_rows,
        [
            "family",
            "module",
            "row_count",
            "client_packets",
            "server_packets",
            "value_objects",
            "registered_packets",
            "handler_count",
            "post_login_request",
            "post_login_response",
            "response_handler",
            "response_fields",
            "note",
        ],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_protocol_family_rows.tsv",
        family_rows,
        [
            "family",
            "id",
            "name",
            "direction",
            "module",
            "field_count",
            "read_fields",
            "handler_names",
            "logic_names",
            "source_file_count",
            "sample_files",
        ],
    )
    _write_login_post_sync_protocol_families_markdown(
        output_dir / "lua_login_post_sync_protocol_families_report.md",
        export_root=root,
        output_dir=output_dir,
        summary_rows=summary_rows,
        family_rows=family_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_post_sync_protocol_families_report.md"),
        "families_path": str(output_dir / "lua_login_post_sync_protocol_families.tsv"),
        "family_rows_path": str(output_dir / "lua_login_post_sync_protocol_family_rows.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "families": len(summary_rows),
            "family_rows": len(family_rows),
            "schema_only_families": sum(1 for row in summary_rows if not row.get("response_handler")),
            "closed_handler_families": sum(1 for row in summary_rows if row.get("response_handler")),
        },
        "catalog_path": str(catalog_path),
    }
    (output_dir / "lua_login_post_sync_protocol_families_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _scan_lua_marker_hits(
    export_root: Path,
    *,
    markers: tuple[str, ...],
    max_rows_per_marker: int = 40,
) -> list[dict[str, Any]]:
    lua_root = export_root / "by_source" / "lscripts"
    if not lua_root.is_dir():
        return []

    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for path in sorted(lua_root.rglob("*.lua"), key=lambda item: item.as_posix().lower()):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            for marker in markers:
                if counts[marker] >= max_rows_per_marker or marker not in line:
                    continue
                counts[marker] += 1
                if path.name.startswith(("CM_", "SM_")):
                    hit_kind = "packet_class"
                elif "Initializer" in path.name:
                    hit_kind = "initializer"
                elif "EnterGameInfo" in path.name:
                    hit_kind = "post_login_fanout"
                elif "Mgr" in path.name or "NetLogic" in path.name or "Model" in path.name:
                    hit_kind = "logic_surface"
                else:
                    hit_kind = "reference_surface"
                rows.append(
                    {
                        "marker": marker,
                        "kind": hit_kind,
                        "source": _report_path(path, export_root),
                        "line": line_no,
                        "snippet": stripped,
                    }
                )
    return rows


def _catalog_row_by_name(catalog_rows: list[dict[str, Any]], packet_name: str) -> dict[str, Any]:
    return next((row for row in catalog_rows if row.get("name") == packet_name), {})


def _write_login_post_sync_unresolved_handler_gap_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    gap_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login post-sync unresolved handler gaps report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report isolates the post-login sync branches that were historically not closed to visible Lua handlers.",
        "- It records the exact fanout calls, visible packet schemas, protocol-catalog handler state, and nearby candidate surfaces.",
        "- A row can be `resolved_visible_handler` after raw lscript export, or `unresolved_static_gap` when the handler is still absent from the current surface.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Gap Rows",
            "",
            "| Branch | Fanout Call | Request | Response | Response Fields | Catalog Handler | Exact Send Defs | Exact Response Handler Defs | Candidate Hits | Status | Next Surface |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in gap_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('fanout_call', '')}` | `{row.get('request_packet', '')}` | "
            f"`{row.get('response_packet', '')}` | `{str(row.get('response_fields', '')).replace('|', '\\|')}` | "
            f"`{row.get('catalog_response_handler', '')}` | {row.get('exact_send_definition_hits', '')} | "
            f"{row.get('exact_response_handler_definition_hits', '')} | {row.get('candidate_hits', '')} | "
            f"`{row.get('status', '')}` | "
            f"{str(row.get('next_surface', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Response Packet Fields",
            "",
            "| Branch | Packet | ID | # | Field | Wire Method | Type Hint | Source |",
            "| --- | --- | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('packet_name', '')}` | {row.get('packet_id', '')} | "
            f"{row.get('field_index', '')} | `{row.get('field_name', '')}` | `{row.get('read_method', '')}` | "
            f"`{row.get('type_hint', '')}` | `{str(row.get('source', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Candidate Surfaces",
            "",
            "| Branch | Marker | Kind | Source | Line | Snippet |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in candidate_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{str(row.get('marker', '')).replace('|', '\\|')}` | "
            f"{row.get('kind', '')} | `{str(row.get('source', '')).replace('|', '\\|')}` | "
            f"{row.get('line', '')} | `{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(["", "## Key Evidence", "", "| Stage | Kind | Source | Line | Marker | Snippet |", "| --- | --- | --- | ---: | --- | --- |"])
    for row in evidence_rows:
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{str(row.get('source', '')).replace('|', '\\|')}` | "
            f"{row.get('line', '')} | `{str(row.get('marker', '')).replace('|', '\\|')}` | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Gap TSV: `{_report_path(output_dir / 'lua_login_post_sync_unresolved_handler_gaps.tsv', export_root)}`",
            f"- Evidence TSV: `{_report_path(output_dir / 'lua_login_post_sync_unresolved_handler_gap_evidence.tsv', export_root)}`",
            f"- Candidate TSV: `{_report_path(output_dir / 'lua_login_post_sync_unresolved_handler_gap_candidates.tsv', export_root)}`",
            f"- Packet fields TSV: `{_report_path(output_dir / 'lua_login_post_sync_unresolved_handler_gap_packet_fields.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_post_sync_unresolved_handler_gaps_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_post_sync_unresolved_handler_gap_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Record the static evidence gap for post-login sync branches with no visible Lua handler."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    enter_game_info = _find_lua_text_asset(root, "EnterGameInfo.lua", ("LoginFinishGetServerData", "CM_EventSyncAllFun", "CM_YouliPoolInfoFun"))
    lua_initializer = _find_lua_text_asset(root, "LuaInitializer.lua", ("YoulipoolMgr", "XianLvMinesMgr"))
    catalog_path = root / "parsed_configs" / "lua_packet_index" / "protocol_catalog_canonical.tsv"
    catalog_rows = _read_protocol_catalog_canonical(catalog_path) if catalog_path.is_file() else []

    specs = [
        {
            "branch": "event_sync_all",
            "fanout_call": "EventMgr.Inst_get().NetLogic:CM_EventSyncAllFun()",
            "request": "CM_EventSyncAll",
            "response": "SM_EventSyncAll",
            "send_function": "CM_EventSyncAllFun",
            "response_handler": "SM_EventSyncAllFun",
            "markers": ("CM_EventSyncAllFun", "SM_EventSyncAllFun", "CM_EventSyncAll", "SM_EventSyncAll"),
            "next_surface": "Find the missing EventMgr/EventNetLogic export or a native/initializer registration surface for SM_EventSyncAll.",
        },
        {
            "branch": "xianlv_mines_battlefield_buffs",
            "fanout_call": "XianLvMinesMgr.Inst_get().NetLogic:CM_SyncBattleFieldBuffsFun()",
            "request": "CM_SyncBattleFieldBuffs",
            "response": "SM_SyncBattleFieldBuffs",
            "send_function": "CM_SyncBattleFieldBuffsFun",
            "response_handler": "SM_SyncBattleFieldBuffsFun",
            "markers": (
                "XianLvMinesMgr",
                "CM_SyncBattleFieldBuffsFun",
                "SM_SyncBattleFieldBuffsFun",
                "CM_SyncBattleFieldBuffs",
                "SM_SyncBattleFieldBuffs",
            ),
            "next_surface": "Trace XianLvMines manager/module exports and partner battlefield-buff UI references before assuming a model sink.",
        },
        {
            "branch": "travel_youlipool_info",
            "fanout_call": "YoulipoolMgr.Inst_get().NetLogic:CM_YouliPoolInfoFun()",
            "request": "CM_YouliPoolInfo",
            "response": "SM_YouliPoolInfo",
            "send_function": "CM_YouliPoolInfoFun",
            "response_handler": "SM_YouliPoolInfoFun",
            "markers": ("YoulipoolMgr", "CM_YouliPoolInfoFun", "SM_YouliPoolInfoFun", "CM_YouliPoolInfo", "SM_YouliPoolInfo"),
            "next_surface": "Trace YoulipoolMgr initializer and travel draw-data consumers; current packet schema alone does not prove the sink.",
        },
    ]

    evidence_rows: list[dict[str, Any]] = []
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="00-post-login-fanout",
        kind="lua",
        section_marker="function _M.LoginFinishGetServerData()",
        markers=tuple(spec["fanout_call"] for spec in specs),
    )
    _append_probe_evidence(
        evidence_rows,
        path=lua_initializer,
        export_root=root,
        stage="01-initializer-manager-surface",
        kind="lua",
        markers=(
            "YoulipoolMgr",
            "XianLvMinesMgr",
            "YoulipoolMgr.Inst_get().NetLogic:CM_YouliPoolInfoFun()",
        ),
    )

    all_candidate_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    gap_rows: list[dict[str, Any]] = []
    packet_paths_by_name: dict[str, Path | None] = {}
    for spec in specs:
        request_path = _find_lua_text_asset(root, f"{spec['request']}.lua", ("return", spec["request"]))
        response_path = _find_lua_text_asset(root, f"{spec['response']}.lua", ("return", spec["response"]))
        packet_paths_by_name[spec["request"]] = request_path
        packet_paths_by_name[spec["response"]] = response_path
        response_summary = _packet_summary_for_path(root, response_path)
        catalog_response_row = _catalog_row_by_name(catalog_rows, spec["response"])
        branch_candidates = _scan_lua_marker_hits(root, markers=spec["markers"], max_rows_per_marker=30)
        for row in branch_candidates:
            all_candidate_rows.append({"branch": spec["branch"], **row})
        exact_send_defs = [
            row
            for row in branch_candidates
            if row.get("marker") == spec["send_function"] and "function" in str(row.get("snippet", ""))
        ]
        exact_response_handler_defs = [
            row
            for row in branch_candidates
            if row.get("marker") == spec["response_handler"] and "function" in str(row.get("snippet", ""))
        ]
        status = (
            "resolved_visible_handler"
            if catalog_response_row.get("handler_names") or exact_response_handler_defs
            else "unresolved_static_gap"
        )
        for row in _packet_field_rows(root, [response_path]):
            field_rows.append({"branch": spec["branch"], **row})
        gap_rows.append(
            {
                "branch": spec["branch"],
                "fanout_call": spec["fanout_call"],
                "request_packet": spec["request"],
                "response_packet": f"{spec['response']}({response_summary.get('packet_id', '')})",
                "response_fields": response_summary.get("fields", ""),
                "catalog_response_handler": catalog_response_row.get("handler_names", ""),
                "exact_send_definition_hits": len(exact_send_defs),
                "exact_response_handler_definition_hits": len(exact_response_handler_defs),
                "candidate_hits": len(branch_candidates),
                "status": status,
                "next_surface": spec["next_surface"],
            }
        )

    checks = {
        "fanout_calls_visible": all(
            _probe_has(evidence_rows, "00-post-login-fanout", spec["fanout_call"]) for spec in specs
        ),
        "target_packet_schemas_visible": all(
            packet_paths_by_name.get(spec["request"]) and packet_paths_by_name.get(spec["response"]) for spec in specs
        ),
        "catalog_rows_loaded": bool(catalog_rows),
        "candidate_surfaces_recorded": all(row.get("candidate_hits", 0) > 0 for row in gap_rows),
        "branches_classified": all(
            row.get("status") in {"resolved_visible_handler", "unresolved_static_gap"} for row in gap_rows
        ),
    }

    _write_tsv(
        output_dir / "lua_login_post_sync_unresolved_handler_gaps.tsv",
        gap_rows,
        [
            "branch",
            "fanout_call",
            "request_packet",
            "response_packet",
            "response_fields",
            "catalog_response_handler",
            "exact_send_definition_hits",
            "exact_response_handler_definition_hits",
            "candidate_hits",
            "status",
            "next_surface",
        ],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_unresolved_handler_gap_evidence.tsv",
        evidence_rows,
        ["stage", "kind", "source", "line", "marker", "snippet"],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_unresolved_handler_gap_candidates.tsv",
        all_candidate_rows,
        ["branch", "marker", "kind", "source", "line", "snippet"],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_unresolved_handler_gap_packet_fields.tsv",
        field_rows,
        ["branch", "packet_id", "packet_name", "direction", "field_index", "field_name", "read_method", "type_hint", "source", "line"],
    )
    _write_login_post_sync_unresolved_handler_gap_markdown(
        output_dir / "lua_login_post_sync_unresolved_handler_gaps_report.md",
        export_root=root,
        output_dir=output_dir,
        gap_rows=gap_rows,
        evidence_rows=evidence_rows,
        candidate_rows=all_candidate_rows,
        field_rows=field_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_post_sync_unresolved_handler_gaps_report.md"),
        "gaps_path": str(output_dir / "lua_login_post_sync_unresolved_handler_gaps.tsv"),
        "evidence_path": str(output_dir / "lua_login_post_sync_unresolved_handler_gap_evidence.tsv"),
        "candidates_path": str(output_dir / "lua_login_post_sync_unresolved_handler_gap_candidates.tsv"),
        "packet_fields_path": str(output_dir / "lua_login_post_sync_unresolved_handler_gap_packet_fields.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "gaps": len(gap_rows),
            "resolved_branches": sum(1 for row in gap_rows if row.get("status") == "resolved_visible_handler"),
            "unresolved_branches": sum(1 for row in gap_rows if row.get("status") == "unresolved_static_gap"),
            "evidence": len(evidence_rows),
            "candidate_hits": len(all_candidate_rows),
            "response_fields": len(field_rows),
        },
        "catalog_path": str(catalog_path) if catalog_path.is_file() else "",
    }
    (output_dir / "lua_login_post_sync_unresolved_handler_gaps_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_login_post_sync_manager_source_gap_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    manager_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    near_file_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login post-sync manager source gaps report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report checks whether the manager classes behind historically unresolved post-login sync calls are present as Lua text assets.",
        "- It distinguishes visible `require/AddSingleton/fanout` references from actual exported manager class source files.",
        "- Rows can be resolved after raw lscript export; absent rows still point to extractor coverage, generated binding, native export, or runtime observation.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Manager Requirements",
            "",
            "| Branch | Alias | Require Package | Fanout Call | Require Hits | Singleton Hits | Fanout Hits | Exact File Hits | Package Class Hits | Verdict |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in manager_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('manager_alias', '')}` | `{row.get('require_package', '')}` | "
            f"`{row.get('fanout_call', '')}` | {row.get('require_hits', '')} | {row.get('singleton_hits', '')} | "
            f"{row.get('fanout_hits', '')} | {row.get('exact_file_hits', '')} | "
            f"{row.get('package_loaded_hits', '')} | {str(row.get('verdict', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Near-Name Files",
            "",
            "| Branch | Alias | Source | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in near_file_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('manager_alias', '')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` | {str(row.get('note', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| Branch | Marker | Kind | Source | Line | Snippet |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in evidence_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{str(row.get('marker', '')).replace('|', '\\|')}` | "
            f"{row.get('kind', '')} | `{str(row.get('source', '')).replace('|', '\\|')}` | "
            f"{row.get('line', '')} | `{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Managers TSV: `{_report_path(output_dir / 'lua_login_post_sync_manager_source_gaps.tsv', export_root)}`",
            f"- Evidence TSV: `{_report_path(output_dir / 'lua_login_post_sync_manager_source_gap_evidence.tsv', export_root)}`",
            f"- Near-name files TSV: `{_report_path(output_dir / 'lua_login_post_sync_manager_source_gap_near_files.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_post_sync_manager_source_gaps_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_post_sync_manager_source_gap_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Check whether unresolved post-login manager classes exist in the exported Lua text surface."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    lua_root = root / "by_source" / "lscripts"
    specs = [
        {
            "branch": "event_sync_all",
            "manager_alias": "EventMgr",
            "require_package": "GameSystem.Game.Event.Mgr.EventMgr",
            "expected_filename": "EventMgr.lua",
            "fanout_call": "EventMgr.Inst_get().NetLogic:CM_EventSyncAllFun()",
            "near_name_note": "Near-name files such as LuaEventMgr or TravelEventMgr are not the required GameSystem.Game.Event.Mgr.EventMgr class.",
        },
        {
            "branch": "xianlv_mines_battlefield_buffs",
            "manager_alias": "XianLvMinesMgr",
            "require_package": "GameSystem.Game.XianLvMines.Mgr.XianLvMinesMgr",
            "expected_filename": "XianLvMinesMgr.lua",
            "fanout_call": "XianLvMinesMgr.Inst_get().NetLogic:CM_SyncBattleFieldBuffsFun()",
            "near_name_note": "No exact manager source file is exported; visible rows are consumers of the singleton.",
        },
        {
            "branch": "travel_youlipool_info",
            "manager_alias": "YoulipoolMgr",
            "require_package": "GameSystem.Game.Youlipool.Mgr.YoulipoolMgr",
            "expected_filename": "YoulipoolMgr.lua",
            "fanout_call": "YoulipoolMgr.Inst_get().NetLogic:CM_YouliPoolInfoFun()",
            "near_name_note": "No exact manager source file is exported; visible rows are consumers of the singleton.",
        },
    ]

    manager_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    near_file_rows: list[dict[str, Any]] = []
    for spec in specs:
        package = spec["require_package"]
        alias = spec["manager_alias"]
        require_markers = (
            package,
            f"LuaEngineBridge:AddSingleton({alias}.Inst_get())",
            spec["fanout_call"],
        )
        branch_evidence = _scan_lua_marker_hits(root, markers=require_markers, max_rows_per_marker=20)
        package_loaded_rows = _scan_lua_marker_hits(
            root,
            markers=(f'package.loaded["{package}"]', f"package.loaded['{package}']"),
            max_rows_per_marker=20,
        )
        exact_files = sorted(lua_root.rglob(spec["expected_filename"]), key=lambda item: item.as_posix().lower()) if lua_root.is_dir() else []
        near_files = (
            sorted(lua_root.rglob(f"*{alias}.lua"), key=lambda item: item.as_posix().lower())
            if lua_root.is_dir()
            else []
        )
        for row in branch_evidence:
            evidence_rows.append({"branch": spec["branch"], **row})
        for row in package_loaded_rows:
            evidence_rows.append({"branch": spec["branch"], **row})
        for near_path in near_files[:10]:
            near_file_rows.append(
                {
                    "branch": spec["branch"],
                    "manager_alias": alias,
                    "source": _report_path(near_path, root),
                    "note": "exact expected file" if near_path.name == spec["expected_filename"] else spec["near_name_note"],
                }
            )
        require_hits = sum(1 for row in branch_evidence if row.get("marker") == package)
        singleton_hits = sum(1 for row in branch_evidence if row.get("marker") == f"LuaEngineBridge:AddSingleton({alias}.Inst_get())")
        fanout_hits = sum(1 for row in branch_evidence if row.get("marker") == spec["fanout_call"])
        package_loaded_hits = len(package_loaded_rows)
        exact_file_hits = len([path for path in exact_files if path.name == spec["expected_filename"]])
        if exact_file_hits > 0 and package_loaded_hits > 0:
            verdict = "manager source resolved in exported Lua text assets"
        elif require_hits and singleton_hits and fanout_hits and exact_file_hits == 0 and package_loaded_hits == 0:
            verdict = "required singleton is visible, but its Lua class source/package is absent from this export"
        else:
            verdict = "needs review"
        manager_rows.append(
            {
                "branch": spec["branch"],
                "manager_alias": alias,
                "require_package": package,
                "expected_filename": spec["expected_filename"],
                "fanout_call": spec["fanout_call"],
                "require_hits": require_hits,
                "singleton_hits": singleton_hits,
                "fanout_hits": fanout_hits,
                "exact_file_hits": exact_file_hits,
                "package_loaded_hits": package_loaded_hits,
                "near_name_file_hits": len(near_files),
                "verdict": verdict,
            }
        )

    checks = {
        "manager_requires_visible": all(int(row.get("require_hits") or 0) > 0 for row in manager_rows),
        "singleton_registrations_visible": all(int(row.get("singleton_hits") or 0) > 0 for row in manager_rows),
        "fanout_calls_visible": all(int(row.get("fanout_hits") or 0) > 0 for row in manager_rows),
        "manager_source_state_classified": all(
            row.get("verdict")
            in {
                "manager source resolved in exported Lua text assets",
                "required singleton is visible, but its Lua class source/package is absent from this export",
            }
            for row in manager_rows
        ),
    }

    _write_tsv(
        output_dir / "lua_login_post_sync_manager_source_gaps.tsv",
        manager_rows,
        [
            "branch",
            "manager_alias",
            "require_package",
            "expected_filename",
            "fanout_call",
            "require_hits",
            "singleton_hits",
            "fanout_hits",
            "exact_file_hits",
            "package_loaded_hits",
            "near_name_file_hits",
            "verdict",
        ],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_manager_source_gap_evidence.tsv",
        evidence_rows,
        ["branch", "marker", "kind", "source", "line", "snippet"],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_manager_source_gap_near_files.tsv",
        near_file_rows,
        ["branch", "manager_alias", "source", "note"],
    )
    _write_login_post_sync_manager_source_gap_markdown(
        output_dir / "lua_login_post_sync_manager_source_gaps_report.md",
        export_root=root,
        output_dir=output_dir,
        manager_rows=manager_rows,
        evidence_rows=evidence_rows,
        near_file_rows=near_file_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_post_sync_manager_source_gaps_report.md"),
        "managers_path": str(output_dir / "lua_login_post_sync_manager_source_gaps.tsv"),
        "evidence_path": str(output_dir / "lua_login_post_sync_manager_source_gap_evidence.tsv"),
        "near_files_path": str(output_dir / "lua_login_post_sync_manager_source_gap_near_files.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "managers": len(manager_rows),
            "resolved_managers": sum(
                1 for row in manager_rows if row.get("verdict") == "manager source resolved in exported Lua text assets"
            ),
            "missing_managers": sum(
                1
                for row in manager_rows
                if row.get("verdict") == "required singleton is visible, but its Lua class source/package is absent from this export"
            ),
            "evidence": len(evidence_rows),
            "near_name_files": len(near_file_rows),
        },
    }
    (output_dir / "lua_login_post_sync_manager_source_gaps_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _scan_text_roots_marker_hits(
    export_root: Path,
    *,
    scan_roots: list[Path],
    markers: tuple[str, ...],
    max_rows_per_marker: int = 60,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for scan_root in scan_roots:
        if not scan_root.is_dir():
            continue
        for path in sorted(scan_root.rglob("*"), key=lambda item: item.as_posix().lower()):
            if not path.is_file() or path.suffix.lower() not in {".cs", ".txt", ".json", ".tsv"}:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                stripped = line.strip()
                for marker in markers:
                    if counts[marker] >= max_rows_per_marker or marker not in line:
                        continue
                    counts[marker] += 1
                    rows.append(
                        {
                            "marker": marker,
                            "scan_root": _report_path(scan_root, export_root),
                            "source": _report_path(path, export_root),
                            "line": line_no,
                            "snippet": stripped,
                        }
                    )
    return rows


def _write_login_post_sync_cpp2il_manager_surface_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    surface_rows: list[dict[str, Any]],
    hit_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login post-sync Cpp2IL manager surface report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report checks whether Cpp2IL diffable C# or ISIL exposes the missing post-login Lua managers or their target handler names.",
        "- Generic C# event infrastructure is separated from the required `GameSystem.Game.*.Mgr.*` Lua manager packages.",
        "- Current evidence should be read as a surface inventory, not as runtime behavior proof.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Surface Summary",
            "",
            "| Branch | Manager Alias | Required Package | Alias Hits | Package Hits | Send Function Hits | Response Handler Hits | Verdict |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in surface_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('manager_alias', '')}` | `{row.get('require_package', '')}` | "
            f"{row.get('manager_alias_hits', '')} | {row.get('require_package_hits', '')} | "
            f"{row.get('send_function_hits', '')} | {row.get('response_handler_hits', '')} | "
            f"{str(row.get('verdict', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Hits",
            "",
            "| Branch | Marker | Scan Root | Source | Line | Snippet |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in hit_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{str(row.get('marker', '')).replace('|', '\\|')}` | "
            f"`{str(row.get('scan_root', '')).replace('|', '\\|')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` | {row.get('line', '')} | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Summary TSV: `{_report_path(output_dir / 'lua_login_post_sync_cpp2il_manager_surface.tsv', export_root)}`",
            f"- Hits TSV: `{_report_path(output_dir / 'lua_login_post_sync_cpp2il_manager_surface_hits.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_post_sync_cpp2il_manager_surface_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_post_sync_cpp2il_manager_surface_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Inventory Cpp2IL surfaces for post-login Lua manager gap names."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    diffable_root = output_dir / "cpp2il_2022_1_pre21_arm64_diffable_cs"
    isil_root = output_dir / "cpp2il_2022_1_pre21_arm64_isil"
    specs = [
        {
            "branch": "event_sync_all",
            "manager_alias": "EventMgr",
            "require_package": "GameSystem.Game.Event.Mgr.EventMgr",
            "send_function": "CM_EventSyncAllFun",
            "response_handler": "SM_EventSyncAllFun",
            "generic_note": "Only generic C# EventMgr/CsCallLuaMgr event bridge hits are expected here.",
        },
        {
            "branch": "xianlv_mines_battlefield_buffs",
            "manager_alias": "XianLvMinesMgr",
            "require_package": "GameSystem.Game.XianLvMines.Mgr.XianLvMinesMgr",
            "send_function": "CM_SyncBattleFieldBuffsFun",
            "response_handler": "SM_SyncBattleFieldBuffsFun",
            "generic_note": "No Cpp2IL surface should be treated as the missing Lua manager unless the exact package or handler name appears.",
        },
        {
            "branch": "travel_youlipool_info",
            "manager_alias": "YoulipoolMgr",
            "require_package": "GameSystem.Game.Youlipool.Mgr.YoulipoolMgr",
            "send_function": "CM_YouliPoolInfoFun",
            "response_handler": "SM_YouliPoolInfoFun",
            "generic_note": "No Cpp2IL surface should be treated as the missing Lua manager unless the exact package or handler name appears.",
        },
    ]

    surface_rows: list[dict[str, Any]] = []
    hit_rows: list[dict[str, Any]] = []
    scan_roots = [diffable_root, isil_root]
    for spec in specs:
        markers = (
            spec["manager_alias"],
            spec["require_package"],
            spec["send_function"],
            spec["response_handler"],
        )
        branch_hits = _scan_text_roots_marker_hits(root, scan_roots=scan_roots, markers=markers, max_rows_per_marker=80)
        for row in branch_hits:
            hit_rows.append({"branch": spec["branch"], **row})
        alias_hits = sum(1 for row in branch_hits if row.get("marker") == spec["manager_alias"])
        package_hits = sum(1 for row in branch_hits if row.get("marker") == spec["require_package"])
        send_hits = sum(1 for row in branch_hits if row.get("marker") == spec["send_function"])
        response_hits = sum(1 for row in branch_hits if row.get("marker") == spec["response_handler"])
        if package_hits == 0 and send_hits == 0 and response_hits == 0:
            verdict = spec["generic_note"] if alias_hits else "No Cpp2IL hits for the required package or handler names."
        else:
            verdict = "needs review"
        surface_rows.append(
            {
                "branch": spec["branch"],
                "manager_alias": spec["manager_alias"],
                "require_package": spec["require_package"],
                "send_function": spec["send_function"],
                "response_handler": spec["response_handler"],
                "manager_alias_hits": alias_hits,
                "require_package_hits": package_hits,
                "send_function_hits": send_hits,
                "response_handler_hits": response_hits,
                "verdict": verdict,
            }
        )

    checks = {
        "cpp2il_roots_visible": diffable_root.is_dir() and isil_root.is_dir(),
        "required_game_manager_packages_absent": all(int(row.get("require_package_hits") or 0) == 0 for row in surface_rows),
        "target_send_function_names_absent": all(int(row.get("send_function_hits") or 0) == 0 for row in surface_rows),
        "target_response_handler_names_absent": all(int(row.get("response_handler_hits") or 0) == 0 for row in surface_rows),
        "generic_event_manager_boundary_visible": any(
            row.get("branch") == "event_sync_all" and int(row.get("manager_alias_hits") or 0) > 0 for row in surface_rows
        ),
    }

    _write_tsv(
        output_dir / "lua_login_post_sync_cpp2il_manager_surface.tsv",
        surface_rows,
        [
            "branch",
            "manager_alias",
            "require_package",
            "send_function",
            "response_handler",
            "manager_alias_hits",
            "require_package_hits",
            "send_function_hits",
            "response_handler_hits",
            "verdict",
        ],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_cpp2il_manager_surface_hits.tsv",
        hit_rows,
        ["branch", "marker", "scan_root", "source", "line", "snippet"],
    )
    _write_login_post_sync_cpp2il_manager_surface_markdown(
        output_dir / "lua_login_post_sync_cpp2il_manager_surface_report.md",
        export_root=root,
        output_dir=output_dir,
        surface_rows=surface_rows,
        hit_rows=hit_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_post_sync_cpp2il_manager_surface_report.md"),
        "summary_path": str(output_dir / "lua_login_post_sync_cpp2il_manager_surface.tsv"),
        "hits_path": str(output_dir / "lua_login_post_sync_cpp2il_manager_surface_hits.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "branches": len(surface_rows),
            "hits": len(hit_rows),
            "generic_eventmgr_hits": sum(
                int(row.get("manager_alias_hits") or 0) for row in surface_rows if row.get("branch") == "event_sync_all"
            ),
        },
    }
    (output_dir / "lua_login_post_sync_cpp2il_manager_surface_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _read_lscripts_text_asset_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _write_login_post_sync_lua_loader_boundary_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    asset_rows: list[dict[str, Any]],
    loader_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login post-sync Lua loader boundary report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report connects the missing manager-source gap to the available Lua loader and text-asset inventory surfaces.",
        "- The loader bridge is visible in Cpp2IL, but the current `hot_update_lscripts_text_assets.tsv` index has no exact manager assets for the unresolved post-login branches.",
        "- This means the next search should focus on hidden/generated Lua or runtime loader behavior, not on the already exported text assets.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Asset Inventory",
            "",
            "| Branch | Expected Asset | Exact Asset Hits | Require-Package Hits | Near-Name Assets | Note |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in asset_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('expected_asset', '')}` | {row.get('exact_asset_hits', '')} | "
            f"{row.get('require_package_hits', '')} | {row.get('near_name_asset_hits', '')} | "
            f"{str(row.get('note', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Loader Evidence",
            "",
            "| Marker | Scan Root | Source | Line | Snippet |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in loader_rows:
        lines.append(
            f"| `{str(row.get('marker', '')).replace('|', '\\|')}` | `{str(row.get('scan_root', '')).replace('|', '\\|')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` | {row.get('line', '')} | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Assets TSV: `{_report_path(output_dir / 'lua_login_post_sync_lua_loader_boundary_assets.tsv', export_root)}`",
            f"- Loader hits TSV: `{_report_path(output_dir / 'lua_login_post_sync_lua_loader_boundary_hits.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_post_sync_lua_loader_boundary_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_post_sync_lua_loader_boundary_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Tie missing post-login manager sources to the current Lua loader and text asset inventory."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    text_asset_index = output_dir / "hot_update_lscripts_text_assets.tsv"
    rows = _read_lscripts_text_asset_rows(text_asset_index)
    specs = [
        {
            "branch": "event_sync_all",
            "alias": "EventMgr",
            "require_package": "GameSystem.Game.Event.Mgr.EventMgr",
            "expected_asset": "EventMgr.lua",
            "near_note": "Near-name LuaEventMgr/TravelEventMgr assets are not the required EventMgr class.",
        },
        {
            "branch": "xianlv_mines_battlefield_buffs",
            "alias": "XianLvMinesMgr",
            "require_package": "GameSystem.Game.XianLvMines.Mgr.XianLvMinesMgr",
            "expected_asset": "XianLvMinesMgr.lua",
            "near_note": "No near-name asset was found in the text asset inventory.",
        },
        {
            "branch": "travel_youlipool_info",
            "alias": "YoulipoolMgr",
            "require_package": "GameSystem.Game.Youlipool.Mgr.YoulipoolMgr",
            "expected_asset": "YoulipoolMgr.lua",
            "near_note": "No near-name asset was found in the text asset inventory.",
        },
    ]

    asset_summary_rows: list[dict[str, Any]] = []
    for spec in specs:
        exact_assets = [row for row in rows if row.get("asset_name") == spec["expected_asset"]]
        require_hits = [
            row
            for row in rows
            if spec["require_package"] in str(row.get("requires", ""))
            or spec["require_package"] in str(row.get("functions", ""))
            or spec["require_package"] in str(row.get("terms", ""))
        ]
        near_assets = [
            row
            for row in rows
            if spec["alias"] in str(row.get("asset_name", "")) and row.get("asset_name") != spec["expected_asset"]
        ]
        asset_summary_rows.append(
            {
                "branch": spec["branch"],
                "expected_asset": spec["expected_asset"],
                "require_package": spec["require_package"],
                "exact_asset_hits": len(exact_assets),
                "require_package_hits": len(require_hits),
                "near_name_asset_hits": len(near_assets),
                "near_name_assets": ", ".join(str(row.get("asset_name", "")) for row in near_assets[:8]),
                "note": spec["near_note"] if near_assets or spec["alias"] == "EventMgr" else "Exact manager source is absent from the text asset inventory.",
            }
        )

    diffable_root = output_dir / "cpp2il_2022_1_pre21_arm64_diffable_cs"
    isil_root = output_dir / "cpp2il_2022_1_pre21_arm64_isil"
    loader_markers = (
        "LScriptData.LoadScript",
        "AssetManager",
        "ToLua.AddLuaLoader",
        "LuaStatePtr.LuaRequire",
        "LuaState.DoString",
        "LuaSingleton.LuaBeginAddSingleton",
    )
    loader_rows = _scan_text_roots_marker_hits(
        root,
        scan_roots=[diffable_root, isil_root],
        markers=loader_markers,
        max_rows_per_marker=20,
    )

    checks = {
        "text_asset_index_visible": text_asset_index.is_file(),
        "lua_loader_bridge_visible": bool(loader_rows),
        "exact_manager_assets_absent": all(int(row.get("exact_asset_hits") or 0) == 0 for row in asset_summary_rows),
        "required_packages_absent_from_asset_index": all(int(row.get("require_package_hits") or 0) == 0 for row in asset_summary_rows),
        "event_near_name_assets_are_only_near_names": any(
            row.get("branch") == "event_sync_all" and int(row.get("near_name_asset_hits") or 0) > 0 for row in asset_summary_rows
        ),
    }

    _write_tsv(
        output_dir / "lua_login_post_sync_lua_loader_boundary_assets.tsv",
        asset_summary_rows,
        [
            "branch",
            "expected_asset",
            "require_package",
            "exact_asset_hits",
            "require_package_hits",
            "near_name_asset_hits",
            "near_name_assets",
            "note",
        ],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_lua_loader_boundary_hits.tsv",
        loader_rows,
        ["marker", "scan_root", "source", "line", "snippet"],
    )
    _write_login_post_sync_lua_loader_boundary_markdown(
        output_dir / "lua_login_post_sync_lua_loader_boundary_report.md",
        export_root=root,
        output_dir=output_dir,
        asset_rows=asset_summary_rows,
        loader_rows=loader_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_post_sync_lua_loader_boundary_report.md"),
        "assets_path": str(output_dir / "lua_login_post_sync_lua_loader_boundary_assets.tsv"),
        "loader_hits_path": str(output_dir / "lua_login_post_sync_lua_loader_boundary_hits.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "asset_rows": len(asset_summary_rows),
            "loader_hits": len(loader_rows),
            "text_asset_index_rows": len(rows),
        },
    }
    (output_dir / "lua_login_post_sync_lua_loader_boundary_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _binary_marker_offsets(data: bytes, marker: str, *, limit: int = 12) -> list[int]:
    marker_bytes = marker.encode("utf-8")
    offsets: list[int] = []
    start = 0
    while len(offsets) < limit:
        offset = data.find(marker_bytes, start)
        if offset < 0:
            break
        offsets.append(offset)
        start = offset + max(len(marker_bytes), 1)
    return offsets


def _binary_marker_count(data: bytes, marker: str) -> int:
    marker_bytes = marker.encode("utf-8")
    if not marker_bytes:
        return 0
    count = 0
    start = 0
    while True:
        offset = data.find(marker_bytes, start)
        if offset < 0:
            return count
        count += 1
        start = offset + len(marker_bytes)


def _binary_marker_snippet(data: bytes, offset: int, marker: str, *, radius: int = 72) -> str:
    start = max(0, offset - radius)
    stop = min(len(data), offset + len(marker.encode("utf-8")) + radius)
    text = data[start:stop].decode("utf-8", errors="ignore")
    text = text.replace("\x00", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()[:220]


def _write_login_post_sync_raw_lscript_bundle_gap_markdown(
    path: Path,
    *,
    export_root: Path,
    resource_root: Path,
    output_dir: Path,
    bundle_rows: list[dict[str, Any]],
    hit_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login post-sync raw lscript bundle gap report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report compares the unresolved post-login manager branches against raw `frxx_game_files/lscripts` bundles.",
        "- It is a boundary probe: it does not unpack or modify bundles, it only records whether raw bundle bytes expose markers that the current text-asset export missed.",
        "- The important next step is to extend the lscript export path to cover these raw bundles before continuing deeper protocol or C# analysis.",
        "",
        f"- Resource root: `{resource_root}`",
        f"- Export root: `{export_root}`",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Bundle Gaps",
            "",
            "| Branch | Pattern | Raw Bundles | Indexed Bundles | Exported Bundle Paths | Manager Asset Hits | Alias Hits | Package Hits | Send Hits | Response Hits | Verdict |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in bundle_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('raw_pattern', '')}` | {row.get('raw_bundle_count', '')} | "
            f"{row.get('hot_update_index_bundle_rows', '')} | {row.get('current_export_bundle_path_matches', '')} | "
            f"{row.get('expected_asset_hits', '')} | {row.get('manager_alias_hits', '')} | {row.get('require_package_hits', '')} | "
            f"{row.get('send_function_hits', '')} | {row.get('response_handler_hits', '')} | "
            f"{str(row.get('verdict', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Byte Hits",
            "",
            "| Branch | Marker Type | Marker | Source | Offset | Snippet |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in hit_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('marker_type', '')}` | `{str(row.get('marker', '')).replace('|', '\\|')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` | {row.get('offset', '')} | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Bundle TSV: `{_report_path(output_dir / 'lua_login_post_sync_raw_lscript_bundle_gaps.tsv', export_root)}`",
            f"- Byte hits TSV: `{_report_path(output_dir / 'lua_login_post_sync_raw_lscript_bundle_gap_hits.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_post_sync_raw_lscript_bundle_gaps_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_post_sync_raw_lscript_bundle_gap_probe(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Compare missing post-login Lua managers against raw lscript bundles."""

    resource_base = resolve_fanxiu_resource_root(resource_root)
    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    lscript_root = resource_base / "lscripts"
    text_asset_index = output_dir / "hot_update_lscripts_text_assets.tsv"
    text_asset_rows = _read_lscripts_text_asset_rows(text_asset_index)
    by_source_root = root / "by_source" / "lscripts"
    exported_paths = [
        path
        for path in sorted(by_source_root.rglob("*"), key=lambda item: item.as_posix().lower())
        if by_source_root.is_dir()
    ]
    specs = [
        {
            "branch": "event_sync_all",
            "raw_pattern": "gamesystem/game/event_*.bytes",
            "manager_alias": "EventMgr",
            "require_package": "GameSystem.Game.Event.Mgr.EventMgr",
            "expected_asset": "EventMgr.lua",
            "send_function": "CM_EventSyncAllFun",
            "response_handler": "SM_EventSyncAllFun",
        },
        {
            "branch": "xianlv_mines_battlefield_buffs",
            "raw_pattern": "gamesystem/game/xianlvmines_*.bytes",
            "manager_alias": "XianLvMinesMgr",
            "require_package": "GameSystem.Game.XianLvMines.Mgr.XianLvMinesMgr",
            "expected_asset": "XianLvMinesMgr.lua",
            "send_function": "CM_SyncBattleFieldBuffsFun",
            "response_handler": "SM_SyncBattleFieldBuffsFun",
        },
        {
            "branch": "travel_youlipool_info",
            "raw_pattern": "gamesystem/game/youlipool_*.bytes",
            "manager_alias": "YoulipoolMgr",
            "require_package": "GameSystem.Game.Youlipool.Mgr.YoulipoolMgr",
            "expected_asset": "YoulipoolMgr.lua",
            "send_function": "CM_YouliPoolInfoFun",
            "response_handler": "SM_YouliPoolInfoFun",
        },
    ]

    bundle_rows: list[dict[str, Any]] = []
    hit_rows: list[dict[str, Any]] = []
    for spec in specs:
        raw_bundles = sorted(lscript_root.glob(spec["raw_pattern"]), key=lambda item: item.as_posix().lower())
        bundle_stems = {path.stem for path in raw_bundles}
        indexed_bundle_rows = [
            row
            for row in text_asset_rows
            if any(
                stem in str(row.get(column, ""))
                for stem in bundle_stems
                for column in ("group", "logical_path", "actual_path", "output_path")
            )
        ]
        exported_path_matches = [
            path for path in exported_paths if any(stem in path.as_posix() for stem in bundle_stems)
        ]
        markers = [
            ("expected_asset", spec["expected_asset"]),
            ("manager_alias", spec["manager_alias"]),
            ("require_package", spec["require_package"]),
            ("send_function", spec["send_function"]),
            ("response_handler", spec["response_handler"]),
        ]
        marker_counts: Counter[str] = Counter()
        for bundle_path in raw_bundles:
            try:
                data = bundle_path.read_bytes()
            except OSError:
                continue
            for marker_type, marker in markers:
                count = _binary_marker_count(data, marker)
                marker_counts[marker_type] += count
                for offset in _binary_marker_offsets(data, marker):
                    hit_rows.append(
                        {
                            "branch": spec["branch"],
                            "marker_type": marker_type,
                            "marker": marker,
                            "source": _report_path(bundle_path, resource_base),
                            "offset": offset,
                            "snippet": _binary_marker_snippet(data, offset, marker),
                        }
                    )
        if marker_counts["expected_asset"] > 0 and not indexed_bundle_rows:
            verdict = "raw bundle exposes a manager asset marker that the current text-asset index missed"
        elif raw_bundles and not indexed_bundle_rows:
            verdict = "raw bundle exists but current text-asset index has no matching bundle row"
        elif not raw_bundles:
            verdict = "raw bundle not found under resource lscript root"
        else:
            verdict = "raw bundle is already represented in the current text-asset index"
        bundle_rows.append(
            {
                "branch": spec["branch"],
                "raw_pattern": spec["raw_pattern"],
                "raw_bundle_count": len(raw_bundles),
                "raw_bundles": ", ".join(_report_path(path, resource_base) for path in raw_bundles),
                "hot_update_index_bundle_rows": len(indexed_bundle_rows),
                "current_export_bundle_path_matches": len(exported_path_matches),
                "expected_asset": spec["expected_asset"],
                "expected_asset_hits": marker_counts["expected_asset"],
                "manager_alias": spec["manager_alias"],
                "manager_alias_hits": marker_counts["manager_alias"],
                "require_package": spec["require_package"],
                "require_package_hits": marker_counts["require_package"],
                "send_function": spec["send_function"],
                "send_function_hits": marker_counts["send_function"],
                "response_handler": spec["response_handler"],
                "response_handler_hits": marker_counts["response_handler"],
                "verdict": verdict,
            }
        )

    checks = {
        "raw_lscript_root_visible": lscript_root.is_dir(),
        "raw_target_bundles_visible": all(int(row.get("raw_bundle_count") or 0) > 0 for row in bundle_rows),
        "current_text_asset_index_missing_target_bundles": all(
            int(row.get("hot_update_index_bundle_rows") or 0) == 0 for row in bundle_rows
        ),
        "current_by_source_missing_target_bundle_hashes": all(
            int(row.get("current_export_bundle_path_matches") or 0) == 0 for row in bundle_rows
        ),
        "event_mgr_asset_visible_in_raw_bytes": any(
            row.get("branch") == "event_sync_all" and int(row.get("expected_asset_hits") or 0) > 0 for row in bundle_rows
        ),
        "full_package_strings_still_absent_in_raw_bytes": all(
            int(row.get("require_package_hits") or 0) == 0 for row in bundle_rows
        ),
        "xianlv_youlipool_manager_aliases_absent_in_raw_bytes": all(
            int(row.get("manager_alias_hits") or 0) == 0
            for row in bundle_rows
            if row.get("branch") in {"xianlv_mines_battlefield_buffs", "travel_youlipool_info"}
        ),
    }

    _write_tsv(
        output_dir / "lua_login_post_sync_raw_lscript_bundle_gaps.tsv",
        bundle_rows,
        [
            "branch",
            "raw_pattern",
            "raw_bundle_count",
            "raw_bundles",
            "hot_update_index_bundle_rows",
            "current_export_bundle_path_matches",
            "expected_asset",
            "expected_asset_hits",
            "manager_alias",
            "manager_alias_hits",
            "require_package",
            "require_package_hits",
            "send_function",
            "send_function_hits",
            "response_handler",
            "response_handler_hits",
            "verdict",
        ],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_raw_lscript_bundle_gap_hits.tsv",
        hit_rows,
        ["branch", "marker_type", "marker", "source", "offset", "snippet"],
    )
    _write_login_post_sync_raw_lscript_bundle_gap_markdown(
        output_dir / "lua_login_post_sync_raw_lscript_bundle_gaps_report.md",
        export_root=root,
        resource_root=resource_base,
        output_dir=output_dir,
        bundle_rows=bundle_rows,
        hit_rows=hit_rows,
        checks=checks,
    )

    result = {
        "resource_root": str(resource_base),
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_post_sync_raw_lscript_bundle_gaps_report.md"),
        "bundle_path": str(output_dir / "lua_login_post_sync_raw_lscript_bundle_gaps.tsv"),
        "hits_path": str(output_dir / "lua_login_post_sync_raw_lscript_bundle_gap_hits.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "branches": len(bundle_rows),
            "raw_bundles": sum(int(row.get("raw_bundle_count") or 0) for row in bundle_rows),
            "byte_hits": len(hit_rows),
            "event_mgr_asset_hits": sum(
                int(row.get("expected_asset_hits") or 0) for row in bundle_rows if row.get("branch") == "event_sync_all"
            ),
        },
    }
    (output_dir / "lua_login_post_sync_raw_lscript_bundle_gaps_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _append_lua_marker_evidence(
    rows: list[dict[str, Any]],
    *,
    branch: str,
    stage: str,
    source: Path | None,
    export_root: Path,
    markers: tuple[str, ...],
    max_rows_per_marker: int = 3,
) -> None:
    if source is None or not source.is_file():
        return
    try:
        lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    counts: Counter[str] = Counter()
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        for marker in markers:
            if counts[marker] >= max_rows_per_marker or marker not in line:
                continue
            counts[marker] += 1
            rows.append(
                {
                    "branch": branch,
                    "stage": stage,
                    "source": _report_path(source, export_root),
                    "line": line_no,
                    "marker": marker,
                    "snippet": stripped,
                }
            )


def _write_login_post_sync_raw_lscript_handler_closure_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    closure_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login post-sync raw lscript handler closure report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report exports the raw lscript bundles that were missing from the prior text-asset index and checks whether they close the post-login handler gaps.",
        "- It is still static analysis: it proves local Lua send/receive/model-sink code is visible, not live server behavior.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Handler Closures",
            "",
            "| Branch | Raw Bundle | Assets | Manager | NetLogic | Request | Response | Model Sink | Event/UI Signal | Status |",
            "| --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in closure_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('raw_bundle', '')}` | {row.get('asset_count', '')} | "
            f"`{row.get('manager_file', '')}` | `{row.get('netlogic_file', '')}` | `{row.get('request_function', '')}` | "
            f"`{row.get('response_function', '')}` | `{row.get('model_sink', '')}` | "
            f"`{row.get('event_signal', '')}` | {str(row.get('status', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| Branch | Stage | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        lines.append(
            f"| `{row.get('branch', '')}` | `{row.get('stage', '')}` | `{str(row.get('source', '')).replace('|', '\\|')}` | "
            f"{row.get('line', '')} | `{str(row.get('marker', '')).replace('|', '\\|')}` | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Closures TSV: `{_report_path(output_dir / 'lua_login_post_sync_raw_lscript_handler_closures.tsv', export_root)}`",
            f"- Evidence TSV: `{_report_path(output_dir / 'lua_login_post_sync_raw_lscript_handler_closure_evidence.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_post_sync_raw_lscript_handler_closure_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_post_sync_raw_lscript_handler_closure_probe(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Export missing raw lscript bundles and close post-login handler sinks."""

    resource_base = resolve_fanxiu_resource_root(resource_root)
    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    specs = [
        {
            "branch": "event_sync_all",
            "raw_bundle": "lscripts/gamesystem/game/event_4202f391f2fa425c1cd3864ebd848b62.bytes",
            "manager_file": "EventMgr.lua",
            "require_package": "GameSystem.Game.Event.Mgr.EventMgr",
            "netlogic_file": "EventNetLogic.lua",
            "model_file": "EventData.lua",
            "request_function": "CM_EventSyncAllFun",
            "response_function": "SM_EventSyncAllFun",
            "response_sink": "EventMgr.Inst_get().Model.EventData:EventInfo(msg.events)",
            "model_sink": "EventData.EventInfo(events)",
            "event_signal": "LocationDetectionMgr.StartCheckEvent",
            "request_markers": ("function _M.CM_EventSyncAllFun", "SocketManager.Inst_get():F_SendMsg(CM_EventSyncAll)"),
            "response_markers": ("function _M.SM_EventSyncAllFun", "EventMgr.Inst_get().Model.EventData:EventInfo(msg.events)"),
            "model_markers": ("function _M.EventInfo(self,events)", "self:AddEventVO(eventvo)", "LocationDetectionMgr.Inst_get():StartCheckEvent()"),
        },
        {
            "branch": "xianlv_mines_battlefield_buffs",
            "raw_bundle": "lscripts/gamesystem/game/xianlvmines_ba5b4cf8259585b87649c7c98578275a.bytes",
            "manager_file": "XianLvMinesMgr.lua",
            "require_package": "GameSystem.Game.XianLvMines.Mgr.XianLvMinesMgr",
            "netlogic_file": "XianLvMinesNetLogic.lua",
            "model_file": "XianLvMinesModel.lua",
            "request_function": "CM_SyncBattleFieldBuffsFun",
            "response_function": "SM_SyncBattleFieldBuffsFun",
            "response_sink": "XianLvMinesMgr.Inst_get().Model:SetBattleFieldBuffs(msg)",
            "model_sink": "XianLvMinesModel.SetBattleFieldBuffs(msg.activityBaseIdToBuff)",
            "event_signal": "XianLvMinesUpdateBattleFieldBuffs",
            "request_markers": ("function _M.CM_SyncBattleFieldBuffsFun", "SocketManager.Inst_get():F_SendMsg(CM_SyncBattleFieldBuffs)"),
            "response_markers": ("function _M.SM_SyncBattleFieldBuffsFun", "XianLvMinesMgr.Inst_get().Model:SetBattleFieldBuffs(msg)"),
            "model_markers": (
                "function _M.SetBattleFieldBuffs(self,msg)",
                "self.Data:SetBattleFieldBuffs(msg and msg.activityBaseIdToBuff)",
                "self:RaiseEvent(XianLvMinesType.EventType.XianLvMinesUpdateBattleFieldBuffs)",
            ),
        },
        {
            "branch": "travel_youlipool_info",
            "raw_bundle": "lscripts/gamesystem/game/youlipool_3a952de7d13bbf1ff3e6134fcfd2e3ae.bytes",
            "manager_file": "YoulipoolMgr.lua",
            "require_package": "GameSystem.Game.Youlipool.Mgr.YoulipoolMgr",
            "netlogic_file": "YoulipoolNetLogic.lua",
            "model_file": "YoulipoolModel.lua",
            "request_function": "CM_YouliPoolInfoFun",
            "response_function": "SM_YouliPoolInfoFun",
            "response_sink": "YoulipoolMgr.Inst_get().Model:SetInfoMap(msg.infoMap)",
            "model_sink": "YoulipoolModel.SetInfoMap(infoMap)",
            "event_signal": "YoulipoolType.EventType.InfoUpdate",
            "request_markers": ("function _M.CM_YouliPoolInfoFun", "SocketManager.Inst_get():F_SendMsg(CM_YouliPoolInfo)"),
            "response_markers": ("function _M.SM_YouliPoolInfoFun", "YoulipoolMgr.Inst_get().Model:SetInfoMap(msg.infoMap)"),
            "model_markers": (
                "function _M.SetInfoMap(self,infoMap)",
                "self.YoulipoolData:SetInfoMap(infoMap)",
                "self:RaiseEvent(YoulipoolType.EventType.InfoUpdate)",
            ),
        },
    ]

    closure_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for spec in specs:
        export_result = export_fanxiu_unity_text_assets(
            spec["raw_bundle"],
            resource_root=resource_base,
            export_root=root,
        )
        text_dir = Path(str(export_result.get("output_dir") or ""))
        manager_path = text_dir / spec["manager_file"]
        netlogic_path = text_dir / spec["netlogic_file"]
        model_path = text_dir / spec["model_file"]
        asset_names = [str(item.get("name") or "") for item in export_result.get("items", [])]
        status_checks = {
            "manager": manager_path.is_file(),
            "netlogic": netlogic_path.is_file(),
            "model": model_path.is_file(),
        }
        _append_lua_marker_evidence(
            evidence_rows,
            branch=spec["branch"],
            stage="manager-source",
            source=manager_path,
            export_root=root,
            markers=(f'package.loaded["{spec["require_package"]}"]', spec["manager_file"].removesuffix(".lua")),
        )
        _append_lua_marker_evidence(
            evidence_rows,
            branch=spec["branch"],
            stage="request-send",
            source=netlogic_path,
            export_root=root,
            markers=spec["request_markers"],
        )
        _append_lua_marker_evidence(
            evidence_rows,
            branch=spec["branch"],
            stage="response-handler",
            source=netlogic_path,
            export_root=root,
            markers=spec["response_markers"],
        )
        _append_lua_marker_evidence(
            evidence_rows,
            branch=spec["branch"],
            stage="model-sink",
            source=model_path,
            export_root=root,
            markers=spec["model_markers"],
        )
        branch_evidence = [row for row in evidence_rows if row.get("branch") == spec["branch"]]
        closure_rows.append(
            {
                "branch": spec["branch"],
                "raw_bundle": spec["raw_bundle"],
                "output_dir": str(text_dir),
                "asset_count": len(asset_names),
                "asset_names": " | ".join(asset_names[:80]),
                "manager_file": spec["manager_file"],
                "netlogic_file": spec["netlogic_file"],
                "model_file": spec["model_file"],
                "request_function": spec["request_function"],
                "response_function": spec["response_function"],
                "response_sink": spec["response_sink"],
                "model_sink": spec["model_sink"],
                "event_signal": spec["event_signal"],
                "evidence_rows": len(branch_evidence),
                "status": "closed_lua_handler" if all(status_checks.values()) and branch_evidence else "needs_review",
            }
        )

    checks = {
        "raw_bundles_exported": all(int(row.get("asset_count") or 0) > 0 for row in closure_rows),
        "manager_sources_visible": all(row.get("status") == "closed_lua_handler" for row in closure_rows),
        "event_sync_handler_closed": any(
            row.get("branch") == "event_sync_all" and row.get("status") == "closed_lua_handler" for row in closure_rows
        ),
        "xianlv_battlefield_buffs_handler_closed": any(
            row.get("branch") == "xianlv_mines_battlefield_buffs" and row.get("status") == "closed_lua_handler"
            for row in closure_rows
        ),
        "youlipool_info_handler_closed": any(
            row.get("branch") == "travel_youlipool_info" and row.get("status") == "closed_lua_handler" for row in closure_rows
        ),
    }

    _write_tsv(
        output_dir / "lua_login_post_sync_raw_lscript_handler_closures.tsv",
        closure_rows,
        [
            "branch",
            "raw_bundle",
            "output_dir",
            "asset_count",
            "asset_names",
            "manager_file",
            "netlogic_file",
            "model_file",
            "request_function",
            "response_function",
            "response_sink",
            "model_sink",
            "event_signal",
            "evidence_rows",
            "status",
        ],
    )
    _write_tsv(
        output_dir / "lua_login_post_sync_raw_lscript_handler_closure_evidence.tsv",
        evidence_rows,
        ["branch", "stage", "source", "line", "marker", "snippet"],
    )
    _write_login_post_sync_raw_lscript_handler_closure_markdown(
        output_dir / "lua_login_post_sync_raw_lscript_handler_closure_report.md",
        export_root=root,
        output_dir=output_dir,
        closure_rows=closure_rows,
        evidence_rows=evidence_rows,
        checks=checks,
    )

    result = {
        "resource_root": str(resource_base),
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_post_sync_raw_lscript_handler_closure_report.md"),
        "closures_path": str(output_dir / "lua_login_post_sync_raw_lscript_handler_closures.tsv"),
        "evidence_path": str(output_dir / "lua_login_post_sync_raw_lscript_handler_closure_evidence.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "branches": len(closure_rows),
            "exported_assets": sum(int(row.get("asset_count") or 0) for row in closure_rows),
            "closed_handlers": sum(1 for row in closure_rows if row.get("status") == "closed_lua_handler"),
            "evidence": len(evidence_rows),
        },
    }
    (output_dir / "lua_login_post_sync_raw_lscript_handler_closure_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_lua_raw_lscript_export_coverage_markdown(
    path: Path,
    *,
    export_root: Path,
    resource_root: Path,
    output_dir: Path,
    rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    by_status = Counter(str(row.get("status") or "") for row in rows)
    missing_by_group = Counter(str(row.get("group") or "") for row in rows if row.get("status") == "missing_export_by_hash")
    lines = [
        "# Lua raw lscript export coverage report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- Resource root: `{resource_root}`",
        f"- Export root: `{export_root}`",
        f"- Raw lscript bundles: {len(rows)}",
        f"- Status: {', '.join(f'{key}:{value}' for key, value in by_status.most_common())}",
        f"- Top missing groups: {', '.join(f'{key}:{value}' for key, value in missing_by_group.most_common(12))}",
        "",
        "This is an export coverage inventory. Missing rows mean the raw bundle exists under `frxx_game_files/lscripts`, but the current `by_source/lscripts` export tree has no path containing the same bundle hash/stem.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Missing Sample",
            "",
            "| Raw Path | Group | Module | Size | Text-Asset Index Rows |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for row in [item for item in rows if item.get("status") == "missing_export_by_hash"][:80]:
        lines.append(
            f"| `{row.get('raw_path', '')}` | `{row.get('group', '')}` | `{row.get('module', '')}` | "
            f"{row.get('byte_size', '')} | {row.get('hot_update_index_rows', '')} |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Coverage TSV: `{_report_path(output_dir / 'lua_raw_lscript_export_coverage.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_raw_lscript_export_coverage_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_raw_lscript_export_coverage_probe(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Inventory raw lscript bundles covered by the current by_source export tree."""

    resource_base = resolve_fanxiu_resource_root(resource_root)
    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    lscript_root = resource_base / "lscripts"
    by_source_root = root / "by_source" / "lscripts"
    text_asset_index = output_dir / "hot_update_lscripts_text_assets.tsv"
    text_asset_rows = _read_lscripts_text_asset_rows(text_asset_index)
    exported_paths = [path.as_posix().lower() for path in by_source_root.rglob("*")] if by_source_root.is_dir() else []

    rows: list[dict[str, Any]] = []
    if lscript_root.is_dir():
        for raw_path in sorted(lscript_root.rglob("*.bytes"), key=lambda item: item.as_posix().lower()):
            rel_path = raw_path.relative_to(resource_base).as_posix()
            parts = rel_path.split("/")
            group = "/".join(parts[:3]) if len(parts) >= 3 else "/".join(parts[:-1])
            module = raw_path.stem.rsplit("_", 1)[0]
            stem = raw_path.stem.lower()
            export_match_count = sum(1 for value in exported_paths if stem in value)
            hot_update_index_rows = sum(
                1
                for row in text_asset_rows
                if any(
                    stem in str(row.get(column, "")).lower()
                    for column in ("group", "logical_path", "actual_path", "output_path")
                )
            )
            rows.append(
                {
                    "raw_path": rel_path,
                    "group": group,
                    "module": module,
                    "stem": raw_path.stem,
                    "byte_size": raw_path.stat().st_size,
                    "export_match_count": export_match_count,
                    "hot_update_index_rows": hot_update_index_rows,
                    "status": "covered_by_hash" if export_match_count else "missing_export_by_hash",
                }
            )

    _write_tsv(
        output_dir / "lua_raw_lscript_export_coverage.tsv",
        rows,
        [
            "raw_path",
            "group",
            "module",
            "stem",
            "byte_size",
            "export_match_count",
            "hot_update_index_rows",
            "status",
        ],
    )
    checks = {
        "raw_lscript_root_visible": lscript_root.is_dir(),
        "by_source_lscript_root_visible": by_source_root.is_dir(),
        "raw_bundles_found": bool(rows),
        "coverage_status_computed": all(
            row.get("status") in {"covered_by_hash", "missing_export_by_hash"} for row in rows
        ),
    }
    _write_lua_raw_lscript_export_coverage_markdown(
        output_dir / "lua_raw_lscript_export_coverage_report.md",
        export_root=root,
        resource_root=resource_base,
        output_dir=output_dir,
        rows=rows,
        checks=checks,
    )

    by_status = Counter(str(row.get("status") or "") for row in rows)
    result = {
        "resource_root": str(resource_base),
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_raw_lscript_export_coverage_report.md"),
        "coverage_path": str(output_dir / "lua_raw_lscript_export_coverage.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "raw_bundles": len(rows),
            "covered_by_hash": by_status.get("covered_by_hash", 0),
            "missing_export_by_hash": by_status.get("missing_export_by_hash", 0),
            "hot_update_index_rows": len(text_asset_rows),
        },
    }
    (output_dir / "lua_raw_lscript_export_coverage_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_LUA_ANY_REQUIRE_RE = re.compile(r"""require\s*(?:\(\s*)?["']([^"']+)["']""")
_LUA_FUNCTION_NAME_RE = re.compile(r"""(?m)^\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)""")
_LUA_FUNCTION_LINE_RE = re.compile(r"""^\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)""")
_LUA_M_FUNCTION_START_RE = re.compile(r"""^\s*function\s+_M[\.:](?P<name>[A-Za-z0-9_]+)\s*\((?P<args>[^)]*)\)""")
_LUA_REQUIRE_LINE_RE = re.compile(
    r"""(?:(?:local\s+)?(?P<alias>[A-Za-z_]\w*)\s*=\s*)?require\s*(?:\(\s*)?["'](?P<package>[^"']+)["']"""
)
_LUA_PACKET_VAR_RE = re.compile(r"""\b_?(C[MS]_[A-Za-z0-9_]+)\b""")
_LUA_PACKET_FIELD_ASSIGN_RE = re.compile(r"""\b(?P<packet>C[MS]_[A-Za-z0-9_]+)\.(?P<field>[A-Za-z0-9_]+)\s*=\s*(?P<value>.+)""")
_LUA_PACKET_FIELD_COLLECTION_ADD_RE = re.compile(
    r"""\b(?P<packet>C[MS]_[A-Za-z0-9_]+)\.(?P<field>[A-Za-z0-9_]+)\s*:\s*(?P<method>Add|LuaDic_Add)\s*\((?P<value>.*)\)"""
)
_LUA_MESSAGE_POOL_ASSIGN_RE = re.compile(
    r"""\b(?:local\s+)?(?P<var>[A-Za-z_]\w*)\s*=\s*.*?GetMessageFromPools\s*\(\s*(?P<alias>_?C[MS]_[A-Za-z0-9_]+)\s*\)"""
)
_REPORT_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _scan_lua_export_text_asset(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {
            "line_count": 0,
            "function_count": 0,
            "requires": "",
            "functions": "",
        }
    require_matches = _LUA_ANY_REQUIRE_RE.findall(text)
    function_matches = _LUA_FUNCTION_NAME_RE.findall(text)
    requires = list(dict.fromkeys(require_matches))[:12]
    functions = list(dict.fromkeys(function_matches))[:12]
    return {
        "line_count": text.count("\n") + (1 if text else 0),
        "function_count": len(function_matches),
        "requires": " | ".join(requires),
        "functions": " | ".join(functions),
    }


def _safe_report_slug(value: str) -> str:
    slug = _REPORT_SLUG_RE.sub("_", value.strip().lower()).strip("._-")
    return slug or "module"


def _classify_lua_surface_file(filename: str, package_name: str) -> str:
    stem = filename.removesuffix(".lua")
    lower_text = f"{stem}.{package_name}".lower()
    if stem.startswith(("CM_", "SM_")) or ".packet." in package_name:
        return "packet"
    if "netlogic" in lower_text:
        return "net_logic"
    if stem.endswith("Mgr") or ".mgr." in lower_text or ".manager." in lower_text:
        return "manager"
    if "buff" in lower_text:
        return "buff"
    if "skill" in lower_text:
        return "skill"
    if "effect" in lower_text:
        return "effect"
    if any(token in lower_text for token in ("view", "panel", "item", ".ui.", "activity")):
        return "ui"
    if any(token in lower_text for token in ("entity", "bot", "monster", "partner")):
        return "entity"
    if any(token in lower_text for token in ("model", "data", "vo", "dto")):
        return "model_data"
    if any(token in lower_text for token in ("type", "const", "define")):
        return "const"
    return "other"


def _lua_function_kind(name: str) -> str:
    short_name = name.rsplit(".", 1)[-1].rsplit(":", 1)[-1]
    if short_name.startswith("CM_") and short_name.endswith("Fun"):
        return "client_request"
    if short_name.startswith("SM_") and short_name.endswith("Fun"):
        return "server_handler"
    if short_name.startswith(("On", "Open", "Close", "Refresh", "Update")):
        return "ui_lifecycle"
    return "function"


def _lua_marker_category(line: str) -> str:
    if "F_Register(" in line:
        return "packet_registration"
    if "F_SendMsg(" in line:
        return "packet_send"
    if "GetMessageFromPools(" in line:
        return "message_pool"
    if "RaiseEvent(" in line:
        return "event_raise"
    if "AddSingleton(" in line:
        return "singleton_registration"
    if "NetLogic" in line and re.search(r"\bC[MS]_[A-Za-z0-9_]+Fun\b", line):
        return "netlogic_call"
    if "require(" in line or "require " in line:
        return "dynamic_require"
    return ""


def _extract_lua_m_function_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body_lines: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = _LUA_M_FUNCTION_START_RE.search(line)
        if match:
            if current is not None:
                current["line_end"] = line_no - 1
                current["body"] = "\n".join(body_lines)
                blocks.append(current)
            current = {
                "function_name": match.group("name"),
                "args": match.group("args").strip(),
                "line_start": line_no,
                "line_end": line_no,
                "body": "",
            }
            body_lines = [line]
        elif current is not None:
            body_lines.append(line)
    if current is not None:
        current["line_end"] = len(text.splitlines())
        current["body"] = "\n".join(body_lines)
        blocks.append(current)
    return blocks


def _packet_package_for_name(packet_name: str, alias_to_package: dict[str, str]) -> str:
    return alias_to_package.get(f"_{packet_name}") or alias_to_package.get(packet_name) or ""


def _netlogic_edge_category(line: str) -> str:
    if "F_Register(" in line:
        return "packet_registration"
    if "F_Unregister(" in line:
        return "packet_unregistration"
    if "F_SendMsg(" in line:
        return "packet_send"
    if "GetMessageFromPools(" in line:
        return "message_pool"
    if "RaiseEvent(" in line:
        return "event_raise"
    if ".Model:" in line or ".Model." in line:
        return "model_call"
    if "Mgr.Inst_get" in line:
        return "manager_call"
    if "OpenPanel" in line or "ShowPanel" in line:
        return "ui_open"
    return ""


def _extract_netlogic_target(line: str) -> str:
    stripped = line.strip()
    for pattern in (
        r"F_SendMsg\(([^)]+)\)",
        r"GetMessageFromPools\(([^)]+)\)",
        r"F_Register\(([^,]+)",
        r"F_Unregister\(([^)]+)\)",
        r"([A-Za-z0-9_]+Mgr\.Inst_get\(\)[A-Za-z0-9_:\.]+)",
        r"([A-Za-z0-9_]+\.Inst_get\(\)[A-Za-z0-9_:\.]+)",
        r"([A-Za-z0-9_]+:RaiseEvent\([^)]*)",
        r"([A-Za-z0-9_]+\.RaiseEvent\([^)]*)",
    ):
        match = re.search(pattern, stripped)
        if match:
            return match.group(1).strip()
    return stripped[:160]


def _iter_lscript_module_lua_files(root: Path, group: str, module: str) -> list[Path]:
    module_key = module.strip().lower()
    lua_group_root = root / "by_source" / "lscripts" / Path(group.strip().strip("/\\"))
    lua_files: list[Path] = []
    if not lua_group_root.is_dir():
        return lua_files
    for candidate in sorted(lua_group_root.iterdir(), key=lambda item: item.name.lower()):
        if not candidate.is_dir():
            continue
        candidate_module, _bundle_hash = _split_lscript_bundle_name(candidate.name)
        if candidate_module.lower() != module_key:
            continue
        text_assets_dir = candidate / "text_assets"
        if text_assets_dir.is_dir():
            lua_files.extend(sorted(text_assets_dir.glob("*.lua"), key=lambda item: item.name.lower()))
    return lua_files


def _source_rows_for_lua_function(
    *,
    root: Path,
    path: Path,
    module: str,
    group: str,
    source_kind: str,
    function_name: str,
    max_lines: int = 80,
) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    try:
        rel_path = path.relative_to(root).as_posix()
    except ValueError:
        rel_path = path.as_posix()
    rows: list[dict[str, Any]] = []
    for block in _extract_lua_m_function_blocks(text):
        if str(block.get("function_name") or "") != function_name:
            continue
        start_line = int(block.get("line_start") or 1)
        for offset, line in enumerate(str(block.get("body") or "").splitlines()[:max_lines], start=start_line):
            rows.append(
                {
                    "group": group,
                    "module": module,
                    "source_kind": source_kind,
                    "asset_name": path.name,
                    "relative_path": rel_path,
                    "function_name": function_name,
                    "line": offset,
                    "snippet": line.strip(),
                }
            )
        break
    return rows


def _source_rows_for_all_lua_functions_named(
    *,
    root: Path,
    module_files: list[Path],
    module: str,
    group: str,
    source_kind: str,
    function_name: str,
    max_lines: int = 80,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in module_files:
        rows.extend(
            _source_rows_for_lua_function(
                root=root,
                path=path,
                module=module,
                group=group,
                source_kind=source_kind,
                function_name=function_name,
                max_lines=max_lines,
            )
        )
    return rows


def _target_function_hints(target: str) -> list[tuple[str, str]]:
    match = re.search(r":(?P<method>[A-Za-z_][A-Za-z0-9_]*)\b", target)
    if not match:
        return []
    method_name = match.group("method")
    owner_expr = target[: match.start()]
    ignored_names = {"Inst_get", "Model"}
    class_names: list[str] = []
    for name in reversed(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", owner_expr)):
        if name in ignored_names or name in class_names:
            continue
        class_names.append(name)
    return [(class_name, method_name) for class_name in class_names]


def _target_function_hint(target: str) -> tuple[str, str]:
    hints = _target_function_hints(target)
    return hints[0] if hints else ("", "")


def _source_rows_for_target_function(
    *,
    root: Path,
    module_files: list[Path],
    module: str,
    group: str,
    target: str,
    max_lines: int = 80,
) -> list[dict[str, Any]]:
    hints = _target_function_hints(target)
    if not hints:
        return []
    for class_name, method_name in hints:
        preferred = [path for path in module_files if path.stem.lower() == class_name.lower()]
        candidates = preferred or [path for path in module_files if class_name.lower() in path.stem.lower()]
        for path in candidates:
            rows = _source_rows_for_lua_function(
                root=root,
                path=path,
                module=module,
                group=group,
                source_kind="target_function",
                function_name=method_name,
                max_lines=max_lines,
            )
            if rows:
                return rows
    for method_name in dict.fromkeys(method_name for _, method_name in hints):
        rows = _source_rows_for_all_lua_functions_named(
            root=root,
            module_files=module_files,
            module=module,
            group=group,
            source_kind="target_function",
            function_name=method_name,
            max_lines=max_lines,
        )
        if rows:
            return rows
    return []


_RELATED_METHOD_CALL_RE = re.compile(r"""[:.](?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*\(""")
_RELATED_METHOD_IGNORES = {
    "Add",
    "Count",
    "Contains",
    "F_SendMsg",
    "GetMessageFromPools",
    "Inst_get",
    "RaiseEvent",
}


def _source_rows_for_related_method_calls(
    *,
    root: Path,
    module_files: list[Path],
    module: str,
    group: str,
    seed_rows: list[dict[str, Any]],
    existing_keys: set[tuple[str, str]],
    max_lines: int = 80,
    max_depth: int = 2,
    max_methods: int = 24,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    queue = list(seed_rows)
    seen_methods: set[str] = set()
    for _depth in range(max_depth):
        next_queue: list[dict[str, Any]] = []
        for row in queue:
            snippet = str(row.get("snippet") or "")
            asset_name = str(row.get("asset_name") or "")
            current_function = str(row.get("function_name") or "")
            is_data_asset = asset_name.lower().endswith("data.lua")
            for match in _RELATED_METHOD_CALL_RE.finditer(snippet):
                method_name = match.group("method")
                if method_name in _RELATED_METHOD_IGNORES or method_name in seen_methods:
                    continue
                same_named_handoff = method_name == current_function
                data_state_helper = is_data_asset and method_name.startswith(("Set", "Update"))
                if not same_named_handoff and not data_state_helper:
                    continue
                seen_methods.add(method_name)
                if len(seen_methods) > max_methods:
                    return rows
                candidate_rows = _source_rows_for_all_lua_functions_named(
                    root=root,
                    module_files=module_files,
                    module=module,
                    group=group,
                    source_kind="related_function",
                    function_name=method_name,
                    max_lines=max_lines,
                )
                fresh_rows = [
                    candidate
                    for candidate in candidate_rows
                    if (str(candidate.get("relative_path") or ""), str(candidate.get("function_name") or "")) not in existing_keys
                ]
                for candidate in fresh_rows:
                    existing_keys.add((str(candidate.get("relative_path") or ""), str(candidate.get("function_name") or "")))
                rows.extend(fresh_rows)
                next_queue.extend(fresh_rows)
        queue = next_queue
        if not queue:
            break
    return rows


def _write_lua_lscript_module_surface_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    module: str,
    group: str,
    file_rows: list[dict[str, Any]],
    function_rows: list[dict[str, Any]],
    require_rows: list[dict[str, Any]],
    marker_rows: list[dict[str, Any]],
    protocol_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    by_kind = Counter(str(row.get("file_kind") or "") for row in file_rows)
    by_function_kind = Counter(str(row.get("function_kind") or "") for row in function_rows)
    by_marker = Counter(str(row.get("category") or "") for row in marker_rows)
    by_protocol = Counter(str(row.get("direction") or "") for row in protocol_rows)
    lines = [
        f"# Lua lscript module surface report: {module}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- Export root: `{export_root}`",
        f"- Group: `{group}`",
        f"- Module: `{module}`",
        f"- Lua files: {len(file_rows)}",
        f"- Functions: {len(function_rows)}",
        f"- Requires: {len(require_rows)}",
        f"- Operational markers: {len(marker_rows)}",
        f"- Protocol/package refs: {len(protocol_rows)}",
        f"- File kinds: {', '.join(f'{key}:{value}' for key, value in by_kind.most_common())}",
        f"- Function kinds: {', '.join(f'{key}:{value}' for key, value in by_function_kind.most_common())}",
        f"- Marker kinds: {', '.join(f'{key}:{value}' for key, value in by_marker.most_common())}",
        f"- Protocol directions: {', '.join(f'{key}:{value}' for key, value in by_protocol.most_common())}",
        "",
        "This report is a static, read-only module surface map. It does not patch APKs, hook runtime code, or replay traffic.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Key Files",
            "",
            "| Kind | File | Lines | Functions | Requires | Package |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in sorted(
        file_rows,
        key=lambda item: (
            0 if item.get("file_kind") in {"net_logic", "manager", "model_data"} else 1,
            -int(item.get("line_count") or 0),
            str(item.get("asset_name", "")),
        ),
    )[:80]:
        lines.append(
            f"| `{row.get('file_kind', '')}` | `{row.get('asset_name', '')}` | {row.get('line_count', '')} | "
            f"{row.get('function_count', '')} | {row.get('require_count', '')} | `{str(row.get('package', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Protocol/Packet Package Refs",
            "",
            "| Direction | Packet | Alias | Source File | Package |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in protocol_rows[:80]:
        lines.append(
            f"| `{row.get('direction', '')}` | `{row.get('packet_name', '')}` | `{row.get('alias', '')}` | "
            f"`{row.get('asset_name', '')}` | `{str(row.get('package', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Operational Marker Sample",
            "",
            "| Category | File | Line | Snippet |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for row in marker_rows[:80]:
        lines.append(
            f"| `{row.get('category', '')}` | `{row.get('asset_name', '')}` | {row.get('line', '')} | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    slug = _safe_report_slug(module)
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Files TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_surface_files.tsv', export_root)}`",
            f"- Functions TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_surface_functions.tsv', export_root)}`",
            f"- Requires TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_surface_requires.tsv', export_root)}`",
            f"- Markers TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_surface_markers.tsv', export_root)}`",
            f"- Protocol refs TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_surface_protocol_refs.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / f'lua_lscript_module_{slug}_surface_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_lua_raw_lscript_missing_export_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    bundle_rows: list[dict[str, Any]],
    asset_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
    before_counts: dict[str, Any],
    after_counts: dict[str, Any],
    filters: dict[str, Any],
    checks: dict[str, bool],
) -> None:
    by_status = Counter(str(row.get("status") or "") for row in bundle_rows)
    lines = [
        "# Lua raw lscript missing export report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report exports selected raw lscript bundles that are missing from the current `by_source/lscripts` tree.",
        "- It uses the existing Unity TextAsset exporter and writes outputs under the unified Fanxiu export root.",
        f"- Filters: `{json.dumps(filters, ensure_ascii=False)}`",
        f"- Before coverage: `{json.dumps(before_counts, ensure_ascii=False)}`",
        f"- After coverage: `{json.dumps(after_counts, ensure_ascii=False)}`",
        f"- Bundles selected: {len(bundle_rows)}; text assets exported: {len(asset_rows)}; errors: {len(error_rows)}",
        f"- Export status: {', '.join(f'{key}:{value}' for key, value in by_status.most_common())}",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Bundle Sample",
            "",
            "| Raw Path | Module | Assets | Lua Assets | Status | Output Dir |",
            "| --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in bundle_rows[:80]:
        lines.append(
            f"| `{row.get('raw_path', '')}` | `{row.get('module', '')}` | {row.get('asset_count', '')} | "
            f"{row.get('lua_asset_count', '')} | `{row.get('status', '')}` | `{str(row.get('output_dir', '')).replace('|', '\\|')}` |"
        )

    if error_rows:
        lines.extend(["", "## Errors", "", "| Raw Path | Error |", "| --- | --- |"])
        for row in error_rows[:60]:
            lines.append(f"| `{row.get('raw_path', '')}` | `{str(row.get('error', '')).replace('|', '\\|')}` |")

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Bundles TSV: `{_report_path(output_dir / 'lua_raw_lscript_missing_export_bundles.tsv', export_root)}`",
            f"- Text assets TSV: `{_report_path(output_dir / 'lua_raw_lscript_missing_export_text_assets.tsv', export_root)}`",
            f"- Errors TSV: `{_report_path(output_dir / 'lua_raw_lscript_missing_export_errors.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_raw_lscript_missing_export_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_raw_lscript_missing_export_probe(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
    status: Iterable[str] = ("missing_export_by_hash",),
    group_prefix: str | None = None,
    module_contains: str | None = None,
    limit: int | None = None,
    order_by: str = "path",
    dry_run: bool = False,
    refresh_coverage: bool = True,
) -> dict[str, Any]:
    """Export raw lscript bundles selected from the coverage report."""

    resource_base = resolve_fanxiu_resource_root(resource_root)
    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    before = build_fanxiu_lua_raw_lscript_export_coverage_probe(
        resource_root=resource_base,
        export_root=root,
    )
    coverage_rows = _read_lscripts_text_asset_rows(output_dir / "lua_raw_lscript_export_coverage.tsv")
    status_set = {str(item) for item in status}
    group_text = str(group_prefix or "").strip().lower()
    module_text = str(module_contains or "").strip().lower()
    candidates = [
        row
        for row in coverage_rows
        if (not status_set or row.get("status") in status_set)
        and (not group_text or str(row.get("group", "")).lower().startswith(group_text))
        and (not module_text or module_text in str(row.get("module", "")).lower())
    ]
    if order_by == "size_desc":
        candidates.sort(key=lambda row: int(row.get("byte_size") or 0), reverse=True)
    elif order_by == "size_asc":
        candidates.sort(key=lambda row: int(row.get("byte_size") or 0))
    elif order_by == "module":
        candidates.sort(key=lambda row: (str(row.get("module", "")).lower(), str(row.get("raw_path", "")).lower()))
    else:
        candidates.sort(key=lambda row: str(row.get("raw_path", "")).lower())
    if limit is not None:
        candidates = candidates[: max(0, int(limit))]

    bundle_rows: list[dict[str, Any]] = []
    asset_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    for row in candidates:
        raw_path = str(row.get("raw_path") or "")
        bundle_row: dict[str, Any] = {
            "raw_path": raw_path,
            "group": row.get("group", ""),
            "module": row.get("module", ""),
            "stem": row.get("stem", ""),
            "byte_size": row.get("byte_size", ""),
            "asset_count": 0,
            "lua_asset_count": 0,
            "total_text_bytes": 0,
            "output_dir": "",
            "status": "dry_run" if dry_run else "pending",
            "error": "",
        }
        if dry_run:
            bundle_rows.append(bundle_row)
            continue
        try:
            export_result = export_fanxiu_unity_text_assets(
                raw_path,
                resource_root=resource_base,
                export_root=root,
            )
        except Exception as exc:  # noqa: BLE001 - per-bundle export report.
            bundle_row["status"] = "error"
            bundle_row["error"] = str(exc)
            bundle_rows.append(bundle_row)
            error_rows.append({"raw_path": raw_path, "error": str(exc)})
            continue

        items = list(export_result.get("items") or [])
        output_dir_text = str(export_result.get("output_dir") or "")
        lua_asset_count = 0
        total_text_bytes = 0
        for item in items:
            output_path = Path(str(item.get("output_path") or ""))
            asset_name = str(item.get("name") or output_path.name)
            byte_size = int(item.get("byte_size") or 0)
            total_text_bytes += byte_size
            is_lua = asset_name.lower().endswith(".lua")
            if is_lua:
                lua_asset_count += 1
            scan = _scan_lua_export_text_asset(output_path) if is_lua else {
                "line_count": 0,
                "function_count": 0,
                "requires": "",
                "functions": "",
            }
            asset_rows.append(
                {
                    "raw_path": raw_path,
                    "module": row.get("module", ""),
                    "asset_name": asset_name,
                    "path_id": item.get("path_id", ""),
                    "byte_size": byte_size,
                    "line_count": scan.get("line_count", 0),
                    "function_count": scan.get("function_count", 0),
                    "requires": scan.get("requires", ""),
                    "functions": scan.get("functions", ""),
                    "output_path": str(output_path),
                }
            )
        bundle_row.update(
            {
                "asset_count": len(items),
                "lua_asset_count": lua_asset_count,
                "total_text_bytes": total_text_bytes,
                "output_dir": output_dir_text,
                "status": "exported",
            }
        )
        bundle_rows.append(bundle_row)

    after_counts: dict[str, Any] = {}
    if refresh_coverage and not dry_run:
        after = build_fanxiu_lua_raw_lscript_export_coverage_probe(
            resource_root=resource_base,
            export_root=root,
        )
        after_counts = dict(after.get("counts") or {})

    _write_tsv(
        output_dir / "lua_raw_lscript_missing_export_bundles.tsv",
        bundle_rows,
        [
            "raw_path",
            "group",
            "module",
            "stem",
            "byte_size",
            "asset_count",
            "lua_asset_count",
            "total_text_bytes",
            "output_dir",
            "status",
            "error",
        ],
    )
    _write_tsv(
        output_dir / "lua_raw_lscript_missing_export_text_assets.tsv",
        asset_rows,
        [
            "raw_path",
            "module",
            "asset_name",
            "path_id",
            "byte_size",
            "line_count",
            "function_count",
            "requires",
            "functions",
            "output_path",
        ],
    )
    _write_tsv(output_dir / "lua_raw_lscript_missing_export_errors.tsv", error_rows, ["raw_path", "error"])

    checks = {
        "coverage_rows_loaded": bool(coverage_rows),
        "candidate_selection_nonempty": bool(candidates),
        "no_export_errors": not error_rows,
        "assets_exported_or_dry_run": dry_run or bool(asset_rows),
    }
    filters = {
        "status": sorted(status_set),
        "group_prefix": group_prefix or "",
        "module_contains": module_contains or "",
        "limit": limit,
        "order_by": order_by,
        "dry_run": dry_run,
        "refresh_coverage": refresh_coverage,
    }
    _write_lua_raw_lscript_missing_export_markdown(
        output_dir / "lua_raw_lscript_missing_export_report.md",
        export_root=root,
        output_dir=output_dir,
        bundle_rows=bundle_rows,
        asset_rows=asset_rows,
        error_rows=error_rows,
        before_counts=dict(before.get("counts") or {}),
        after_counts=after_counts,
        filters=filters,
        checks=checks,
    )

    result = {
        "resource_root": str(resource_base),
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_raw_lscript_missing_export_report.md"),
        "bundles_path": str(output_dir / "lua_raw_lscript_missing_export_bundles.tsv"),
        "text_assets_path": str(output_dir / "lua_raw_lscript_missing_export_text_assets.tsv"),
        "errors_path": str(output_dir / "lua_raw_lscript_missing_export_errors.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "filters": filters,
        "counts": {
            "coverage_rows": len(coverage_rows),
            "candidates": len(candidates),
            "bundles": len(bundle_rows),
            "exported": sum(1 for row in bundle_rows if row.get("status") == "exported"),
            "dry_run": sum(1 for row in bundle_rows if row.get("status") == "dry_run"),
            "errors": len(error_rows),
            "text_assets": len(asset_rows),
            "lua_assets": sum(1 for row in asset_rows if str(row.get("asset_name", "")).lower().endswith(".lua")),
            "before_missing": (before.get("counts") or {}).get("missing_export_by_hash", 0),
            "after_missing": after_counts.get("missing_export_by_hash", ""),
        },
    }
    (output_dir / "lua_raw_lscript_missing_export_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_LSCRIPT_HASH_SUFFIX_RE = re.compile(r"^(?P<module>.+)_(?P<hash>[0-9a-fA-F]{32})$")


def _split_lscript_bundle_name(bundle_name: str) -> tuple[str, str]:
    match = _LSCRIPT_HASH_SUFFIX_RE.match(bundle_name)
    if not match:
        return bundle_name, ""
    return match.group("module"), match.group("hash").lower()


def _normalized_path_key(path: str | Path) -> str:
    return str(path).replace("/", "\\").lower()


def _lscript_origin_for_path(path: Path, hot_update_paths: set[str], missing_export_paths: set[str]) -> str:
    key = _normalized_path_key(path)
    if key in hot_update_paths and key in missing_export_paths:
        return "hot_update_and_raw_missing_export"
    if key in hot_update_paths:
        return "hot_update_diff"
    if key in missing_export_paths:
        return "raw_missing_export"
    return "preexisting_or_manual_export"


def _write_lua_lscript_surface_inventory_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    bundle_rows: list[dict[str, Any]],
    module_rows: list[dict[str, Any]],
    asset_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    by_group = Counter(str(row.get("group") or "") for row in bundle_rows)
    by_origin = Counter(str(row.get("origin") or "") for row in asset_rows)
    lines = [
        "# Lua lscript surface inventory report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- Export root: `{export_root}`",
        f"- Bundles: {len(bundle_rows)}",
        f"- Module groups: {len(module_rows)}",
        f"- Lua assets: {len(asset_rows)}",
        f"- Groups: {', '.join(f'{key}:{value}' for key, value in by_group.most_common(16))}",
        f"- Origins: {', '.join(f'{key}:{value}' for key, value in by_origin.most_common())}",
        "",
        "This is a full exported `by_source/lscripts` inventory. Use it to choose the next reverse target after raw lscript coverage was completed.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Top Modules By Lua Assets",
            "",
            "| Group | Module | Bundles | Assets | Packets | Functions | Origin | Sample Assets |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in module_rows[:40]:
        lines.append(
            f"| `{row.get('group', '')}` | `{row.get('module', '')}` | {row.get('bundle_count', '')} | "
            f"{row.get('asset_count', '')} | {row.get('packet_file_count', '')} | {row.get('function_count', '')} | "
            f"`{row.get('origins', '')}` | `{str(row.get('sample_assets', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Bundles TSV: `{_report_path(output_dir / 'lua_lscript_surface_bundles.tsv', export_root)}`",
            f"- Modules TSV: `{_report_path(output_dir / 'lua_lscript_surface_modules.tsv', export_root)}`",
            f"- Assets TSV: `{_report_path(output_dir / 'lua_lscript_surface_assets.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_lscript_surface_inventory_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_lscript_surface_inventory_probe(
    *,
    export_root: str | Path | None = None,
    max_asset_rows: int | None = None,
) -> dict[str, Any]:
    """Inventory the full exported Lua lscript surface after raw bundle export."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    lua_root = root / "by_source" / "lscripts"
    hot_update_paths = {
        _normalized_path_key(row.get("output_path") or "")
        for row in _read_lscripts_text_asset_rows(output_dir / "hot_update_lscripts_text_assets.tsv")
        if row.get("output_path")
    }
    missing_export_paths = {
        _normalized_path_key(row.get("output_path") or "")
        for row in _read_lscripts_text_asset_rows(output_dir / "lua_raw_lscript_missing_export_text_assets.tsv")
        if row.get("output_path")
    }

    asset_rows: list[dict[str, Any]] = []
    bundle_acc: dict[str, dict[str, Any]] = {}
    module_acc: dict[tuple[str, str], dict[str, Any]] = {}
    lua_files = sorted(lua_root.rglob("text_assets/*.lua"), key=lambda item: item.as_posix().lower()) if lua_root.is_dir() else []
    if max_asset_rows is not None:
        lua_files = lua_files[:max_asset_rows]

    for path in lua_files:
        try:
            rel_path = path.relative_to(root).as_posix()
            bundle_rel = path.parent.parent.relative_to(lua_root).as_posix()
        except ValueError:
            continue
        bundle_name = path.parent.parent.name
        group = path.parent.parent.parent.relative_to(lua_root).as_posix() if path.parent.parent.parent != lua_root else "."
        module, bundle_hash = _split_lscript_bundle_name(bundle_name)
        origin = _lscript_origin_for_path(path, hot_update_paths, missing_export_paths)
        scan = _scan_lua_export_text_asset(path)
        packet_item = _parse_packet_file(path, root) if path.name.startswith(("CM_", "SM_")) or path.name.endswith("VO.lua") else None
        packet_id = packet_item.get("id", "") if packet_item else ""
        packet_name = packet_item.get("name", "") if packet_item else ""
        direction = packet_item.get("direction", "") if packet_item else _direction_for_name(path.stem)
        package_name = ""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            package_name = next((match.group(1) for match in _PACKAGE_RE.finditer(text)), "")
        except OSError:
            pass
        stat = path.stat()
        require_count = len([item for item in str(scan.get("requires") or "").split(" | ") if item])
        function_count = int(scan.get("function_count") or 0)
        asset_row = {
            "group": group,
            "module": module,
            "bundle": bundle_name,
            "bundle_hash": bundle_hash,
            "bundle_rel": bundle_rel,
            "asset_name": path.name,
            "relative_path": rel_path,
            "origin": origin,
            "byte_size": stat.st_size,
            "line_count": scan.get("line_count", 0),
            "function_count": function_count,
            "require_count": require_count,
            "packet_id": packet_id,
            "packet_name": packet_name,
            "direction": direction,
            "package": package_name,
            "requires": scan.get("requires", ""),
            "functions": scan.get("functions", ""),
        }
        asset_rows.append(asset_row)

        bundle_data = bundle_acc.setdefault(
            bundle_rel,
            {
                "group": group,
                "module": module,
                "bundle": bundle_name,
                "bundle_hash": bundle_hash,
                "bundle_rel": bundle_rel,
                "asset_count": 0,
                "packet_file_count": 0,
                "cm_packet_count": 0,
                "sm_packet_count": 0,
                "vo_file_count": 0,
                "function_count": 0,
                "require_count": 0,
                "total_bytes": 0,
                "total_lines": 0,
                "origins": Counter(),
                "sample_assets": [],
            },
        )
        bundle_data["asset_count"] += 1
        bundle_data["packet_file_count"] += 1 if packet_name else 0
        bundle_data["cm_packet_count"] += 1 if str(packet_name).startswith("CM_") else 0
        bundle_data["sm_packet_count"] += 1 if str(packet_name).startswith("SM_") else 0
        bundle_data["vo_file_count"] += 1 if str(packet_name).endswith("VO") or path.name.endswith("VO.lua") else 0
        bundle_data["function_count"] += function_count
        bundle_data["require_count"] += require_count
        bundle_data["total_bytes"] += stat.st_size
        bundle_data["total_lines"] += int(scan.get("line_count") or 0)
        bundle_data["origins"][origin] += 1
        if len(bundle_data["sample_assets"]) < 12:
            bundle_data["sample_assets"].append(path.name)

        module_key = (group, module)
        module_data = module_acc.setdefault(
            module_key,
            {
                "group": group,
                "module": module,
                "bundle_count": set(),
                "asset_count": 0,
                "packet_file_count": 0,
                "cm_packet_count": 0,
                "sm_packet_count": 0,
                "vo_file_count": 0,
                "function_count": 0,
                "require_count": 0,
                "total_bytes": 0,
                "total_lines": 0,
                "origins": Counter(),
                "sample_assets": [],
            },
        )
        module_data["bundle_count"].add(bundle_rel)
        module_data["asset_count"] += 1
        module_data["packet_file_count"] += 1 if packet_name else 0
        module_data["cm_packet_count"] += 1 if str(packet_name).startswith("CM_") else 0
        module_data["sm_packet_count"] += 1 if str(packet_name).startswith("SM_") else 0
        module_data["vo_file_count"] += 1 if str(packet_name).endswith("VO") or path.name.endswith("VO.lua") else 0
        module_data["function_count"] += function_count
        module_data["require_count"] += require_count
        module_data["total_bytes"] += stat.st_size
        module_data["total_lines"] += int(scan.get("line_count") or 0)
        module_data["origins"][origin] += 1
        if len(module_data["sample_assets"]) < 16:
            module_data["sample_assets"].append(path.name)

    def finish_counter(counter: Counter[str]) -> str:
        return ", ".join(f"{key}:{value}" for key, value in counter.most_common())

    bundle_rows: list[dict[str, Any]] = []
    for data in bundle_acc.values():
        row = dict(data)
        row["origins"] = finish_counter(data["origins"])
        row["sample_assets"] = " | ".join(data["sample_assets"])
        bundle_rows.append(row)
    bundle_rows.sort(key=lambda row: (-int(row["asset_count"]), str(row["group"]), str(row["module"]), str(row["bundle"])))

    module_rows: list[dict[str, Any]] = []
    for data in module_acc.values():
        row = dict(data)
        row["bundle_count"] = len(data["bundle_count"])
        row["origins"] = finish_counter(data["origins"])
        row["sample_assets"] = " | ".join(data["sample_assets"])
        module_rows.append(row)
    module_rows.sort(key=lambda row: (-int(row["asset_count"]), -int(row["function_count"]), str(row["group"]), str(row["module"])))

    _write_tsv(
        output_dir / "lua_lscript_surface_assets.tsv",
        asset_rows,
        [
            "group",
            "module",
            "bundle",
            "bundle_hash",
            "bundle_rel",
            "asset_name",
            "relative_path",
            "origin",
            "byte_size",
            "line_count",
            "function_count",
            "require_count",
            "packet_id",
            "packet_name",
            "direction",
            "package",
            "requires",
            "functions",
        ],
    )
    _write_tsv(
        output_dir / "lua_lscript_surface_bundles.tsv",
        bundle_rows,
        [
            "group",
            "module",
            "bundle",
            "bundle_hash",
            "bundle_rel",
            "asset_count",
            "packet_file_count",
            "cm_packet_count",
            "sm_packet_count",
            "vo_file_count",
            "function_count",
            "require_count",
            "total_bytes",
            "total_lines",
            "origins",
            "sample_assets",
        ],
    )
    _write_tsv(
        output_dir / "lua_lscript_surface_modules.tsv",
        module_rows,
        [
            "group",
            "module",
            "bundle_count",
            "asset_count",
            "packet_file_count",
            "cm_packet_count",
            "sm_packet_count",
            "vo_file_count",
            "function_count",
            "require_count",
            "total_bytes",
            "total_lines",
            "origins",
            "sample_assets",
        ],
    )

    checks = {
        "lscript_export_root_visible": lua_root.is_dir(),
        "assets_indexed": len(asset_rows) > 0,
        "bundles_indexed": len(bundle_rows) > 0,
        "modules_indexed": len(module_rows) > 0,
    }
    report_path = output_dir / "lua_lscript_surface_inventory_report.md"
    _write_lua_lscript_surface_inventory_markdown(
        report_path,
        export_root=root,
        output_dir=output_dir,
        bundle_rows=bundle_rows,
        module_rows=module_rows,
        asset_rows=asset_rows,
        checks=checks,
    )

    origin_counts = Counter(str(row.get("origin") or "") for row in asset_rows)
    group_counts = Counter(str(row.get("group") or "") for row in bundle_rows)
    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(report_path),
        "bundles_path": str(output_dir / "lua_lscript_surface_bundles.tsv"),
        "modules_path": str(output_dir / "lua_lscript_surface_modules.tsv"),
        "assets_path": str(output_dir / "lua_lscript_surface_assets.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "assets": len(asset_rows),
            "bundles": len(bundle_rows),
            "modules": len(module_rows),
            "packet_files": sum(1 for row in asset_rows if row.get("packet_name")),
            "functions": sum(int(row.get("function_count") or 0) for row in asset_rows),
            "groups": dict(group_counts.most_common()),
            "origins": dict(origin_counts.most_common()),
        },
        "top_modules": module_rows[:40],
    }
    (output_dir / "lua_lscript_surface_inventory_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def build_fanxiu_lua_lscript_module_surface_probe(
    *,
    module: str,
    group: str = "gamesystem/game",
    export_root: str | Path | None = None,
    max_files: int | None = None,
    max_marker_rows: int = 5000,
) -> dict[str, Any]:
    """Index one exported Lua lscript module into files, functions, dependencies, and protocol refs."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    module_key = module.strip().lower()
    group_key = group.strip().strip("/\\")
    if not module_key:
        raise FanxiuResourceError("module 不能为空")

    lua_group_root = root / "by_source" / "lscripts" / Path(group_key)
    bundle_dirs: list[Path] = []
    if lua_group_root.is_dir():
        for candidate in sorted(lua_group_root.iterdir(), key=lambda item: item.name.lower()):
            if not candidate.is_dir():
                continue
            candidate_module, _bundle_hash = _split_lscript_bundle_name(candidate.name)
            if candidate_module.lower() == module_key:
                bundle_dirs.append(candidate)

    lua_files: list[Path] = []
    for bundle_dir in bundle_dirs:
        text_assets_dir = bundle_dir / "text_assets"
        if text_assets_dir.is_dir():
            lua_files.extend(sorted(text_assets_dir.glob("*.lua"), key=lambda item: item.name.lower()))
    if max_files is not None:
        lua_files = lua_files[: max(0, int(max_files))]

    file_rows: list[dict[str, Any]] = []
    function_rows: list[dict[str, Any]] = []
    require_rows: list[dict[str, Any]] = []
    marker_rows: list[dict[str, Any]] = []
    protocol_rows: list[dict[str, Any]] = []
    dynamic_require_rows: list[dict[str, Any]] = []

    for path in lua_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scan = _scan_lua_export_text_asset(path)
        package_name = next((match.group(1) for match in _PACKAGE_RE.finditer(text)), "")
        try:
            rel_path = path.relative_to(root).as_posix()
            bundle_rel = path.parent.parent.relative_to(root / "by_source" / "lscripts").as_posix()
        except ValueError:
            rel_path = path.as_posix()
            bundle_rel = path.parent.parent.name
        bundle_name = path.parent.parent.name
        _bundle_module, bundle_hash = _split_lscript_bundle_name(bundle_name)
        file_kind = _classify_lua_surface_file(path.name, package_name)
        stat = path.stat()
        require_count = len([item for item in str(scan.get("requires") or "").split(" | ") if item])
        function_count = int(scan.get("function_count") or 0)
        file_rows.append(
            {
                "group": group_key,
                "module": module,
                "bundle": bundle_name,
                "bundle_hash": bundle_hash,
                "bundle_rel": bundle_rel,
                "asset_name": path.name,
                "relative_path": rel_path,
                "file_kind": file_kind,
                "byte_size": stat.st_size,
                "line_count": scan.get("line_count", 0),
                "function_count": function_count,
                "require_count": require_count,
                "package": package_name,
                "requires": scan.get("requires", ""),
                "functions": scan.get("functions", ""),
            }
        )

        for line_no, line in enumerate(text.splitlines(), start=1):
            function_match = _LUA_FUNCTION_LINE_RE.search(line)
            if function_match:
                function_name = function_match.group(1)
                function_rows.append(
                    {
                        "group": group_key,
                        "module": module,
                        "bundle": bundle_name,
                        "asset_name": path.name,
                        "relative_path": rel_path,
                        "line": line_no,
                        "function_name": function_name,
                        "function_kind": _lua_function_kind(function_name),
                        "snippet": line.strip(),
                    }
                )

            require_match = _LUA_REQUIRE_LINE_RE.search(line)
            if require_match:
                required_package = require_match.group("package")
                alias = require_match.group("alias") or ""
                require_row = {
                    "group": group_key,
                    "module": module,
                    "bundle": bundle_name,
                    "asset_name": path.name,
                    "relative_path": rel_path,
                    "line": line_no,
                    "alias": alias,
                    "package": required_package,
                    "required_module": _module_for_package(required_package),
                    "snippet": line.strip(),
                }
                require_rows.append(require_row)
                packet_name = required_package.rsplit(".", 1)[-1] if ".packet." in required_package else ""
                if packet_name.startswith(("CM_", "SM_")):
                    protocol_rows.append(
                        {
                            **require_row,
                            "packet_name": packet_name,
                            "direction": _direction_for_name(packet_name),
                        }
                    )
            elif "require(" in line or "require " in line:
                dynamic_require_rows.append(
                    {
                        "group": group_key,
                        "module": module,
                        "bundle": bundle_name,
                        "asset_name": path.name,
                        "relative_path": rel_path,
                        "line": line_no,
                        "alias": "",
                        "package": "",
                        "required_module": "",
                        "snippet": line.strip(),
                    }
                )

            category = _lua_marker_category(line)
            if category and len(marker_rows) < max_marker_rows:
                marker_rows.append(
                    {
                        "group": group_key,
                        "module": module,
                        "bundle": bundle_name,
                        "asset_name": path.name,
                        "relative_path": rel_path,
                        "line": line_no,
                        "category": category,
                        "snippet": line.strip(),
                    }
                )

    require_rows.extend(dynamic_require_rows)
    slug = _safe_report_slug(module)
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_surface_files.tsv",
        file_rows,
        [
            "group",
            "module",
            "bundle",
            "bundle_hash",
            "bundle_rel",
            "asset_name",
            "relative_path",
            "file_kind",
            "byte_size",
            "line_count",
            "function_count",
            "require_count",
            "package",
            "requires",
            "functions",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_surface_functions.tsv",
        function_rows,
        [
            "group",
            "module",
            "bundle",
            "asset_name",
            "relative_path",
            "line",
            "function_name",
            "function_kind",
            "snippet",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_surface_requires.tsv",
        require_rows,
        [
            "group",
            "module",
            "bundle",
            "asset_name",
            "relative_path",
            "line",
            "alias",
            "package",
            "required_module",
            "snippet",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_surface_markers.tsv",
        marker_rows,
        ["group", "module", "bundle", "asset_name", "relative_path", "line", "category", "snippet"],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_surface_protocol_refs.tsv",
        protocol_rows,
        [
            "group",
            "module",
            "bundle",
            "asset_name",
            "relative_path",
            "line",
            "alias",
            "packet_name",
            "direction",
            "package",
            "required_module",
            "snippet",
        ],
    )

    checks = {
        "lscript_group_root_visible": lua_group_root.is_dir(),
        "module_bundles_found": bool(bundle_dirs),
        "lua_files_indexed": bool(file_rows),
        "net_logic_or_protocol_refs_visible": any(row.get("file_kind") == "net_logic" for row in file_rows) or bool(protocol_rows),
    }
    report_path = output_dir / f"lua_lscript_module_{slug}_surface_report.md"
    _write_lua_lscript_module_surface_markdown(
        report_path,
        export_root=root,
        output_dir=output_dir,
        module=module,
        group=group_key,
        file_rows=file_rows,
        function_rows=function_rows,
        require_rows=require_rows,
        marker_rows=marker_rows,
        protocol_rows=protocol_rows,
        checks=checks,
    )

    by_kind = Counter(str(row.get("file_kind") or "") for row in file_rows)
    by_function_kind = Counter(str(row.get("function_kind") or "") for row in function_rows)
    by_marker = Counter(str(row.get("category") or "") for row in marker_rows)
    by_protocol = Counter(str(row.get("direction") or "") for row in protocol_rows)
    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(report_path),
        "files_path": str(output_dir / f"lua_lscript_module_{slug}_surface_files.tsv"),
        "functions_path": str(output_dir / f"lua_lscript_module_{slug}_surface_functions.tsv"),
        "requires_path": str(output_dir / f"lua_lscript_module_{slug}_surface_requires.tsv"),
        "markers_path": str(output_dir / f"lua_lscript_module_{slug}_surface_markers.tsv"),
        "protocol_refs_path": str(output_dir / f"lua_lscript_module_{slug}_surface_protocol_refs.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "filters": {
            "module": module,
            "group": group_key,
            "max_files": max_files,
            "max_marker_rows": max_marker_rows,
        },
        "counts": {
            "bundles": len(bundle_dirs),
            "files": len(file_rows),
            "functions": len(function_rows),
            "requires": len(require_rows),
            "markers": len(marker_rows),
            "protocol_refs": len(protocol_rows),
            "file_kinds": dict(by_kind.most_common()),
            "function_kinds": dict(by_function_kind.most_common()),
            "marker_kinds": dict(by_marker.most_common()),
            "protocol_directions": dict(by_protocol.most_common()),
        },
        "top_files": sorted(
            file_rows,
            key=lambda item: (-int(item.get("line_count") or 0), str(item.get("asset_name", ""))),
        )[:40],
        "protocol_refs": protocol_rows[:80],
    }
    (output_dir / f"lua_lscript_module_{slug}_surface_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_lua_lscript_module_netlogic_flow_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    module: str,
    group: str,
    function_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    by_kind = Counter(str(row.get("function_kind") or "") for row in function_rows)
    by_edge = Counter(str(row.get("category") or "") for row in edge_rows)
    lines = [
        f"# Lua lscript module NetLogic flow report: {module}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- Export root: `{export_root}`",
        f"- Group: `{group}`",
        f"- Module: `{module}`",
        f"- NetLogic functions: {len(function_rows)}",
        f"- Packet field assignments: {len(field_rows)}",
        f"- Flow edges: {len(edge_rows)}",
        f"- Function kinds: {', '.join(f'{key}:{value}' for key, value in by_kind.most_common())}",
        f"- Edge kinds: {', '.join(f'{key}:{value}' for key, value in by_edge.most_common())}",
        "",
        "This report statically slices NetLogic Lua function bodies. It records request field writes and local callback/sink lines only; it does not replay packets.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Request / Handler Functions",
            "",
            "| Kind | Function | Lines | Packets | Assigned Fields | Edge Count |",
            "| --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in function_rows[:120]:
        lines.append(
            f"| `{row.get('function_kind', '')}` | `{row.get('function_name', '')}` | "
            f"{row.get('line_start', '')}-{row.get('line_end', '')} | `{str(row.get('packet_refs', '')).replace('|', '\\|')}` | "
            f"`{str(row.get('assigned_fields', '')).replace('|', '\\|')}` | {row.get('edge_count', '')} |"
        )

    lines.extend(
        [
            "",
            "## Field Assignment Sample",
            "",
            "| Function | Packet | Field | Value | Line |",
            "| --- | --- | --- | --- | ---: |",
        ]
    )
    for row in field_rows[:100]:
        lines.append(
            f"| `{row.get('function_name', '')}` | `{row.get('packet_name', '')}` | `{row.get('field_name', '')}` | "
            f"`{str(row.get('value_expr', '')).replace('|', '\\|')}` | {row.get('line', '')} |"
        )

    lines.extend(
        [
            "",
            "## Flow Edge Sample",
            "",
            "| Category | Function | Target | Line | Snippet |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in edge_rows[:120]:
        lines.append(
            f"| `{row.get('category', '')}` | `{row.get('function_name', '')}` | `{str(row.get('target', '')).replace('|', '\\|')}` | "
            f"{row.get('line', '')} | `{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    slug = _safe_report_slug(module)
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Functions TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_netlogic_flow_functions.tsv', export_root)}`",
            f"- Field assignments TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_netlogic_flow_fields.tsv', export_root)}`",
            f"- Edges TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_netlogic_flow_edges.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / f'lua_lscript_module_{slug}_netlogic_flow_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_lscript_module_netlogic_flow_probe(
    *,
    module: str,
    group: str = "gamesystem/game",
    export_root: str | Path | None = None,
    max_functions: int | None = None,
) -> dict[str, Any]:
    """Slice one module's NetLogic Lua functions into request fields and local sink edges."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    module_key = module.strip().lower()
    group_key = group.strip().strip("/\\")
    if not module_key:
        raise FanxiuResourceError("module 不能为空")

    lua_group_root = root / "by_source" / "lscripts" / Path(group_key)
    netlogic_files: list[Path] = []
    if lua_group_root.is_dir():
        for candidate in sorted(lua_group_root.iterdir(), key=lambda item: item.name.lower()):
            if not candidate.is_dir():
                continue
            candidate_module, _bundle_hash = _split_lscript_bundle_name(candidate.name)
            if candidate_module.lower() != module_key:
                continue
            text_assets_dir = candidate / "text_assets"
            if text_assets_dir.is_dir():
                netlogic_files.extend(
                    sorted(
                        [path for path in text_assets_dir.glob("*.lua") if "netlogic" in path.name.lower()],
                        key=lambda item: item.name.lower(),
                    )
                )

    function_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []

    for path in netlogic_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        try:
            rel_path = path.relative_to(root).as_posix()
        except ValueError:
            rel_path = path.as_posix()
        bundle_name = path.parent.parent.name
        alias_to_package = {
            match.group("alias"): match.group("package")
            for match in _LUA_REQUIRE_LINE_RE.finditer(text)
            if match.group("alias")
        }
        blocks = _extract_lua_m_function_blocks(text)
        if max_functions is not None:
            blocks = blocks[: max(0, int(max_functions))]
        for block in blocks:
            body = str(block.get("body") or "")
            function_name = str(block.get("function_name") or "")
            function_kind = _lua_function_kind(function_name)
            body_lines = body.splitlines()
            message_pool_packets = {
                match.group("var"): match.group("alias").lstrip("_")
                for line in body_lines
                if (match := _LUA_MESSAGE_POOL_ASSIGN_RE.search(line))
            }
            packet_refs = [
                message_pool_packets.get(packet_name, packet_name)
                for packet_name in dict.fromkeys(_LUA_PACKET_VAR_RE.findall(body))
                if not packet_name.endswith("Fun")
            ]
            packet_packages = [
                package
                for package in (_packet_package_for_name(packet_name, alias_to_package) for packet_name in packet_refs)
                if package
            ]
            assigned_fields: list[str] = []
            function_edge_count = 0
            function_field_count = 0
            for offset, line in enumerate(body_lines, start=int(block.get("line_start") or 1)):
                assign_match = _LUA_PACKET_FIELD_ASSIGN_RE.search(line)
                collection_add_match = _LUA_PACKET_FIELD_COLLECTION_ADD_RE.search(line)
                if assign_match or collection_add_match:
                    match = assign_match or collection_add_match
                    assert match is not None
                    packet_var = match.group("packet")
                    packet_name = message_pool_packets.get(packet_var, packet_var)
                    field_name = match.group("field")
                    value_expr = match.group("value").strip()
                    if collection_add_match:
                        value_expr = f"{collection_add_match.group('method')}({value_expr})"
                    assigned_fields.append(f"{packet_name}.{field_name}")
                    function_field_count += 1
                    field_rows.append(
                        {
                            "group": group_key,
                            "module": module,
                            "bundle": bundle_name,
                            "asset_name": path.name,
                            "relative_path": rel_path,
                            "function_name": function_name,
                            "function_kind": function_kind,
                            "line": offset,
                            "packet_name": packet_name,
                            "field_name": field_name,
                            "value_expr": value_expr,
                            "snippet": line.strip(),
                        }
                    )
                category = _netlogic_edge_category(line)
                if category:
                    function_edge_count += 1
                    edge_rows.append(
                        {
                            "group": group_key,
                            "module": module,
                            "bundle": bundle_name,
                            "asset_name": path.name,
                            "relative_path": rel_path,
                            "function_name": function_name,
                            "function_kind": function_kind,
                            "line": offset,
                            "category": category,
                            "target": _extract_netlogic_target(line),
                            "snippet": line.strip(),
                        }
                    )
            function_rows.append(
                {
                    "group": group_key,
                    "module": module,
                    "bundle": bundle_name,
                    "asset_name": path.name,
                    "relative_path": rel_path,
                    "function_name": function_name,
                    "function_kind": function_kind,
                    "args": block.get("args", ""),
                    "line_start": block.get("line_start", ""),
                    "line_end": block.get("line_end", ""),
                    "packet_refs": " | ".join(packet_refs),
                    "packet_packages": " | ".join(dict.fromkeys(packet_packages)),
                    "assigned_fields": " | ".join(dict.fromkeys(assigned_fields)),
                    "field_assignment_count": function_field_count,
                    "edge_count": function_edge_count,
                }
            )

    function_rows.sort(
        key=lambda row: (
            0 if row.get("function_kind") in {"client_request", "server_handler"} else 1,
            str(row.get("function_name", "")),
        )
    )
    field_rows.sort(key=lambda row: (str(row.get("function_name", "")), int(row.get("line") or 0)))
    edge_rows.sort(key=lambda row: (str(row.get("function_name", "")), int(row.get("line") or 0)))

    slug = _safe_report_slug(module)
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_netlogic_flow_functions.tsv",
        function_rows,
        [
            "group",
            "module",
            "bundle",
            "asset_name",
            "relative_path",
            "function_name",
            "function_kind",
            "args",
            "line_start",
            "line_end",
            "packet_refs",
            "packet_packages",
            "assigned_fields",
            "field_assignment_count",
            "edge_count",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_netlogic_flow_fields.tsv",
        field_rows,
        [
            "group",
            "module",
            "bundle",
            "asset_name",
            "relative_path",
            "function_name",
            "function_kind",
            "line",
            "packet_name",
            "field_name",
            "value_expr",
            "snippet",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_netlogic_flow_edges.tsv",
        edge_rows,
        [
            "group",
            "module",
            "bundle",
            "asset_name",
            "relative_path",
            "function_name",
            "function_kind",
            "line",
            "category",
            "target",
            "snippet",
        ],
    )

    checks = {
        "lscript_group_root_visible": lua_group_root.is_dir(),
        "netlogic_files_found": bool(netlogic_files),
        "functions_indexed": bool(function_rows),
        "request_functions_visible": any(row.get("function_kind") == "client_request" for row in function_rows),
        "handler_functions_visible": any(row.get("function_kind") == "server_handler" for row in function_rows),
        "send_edges_visible": any(row.get("category") == "packet_send" for row in edge_rows),
    }
    report_path = output_dir / f"lua_lscript_module_{slug}_netlogic_flow_report.md"
    _write_lua_lscript_module_netlogic_flow_markdown(
        report_path,
        export_root=root,
        output_dir=output_dir,
        module=module,
        group=group_key,
        function_rows=function_rows,
        field_rows=field_rows,
        edge_rows=edge_rows,
        checks=checks,
    )

    by_kind = Counter(str(row.get("function_kind") or "") for row in function_rows)
    by_edge = Counter(str(row.get("category") or "") for row in edge_rows)
    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(report_path),
        "functions_path": str(output_dir / f"lua_lscript_module_{slug}_netlogic_flow_functions.tsv"),
        "fields_path": str(output_dir / f"lua_lscript_module_{slug}_netlogic_flow_fields.tsv"),
        "edges_path": str(output_dir / f"lua_lscript_module_{slug}_netlogic_flow_edges.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "filters": {
            "module": module,
            "group": group_key,
            "max_functions": max_functions,
        },
        "counts": {
            "netlogic_files": len(netlogic_files),
            "functions": len(function_rows),
            "field_assignments": len(field_rows),
            "edges": len(edge_rows),
            "function_kinds": dict(by_kind.most_common()),
            "edge_kinds": dict(by_edge.most_common()),
        },
        "functions": function_rows[:120],
    }
    (output_dir / f"lua_lscript_module_{slug}_netlogic_flow_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_lua_lscript_module_protocol_schema_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    module: str,
    group: str,
    schema_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    by_direction = Counter(str(row.get("direction") or "") for row in schema_rows)
    by_status = Counter(str(row.get("assignment_status") or "") for row in schema_rows)
    lines = [
        f"# Lua lscript module protocol schema report: {module}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- Export root: `{export_root}`",
        f"- Group: `{group}`",
        f"- Module: `{module}`",
        f"- Packets joined: {len(schema_rows)}",
        f"- Field rows joined: {len(field_rows)}",
        f"- Directions: {', '.join(f'{key}:{value}' for key, value in by_direction.most_common())}",
        f"- Assignment statuses: {', '.join(f'{key}:{value}' for key, value in by_status.most_common())}",
        "",
        "This report joins one module's NetLogic flow to the rebuilt Lua packet index. It is a schema/static-code alignment report, not runtime traffic.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Packet Schema Join",
            "",
            "| Direction | Packet | Id | Fields | NetLogic Functions | Assigned Fields | Status |",
            "| --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in schema_rows[:120]:
        lines.append(
            f"| `{row.get('direction', '')}` | `{row.get('packet_name', '')}` | {row.get('packet_id', '')} | "
            f"{row.get('field_count', '')} | `{str(row.get('netlogic_functions', '')).replace('|', '\\|')}` | "
            f"`{str(row.get('assigned_fields', '')).replace('|', '\\|')}` | `{row.get('assignment_status', '')}` |"
        )

    lines.extend(
        [
            "",
            "## Field Rows",
            "",
            "| Packet | Index | Field | Read Method | Type Hint | Assigned In NetLogic |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in field_rows[:160]:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('field_index', '')} | `{row.get('field_name', '')}` | "
            f"`{row.get('read_method', '')}` | `{row.get('type_hint', '')}` | `{row.get('assigned_in_netlogic', '')}` |"
        )

    slug = _safe_report_slug(module)
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Schemas TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_protocol_schemas.tsv', export_root)}`",
            f"- Fields TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_protocol_fields.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / f'lua_lscript_module_{slug}_protocol_schema_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_lscript_module_protocol_schema_probe(
    *,
    module: str,
    group: str = "gamesystem/game",
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Join one module's NetLogic packet refs to the rebuilt Lua packet schema index."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    module_key = module.strip()
    group_key = group.strip().strip("/\\")
    if not module_key:
        raise FanxiuResourceError("module 不能为空")

    flow_result = build_fanxiu_lua_lscript_module_netlogic_flow_probe(
        module=module_key,
        group=group_key,
        export_root=root,
    )
    function_rows = _read_tsv_rows(Path(str(flow_result["functions_path"])))

    packet_index_dir = root / "parsed_configs" / "lua_packet_index"
    packets_path = packet_index_dir / "packets.tsv"
    fields_path = packet_index_dir / "packet_fields.tsv"
    if not packets_path.is_file() or not fields_path.is_file():
        build_fanxiu_lua_packet_index(export_root=root)
    packet_rows_raw = _read_tsv_rows(packets_path)
    packet_field_rows_raw = _read_tsv_rows(fields_path)

    packet_to_functions: dict[str, set[str]] = {}
    packet_to_assigned_fields: dict[str, set[str]] = {}
    for row in function_rows:
        function_name = str(row.get("function_name") or "")
        packet_names: list[str] = []
        if function_name.startswith(("CM_", "SM_")) and function_name.endswith("Fun"):
            packet_name = function_name
            while packet_name.endswith("Fun"):
                packet_name = packet_name.removesuffix("Fun")
            packet_names.append(packet_name)
        packet_names.extend(
            item.strip()
            for item in str(row.get("packet_refs") or "").split(" | ")
            if item.strip() and not item.strip().endswith("Fun")
        )
        for packet_name in dict.fromkeys(packet_names):
            packet_to_functions.setdefault(packet_name, set()).add(function_name)
        for assigned in str(row.get("assigned_fields") or "").split(" | "):
            assigned = assigned.strip()
            if "." not in assigned:
                continue
            packet_name, field_name = assigned.split(".", 1)
            packet_to_assigned_fields.setdefault(packet_name, set()).add(field_name)

    wanted_packets = set(packet_to_functions)
    packet_by_name: dict[str, dict[str, Any]] = {}
    for row in packet_rows_raw:
        packet_name = str(row.get("name") or "")
        if packet_name in wanted_packets and packet_name not in packet_by_name:
            packet_by_name[packet_name] = row

    fields_by_packet: dict[str, list[dict[str, Any]]] = {}
    seen_field_keys: set[tuple[str, str, str, str, str]] = set()
    for row in packet_field_rows_raw:
        packet_name = str(row.get("packet_name") or "")
        if packet_name not in wanted_packets:
            continue
        key = (
            packet_name,
            str(row.get("field_index") or ""),
            str(row.get("field_name") or ""),
            str(row.get("read_method") or ""),
            str(row.get("type_hint") or ""),
        )
        if key in seen_field_keys:
            continue
        seen_field_keys.add(key)
        fields_by_packet.setdefault(packet_name, []).append(row)

    def packet_sort_key(packet_name: str) -> tuple[int, str]:
        packet = packet_by_name.get(packet_name, {})
        try:
            packet_id = int(packet.get("id") or 0)
        except ValueError:
            packet_id = 0
        return packet_id, packet_name

    schema_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    for packet_name in sorted(wanted_packets, key=packet_sort_key):
        packet = packet_by_name.get(packet_name, {})
        packet_fields = fields_by_packet.get(packet_name, [])
        schema_field_names = {str(row.get("field_name") or "") for row in packet_fields if row.get("field_name")}
        assigned_fields = packet_to_assigned_fields.get(packet_name, set())
        missing_assigned = sorted(field for field in assigned_fields if field not in schema_field_names)
        if not packet:
            assignment_status = "missing_packet_schema"
        elif missing_assigned:
            assignment_status = "assigned_field_missing_from_schema"
        elif assigned_fields:
            assignment_status = "assigned_fields_match_schema"
        elif schema_field_names and str(packet.get("direction") or "") == "client_to_server":
            assignment_status = "schema_fields_no_static_assignment"
        else:
            assignment_status = "no_client_assignments"
        functions = sorted(packet_to_functions.get(packet_name, set()))
        schema_rows.append(
            {
                "group": group_key,
                "module": module_key,
                "packet_name": packet_name,
                "packet_id": packet.get("id", ""),
                "direction": packet.get("direction", _direction_for_name(packet_name)),
                "packet_module": packet.get("module", ""),
                "field_count": packet.get("field_count", len(packet_fields)),
                "base_class": packet.get("base_class", ""),
                "relative_path": packet.get("relative_path", ""),
                "package": packet.get("package", ""),
                "netlogic_functions": " | ".join(functions),
                "assigned_fields": " | ".join(sorted(assigned_fields)),
                "schema_fields": " | ".join(str(row.get("field_name") or "") for row in packet_fields),
                "missing_assigned_fields": " | ".join(missing_assigned),
                "assignment_status": assignment_status,
            }
        )
        for field in packet_fields:
            field_name = str(field.get("field_name") or "")
            field_rows.append(
                {
                    "group": group_key,
                    "module": module_key,
                    "packet_name": packet_name,
                    "packet_id": packet.get("id", field.get("packet_id", "")),
                    "direction": packet.get("direction", field.get("direction", "")),
                    "packet_module": packet.get("module", field.get("module", "")),
                    "field_index": field.get("field_index", ""),
                    "field_name": field_name,
                    "read_method": field.get("read_method", ""),
                    "type_hint": field.get("type_hint", ""),
                    "line": field.get("line", ""),
                    "assigned_in_netlogic": "yes" if field_name in assigned_fields else "",
                    "netlogic_functions": " | ".join(functions),
                    "relative_path": field.get("relative_path", ""),
                }
            )

    slug = _safe_report_slug(module_key)
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_protocol_schemas.tsv",
        schema_rows,
        [
            "group",
            "module",
            "packet_name",
            "packet_id",
            "direction",
            "packet_module",
            "field_count",
            "base_class",
            "relative_path",
            "package",
            "netlogic_functions",
            "assigned_fields",
            "schema_fields",
            "missing_assigned_fields",
            "assignment_status",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_protocol_fields.tsv",
        field_rows,
        [
            "group",
            "module",
            "packet_name",
            "packet_id",
            "direction",
            "packet_module",
            "field_index",
            "field_name",
            "read_method",
            "type_hint",
            "line",
            "assigned_in_netlogic",
            "netlogic_functions",
            "relative_path",
        ],
    )

    checks = {
        "netlogic_flow_confirmed": bool(flow_result.get("confirmed")),
        "packet_index_visible": bool(packet_rows_raw),
        "packet_fields_visible": bool(packet_field_rows_raw),
        "packet_refs_joined": bool(schema_rows),
        "no_missing_packet_schema": all(row.get("assignment_status") != "missing_packet_schema" for row in schema_rows),
        "assigned_fields_align": all(row.get("assignment_status") != "assigned_field_missing_from_schema" for row in schema_rows),
    }
    report_path = output_dir / f"lua_lscript_module_{slug}_protocol_schema_report.md"
    _write_lua_lscript_module_protocol_schema_markdown(
        report_path,
        export_root=root,
        output_dir=output_dir,
        module=module_key,
        group=group_key,
        schema_rows=schema_rows,
        field_rows=field_rows,
        checks=checks,
    )

    by_status = Counter(str(row.get("assignment_status") or "") for row in schema_rows)
    by_direction = Counter(str(row.get("direction") or "") for row in schema_rows)
    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(report_path),
        "schemas_path": str(output_dir / f"lua_lscript_module_{slug}_protocol_schemas.tsv"),
        "fields_path": str(output_dir / f"lua_lscript_module_{slug}_protocol_fields.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "packets": len(schema_rows),
            "fields": len(field_rows),
            "assignment_statuses": dict(by_status.most_common()),
            "directions": dict(by_direction.most_common()),
        },
        "schemas": schema_rows[:120],
    }
    (output_dir / f"lua_lscript_module_{slug}_protocol_schema_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_lua_lscript_module_packet_pair_flow_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    module: str,
    group: str,
    request_packet: str,
    response_packet: str,
    packet_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    consumed_msg_field_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    by_edge = Counter(str(row.get("category") or "") for row in edge_rows)
    by_msg_status = Counter(str(row.get("status") or "") for row in consumed_msg_field_rows)
    lines = [
        f"# Lua lscript module packet pair flow report: {module}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- Export root: `{export_root}`",
        f"- Group: `{group}`",
        f"- Module: `{module}`",
        f"- Request packet: `{request_packet}`",
        f"- Response packet: `{response_packet}`",
        f"- Packet rows: {len(packet_rows)}",
        f"- Field rows: {len(field_rows)}",
        f"- Flow edges: {len(edge_rows)}",
        f"- Source rows: {len(source_rows)}",
        f"- Edge kinds: {', '.join(f'{key}:{value}' for key, value in by_edge.most_common())}",
        "",
        "This report focuses one CM/SM pair by joining module protocol schemas, NetLogic edges, and local Lua source slices.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Packet Rows",
            "",
            "| Direction | Packet | Id | Fields | Assigned Fields | Status |",
            "| --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in packet_rows:
        lines.append(
            f"| `{row.get('direction', '')}` | `{row.get('packet_name', '')}` | {row.get('packet_id', '')} | "
            f"{row.get('field_count', '')} | `{str(row.get('assigned_fields', '')).replace('|', '\\|')}` | "
            f"`{row.get('assignment_status', '')}` |"
        )

    lines.extend(
        [
            "",
            "## Fields",
            "",
            "| Packet | Index | Field | Read Method | Assigned |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('field_index', '')} | `{row.get('field_name', '')}` | "
            f"`{row.get('read_method', '')}` | `{row.get('assigned_in_netlogic', '')}` |"
        )

    lines.extend(
        [
            "",
            "## Response Msg Field Consumers",
            "",
            "This table compares `msg.xxx` fields read by the selected response handler and sliced downstream functions against the response packet schema. "
            "It is a static consistency signal; `code` is inherited from the base result class and is not expected in packet-specific schema rows.",
            "",
            f"- Statuses: {', '.join(f'{key}:{value}' for key, value in by_msg_status.most_common()) or 'none'}",
            "",
            "| Kind | Function | Field | Status | Line | Snippet |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in consumed_msg_field_rows[:120]:
        lines.append(
            f"| `{row.get('source_kind', '')}` | `{row.get('function_name', '')}` | `{row.get('field_name', '')}` | `{row.get('status', '')}` | "
            f"{row.get('line', '')} | `{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Flow Edges",
            "",
            "| Function | Category | Target | Line | Snippet |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in edge_rows[:120]:
        lines.append(
            f"| `{row.get('function_name', '')}` | `{row.get('category', '')}` | `{str(row.get('target', '')).replace('|', '\\|')}` | "
            f"{row.get('line', '')} | `{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    lines.extend(
        [
            "",
            "## Source Slice Sample",
            "",
            "| Kind | Function | File | Line | Snippet |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in source_rows[:120]:
        lines.append(
            f"| `{row.get('source_kind', '')}` | `{row.get('function_name', '')}` | `{row.get('asset_name', '')}` | "
            f"{row.get('line', '')} | `{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )

    slug = _safe_report_slug(f"{module}_{request_packet}_{response_packet}")
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Packets TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_pair_packets.tsv', export_root)}`",
            f"- Fields TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_pair_fields.tsv', export_root)}`",
            f"- Msg fields TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_pair_msg_fields.tsv', export_root)}`",
            f"- Edges TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_pair_edges.tsv', export_root)}`",
            f"- Sources TSV: `{_report_path(output_dir / f'lua_lscript_module_{slug}_pair_sources.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / f'lua_lscript_module_{slug}_pair_flow_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_lscript_module_packet_pair_flow_probe(
    *,
    module: str,
    request_packet: str,
    response_packet: str | None = None,
    group: str = "gamesystem/game",
    export_root: str | Path | None = None,
    max_source_lines: int = 80,
) -> dict[str, Any]:
    """Drill one CM/SM pair into schema rows, NetLogic edges, and local source slices."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    module_key = module.strip()
    request_name = request_packet.strip()
    response_name = (response_packet or "").strip()
    if not module_key or not request_name:
        raise FanxiuResourceError("module 和 request_packet 不能为空")
    if not response_name and request_name.startswith("CM_"):
        response_name = f"SM_{request_name[3:]}"
    group_key = group.strip().strip("/\\")

    schema_result = build_fanxiu_lua_lscript_module_protocol_schema_probe(
        module=module_key,
        group=group_key,
        export_root=root,
    )
    schema_rows = _read_tsv_rows(Path(str(schema_result["schemas_path"])))
    field_rows_all = _read_tsv_rows(Path(str(schema_result["fields_path"])))
    flow_result = build_fanxiu_lua_lscript_module_netlogic_flow_probe(
        module=module_key,
        group=group_key,
        export_root=root,
    )
    edge_rows_all = _read_tsv_rows(Path(str(flow_result["edges_path"])))

    selected_packets = {request_name, response_name}
    packet_rows = [row for row in schema_rows if row.get("packet_name") in selected_packets]
    field_rows = [row for row in field_rows_all if row.get("packet_name") in selected_packets]

    function_names: set[str] = set()
    for row in packet_rows:
        packet_name = str(row.get("packet_name") or "")
        for function_name in str(row.get("netlogic_functions") or "").split(" | "):
            function_name = function_name.strip()
            if function_name.startswith(packet_name) and function_name.endswith("Fun"):
                function_names.add(function_name)
    edge_rows = [row for row in edge_rows_all if row.get("function_name") in function_names]

    module_files = _iter_lscript_module_lua_files(root, group_key, module_key)
    netlogic_files = [path for path in module_files if "netlogic" in path.name.lower()]
    source_rows: list[dict[str, Any]] = []
    source_keys: set[tuple[str, str]] = set()
    for function_name in sorted(function_names):
        for path in netlogic_files:
            rows = _source_rows_for_lua_function(
                root=root,
                path=path,
                module=module_key,
                group=group_key,
                source_kind="netlogic_function",
                function_name=function_name,
                max_lines=max_source_lines,
            )
            if rows:
                source_rows.extend(rows)
                source_keys.update((str(row.get("relative_path") or ""), str(row.get("function_name") or "")) for row in rows)
                break
    seen_targets: set[str] = set()
    for edge in edge_rows:
        if edge.get("category") not in {"manager_call", "model_call"}:
            continue
        target = str(edge.get("target") or "")
        if target in seen_targets:
            continue
        seen_targets.add(target)
        target_rows = _source_rows_for_target_function(
            root=root,
            module_files=module_files,
            module=module_key,
            group=group_key,
            target=target,
            max_lines=max_source_lines,
        )
        fresh_rows = [
            row
            for row in target_rows
            if (str(row.get("relative_path") or ""), str(row.get("function_name") or "")) not in source_keys
        ]
        source_rows.extend(fresh_rows)
        source_keys.update((str(row.get("relative_path") or ""), str(row.get("function_name") or "")) for row in fresh_rows)
    source_rows.extend(
        _source_rows_for_related_method_calls(
            root=root,
            module_files=module_files,
            module=module_key,
            group=group_key,
            seed_rows=source_rows,
            existing_keys=source_keys,
            max_lines=max_source_lines,
        )
    )

    response_schema_fields = {
        str(row.get("field_name") or "")
        for row in field_rows
        if row.get("packet_name") == response_name and row.get("field_name")
    }
    response_function_names = {
        function_name
        for function_name in function_names
        if response_name and function_name.startswith(response_name) and function_name.endswith("Fun")
    }
    base_result_fields = {"code"}
    consumed_msg_field_rows: list[dict[str, Any]] = []
    seen_consumed_keys: set[tuple[str, str, str, str, str]] = set()
    for row in source_rows:
        source_kind = str(row.get("source_kind") or "")
        function_name = str(row.get("function_name") or "")
        is_response_handler = source_kind == "netlogic_function" and function_name in response_function_names
        is_downstream_slice = source_kind in {"target_function", "related_function"}
        if not (is_response_handler or is_downstream_slice):
            continue
        snippet = str(row.get("snippet") or "")
        for match in re.finditer(r"\bmsg\.([A-Za-z_][A-Za-z0-9_]*)", snippet):
            field_name = match.group(1)
            key = (
                source_kind,
                str(row.get("relative_path") or ""),
                function_name,
                field_name,
                str(row.get("line") or ""),
            )
            if key in seen_consumed_keys:
                continue
            seen_consumed_keys.add(key)
            if field_name in response_schema_fields:
                status = "schema_field"
            elif field_name in base_result_fields:
                status = "base_result_field"
            else:
                status = "missing_from_packet_schema"
            consumed_msg_field_rows.append(
                {
                    "group": group_key,
                    "module": module_key,
                    "packet_name": response_name,
                    "source_kind": source_kind,
                    "function_name": function_name,
                    "field_name": field_name,
                    "status": status,
                    "line": row.get("line", ""),
                    "snippet": snippet,
                    "relative_path": row.get("relative_path", ""),
                }
            )

    slug = _safe_report_slug(f"{module_key}_{request_name}_{response_name}")
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_pair_packets.tsv",
        packet_rows,
        [
            "group",
            "module",
            "packet_name",
            "packet_id",
            "direction",
            "packet_module",
            "field_count",
            "base_class",
            "relative_path",
            "package",
            "netlogic_functions",
            "assigned_fields",
            "schema_fields",
            "missing_assigned_fields",
            "assignment_status",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_pair_fields.tsv",
        field_rows,
        [
            "group",
            "module",
            "packet_name",
            "packet_id",
            "direction",
            "packet_module",
            "field_index",
            "field_name",
            "read_method",
            "type_hint",
            "line",
            "assigned_in_netlogic",
            "netlogic_functions",
            "relative_path",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_pair_msg_fields.tsv",
        consumed_msg_field_rows,
        [
            "group",
            "module",
            "packet_name",
            "source_kind",
            "function_name",
            "field_name",
            "status",
            "line",
            "snippet",
            "relative_path",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_pair_edges.tsv",
        edge_rows,
        [
            "group",
            "module",
            "bundle",
            "asset_name",
            "relative_path",
            "function_name",
            "function_kind",
            "line",
            "category",
            "target",
            "snippet",
        ],
    )
    _write_tsv(
        output_dir / f"lua_lscript_module_{slug}_pair_sources.tsv",
        source_rows,
        ["group", "module", "source_kind", "asset_name", "relative_path", "function_name", "line", "snippet"],
    )

    checks = {
        "protocol_schema_confirmed": bool(schema_result.get("confirmed")),
        "netlogic_flow_confirmed": bool(flow_result.get("confirmed")),
        "request_packet_joined": any(row.get("packet_name") == request_name for row in packet_rows),
        "response_packet_joined": bool(not response_name) or any(row.get("packet_name") == response_name for row in packet_rows),
        "netlogic_functions_found": bool(function_names),
        "flow_edges_found": bool(edge_rows),
        "source_rows_found": bool(source_rows),
    }
    report_path = output_dir / f"lua_lscript_module_{slug}_pair_flow_report.md"
    _write_lua_lscript_module_packet_pair_flow_markdown(
        report_path,
        export_root=root,
        output_dir=output_dir,
        module=module_key,
        group=group_key,
        request_packet=request_name,
        response_packet=response_name,
        packet_rows=packet_rows,
        field_rows=field_rows,
        edge_rows=edge_rows,
        source_rows=source_rows,
        consumed_msg_field_rows=consumed_msg_field_rows,
        checks=checks,
    )

    by_edge = Counter(str(row.get("category") or "") for row in edge_rows)
    by_msg_status = Counter(str(row.get("status") or "") for row in consumed_msg_field_rows)
    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(report_path),
        "packets_path": str(output_dir / f"lua_lscript_module_{slug}_pair_packets.tsv"),
        "fields_path": str(output_dir / f"lua_lscript_module_{slug}_pair_fields.tsv"),
        "msg_fields_path": str(output_dir / f"lua_lscript_module_{slug}_pair_msg_fields.tsv"),
        "edges_path": str(output_dir / f"lua_lscript_module_{slug}_pair_edges.tsv"),
        "sources_path": str(output_dir / f"lua_lscript_module_{slug}_pair_sources.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "filters": {
            "module": module_key,
            "group": group_key,
            "request_packet": request_name,
            "response_packet": response_name,
            "max_source_lines": max_source_lines,
        },
        "counts": {
            "packet_rows": len(packet_rows),
            "field_rows": len(field_rows),
            "edge_rows": len(edge_rows),
            "source_rows": len(source_rows),
            "edge_kinds": dict(by_edge.most_common()),
            "msg_field_statuses": dict(by_msg_status.most_common()),
            "netlogic_functions": len(function_names),
        },
        "packets": packet_rows,
        "edges": edge_rows[:80],
        "msg_fields": consumed_msg_field_rows[:80],
    }
    (output_dir / f"lua_lscript_module_{slug}_pair_flow_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_login_finish_post_sync_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, Any]],
    sync_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua login finish post-sync report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report traces the first visible Lua actions after the login finish gate calls `LoginFinishGetServerData()`.",
        "- The flow sends `CM_LoginFinish(20007)` before raising `LoginSucceed`, then fans out into event sync, medicine/client attributes, subpackage init, partner battlefield buffs, world-level worship info, and a conditional travel/Youli pool sync.",
        "- Some entries close to concrete Lua packets; others are currently only confirmed manager calls in the exported Lua surface.",
        "",
        "Reconstructed chain:",
        "",
        "```text",
        "EnterGameInfo.CheckAndSendFinish()",
        "  -> LoginNetLogic.CM_LoginFinishSent()",
        "  -> LoginModel.RaiseEvent(LoginSucceed)",
        "  -> EnterGameInfo.LoginFinishGetServerData()",
        "       -> EventMgr.NetLogic.CM_EventSyncAllFun()",
        "       -> MedicineMgr.GetClientAttributes()",
        "       -> SubpackageMgr.StartInit()",
        "       -> XianLvMinesMgr.NetLogic.CM_SyncBattleFieldBuffsFun()",
        "       -> WorldlevelMgr.NetLogic.CM_WorldLevelWorshipInfoSyncFun()",
        "       -> if Travel open: YoulipoolMgr.NetLogic.CM_YouliPoolInfoFun()",
        "       -> PhoneHelper.F_UploadThinkingLaunchProcess(GameLogin_Success)",
        "```",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Post-login Sync Entries",
            "",
            "| Entry | Kind | Call | Request Packet | Response Packet | Packet Status | Note |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in sync_rows:
        lines.append(
            f"| `{row.get('entry', '')}` | {row.get('kind', '')} | `{row.get('call', '')}` | "
            f"`{row.get('request_packet', '')}` | `{row.get('response_packet', '')}` | "
            f"{row.get('packet_status', '')} | {str(row.get('note', '')).replace('|', '\\|')} |"
        )

    lines.extend(["", "## Response Packet Fields", "", "| Packet | ID | # | Field | Wire Method | Type Hint | Source |", "| --- | ---: | ---: | --- | --- | --- | --- |"])
    for row in field_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('packet_id', '')} | {row.get('field_index', '')} | "
            f"`{row.get('field_name', '')}` | `{row.get('read_method', '')}` | `{row.get('type_hint', '')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` |"
        )

    lines.extend(["", "## Key Evidence", "", "| Stage | Kind | Source | Line | Marker | Snippet |", "| --- | --- | --- | ---: | --- | --- |"])
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Evidence TSV: `{_report_path(output_dir / 'lua_login_finish_post_sync_evidence.tsv', export_root)}`",
            f"- Sync entries TSV: `{_report_path(output_dir / 'lua_login_finish_post_sync_entries.tsv', export_root)}`",
            f"- Packet fields TSV: `{_report_path(output_dir / 'lua_login_finish_post_sync_packet_fields.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_login_finish_post_sync_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_login_finish_post_sync_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Trace the first visible Lua sync fanout after CM_LoginFinish/LoginSucceed."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    enter_game_info = _find_lua_text_asset(root, "EnterGameInfo.lua", ("LoginFinishGetServerData", "CM_LoginFinishSent", "LoginSucceed"))
    login_net_logic = _find_lua_text_asset(root, "LoginNetLogic.lua", ("CM_LoginFinishSent", "CM_LoginFinish"))
    worldlevel_net_logic = _find_lua_text_asset(root, "WorldlevelNetLogic.lua", ("CM_WorldLevelWorshipInfoSyncFun", "SM_WorldLevelWorshipInfoSyncFun"))
    lua_initializer = _find_lua_text_asset(root, "LuaInitializer.lua", ("InitByFunctionOpen", "CM_YouliPoolInfoFun"))
    vo_url = _find_lua_text_asset(root, "VO_URL.lua", ("CM_LoginFinish", "CM_EventSyncAll", "CM_WorldLevelWorshipInfoSync"))

    packet_paths = {
        "CM_LoginFinish": _find_lua_text_asset(root, "CM_LoginFinish.lua", ("return 20007", "CM_LoginFinish")),
        "CM_EventSyncAll": _find_lua_text_asset(root, "CM_EventSyncAll.lua", ("return 38005", "CM_EventSyncAll")),
        "SM_EventSyncAll": _find_lua_text_asset(root, "SM_EventSyncAll.lua", ("return 38006", "self:readMessageList2List(self.events)")),
        "CM_SyncBattleFieldBuffs": _find_lua_text_asset(root, "CM_SyncBattleFieldBuffs.lua", ("return 87641", "CM_SyncBattleFieldBuffs")),
        "SM_SyncBattleFieldBuffs": _find_lua_text_asset(
            root,
            "SM_SyncBattleFieldBuffs.lua",
            ("return 87642", "activityBaseIdToBuff"),
        ),
        "CM_WorldLevelWorshipInfoSync": _find_lua_text_asset(
            root,
            "CM_WorldLevelWorshipInfoSync.lua",
            ("return 10083", "CM_WorldLevelWorshipInfoSync"),
        ),
        "SM_WorldLevelWorshipInfoSync": _find_lua_text_asset(
            root,
            "SM_WorldLevelWorshipInfoSync.lua",
            ("return 10084", "worshipTimes", "rewardItems"),
        ),
        "CM_YouliPoolInfo": _find_lua_text_asset(root, "CM_YouliPoolInfo.lua", ("return 14371", "CM_YouliPoolInfo")),
        "SM_YouliPoolInfo": _find_lua_text_asset(root, "SM_YouliPoolInfo.lua", ("return 14372", "infoMap")),
    }

    evidence_rows: list[dict[str, Any]] = []
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="00-finish-gate",
        kind="lua",
        section_marker="function _M.CheckAndSendFinish(self)",
        markers=(
            "LoginMgr.Inst_get().LoginNetLogic.CM_LoginFinishSent()",
            "LoginMgr.Inst_get().LoginModel:RaiseEvent(LoginType.EventType.LoginSucceed)",
            "self.LoginFinishGetServerData()",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=login_net_logic,
        export_root=root,
        stage="01-send-login-finish",
        kind="lua",
        section_marker="function _M.CM_LoginFinishSent()",
        markers=(
            "local CM_LoginFinish=SocketManager.Inst_get():GetMessageFromPools(_CM_LoginFinish)",
            "SocketManager.Inst_get():F_SendMsg(CM_LoginFinish)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=enter_game_info,
        export_root=root,
        stage="02-post-sync-fanout",
        kind="lua",
        section_marker="function _M.LoginFinishGetServerData()",
        markers=(
            "EventMgr.Inst_get().NetLogic:CM_EventSyncAllFun()",
            "MedicineMgr.Inst_get():GetClientAttributes()",
            "SubpackageMgr.Inst_get():StartInit()",
            "XianLvMinesMgr.Inst_get().NetLogic:CM_SyncBattleFieldBuffsFun()",
            "WorldlevelMgr.Inst_get().NetLogic:CM_WorldLevelWorshipInfoSyncFun()",
            "if FunctionMgr.Inst_get():CheckFunctionOpen(FunctionType.Travel)then",
            "YoulipoolMgr.Inst_get().NetLogic:CM_YouliPoolInfoFun()",
            "PhoneHelper.F_UploadThinkingLaunchProcess(SdkLaunchEventType.GameLogin_Success,\"\")",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=worldlevel_net_logic,
        export_root=root,
        stage="03-worldlevel-send",
        kind="lua",
        section_marker="function _M.CM_WorldLevelWorshipInfoSyncFun(self)",
        markers=(
            "local CM_WorldLevelWorshipInfoSync=SocketManager.Inst_get():GetMessageFromPools(_CM_WorldLevelWorshipInfoSync)",
            "SocketManager.Inst_get():F_SendMsg(CM_WorldLevelWorshipInfoSync)",
        ),
    )
    _append_probe_evidence_in_section(
        evidence_rows,
        path=lua_initializer,
        export_root=root,
        stage="04-travel-feature-init",
        kind="lua",
        section_marker="function _M.GameStartInit()",
        markers=(
            "FunctionMgr.Inst_get().Model:InitByFunctionOpen(FunctionType.Travel,function()",
            "YoulipoolMgr.Inst_get().NetLogic:CM_YouliPoolInfoFun()",
        ),
        end_markers=("return _M",),
    )
    _append_probe_evidence(
        evidence_rows,
        path=vo_url,
        export_root=root,
        stage="05-packet-id-map",
        kind="lua",
        markers=(
            "['20007']=setmetatable({'20007','module.user.login.packet.CM_LoginFinish',},_o),",
            "['38005']=setmetatable({'38005','module.player.event.packet.CM_EventSyncAll',},_o),",
            "['87641']=setmetatable({'87641','module.player.partner.packet.CM_SyncBattleFieldBuffs',},_o),",
            "['10083']=setmetatable({'10083','module.world.worldlevel.packet.CM_WorldLevelWorshipInfoSync',},_o),",
            "['14371']=setmetatable({'14371','module.player.inner.youlipool.packet.CM_YouliPoolInfo',},_o),",
        ),
    )

    summaries = {name: _packet_summary_for_path(root, path) for name, path in packet_paths.items()}

    def packet_label(name: str) -> str:
        item = summaries.get(name) or {}
        packet_id = item.get("packet_id")
        return f"{name}({packet_id})" if packet_id not in {"", None} else name

    sync_rows = [
        {
            "entry": "login_finish_ack",
            "kind": "socket_request",
            "call": "LoginNetLogic.CM_LoginFinishSent()",
            "request_packet": packet_label("CM_LoginFinish"),
            "response_packet": "",
            "packet_status": "closed_request_send",
            "note": "Sent before LoginSucceed fanout; packet has no visible payload fields.",
        },
        {
            "entry": "event_sync_all",
            "kind": "socket_request_response",
            "call": "EventMgr.NetLogic.CM_EventSyncAllFun()",
            "request_packet": packet_label("CM_EventSyncAll"),
            "response_packet": packet_label("SM_EventSyncAll"),
            "packet_status": "packet_ids_mapped",
            "note": "Exported Lua contains the post-login call and packet classes; the concrete NetLogic function body is not visible in the current export.",
        },
        {
            "entry": "medicine_client_attributes",
            "kind": "manager_call",
            "call": "MedicineMgr.GetClientAttributes()",
            "request_packet": "",
            "response_packet": "",
            "packet_status": "manager_call_only",
            "note": "Only the manager call is visible in this probe; no concrete Lua packet was pinned.",
        },
        {
            "entry": "subpackage_start_init",
            "kind": "manager_call",
            "call": "SubpackageMgr.StartInit()",
            "request_packet": "",
            "response_packet": "",
            "packet_status": "manager_call_only",
            "note": "Resource/subpackage initialization, not a game business packet in this static surface.",
        },
        {
            "entry": "xianlv_mines_battlefield_buffs",
            "kind": "socket_request_response",
            "call": "XianLvMinesMgr.NetLogic.CM_SyncBattleFieldBuffsFun()",
            "request_packet": packet_label("CM_SyncBattleFieldBuffs"),
            "response_packet": packet_label("SM_SyncBattleFieldBuffs"),
            "packet_status": "packet_ids_mapped",
            "note": "Packet class/schema is visible; the concrete NetLogic send function body is not visible in the current export.",
        },
        {
            "entry": "worldlevel_worship_info",
            "kind": "socket_request_response",
            "call": "WorldlevelMgr.NetLogic.CM_WorldLevelWorshipInfoSyncFun()",
            "request_packet": packet_label("CM_WorldLevelWorshipInfoSync"),
            "response_packet": packet_label("SM_WorldLevelWorshipInfoSync"),
            "packet_status": "closed_request_send",
            "note": "WorldlevelNetLogic sends the request and registers the response handler in exported Lua.",
        },
        {
            "entry": "travel_youlipool_info",
            "kind": "conditional_socket_request_response",
            "call": "YoulipoolMgr.NetLogic.CM_YouliPoolInfoFun()",
            "request_packet": packet_label("CM_YouliPoolInfo"),
            "response_packet": packet_label("SM_YouliPoolInfo"),
            "packet_status": "conditional_packet_ids_mapped",
            "note": "Only called when FunctionType.Travel is open; LuaInitializer has a second feature-open callback path.",
        },
        {
            "entry": "sdk_launch_success",
            "kind": "sdk_event",
            "call": "PhoneHelper.F_UploadThinkingLaunchProcess(GameLogin_Success)",
            "request_packet": "",
            "response_packet": "",
            "packet_status": "non_socket_analytics",
            "note": "Launch analytics/reporting marker, not a gameplay request.",
        },
    ]

    field_rows = _packet_field_rows(
        root,
        [
            packet_paths["SM_EventSyncAll"],
            packet_paths["SM_SyncBattleFieldBuffs"],
            packet_paths["SM_WorldLevelWorshipInfoSync"],
            packet_paths["SM_YouliPoolInfo"],
        ],
    )
    checks = {
        "finish_gate_calls_post_sync": _probe_has(evidence_rows, "00-finish-gate", "self.LoginFinishGetServerData()")
        and _probe_has(evidence_rows, "01-send-login-finish", "SocketManager.Inst_get():F_SendMsg(CM_LoginFinish)"),
        "core_post_sync_calls_present": all(
            _probe_has(evidence_rows, "02-post-sync-fanout", marker)
            for marker in (
                "EventMgr.Inst_get().NetLogic:CM_EventSyncAllFun()",
                "MedicineMgr.Inst_get():GetClientAttributes()",
                "SubpackageMgr.Inst_get():StartInit()",
                "XianLvMinesMgr.Inst_get().NetLogic:CM_SyncBattleFieldBuffsFun()",
                "WorldlevelMgr.Inst_get().NetLogic:CM_WorldLevelWorshipInfoSyncFun()",
            )
        ),
        "travel_sync_is_conditional": _probe_has(
            evidence_rows,
            "02-post-sync-fanout",
            "if FunctionMgr.Inst_get():CheckFunctionOpen(FunctionType.Travel)then",
        )
        and _probe_has(evidence_rows, "02-post-sync-fanout", "YoulipoolMgr.Inst_get().NetLogic:CM_YouliPoolInfoFun()"),
        "worldlevel_send_closed": _probe_has(
            evidence_rows,
            "03-worldlevel-send",
            "SocketManager.Inst_get():F_SendMsg(CM_WorldLevelWorshipInfoSync)",
        ),
        "packet_ids_mapped": all(
            summaries.get(name, {}).get("packet_id") not in {"", None}
            for name in (
                "CM_LoginFinish",
                "CM_EventSyncAll",
                "SM_EventSyncAll",
                "CM_SyncBattleFieldBuffs",
                "SM_SyncBattleFieldBuffs",
                "CM_WorldLevelWorshipInfoSync",
                "SM_WorldLevelWorshipInfoSync",
                "CM_YouliPoolInfo",
                "SM_YouliPoolInfo",
            )
        ),
        "response_fields_mapped": {row.get("packet_name") for row in field_rows}
        >= {"SM_EventSyncAll", "SM_SyncBattleFieldBuffs", "SM_WorldLevelWorshipInfoSync", "SM_YouliPoolInfo"},
    }

    _write_tsv(
        output_dir / "lua_login_finish_post_sync_evidence.tsv",
        evidence_rows,
        ["stage", "kind", "source", "line", "marker", "snippet"],
    )
    _write_tsv(
        output_dir / "lua_login_finish_post_sync_entries.tsv",
        sync_rows,
        ["entry", "kind", "call", "request_packet", "response_packet", "packet_status", "note"],
    )
    _write_tsv(
        output_dir / "lua_login_finish_post_sync_packet_fields.tsv",
        field_rows,
        ["packet_id", "packet_name", "direction", "field_index", "field_name", "read_method", "type_hint", "source", "line"],
    )
    _write_login_finish_post_sync_markdown(
        output_dir / "lua_login_finish_post_sync_report.md",
        export_root=root,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        sync_rows=sync_rows,
        field_rows=field_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_login_finish_post_sync_report.md"),
        "evidence_path": str(output_dir / "lua_login_finish_post_sync_evidence.tsv"),
        "entries_path": str(output_dir / "lua_login_finish_post_sync_entries.tsv"),
        "packet_fields_path": str(output_dir / "lua_login_finish_post_sync_packet_fields.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "evidence": len(evidence_rows),
            "sync_entries": len(sync_rows),
            "response_fields": len(field_rows),
        },
        "packet_summaries": summaries,
        "sources": {
            "enter_game_info": str(enter_game_info) if enter_game_info else "",
            "login_net_logic": str(login_net_logic) if login_net_logic else "",
            "worldlevel_net_logic": str(worldlevel_net_logic) if worldlevel_net_logic else "",
            "lua_initializer": str(lua_initializer) if lua_initializer else "",
            "vo_url": str(vo_url) if vo_url else "",
        },
    }
    (output_dir / "lua_login_finish_post_sync_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_sm_login_nested_vo_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    object_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    nested_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# SM_Login nested VO report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report expands the first-level complex objects carried by `SM_Login(20002)`.",
        "- It focuses on the login snapshot objects consumed immediately by `EnterGameInfo.StartEnter_4/5`: `RoleVO`, `MapInfoVO`, `FunctionOpenVo`, `CrossVO`, and `SimpleClubVO`.",
        "- The result is a compact schema map for static reading and future privacy-filtered packet decoding.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(["", "## Objects", "", "| Object | ID | Direction | Field Count | Source |", "| --- | ---: | --- | ---: | --- |"])
    for row in object_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('packet_id', '')} | {row.get('direction', '')} | "
            f"{row.get('field_count', '')} | `{str(row.get('source', '')).replace('|', '\\|')}` |"
        )

    lines.extend(["", "## Fields", "", "| Object | # | Field | Wire Method | Type Hint | Source | Line |", "| --- | ---: | --- | --- | --- | --- | ---: |"])
    for row in field_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('field_index', '')} | `{row.get('field_name', '')}` | "
            f"`{row.get('read_method', '')}` | `{row.get('type_hint', '')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` | {row.get('line', '')} |"
        )

    lines.extend(["", "## Nested Type References", "", "| Object | Field | Wire Method | Type Hint | Meaning |", "| --- | --- | --- | --- | --- |"])
    for row in nested_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | `{row.get('field_name', '')}` | `{row.get('read_method', '')}` | "
            f"`{row.get('type_hint', '')}` | {str(row.get('meaning', '')).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Objects TSV: `{_report_path(output_dir / 'lua_sm_login_nested_vo_objects.tsv', export_root)}`",
            f"- Fields TSV: `{_report_path(output_dir / 'lua_sm_login_nested_vo_fields.tsv', export_root)}`",
            f"- Nested refs TSV: `{_report_path(output_dir / 'lua_sm_login_nested_vo_refs.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_sm_login_nested_vo_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_sm_login_nested_vo_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Expand SM_Login's first-level nested VO schemas."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    packet_paths = {
        "SM_Login": _find_lua_text_asset(root, "SM_Login.lua", ("return 20002", "RoleVO", "MapInfoVO", "FunctionOpenVo")),
        "RoleVO": _find_lua_text_asset(root, "RoleVO.lua", ("return 30051", "self.roleId=self:readLong()", "self.name=self:readString()")),
        "MapInfoVO": _find_lua_text_asset(root, "MapInfoVO.lua", ("return 40058", "self.mapId=self:readInt()", "Grid3DVO")),
        "FunctionOpenVo": _find_lua_text_asset(root, "FunctionOpenVo.lua", ("return 30710", "openFunctionClickList", "gmAll")),
        "CrossVO": _find_lua_text_asset(root, "CrossVO.lua", ("return 59603", "self.crossGroup=self:readInt()", "serverList")),
        "SimpleClubVO": _find_lua_text_asset(root, "SimpleClubVO.lua", ("return 80192", "self.name=self:readString()", "belongAlliance")),
    }

    object_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    for name, vo_path in packet_paths.items():
        item = _parse_packet_file(vo_path, root) if vo_path else None
        if not item:
            object_rows.append(
                {
                    "packet_name": name,
                    "packet_id": "",
                    "direction": "",
                    "field_count": 0,
                    "source": "<missing>",
                }
            )
            continue
        object_rows.append(
            {
                "packet_name": item["name"],
                "packet_id": item["id"],
                "direction": item["direction"],
                "field_count": item["field_count"],
                "source": _report_path(vo_path, root),
            }
        )
        for field in item["fields"]:
            field_rows.append(
                {
                    "packet_id": item["id"],
                    "packet_name": item["name"],
                    "direction": item["direction"],
                    "field_index": field["field_index"],
                    "field_name": field["field_name"],
                    "read_method": field["read_method"],
                    "type_hint": field["type_hint"],
                    "source": _report_path(vo_path, root),
                    "line": field["line"],
                }
            )

    nested_meanings = {
        ("SM_Login", "role"): "main player role snapshot",
        ("SM_Login", "mapInfo"): "scene/map restore snapshot",
        ("SM_Login", "wallet"): "wallet/currency list",
        ("SM_Login", "functionOpen"): "feature open state",
        ("SM_Login", "crossVO"): "cross-server state",
        ("SM_Login", "clubVO"): "simple club/faction info",
        ("RoleVO", "hangPoint"): "saved hang/position point",
        ("RoleVO", "face"): "avatar/face list",
        ("RoleVO", "multiCareers"): "multi-career list",
        ("RoleVO", "attributes"): "role attribute map",
        ("RoleVO", "skill"): "learned skill snapshot",
        ("RoleVO", "hideMap"): "hidden map/state dictionary",
        ("MapInfoVO", "position"): "Grid3D map position",
        ("FunctionOpenVo", "openFunctionClickList"): "clicked/open function ids",
        ("FunctionOpenVo", "openFunctionList"): "available function ids",
        ("CrossVO", "serverList"): "cross-server list",
        ("CrossVO", "prepareServerList"): "preparation server list",
        ("CrossVO", "meetCrossSceneServers"): "eligible cross-scene server ids",
        ("CrossVO", "conditionLevels"): "cross condition levels",
        ("CrossVO", "crossGroupTimeMap"): "cross-group open time map",
    }
    nested_rows = []
    for row in field_rows:
        method = str(row.get("read_method") or "")
        type_hint = str(row.get("type_hint") or "")
        key = (str(row.get("packet_name") or ""), str(row.get("field_name") or ""))
        if type_hint or "MessageList" in method or "MessageMap" in method or key in nested_meanings:
            nested_rows.append(
                {
                    "packet_name": row.get("packet_name", ""),
                    "field_name": row.get("field_name", ""),
                    "read_method": method,
                    "type_hint": type_hint,
                    "meaning": nested_meanings.get(key, ""),
                }
            )

    fields_by_object: dict[str, set[str]] = {}
    for row in field_rows:
        fields_by_object.setdefault(str(row.get("packet_name") or ""), set()).add(str(row.get("field_name") or ""))

    checks = {
        "sm_login_vo_fields_present": {"role", "mapInfo", "wallet", "functionOpen", "crossVO", "clubVO"}.issubset(
            fields_by_object.get("SM_Login", set())
        ),
        "role_vo_core_fields_present": {
            "roleId",
            "name",
            "level",
            "vipLevel",
            "battleScore",
            "finishNewRoleTask",
            "createStep",
            "createTime",
        }.issubset(fields_by_object.get("RoleVO", set())),
        "map_info_core_fields_present": {"mapId", "direction", "position", "dungeonId"}.issubset(
            fields_by_object.get("MapInfoVO", set())
        ),
        "function_open_fields_present": {"openFunctionClickList", "openFunctionList", "gmAll", "gmNormalAll"}.issubset(
            fields_by_object.get("FunctionOpenVo", set())
        ),
        "cross_vo_fields_present": {"crossGroup", "serverList", "prepareServerList", "avgWorldLevel", "conditionLevels"}.issubset(
            fields_by_object.get("CrossVO", set())
        ),
        "simple_club_fields_present": {"id", "name", "level", "belongAlliance"}.issubset(
            fields_by_object.get("SimpleClubVO", set())
        ),
    }

    _write_tsv(
        output_dir / "lua_sm_login_nested_vo_objects.tsv",
        object_rows,
        ["packet_name", "packet_id", "direction", "field_count", "source"],
    )
    _write_tsv(
        output_dir / "lua_sm_login_nested_vo_fields.tsv",
        field_rows,
        ["packet_id", "packet_name", "direction", "field_index", "field_name", "read_method", "type_hint", "source", "line"],
    )
    _write_tsv(
        output_dir / "lua_sm_login_nested_vo_refs.tsv",
        nested_rows,
        ["packet_name", "field_name", "read_method", "type_hint", "meaning"],
    )
    _write_sm_login_nested_vo_markdown(
        output_dir / "lua_sm_login_nested_vo_report.md",
        export_root=root,
        output_dir=output_dir,
        object_rows=object_rows,
        field_rows=field_rows,
        nested_rows=nested_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_sm_login_nested_vo_report.md"),
        "objects_path": str(output_dir / "lua_sm_login_nested_vo_objects.tsv"),
        "fields_path": str(output_dir / "lua_sm_login_nested_vo_fields.tsv"),
        "refs_path": str(output_dir / "lua_sm_login_nested_vo_refs.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "objects": len(object_rows),
            "fields": len(field_rows),
            "nested_refs": len(nested_rows),
        },
        "sources": {name: str(path) if path else "" for name, path in packet_paths.items()},
    }
    (output_dir / "lua_sm_login_nested_vo_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_sm_login_nested_vo_depth2_markdown(
    path: Path,
    *,
    export_root: Path,
    output_dir: Path,
    object_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# SM_Login nested VO depth-2 report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report expands the typed second-level beans reachable from the first `SM_Login(20002)` object map.",
        "- Current focus: `RoleVO.hangPoint -> HangPointVO`, `RoleVO.skill -> SM_SkillVO`, and `MapInfoVO.position -> Grid3DVO`.",
        "- List/map element types that are not named in the generated Lua remain represented as list/map fields rather than guessed objects.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(["", "## Relations", "", "| Parent | Field | Child | Meaning |", "| --- | --- | --- | --- |"])
    for row in relation_rows:
        lines.append(
            f"| `{row.get('parent_object', '')}` | `{row.get('parent_field', '')}` | `{row.get('child_object', '')}` | "
            f"{str(row.get('meaning', '')).replace('|', '\\|')} |"
        )

    lines.extend(["", "## Objects", "", "| Object | ID | Direction | Field Count | Source |", "| --- | ---: | --- | ---: | --- |"])
    for row in object_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('packet_id', '')} | {row.get('direction', '')} | "
            f"{row.get('field_count', '')} | `{str(row.get('source', '')).replace('|', '\\|')}` |"
        )

    lines.extend(["", "## Fields", "", "| Object | # | Field | Wire Method | Type Hint | Source | Line |", "| --- | ---: | --- | --- | --- | --- | ---: |"])
    for row in field_rows:
        lines.append(
            f"| `{row.get('packet_name', '')}` | {row.get('field_index', '')} | `{row.get('field_name', '')}` | "
            f"`{row.get('read_method', '')}` | `{row.get('type_hint', '')}` | "
            f"`{str(row.get('source', '')).replace('|', '\\|')}` | {row.get('line', '')} |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_report_path(path, export_root)}`",
            f"- Objects TSV: `{_report_path(output_dir / 'lua_sm_login_nested_vo_depth2_objects.tsv', export_root)}`",
            f"- Fields TSV: `{_report_path(output_dir / 'lua_sm_login_nested_vo_depth2_fields.tsv', export_root)}`",
            f"- Relations TSV: `{_report_path(output_dir / 'lua_sm_login_nested_vo_depth2_relations.tsv', export_root)}`",
            f"- JSON: `{_report_path(output_dir / 'lua_sm_login_nested_vo_depth2_report.json', export_root)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_sm_login_nested_vo_depth2_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Expand typed second-level beans under the SM_Login snapshot objects."""

    root = resolve_fanxiu_export_root(export_root)
    output_dir = (root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    packet_paths = {
        "HangPointVO": _find_lua_text_asset(root, "HangPointVO.lua", ("return 40076", "self:readMessageMap2Dic(self.values)")),
        "Grid3DVO": _find_lua_text_asset(root, "Grid3DVO.lua", ("return 40052", "self.x=self:readFloat()", "self.z=self:readFloat()")),
        "SM_SkillVO": _find_lua_text_asset(root, "SM_SkillVO.lua", ("return 30065", "self:readMessageList2List(self.posList)", "learnList")),
    }

    relation_rows = [
        {
            "parent_object": "RoleVO",
            "parent_field": "hangPoint",
            "child_object": "HangPointVO",
            "meaning": "saved hang/scene point dictionary used by role snapshot",
        },
        {
            "parent_object": "RoleVO",
            "parent_field": "skill",
            "child_object": "SM_SkillVO",
            "meaning": "learned/equipped skill snapshot embedded under role",
        },
        {
            "parent_object": "MapInfoVO",
            "parent_field": "position",
            "child_object": "Grid3DVO",
            "meaning": "x/y/z map restore position",
        },
    ]

    object_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    for name, vo_path in packet_paths.items():
        item = _parse_packet_file(vo_path, root) if vo_path else None
        if not item:
            object_rows.append(
                {
                    "packet_name": name,
                    "packet_id": "",
                    "direction": "",
                    "field_count": 0,
                    "source": "<missing>",
                }
            )
            continue
        object_rows.append(
            {
                "packet_name": item["name"],
                "packet_id": item["id"],
                "direction": item["direction"],
                "field_count": item["field_count"],
                "source": _report_path(vo_path, root),
            }
        )
        for field in item["fields"]:
            field_rows.append(
                {
                    "packet_id": item["id"],
                    "packet_name": item["name"],
                    "direction": item["direction"],
                    "field_index": field["field_index"],
                    "field_name": field["field_name"],
                    "read_method": field["read_method"],
                    "type_hint": field["type_hint"],
                    "source": _report_path(vo_path, root),
                    "line": field["line"],
                }
            )

    fields_by_object: dict[str, set[str]] = {}
    for row in field_rows:
        fields_by_object.setdefault(str(row.get("packet_name") or ""), set()).add(str(row.get("field_name") or ""))

    checks = {
        "hang_point_map_present": {"values"}.issubset(fields_by_object.get("HangPointVO", set())),
        "grid3d_xyz_present": {"x", "y", "z"}.issubset(fields_by_object.get("Grid3DVO", set())),
        "skill_lists_present": {"posList", "learnList"}.issubset(fields_by_object.get("SM_SkillVO", set())),
        "relations_are_complete": {row["child_object"] for row in relation_rows} == {"HangPointVO", "Grid3DVO", "SM_SkillVO"},
    }

    _write_tsv(
        output_dir / "lua_sm_login_nested_vo_depth2_objects.tsv",
        object_rows,
        ["packet_name", "packet_id", "direction", "field_count", "source"],
    )
    _write_tsv(
        output_dir / "lua_sm_login_nested_vo_depth2_fields.tsv",
        field_rows,
        ["packet_id", "packet_name", "direction", "field_index", "field_name", "read_method", "type_hint", "source", "line"],
    )
    _write_tsv(
        output_dir / "lua_sm_login_nested_vo_depth2_relations.tsv",
        relation_rows,
        ["parent_object", "parent_field", "child_object", "meaning"],
    )
    _write_sm_login_nested_vo_depth2_markdown(
        output_dir / "lua_sm_login_nested_vo_depth2_report.md",
        export_root=root,
        output_dir=output_dir,
        object_rows=object_rows,
        field_rows=field_rows,
        relation_rows=relation_rows,
        checks=checks,
    )

    result = {
        "export_root": str(root),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_sm_login_nested_vo_depth2_report.md"),
        "objects_path": str(output_dir / "lua_sm_login_nested_vo_depth2_objects.tsv"),
        "fields_path": str(output_dir / "lua_sm_login_nested_vo_depth2_fields.tsv"),
        "relations_path": str(output_dir / "lua_sm_login_nested_vo_depth2_relations.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "objects": len(object_rows),
            "fields": len(field_rows),
            "relations": len(relation_rows),
        },
        "sources": {name: str(path) if path else "" for name, path in packet_paths.items()},
    }
    (output_dir / "lua_sm_login_nested_vo_depth2_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def build_fanxiu_lua_packet_index(
    *,
    source_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_source_dir = _resolve_export_dir(source_dir, DEFAULT_LUA_PACKET_DIR, export_root=export_root)
    out_dir = root / "parsed_configs" / "lua_packet_index"
    out_dir.mkdir(parents=True, exist_ok=True)

    packet_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    wire_field_rows: list[dict[str, Any]] = []
    for path in sorted(resolved_source_dir.glob("*/text_assets/*.lua")):
        item = _parse_packet_file(path, root)
        if not item:
            continue
        packet_rows.append({key: value for key, value in item.items() if key not in {"fields", "wire_fields"}})
        for field in item["fields"]:
            field_rows.append(
                {
                    "packet_name": item["name"],
                    "packet_id": item["id"],
                    "direction": item["direction"],
                    "module": item["module"],
                    "bundle": item["bundle"],
                    "file": item["file"],
                    "field_index": field["field_index"],
                    "field_name": field["field_name"],
                    "read_method": field["read_method"],
                    "type_hint": field["type_hint"],
                    "line": field["line"],
                }
            )
        for field in item["wire_fields"]:
            wire_field_rows.append(
                {
                    "packet_name": item["name"],
                    "packet_id": item["id"],
                    "direction": item["direction"],
                    "module": item["module"],
                    "bundle": item["bundle"],
                    "file": item["file"],
                    "relative_path": item["relative_path"],
                    "access": field["access"],
                    "field_index": field["field_index"],
                    "field_name": field["field_name"],
                    "wire_method": field["wire_method"],
                    "read_method": field["read_method"],
                    "write_method": field["write_method"],
                    "type_hint": field["type_hint"],
                    "line": field["line"],
                }
            )

    packet_rows.sort(key=lambda row: (row["id"] is None, row["id"] or 0, str(row["name"])))
    field_rows.sort(key=lambda row: (row["packet_id"] is None, row["packet_id"] or 0, int(row["field_index"])))
    wire_field_rows.sort(
        key=lambda row: (
            row["packet_id"] is None,
            row["packet_id"] or 0,
            str(row["access"]),
            int(row["field_index"]),
        )
    )
    packet_by_name = {str(row["name"]): row for row in packet_rows}
    registration_rows = _extract_packet_registrations(resolved_source_dir, root, packet_by_name)
    registrations_by_packet: dict[str, list[dict[str, Any]]] = {}
    for row in registration_rows:
        registrations_by_packet.setdefault(str(row["packet_name"]), []).append(row)
    direction_counts = Counter(str(row["direction"]) for row in packet_rows)
    module_counts = Counter(str(row["module"] or "<unknown>") for row in packet_rows)
    duplicate_ids = [
        {"id": packet_id, "names": sorted(names)}
        for packet_id, names in _group_packet_ids(packet_rows).items()
        if packet_id is not None and len(names) > 1
    ]
    stats = {
        "packet_count": len(packet_rows),
        "message_id_count": len({row["id"] for row in packet_rows if row["id"] is not None}),
        "field_count": len(field_rows),
        "wire_field_count": len(wire_field_rows),
        "registration_count": len(registration_rows),
        "registered_packet_count": len(registrations_by_packet),
        "handler_packet_count": len(
            {
                row["packet_name"]
                for row in registration_rows
                if row.get("handler_kind") not in {"", "none"} and row.get("handler_name", "") != ""
            }
        ),
        "faze_packet_count": module_counts.get("player.faze", 0),
        "direction_counts": dict(direction_counts),
        "top_modules": dict(module_counts.most_common(30)),
        "duplicate_id_count": len(duplicate_ids),
    }

    packets_path = out_dir / "packets.tsv"
    fields_path = out_dir / "packet_fields.tsv"
    wire_fields_path = out_dir / "packet_wire_fields.tsv"
    registrations_path = out_dir / "packet_registrations.tsv"
    protocol_catalog_path = out_dir / "protocol_catalog.tsv"
    canonical_protocol_catalog_path = out_dir / "protocol_catalog_canonical.tsv"
    faze_packets_path = out_dir / "faze_packets.tsv"
    json_path = out_dir / "lua_packet_index.json"
    report_path = out_dir / "lua_packet_index_report.md"
    protocol_fields = [
        "id",
        "name",
        "direction",
        "module",
        "field_count",
        "read_fields",
        "write_fields",
        "registration_count",
        "handler_names",
        "logic_names",
        "file",
        "relative_path",
    ]
    canonical_protocol_fields = [
        "id",
        "name",
        "direction",
        "module",
        "field_count",
        "read_fields",
        "write_fields",
        "registration_count",
        "handler_names",
        "logic_names",
        "source_file_count",
        "sample_files",
    ]
    _write_tsv(
        packets_path,
        packet_rows,
        [
            "id",
            "name",
            "direction",
            "module",
            "field_count",
            "base_class",
            "bundle",
            "file",
            "relative_path",
            "package",
        ],
    )
    _write_tsv(
        fields_path,
        field_rows,
        [
            "packet_id",
            "packet_name",
            "field_index",
            "field_name",
            "read_method",
            "type_hint",
            "direction",
            "module",
            "bundle",
            "file",
            "relative_path",
            "line",
        ],
    )
    _write_tsv(
        wire_fields_path,
        wire_field_rows,
        [
            "packet_id",
            "packet_name",
            "access",
            "field_index",
            "field_name",
            "wire_method",
            "type_hint",
            "read_method",
            "write_method",
            "direction",
            "module",
            "bundle",
            "file",
            "line",
        ],
    )
    _write_tsv(
        registrations_path,
        registration_rows,
        [
            "packet_id",
            "packet_name",
            "packet_direction",
            "packet_module",
            "handler_kind",
            "handler_name",
            "logic_name",
            "bundle",
            "file",
            "relative_path",
            "line",
            "snippet",
        ],
    )
    fields_by_id: dict[Any, list[dict[str, Any]]] = {}
    for row in field_rows:
        fields_by_id.setdefault(row.get("packet_id"), []).append(row)
    wire_fields_by_path_access: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in wire_field_rows:
        wire_fields_by_path_access.setdefault((str(row.get("relative_path")), str(row.get("access"))), []).append(row)
    protocol_rows: list[dict[str, Any]] = []
    for row in packet_rows:
        packet_name = str(row.get("name") or "")
        relative_path = str(row.get("relative_path") or "")
        registrations = registrations_by_packet.get(packet_name, [])
        handler_names = sorted({str(item.get("handler_name") or "") for item in registrations if item.get("handler_name")})
        logic_names = sorted({str(item.get("logic_name") or "") for item in registrations if item.get("logic_name")})
        protocol_rows.append(
            {
                "id": row["id"],
                "name": packet_name,
                "direction": row["direction"],
                "module": row["module"],
                "field_count": row["field_count"],
                "read_fields": _field_signature(wire_fields_by_path_access.get((relative_path, "read"), [])),
                "write_fields": _field_signature(wire_fields_by_path_access.get((relative_path, "write"), [])),
                "registration_count": len(registrations),
                "handler_names": ", ".join(handler_names),
                "logic_names": ", ".join(logic_names[:12]),
                "file": row["file"],
                "relative_path": row["relative_path"],
            }
        )
    _write_tsv(protocol_catalog_path, protocol_rows, protocol_fields)
    canonical_protocol_rows = _canonical_protocol_rows(protocol_rows)
    _write_tsv(canonical_protocol_catalog_path, canonical_protocol_rows, canonical_protocol_fields)
    feature_rows: dict[str, list[dict[str, Any]]] = {}
    for row in canonical_protocol_rows:
        feature_key = _protocol_feature_key(row)
        if feature_key:
            feature_rows.setdefault(feature_key, []).append(row)
    feature_paths: dict[str, str] = {}
    for feature_key, rows in sorted(feature_rows.items()):
        feature_path = out_dir / f"protocol_{feature_key}.tsv"
        _write_tsv(feature_path, rows, canonical_protocol_fields)
        feature_paths[feature_key] = str(feature_path)
    stats["canonical_protocol_count"] = len(canonical_protocol_rows)
    stats["feature_protocol_counts"] = {key: len(rows) for key, rows in sorted(feature_rows.items())}
    faze_rows = [
        {
            "id": row["id"],
            "name": row["name"],
            "direction": row["direction"],
            "field_count": row["field_count"],
            "fields": _field_signature(fields_by_id.get(row["id"], [])),
            "file": row["file"],
        }
        for row in packet_rows
        if row.get("module") == "player.faze"
    ]
    _write_tsv(faze_packets_path, faze_rows, ["id", "name", "direction", "field_count", "fields", "file"])
    json_path.write_text(
        json.dumps(
            {
                "source_dir": str(resolved_source_dir),
                "stats": stats,
                "duplicate_ids": duplicate_ids,
                "packets": packet_rows,
                "fields": field_rows,
                "wire_fields": wire_field_rows,
                "registrations": registration_rows,
                "canonical_protocol_count": len(canonical_protocol_rows),
                "feature_protocol_counts": stats["feature_protocol_counts"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        "\n".join(
            [
                "# 凡修 Lua Packet 静态索引",
                "",
                f"- Packet/VO 文件：{stats['packet_count']}",
                f"- 消息 id：{stats['message_id_count']}",
                f"- 字段：{stats['field_count']}",
                f"- 读写线序字段：{stats['wire_field_count']}",
                f"- MessagePool 注册：{stats['registration_count']} 条，覆盖 {stats['registered_packet_count']} 个 packet",
                f"- 去重协议行：{stats['canonical_protocol_count']}",
                f"- player.faze：{stats['faze_packet_count']}",
                f"- 重复消息 id：{stats['duplicate_id_count']}",
                "",
                "## 专题协议子集",
                "",
                *[f"- `{key}`：{value}" for key, value in stats["feature_protocol_counts"].items()],
                "",
                "## 方向统计",
                "",
                *[f"- `{key}`：{value}" for key, value in direction_counts.most_common()],
                "",
                "## 高频模块",
                "",
                *[f"- `{key}`：{value}" for key, value in module_counts.most_common(20)],
            ]
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(out_dir),
        "source_dir": str(resolved_source_dir),
        "stats": stats,
        "files": {
            "index_json": str(json_path),
            "packets_tsv": str(packets_path),
            "packet_fields_tsv": str(fields_path),
            "packet_wire_fields_tsv": str(wire_fields_path),
            "packet_registrations_tsv": str(registrations_path),
            "protocol_catalog_tsv": str(protocol_catalog_path),
            "protocol_catalog_canonical_tsv": str(canonical_protocol_catalog_path),
            "feature_protocol_tsv": feature_paths,
            "faze_packets_tsv": str(faze_packets_path),
            "report": str(report_path),
        },
    }


def _group_packet_ids(rows: list[dict[str, Any]]) -> dict[int | None, set[str]]:
    grouped: dict[int | None, set[str]] = {}
    for row in rows:
        packet_id = row.get("id")
        grouped.setdefault(packet_id, set()).add(str(row.get("name") or ""))
    return grouped
