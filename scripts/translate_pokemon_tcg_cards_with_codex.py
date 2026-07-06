from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.settings import get_settings
from backend.core.pokemon_tcg_translation import (
    translate_energy_symbols,
    translate_rarity,
    translate_resistance,
    translate_set_name,
    translate_stage,
    translate_weakness,
    zh_name,
)
from backend.db import engine, init_db
from backend.models import PokemonTcgCardRecord


DATASET_ID = "childhood_base_jungle_fossil_rocket"
MODEL_NAME = "gpt-5.3-codex-spark"
TRANSLATION_VERSION = "codex_spark_zh_v1"


def dataset_root() -> Path:
    return get_settings().data_dir / "pokemon_tcg" / DATASET_ID


def read_cards(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a JSON list")
    return [item for item in data if isinstance(item, dict)]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_card_slug": card.get("source_card_slug", ""),
        "display_title": card.get("display_title", ""),
        "set_name": card.get("set_name", ""),
        "official_set_code": card.get("official_set_code", ""),
        "official_number": card.get("official_number", ""),
        "official_total": card.get("official_total", ""),
        "official_name": card.get("official_name", ""),
        "pokemon_species": card.get("pokemon_species", ""),
        "hp": card.get("hp", ""),
        "color": card.get("color", ""),
        "stage": card.get("stage", ""),
        "evolves_from": card.get("evolves_from", ""),
        "evolves_into": card.get("evolves_into", ""),
        "attacks_text": card.get("attacks_text", ""),
        "weakness_text": card.get("weakness_text", ""),
        "resistance_text": card.get("resistance_text", ""),
        "retreat_cost": card.get("retreat_cost"),
        "rarity": card.get("rarity", ""),
        "release_date_text": card.get("release_date_text", ""),
        "illustrator_text": card.get("illustrator_text", ""),
        "flavor_text": card.get("flavor_text", ""),
    }


