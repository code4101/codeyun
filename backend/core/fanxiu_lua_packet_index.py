from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root


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
