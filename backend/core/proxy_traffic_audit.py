from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from backend.core.settings import get_settings


DEFAULT_MIHOMO_PIPE = r"\\.\pipe\verge-mihomo"
DIRECT_CHAIN_NAMES = {"DIRECT"}


def get_proxy_traffic_audit_db_path() -> Path:
    configured = (os.getenv("CODEYUN_PROXY_TRAFFIC_AUDIT_DB") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (get_settings().data_dir / "proxy-traffic-audit.sqlite").resolve(strict=False)


def _utc_now_text() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _decode_chunked_http_body(body: bytes) -> bytes:
    output = bytearray()
    index = 0
    while True:
        line_end = body.find(b"\r\n", index)
        if line_end < 0:
            break
        size_text = body[index:line_end].split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError:
            break
        index = line_end + 2
        if size == 0:
            break
        output.extend(body[index:index + size])
        index += size + 2
    return bytes(output)


def request_mihomo_pipe_json(path: str, *, pipe_path: str = DEFAULT_MIHOMO_PIPE) -> dict[str, Any]:
    request = (
        f"GET {path} HTTP/1.1\r\n"
        "Host: mihomo\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    with open(pipe_path, "r+b", buffering=0) as pipe:
        pipe.write(request)
        chunks = []
        while True:
            chunk = pipe.read(65536)
            if not chunk:
                break
            chunks.append(chunk)

    raw = b"".join(chunks)
    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise RuntimeError("Mihomo pipe returned an invalid HTTP response")
    status_line = head.decode("iso-8859-1", errors="replace").splitlines()[0]
    if " 200 " not in status_line:
        raise RuntimeError(f"Mihomo pipe returned {status_line}")
    if b"transfer-encoding: chunked" in head.lower():
        body = _decode_chunked_http_body(body)
    return json.loads(body.decode("utf-8"))


def init_proxy_traffic_audit_db(db_path: Path | None = None) -> Path:
    path = db_path or get_proxy_traffic_audit_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connection_deltas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sampled_at TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                host TEXT NOT NULL,
                destination_ip TEXT NOT NULL,
                destination_port TEXT NOT NULL,
                network TEXT NOT NULL,
                process TEXT NOT NULL,
                process_path TEXT NOT NULL,
                rule TEXT NOT NULL,
                rule_payload TEXT NOT NULL,
                chains_json TEXT NOT NULL,
                proxy_chain TEXT NOT NULL,
                upload_delta INTEGER NOT NULL,
                download_delta INTEGER NOT NULL,
                total_delta INTEGER NOT NULL,
                start_time TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_connection_deltas_sampled_at ON connection_deltas(sampled_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_connection_deltas_host ON connection_deltas(host)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_connection_deltas_rule ON connection_deltas(rule, rule_payload)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_connection_deltas_process ON connection_deltas(process, process_path)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collector_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
    return path


def _metadata_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if value is None:
        return ""
    return str(value)


def _connection_host(metadata: dict[str, Any]) -> str:
    return (
        _metadata_text(metadata, "host")
        or _metadata_text(metadata, "sniffHost")
        or _metadata_text(metadata, "remoteDestination")
        or _metadata_text(metadata, "destinationIP")
    )


def _proxy_chain(chains: list[Any]) -> str:
    names = [str(item) for item in chains if str(item)]
    if not names:
        return ""
    return " -> ".join(names)


def is_proxy_connection(connection: dict[str, Any]) -> bool:
    chains = [str(item) for item in connection.get("chains") or [] if str(item)]
    if not chains:
        return False
    return chains[0] not in DIRECT_CHAIN_NAMES


class ProxyTrafficAuditCollector:
    def __init__(self, *, db_path: Path | None = None, pipe_path: str = DEFAULT_MIHOMO_PIPE) -> None:
        self.db_path = init_proxy_traffic_audit_db(db_path)
        self.pipe_path = pipe_path
        self._last_totals: dict[str, tuple[int, int]] = {}

    def sample_once(self) -> dict[str, Any]:
        payload = request_mihomo_pipe_json("/connections", pipe_path=self.pipe_path)
        sampled_at = _utc_now_text()
        rows: list[tuple[Any, ...]] = []
        active_ids: set[str] = set()
        proxy_connection_count = 0
        proxy_delta_total = 0

        for connection in payload.get("connections") or []:
            if not isinstance(connection, dict):
                continue
            connection_id = str(connection.get("id") or "")
            if not connection_id:
                continue
            active_ids.add(connection_id)
            upload = int(connection.get("upload") or 0)
            download = int(connection.get("download") or 0)
            previous_upload, previous_download = self._last_totals.get(connection_id, (upload, download))
            self._last_totals[connection_id] = (upload, download)
            upload_delta = max(0, upload - previous_upload)
            download_delta = max(0, download - previous_download)
            total_delta = upload_delta + download_delta
            if total_delta <= 0 or not is_proxy_connection(connection):
                continue

            proxy_connection_count += 1
            proxy_delta_total += total_delta
            metadata = connection.get("metadata") if isinstance(connection.get("metadata"), dict) else {}
            chains = [str(item) for item in connection.get("chains") or [] if str(item)]
            rows.append(
                (
                    sampled_at,
                    connection_id,
                    _connection_host(metadata),
                    _metadata_text(metadata, "remoteDestination") or _metadata_text(metadata, "destinationIP"),
                    _metadata_text(metadata, "destinationPort"),
                    _metadata_text(metadata, "network"),
                    _metadata_text(metadata, "process"),
                    _metadata_text(metadata, "processPath"),
                    str(connection.get("rule") or ""),
                    str(connection.get("rulePayload") or ""),
                    json.dumps(chains, ensure_ascii=False),
                    _proxy_chain(chains),
                    upload_delta,
                    download_delta,
                    total_delta,
                    str(connection.get("start") or ""),
                )
            )

        stale_ids = set(self._last_totals) - active_ids
        for stale_id in stale_ids:
            self._last_totals.pop(stale_id, None)

        with sqlite3.connect(self.db_path) as conn:
            if rows:
                conn.executemany(
                    """
                    INSERT INTO connection_deltas (
                        sampled_at, connection_id, host, destination_ip, destination_port,
                        network, process, process_path, rule, rule_payload, chains_json,
                        proxy_chain, upload_delta, download_delta, total_delta, start_time
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            conn.execute(
                """
                INSERT INTO collector_state(key, value) VALUES('last_sample_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (sampled_at,),
            )
            conn.execute(
                """
                INSERT INTO collector_state(key, value) VALUES('last_sample_summary', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (
                    json.dumps(
                        {
                            "connection_count": len(payload.get("connections") or []),
                            "proxy_connection_count": proxy_connection_count,
                            "proxy_delta_total": proxy_delta_total,
                            "inserted_rows": len(rows),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()

        return {
            "sampled_at": sampled_at,
            "connection_count": len(payload.get("connections") or []),
            "proxy_connection_count": proxy_connection_count,
            "proxy_delta_total": proxy_delta_total,
            "inserted_rows": len(rows),
        }

    def run_loop(self, *, interval_seconds: float = 2.0, once: bool = False) -> None:
        while True:
            started_at = time.monotonic()
            summary = self.sample_once()
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            if once:
                return
            elapsed = time.monotonic() - started_at
            time.sleep(max(0.2, float(interval_seconds) - elapsed))


def summarize_proxy_traffic(
    *,
    db_path: Path | None = None,
    hours: int = 24,
    limit: int = 30,
    group_by: str = "host",
) -> dict[str, Any]:
    path = init_proxy_traffic_audit_db(db_path)
    allowed_groups = {
        "host": "host",
        "rule": "rule || ',' || rule_payload",
        "process": "CASE WHEN process_path != '' THEN process_path ELSE process END",
        "chain": "proxy_chain",
    }
    group_expr = allowed_groups.get(group_by, allowed_groups["host"])
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=max(1, int(hours)))).replace(microsecond=0)
    since_text = since.isoformat().replace("+00:00", "Z")
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                {group_expr} AS key,
                SUM(upload_delta) AS upload,
                SUM(download_delta) AS download,
                SUM(total_delta) AS total,
                COUNT(*) AS samples,
                MAX(sampled_at) AS last_seen_at
            FROM connection_deltas
            WHERE sampled_at >= ?
              AND proxy_chain NOT LIKE 'DIRECT%'
            GROUP BY key
            HAVING total > 0
            ORDER BY total DESC
            LIMIT ?
            """,
            (since_text, max(1, int(limit))),
        ).fetchall()
        state = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM collector_state").fetchall()
        }
    return {
        "ok": True,
        "db_path": os.fspath(path),
        "hours": hours,
        "group_by": group_by,
        "state": state,
        "items": [dict(row) for row in rows],
    }
