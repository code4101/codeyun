"""Bound diary model inputs without discarding turns or the ends of answers."""

import json
from typing import Any


def batch_diary_records(
    records: list[dict[str, Any]], *, max_records: int = 24, max_chars: int = 24000
) -> list[list[dict[str, Any]]]:
    """Split oversized messages, then pack every fragment in chronological order.

    Full source fields take precedence over the legacy 320-character previews.
    Fragment metadata keeps partial answers distinguishable from final results.
    """
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    size = 0
    text_limit = max_chars // 4
    for index, record in enumerate(records):
        texts = {
            key: str(record.get(f"{key}_full", record.get(key)) or "")
            for key in ("user_request", "assistant_result")
        }
        part_count = max(1, *( (len(text) + text_limit - 1) // text_limit for text in texts.values()))
        for part in range(part_count):
            item = {
                "record_index": index,
                "thread_id": record.get("thread_id"),
                "thread_title": record.get("thread_title"),
                "time_range": record.get("time_range"),
                "device": record.get("source_device_name"),
                "project": record.get("project_label"),
                "part": part + 1,
                "part_count": part_count,
                **{key: text[part * text_limit:(part + 1) * text_limit] for key, text in texts.items()},
            }
            item_size = len(json.dumps(item, ensure_ascii=False))
            if batch and (len(batch) >= max_records or size + item_size > max_chars):
                batches.append(batch)
                batch, size = [], 0
            batch.append(item)
            size += item_size
    if batch:
        batches.append(batch)
    return batches
