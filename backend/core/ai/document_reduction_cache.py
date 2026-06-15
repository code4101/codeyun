from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from backend.core.ai.document_reduction_storage import get_document_cache_dir


CACHE_DB_FILENAME = "document-index.sqlite"


def get_document_cache_db_path() -> Path:
    return get_document_cache_dir() / CACHE_DB_FILENAME


def ensure_document_cache_schema() -> None:
    with sqlite3.connect(get_document_cache_db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_node_index (
                user_id INTEGER NOT NULL,
                document_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                level INTEGER NOT NULL,
                topic TEXT NOT NULL,
                summary TEXT NOT NULL,
                keywords_json TEXT NOT NULL,
                possible_questions_json TEXT NOT NULL,
                importance TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                child_node_ids_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                search_text TEXT NOT NULL,
                PRIMARY KEY (run_id, node_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_document_node_index_scope
            ON document_node_index (user_id, document_id, run_id, level)
            """
        )
        conn.commit()


def replace_document_run_nodes(
    *,
    user_id: int,
    document_id: str,
    run_id: str,
    nodes: list[dict[str, Any]],
) -> None:
    ensure_document_cache_schema()
    rows = []
    for node in nodes:
        payload = dict(node.get("payload") or {})
        keywords = [str(item).strip() for item in (payload.get("keywords") or []) if str(item).strip()]
        possible_questions = [
            str(item).strip()
            for item in (payload.get("possible_questions") or [])
            if str(item).strip()
        ]
        source_refs = [str(item).strip() for item in (node.get("source_refs") or []) if str(item).strip()]
        child_node_ids = [str(item).strip() for item in (node.get("child_node_ids") or []) if str(item).strip()]
        search_parts = [
            str(payload.get("topic") or "").strip(),
            str(payload.get("summary") or "").strip(),
            " ".join(keywords),
            " ".join(possible_questions),
            " ".join(source_refs),
        ]
        rows.append(
            (
                user_id,
                document_id,
                run_id,
                str(node.get("node_id") or "").strip(),
                int(node.get("level") or 0),
                str(payload.get("topic") or "").strip(),
                str(payload.get("summary") or "").strip(),
                json.dumps(keywords, ensure_ascii=False),
                json.dumps(possible_questions, ensure_ascii=False),
                str(payload.get("importance") or "").strip(),
                json.dumps(source_refs, ensure_ascii=False),
                json.dumps(child_node_ids, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                "\n".join(part for part in search_parts if part),
            )
        )

    with sqlite3.connect(get_document_cache_db_path()) as conn:
        conn.execute(
            "DELETE FROM document_node_index WHERE user_id = ? AND document_id = ? AND run_id = ?",
            (user_id, document_id, run_id),
        )
        conn.executemany(
            """
            INSERT INTO document_node_index (
                user_id, document_id, run_id, node_id, level, topic, summary,
                keywords_json, possible_questions_json, importance, source_refs_json,
                child_node_ids_json, payload_json, search_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def search_document_run_nodes(
    *,
    user_id: int,
    document_id: str,
    run_id: str,
    query_text: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    ensure_document_cache_schema()
    normalized_query = (query_text or "").strip()
    if not normalized_query:
        return []

    with sqlite3.connect(get_document_cache_db_path()) as conn:
        rows = conn.execute(
            """
            SELECT node_id, level, topic, summary, keywords_json, possible_questions_json,
                   importance, source_refs_json, child_node_ids_json, payload_json, search_text
            FROM document_node_index
            WHERE user_id = ? AND document_id = ? AND run_id = ?
            """,
            (user_id, document_id, run_id),
        ).fetchall()

    ranked: list[tuple[int, int, dict[str, Any]]] = []
    query_terms = _build_query_terms(normalized_query)
    for row in rows:
        search_text = str(row[10] or "")
        score = _score_search_text(search_text, query_terms)
        if score <= 0:
            continue
        payload = json.loads(str(row[9] or "{}"))
        ranked.append(
            (
                score,
                -int(row[1] or 0),
                {
                    "node_id": str(row[0] or ""),
                    "level": int(row[1] or 0),
                    "topic": str(row[2] or ""),
                    "summary": str(row[3] or ""),
                    "keywords": json.loads(str(row[4] or "[]")),
                    "possible_questions": json.loads(str(row[5] or "[]")),
                    "importance": str(row[6] or ""),
                    "source_refs": json.loads(str(row[7] or "[]")),
                    "child_node_ids": json.loads(str(row[8] or "[]")),
                    "payload": payload,
                    "score": score,
                },
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1], item[2]["node_id"]))
    return [item[2] for item in ranked[:max(1, limit)]]


def delete_document_nodes(
    *,
    user_id: int,
    document_id: str,
    run_id: str | None = None,
) -> None:
    ensure_document_cache_schema()
    with sqlite3.connect(get_document_cache_db_path()) as conn:
        if run_id:
            conn.execute(
                "DELETE FROM document_node_index WHERE user_id = ? AND document_id = ? AND run_id = ?",
                (user_id, document_id, run_id),
            )
        else:
            conn.execute(
                "DELETE FROM document_node_index WHERE user_id = ? AND document_id = ?",
                (user_id, document_id),
            )
        conn.commit()


def _build_query_terms(query_text: str) -> list[str]:
    compact = query_text.strip()
    raw_terms = [term.strip() for term in re.split(r"[\s,，。；;、!?！？]+", compact) if term.strip()]
    terms = [compact] if compact else []
    terms.extend(raw_terms)

    normalized: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        normalized.append(term)
    return normalized


def _score_search_text(search_text: str, query_terms: list[str]) -> int:
    haystack = (search_text or "").casefold()
    score = 0
    for term in query_terms:
        needle = term.casefold()
        if not needle:
            continue
        if needle in haystack:
            score += max(1, min(8, len(needle)))
    return score
