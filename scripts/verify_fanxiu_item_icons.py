from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.catalog.item import get_fanxiu_item_card
from backend.core.fanxiu.catalog.resources import export_fanxiu_sprite_icon, resolve_fanxiu_export_root


def _load_cards(export_root: Path) -> list[dict[str, Any]]:
    path = export_root / "parsed_configs" / "item_catalog" / "item_catalog.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    cards = data.get("cards")
    if not isinstance(cards, list):
        raise ValueError(f"item catalog has no cards list: {path}")
    return [card for card in cards if isinstance(card, dict)]


def _unique_icon_names(cards: list[dict[str, Any]]) -> list[str]:
    icons: list[str] = []
    seen: set[str] = set()
    for card in cards:
        icon = str(card.get("icon") or "").strip()
        if icon and icon not in seen:
            icons.append(icon)
            seen.add(icon)
    return icons


def _cards_by_icon(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        icon = str(card.get("icon") or "").strip()
        if icon:
            result.setdefault(icon, []).append(card)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Fanxiu Item catalog icon export coverage.")
    parser.add_argument("--export-root", default=None, help="Fanxiu analysis export root")
    parser.add_argument("--limit", type=int, default=0, help="Optional icon limit for quick probes")
    args = parser.parse_args()

    export_root = resolve_fanxiu_export_root(args.export_root)
    cards = _load_cards(export_root)
    icons = _unique_icon_names(cards)
    if args.limit and args.limit > 0:
        icons = icons[: args.limit]
    by_icon = _cards_by_icon(cards)

    failures: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    ok = 0
    alias_count = 0
    for index, icon in enumerate(icons, start=1):
        try:
            result = export_fanxiu_sprite_icon(icon, export_root=export_root)
            ok += 1
            alias_sprite_name = str(result.get("alias_sprite_name") or "")
            if alias_sprite_name:
                alias_count += 1
                sample_cards = by_icon.get(icon, [])[:5]
                aliases.append(
                    {
                        "icon": icon,
                        "alias_sprite_name": alias_sprite_name,
                        "alias_reason": str(result.get("alias_reason") or ""),
                        "sample_item_ids": ",".join(str(card.get("id") or "") for card in sample_cards),
                        "sample_item_names": " | ".join(str(card.get("name") or "") for card in sample_cards),
                        "item_count": len(by_icon.get(icon, [])),
                    }
                )
        except Exception as exc:
            sample_cards = by_icon.get(icon, [])[:5]
            failures.append(
                {
                    "icon": icon,
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "sample_item_ids": ",".join(str(card.get("id") or "") for card in sample_cards),
                    "sample_item_names": " | ".join(str(card.get("name") or "") for card in sample_cards),
                    "item_count": len(by_icon.get(icon, [])),
                }
            )
        if index % 500 == 0:
            print(f"progress={index} ok={ok} fail={len(failures)}")

    output_dir = export_root / "parsed_configs" / "item_catalog"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "icon_export_failures_latest.tsv"
    fields = ["icon", "error", "message", "sample_item_ids", "sample_item_names", "item_count"]
    with report_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(failures)

    alias_report_path = output_dir / "icon_export_aliases_latest.tsv"
    alias_fields = ["icon", "alias_sprite_name", "alias_reason", "sample_item_ids", "sample_item_names", "item_count"]
    with alias_report_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=alias_fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(aliases)

    prefix_counts = Counter(str(row["icon"]).split("_", 1)[0] for row in failures)
    print(
        json.dumps(
            {
                "export_root": str(export_root),
                "card_count": len(cards),
                "unique_icon_count": len(icons),
                "ok": ok,
                "fail": len(failures),
                "alias_count": alias_count,
                "failure_prefix_counts": dict(prefix_counts.most_common()),
                "report": str(report_path),
                "alias_report": str(alias_report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    for row in failures:
        item_id = str(row["sample_item_ids"]).split(",", 1)[0]
        if item_id:
            try:
                card = get_fanxiu_item_card(item_id, export_root=export_root).get("card") or {}
            except Exception:
                card = {}
            if card:
                print(f"failure_detail {item_id} {card.get('name')} icon={row['icon']} type={card.get('type_name')} sub={card.get('sub_type_name')}")


if __name__ == "__main__":
    main()
