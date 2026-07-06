from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.pokemon_tcg_translation import TRANSLATION_VERSION, translate_card
from backend.core.settings import get_settings
from backend.db import engine, init_db
from backend.models import PokemonTcgCardRecord


DATASET_ID = "childhood_base_jungle_fossil_rocket"


def dataset_root() -> Path:
    return get_settings().data_dir / "pokemon_tcg" / DATASET_ID


def read_cards(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a JSON list")
    return [item for item in data if isinstance(item, dict)]


def build_record(card: dict[str, Any], existing: PokemonTcgCardRecord | None = None) -> PokemonTcgCardRecord:
    now = time.time()
    zh_json = translate_card(card)
    record = existing or PokemonTcgCardRecord(
        dataset_id=DATASET_ID,
        source_card_slug=str(card.get("source_card_slug") or ""),
        created_at=now,
    )
    record.dataset_id = DATASET_ID
    record.source = str(card.get("source") or "pkmncards")
    record.source_card_slug = str(card.get("source_card_slug") or "")
    record.source_url = str(card.get("source_url") or "")
    record.set_slug = str(card.get("set_slug") or "")
    record.set_name = str(card.get("set_name") or "")
    record.official_set_code = str(card.get("official_set_code") or "")
    record.official_number = str(card.get("official_number") or "")
    record.official_id = str(card.get("official_id") or "")
    record.official_name = str(card.get("official_name") or "")
    record.pokemon_species = str(card.get("pokemon_species") or "")
    record.display_title = str(card.get("display_title") or "")
    record.local_image_path = str(card.get("local_image_path") or "")
    record.image_sha256 = str(card.get("image_sha256") or "")
    record.raw_json = card
    record.zh_json = zh_json
    record.translation_version = TRANSLATION_VERSION
    record.fetched_at = str(card.get("fetched_at") or "")
    record.translated_at = str(zh_json.get("translated_at") or "")
    record.updated_at = now
    return record


def import_cards(*, input_path: Path) -> dict[str, int]:
    init_db()
    cards = read_cards(input_path)
    inserted = 0
    updated = 0
    with Session(engine) as session:
        for card in cards:
            source_card_slug = str(card.get("source_card_slug") or "")
            existing = session.exec(
                select(PokemonTcgCardRecord).where(
                    PokemonTcgCardRecord.dataset_id == DATASET_ID,
                    PokemonTcgCardRecord.source_card_slug == source_card_slug,
                )
            ).first()
            record = build_record(card, existing)
            if existing is None:
                inserted += 1
            else:
                updated += 1
            session.add(record)
        session.commit()
    return {"inserted": inserted, "updated": updated, "total": len(cards)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Pokemon TCG raw cards into CodeYun database with Chinese text.")
    parser.add_argument("--input", type=Path, default=dataset_root() / "raw_cards.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = import_cards(input_path=args.input.resolve(strict=True))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
