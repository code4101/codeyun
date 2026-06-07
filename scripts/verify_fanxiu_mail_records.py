from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests
from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu_mail_store import ensure_fanxiu_mail_table
from backend.db import engine
from backend.models import FanxiuMailRecord

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _mail_rewards(row: FanxiuMailRecord) -> list[dict[str, Any]]:
    payload = row.payload or {}
    direct = payload.get("mail_rewards")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    packet = payload.get("packet")
    if isinstance(packet, dict) and isinstance(packet.get("mail_rewards"), list):
        return [item for item in packet["mail_rewards"] if isinstance(item, dict)]
    return []


def _mail_content(row: FanxiuMailRecord) -> str:
    payload = row.payload or {}
    direct = payload.get("mail_content_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    packet = payload.get("packet")
    if isinstance(packet, dict) and isinstance(packet.get("mail_content_text"), str):
        return packet["mail_content_text"].strip()
    return ""


def _check_icon(base_url: str, icon: str, timeout: int) -> dict[str, Any] | None:
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/api/fanxiu/resources/icon",
            params={"name": icon},
            timeout=timeout,
        )
    except Exception as exc:
        return {"icon": icon, "error": str(exc)}
    content_type = response.headers.get("content-type", "")
    if response.status_code == 200 and content_type.startswith("image/"):
        return None
    return {
        "icon": icon,
        "status_code": response.status_code,
        "content_type": content_type,
        "detail": response.text[:240],
    }


def _missing_reward_field_names(reward: dict[str, Any]) -> list[str]:
    item_id = str(reward.get("item_id") or "").strip()
    name = str(reward.get("item_name") or "").strip()
    icon = str(reward.get("icon") or reward.get("small_icon") or "").strip()
    missing_parts = []
    if not item_id:
        missing_parts.append("item_id")
    if not name or name.startswith("未知道具") or name.startswith("道具"):
        missing_parts.append("item_name")
    if not icon:
        missing_parts.append("icon")
    return missing_parts


def verify_mail_records(args: argparse.Namespace) -> dict[str, Any]:
    ensure_fanxiu_mail_table()
    icon_failures: list[dict[str, Any]] = []
    sample_contents: list[dict[str, Any]] = []
    sample_rewards: list[dict[str, Any]] = []
    title_content_samples: dict[str, str] = {}
    counters = {
        "records": 0,
        "with_rewards": 0,
        "reward_items": 0,
        "reward_items_with_icon": 0,
        "reward_items_with_name": 0,
        "reward_items_with_item_id": 0,
        "with_content": 0,
    }
    missing_content_records: list[dict[str, Any]] = []
    weak_content_records: list[dict[str, Any]] = []
    malformed_content_records: list[dict[str, Any]] = []
    missing_reward_fields: list[dict[str, Any]] = []
    checked_icons: set[str] = set()

    with Session(engine) as session:
        rows = session.exec(
            select(FanxiuMailRecord)
            .order_by(FanxiuMailRecord.updated_at.desc())
            .limit(args.limit)
        ).all()
        counters["records"] = len(rows)
        for row in rows:
            rewards = _mail_rewards(row)
            content = _mail_content(row)
            if content:
                counters["with_content"] += 1
                title_content_samples.setdefault(str(row.title or ""), content)
                if content.startswith("参数：") and len(weak_content_records) < 40:
                    weak_content_records.append(
                        {
                            "title": row.title,
                            "mail_id": row.mail_id,
                            "mail_type": row.mail_type,
                            "status": row.status,
                            "content": content[:260],
                        }
                    )
                if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", content) and len(malformed_content_records) < 40:
                    malformed_content_records.append(
                        {
                            "title": row.title,
                            "mail_id": row.mail_id,
                            "mail_type": row.mail_type,
                            "status": row.status,
                            "content": repr(content[:260]),
                        }
                    )
                if len(sample_contents) < args.sample_limit:
                    sample_contents.append(
                        {
                            "title": row.title,
                            "status": row.status,
                            "text": content[:220],
                        }
                    )
            elif len(missing_content_records) < 40:
                missing_content_records.append(
                    {
                        "title": row.title,
                        "mail_id": row.mail_id,
                        "status": row.status,
                        "reward_count": len(rewards),
                    }
                )
            if rewards:
                counters["with_rewards"] += 1
            for reward in rewards:
                counters["reward_items"] += 1
                item_id = str(reward.get("item_id") or "").strip()
                name = str(reward.get("item_name") or "").strip()
                icon = str(reward.get("icon") or reward.get("small_icon") or "").strip()
                if item_id:
                    counters["reward_items_with_item_id"] += 1
                if name and not name.startswith("未知道具") and not name.startswith("道具"):
                    counters["reward_items_with_name"] += 1
                missing_parts = _missing_reward_field_names(reward)
                if missing_parts and len(missing_reward_fields) < 40:
                    missing_reward_fields.append(
                        {
                            "title": row.title,
                            "mail_id": row.mail_id,
                            "missing": ",".join(missing_parts),
                            "item_id": item_id,
                            "item_name": name,
                            "icon": icon,
                            "raw": reward,
                        }
                    )
                if len(sample_rewards) < args.sample_limit:
                    sample_rewards.append(
                        {
                            "title": row.title,
                            "name": reward.get("item_name"),
                            "amount": reward.get("amount"),
                            "icon": icon,
                        }
                    )
                if not icon:
                    continue
                counters["reward_items_with_icon"] += 1
                if icon in checked_icons:
                    continue
                checked_icons.add(icon)
                failure = _check_icon(args.base_url, icon, args.api_timeout)
                if failure:
                    icon_failures.append(failure)

    required_content_failures: list[dict[str, str]] = []
    for title, expected_part in {
        "灵脉收益": "所在灵脉",
        "修炼值自动领取通知": "修炼值",
    }.items():
        content = title_content_samples.get(title, "")
        if expected_part not in content:
            required_content_failures.append(
                {
                    "title": title,
                    "expected_part": expected_part,
                    "observed": content,
                }
            )

    return {
        **counters,
        "unique_icons_checked": len(checked_icons),
        "icon_endpoint_failures": icon_failures,
        "missing_content_records": missing_content_records,
        "weak_content_records": weak_content_records,
        "malformed_content_records": malformed_content_records,
        "missing_reward_fields": missing_reward_fields,
        "required_content_failures": required_content_failures,
        "sample_contents": sample_contents,
        "sample_rewards": sample_rewards,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Fanxiu mail records, attachment icons, and key content fallbacks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--sample-limit", type=int, default=10)
    parser.add_argument("--api-timeout", type=int, default=15)
    args = parser.parse_args()
    summary = verify_mail_records(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return (
        1
        if summary["icon_endpoint_failures"]
        or summary["missing_reward_fields"]
        or summary["missing_content_records"]
        or summary["weak_content_records"]
        or summary["malformed_content_records"]
        or summary["required_content_failures"]
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
