import csv
from collections import Counter
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


PROTOCOL_SEMANTIC_FEATURES = {
    "bluestarsea": {
        "title": "BlueStarSea",
        "semantics": "hot_update_bluestarsea_protocol_semantics.tsv",
        "edges": "hot_update_bluestarsea_protocol_semantic_edges.tsv",
        "report": "hot_update_bluestarsea_protocol_semantics_report.md",
    },
    "blld": {
        "title": "BLLD",
        "semantics": "hot_update_blld_protocol_semantics.tsv",
        "edges": "hot_update_blld_protocol_semantic_edges.tsv",
        "report": "hot_update_blld_protocol_semantics_report.md",
    },
    "faze": {
        "title": "Faze",
        "semantics": "hot_update_faze_protocol_semantics.tsv",
        "edges": "hot_update_faze_protocol_semantic_edges.tsv",
        "report": "hot_update_faze_protocol_semantics_report.md",
    },
    "gongfa": {
        "title": "Gongfa",
        "semantics": "hot_update_gongfa_protocol_semantics.tsv",
        "edges": "hot_update_gongfa_protocol_semantic_edges.tsv",
        "report": "hot_update_gongfa_protocol_semantics_report.md",
    },
}


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return [dict(row) for row in csv.DictReader(file, delimiter="\t")]


def _ensure_protocol_semantic_outputs(feature: str, export_base: Path) -> None:
    info = PROTOCOL_SEMANTIC_FEATURES[feature]
    output_dir = export_base / "apk_static_index"
    semantics_path = output_dir / info["semantics"]
    edges_path = output_dir / info["edges"]
    if semantics_path.is_file() and edges_path.is_file():
        return

    from backend.core.fanxiu.catalog import hot_update as fanxiu_hot_update

    builders = {
        "bluestarsea": fanxiu_hot_update.build_fanxiu_bluestarsea_protocol_semantics_probe,
        "blld": fanxiu_hot_update.build_fanxiu_blld_protocol_semantics_probe,
        "faze": fanxiu_hot_update.build_fanxiu_faze_protocol_semantics_probe,
        "gongfa": fanxiu_hot_update.build_fanxiu_gongfa_protocol_semantics_probe,
    }
    builders[feature](export_root=export_base)


def _matches_query(row: dict[str, str], query: str) -> bool:
    if not query:
        return True
    needle = query.lower()
    return any(needle in str(value or "").lower() for value in row.values())


def _matches_edge_query(row: dict[str, str], query: str) -> bool:
    if not query:
        return True
    needle = query.lower()
    return any(needle in str(value or "").lower() for value in row.values())


def _sort_semantic_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    def row_key(row: dict[str, str]) -> tuple[int, int, str]:
        direction_order = {
            "client_to_server": 0,
            "server_to_client": 1,
            "value_object": 2,
        }.get(row.get("direction", ""), 9)
        try:
            packet_id = int(row.get("id") or 0)
        except ValueError:
            packet_id = 0
        return direction_order, packet_id, row.get("packet", "")

    return sorted(rows, key=row_key)


def load_fanxiu_protocol_semantics(
    *,
    feature: str = "bluestarsea",
    query: str = "",
    role: str = "",
    operation: str = "",
    limit: int = 300,
    edge_limit: int = 300,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    feature_key = str(feature or "").strip().lower()
    if feature_key not in PROTOCOL_SEMANTIC_FEATURES:
        feature_key = "bluestarsea"
    export_base = resolve_fanxiu_export_root(export_root)
    _ensure_protocol_semantic_outputs(feature_key, export_base)

    info = PROTOCOL_SEMANTIC_FEATURES[feature_key]
    output_dir = export_base / "apk_static_index"
    semantics_path = output_dir / info["semantics"]
    edges_path = output_dir / info["edges"]
    report_path = output_dir / info["report"]
    rows = _read_tsv(semantics_path)
    edges = _read_tsv(edges_path)

    query_text = str(query or "").strip()
    role_text = str(role or "").strip()
    operation_text = str(operation or "").strip()
    filtered_rows = [
        row
        for row in rows
        if (not role_text or row.get("role") == role_text)
        and (not operation_text or row.get("operation") == operation_text)
        and _matches_query(row, query_text)
    ]
    filtered_rows = _sort_semantic_rows(filtered_rows)
    selected_packets = {row.get("packet", "") for row in filtered_rows if row.get("packet")}
    selected_operations = {row.get("operation", "") for row in filtered_rows if row.get("operation")}

    filtered_edges = []
    for row in edges:
        if operation_text and row.get("source") != operation_text and row.get("evidence") != operation_text:
            continue
        if query_text and not _matches_edge_query(row, query_text):
            source = row.get("source", "")
            target = row.get("target", "")
            if source not in selected_packets and target not in selected_packets and source not in selected_operations:
                continue
        if role_text and selected_packets:
            source = row.get("source", "")
            target = row.get("target", "")
            if source not in selected_packets and target not in selected_packets and row.get("evidence") not in selected_operations:
                continue
        filtered_edges.append(row)

    limit = max(1, min(int(limit or 300), 2000))
    edge_limit = max(1, min(int(edge_limit or 300), 3000))
    return {
        "feature": feature_key,
        "title": info["title"],
        "export_root": str(export_base),
        "outputs": {
            "semantics": str(semantics_path),
            "edges": str(edges_path),
            "report": str(report_path),
        },
        "available_features": [
            {"key": key, "title": value["title"]}
            for key, value in PROTOCOL_SEMANTIC_FEATURES.items()
        ],
        "counts": {
            "rows": len(rows),
            "edges": len(edges),
            "filtered_rows": len(filtered_rows),
            "filtered_edges": len(filtered_edges),
            "by_role": dict(Counter(row.get("role", "") for row in rows).most_common()),
            "by_operation": dict(Counter(row.get("operation", "") for row in rows if row.get("operation")).most_common()),
        },
        "items": filtered_rows[:limit],
        "edges": filtered_edges[:edge_limit],
        "roles": sorted({row.get("role", "") for row in rows if row.get("role")}),
        "operations": sorted({row.get("operation", "") for row in rows if row.get("operation")}),
    }