def build_prompt(cards: list[dict[str, Any]]) -> str:
    payload = [compact_card(card) for card in cards]
    return (
        "你是宝可梦集换式卡牌游戏旧版英文卡牌的简体中文本地化译者。\n"
        "把输入 JSON 数组里的每张卡翻译成自然、准确的中文。要求：\n"
        "1. 只输出一个 JSON 数组，不要 Markdown，不要解释。\n"
        "2. 数组长度和顺序必须与输入一致，每项必须保留 source_card_slug。\n"
        "3. 宝可梦名称使用简体中文官方常用译名；Dark X 译为“黑暗X”。\n"
        "4. TCG 术语保持稳定：Pokémon Power=宝可梦特殊能力，Defending Pokémon=防守宝可梦，"
        "Benched Pokémon=备战宝可梦，damage counter=伤害指示物，retreat cost=撤退费用。\n"
        "5. 能量符号翻译为中文属性名：{C}=无色，{G}=草，{R}=火，{W}=水，{L}=雷，{P}=超能力，{F}=斗。\n"
        "6. 招式说明要完整翻译，不要残留英文规则句；数值、符号、伤害倍率保留。\n"
        "7. 图鉴文本翻译成通顺中文，不要编造原文没有的信息。\n"
        "8. 输出字段固定为：source_card_slug, display_title, set_name, official_name, pokemon_species, "
        "hp_text, color, stage, evolves_from, evolves_into, attacks_text, weakness_text, resistance_text, "
        "retreat_cost, rarity, release_date_text, illustrator_text, flavor_text, source_label。\n"
        "输入：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def extract_json_array(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start < 0 or end < start:
            raise
        data = json.loads(stripped[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("translation output is not a JSON array")
    return [item for item in data if isinstance(item, dict)]


def call_codex(prompt: str, *, timeout: int) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as output_file:
        output_path = Path(output_file.name)
    try:
        codex_exe = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
        if not codex_exe:
            raise RuntimeError("codex CLI not found in PATH")
        command = [
            codex_exe,
            "exec",
            "-m",
            MODEL_NAME,
            "-s",
            "read-only",
            "--ephemeral",
            "--ignore-user-config",
            "--disable",
            "image_generation",
            "--output-last-message",
            str(output_path),
            "-",
        ]
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stdout[-4000:])
        return output_path.read_text(encoding="utf-8")
    finally:
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass


def translate_batch(cards: list[dict[str, Any]], *, timeout: int) -> list[dict[str, Any]]:
    text = call_codex(build_prompt(cards), timeout=timeout)
    translated = extract_json_array(text)
    if len(translated) != len(cards):
        raise ValueError(f"expected {len(cards)} translations, got {len(translated)}")
    expected_slugs = [str(card.get("source_card_slug") or "") for card in cards]
    actual_slugs = [str(item.get("source_card_slug") or "") for item in translated]
    if actual_slugs != expected_slugs:
        raise ValueError(f"slug mismatch: expected {expected_slugs}, got {actual_slugs}")
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for card, item in zip(cards, translated):
        set_zh = translate_set_name(str(card.get("set_name") or ""))
        code = str(card.get("official_set_code") or "")
        number = str(card.get("official_number") or "")
        name_zh = zh_name(str(card.get("official_name") or ""))
        species_zh = zh_name(str(card.get("pokemon_species") or card.get("official_name") or ""))
        hp = str(card.get("hp") or "")
        item["display_title"] = f"{name_zh} · {set_zh}（{code}）#{number}"
        item["set_name"] = set_zh
        item["official_name"] = name_zh
        item["pokemon_species"] = species_zh
        item["hp_text"] = f"{hp} HP" if hp else ""
        item["color"] = translate_energy_symbols(str(card.get("color") or ""))
        item["stage"] = translate_stage(str(card.get("stage") or ""))
        item["evolves_from"] = zh_name(str(card.get("evolves_from") or ""))
        item["evolves_into"] = zh_name(str(card.get("evolves_into") or ""))
        item["weakness_text"] = translate_weakness(str(card.get("weakness_text") or ""))
        item["resistance_text"] = translate_resistance(str(card.get("resistance_text") or ""))
        item["retreat_cost"] = card.get("retreat_cost")
        item["rarity"] = translate_rarity(str(card.get("rarity") or ""))
        item["release_date_text"] = str(card.get("release_date_text") or "")
        item["illustrator_text"] = str(item.get("illustrator_text") or card.get("illustrator_text") or "").replace("illus.", "插画：")
        item["translation_version"] = TRANSLATION_VERSION
        item["translated_at"] = now
    return translated


def merge_translations(cards: list[dict[str, Any]], translations: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for card in cards:
        slug = str(card.get("source_card_slug") or "")
        if slug in translations:
            merged.append(translations[slug])
    return merged


def update_database(cards: list[dict[str, Any]], translations: dict[str, dict[str, Any]]) -> int:
    init_db()
    updated = 0
    by_slug = {str(card.get("source_card_slug") or ""): card for card in cards}
    with Session(engine) as session:
        for slug, zh_json in translations.items():
            card = by_slug.get(slug)
            if not card:
                continue
            record = session.exec(
                select(PokemonTcgCardRecord).where(
                    PokemonTcgCardRecord.dataset_id == DATASET_ID,
                    PokemonTcgCardRecord.source_card_slug == slug,
                )
            ).first()
            now = time.time()
            if record is None:
                record = PokemonTcgCardRecord(dataset_id=DATASET_ID, source_card_slug=slug, created_at=now)
            record.source = str(card.get("source") or "pkmncards")
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
            session.add(record)
            updated += 1
        session.commit()
    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate Pokemon TCG card text with Codex CLI.")
    parser.add_argument("--input", type=Path, default=dataset_root() / "raw_cards.json")
    parser.add_argument("--output", type=Path, default=dataset_root() / "translations_codex_spark.json")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-db", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cards = read_cards(args.input.resolve(strict=True))
    if args.offset:
        cards = cards[args.offset:]
    if args.limit:
        cards = cards[:args.limit]
    translations: dict[str, dict[str, Any]] = {}
    if args.resume and args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if isinstance(existing, list):
            translations = {str(item.get("source_card_slug") or ""): item for item in existing if isinstance(item, dict)}
    pending = [card for card in cards if str(card.get("source_card_slug") or "") not in translations]
    for index in range(0, len(pending), args.batch_size):
        batch = pending[index:index + args.batch_size]
        batch_translations = translate_batch(batch, timeout=args.timeout)
        for item in batch_translations:
            translations[str(item["source_card_slug"])] = item
        ordered = merge_translations(cards, translations)
        write_json(args.output, ordered)
        print(f"translated {min(index + len(batch), len(pending))}/{len(pending)} pending, total={len(ordered)}")
    ordered = merge_translations(cards, translations)
    write_json(args.output, ordered)
    updated = 0 if args.no_db else update_database(cards, translations)
    print(json.dumps({"translated": len(ordered), "db_updated": updated, "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
