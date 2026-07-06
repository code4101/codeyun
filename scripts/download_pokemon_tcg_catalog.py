from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.settings import get_settings


SOURCE = "pkmncards"
DATASET_ID = "childhood_base_jungle_fossil_rocket"
DEFAULT_SETS = {
    "base-set": {
        "name": "Base Set",
        "url": "https://pkmncards.com/set/base-set/",
    },
    "jungle": {
        "name": "Jungle",
        "url": "https://pkmncards.com/set/jungle/",
    },
    "fossil": {
        "name": "Fossil",
        "url": "https://pkmncards.com/set/fossil/",
    },
    "team-rocket": {
        "name": "Team Rocket",
        "url": "https://pkmncards.com/set/team-rocket/",
    },
}
REQUEST_HEADERS = {
    "User-Agent": "CodeYun personal Pokemon TCG catalog research (+local cache)",
}


@dataclass(frozen=True)
class SetCardLink:
    set_slug: str
    set_name: str
    url: str
    title: str
    image_url: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dataset_root() -> Path:
    return get_settings().data_dir / "pokemon_tcg" / DATASET_ID


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def text_of(node: Any) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def multiline_text_of(node: Any) -> str:
    if node is None:
        return ""
    paragraphs: list[str] = []
    for paragraph in node.select("p") if hasattr(node, "select") else []:
        chunks: list[str] = []
        for child in paragraph.children:
            if isinstance(child, Tag) and child.name == "br":
                chunks.append("\n")
                continue
            if isinstance(child, Tag):
                chunks.append(text_of(child))
            else:
                chunks.append(str(child))
        raw_text = " ".join(chunks)
        lines = [" ".join(line.split()) for line in raw_text.splitlines()]
        text = "\n".join(line for line in lines if line)
        if text:
            paragraphs.append(text)
    if paragraphs:
        return "\n\n".join(paragraphs)
    return text_of(node)


def request_with_retries(session: requests.Session, url: str, *, timeout: float = 30.0) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 - retry wrapper reports final failure.
            last_error = exc
            if attempt < 3:
                time.sleep(1.2 * attempt)
    raise RuntimeError(f"request failed after retries: {url}") from last_error


def parse_set_links(session: requests.Session, set_slug: str, set_info: dict[str, str]) -> list[SetCardLink]:
    response = request_with_retries(session, set_info["url"])
    soup = BeautifulSoup(response.text, "html.parser")
    result: list[SetCardLink] = []
    seen: set[str] = set()
    for article in soup.select(".type-pkmn_card"):
        anchor = article.select_one("a.card-image-link") or article.select_one('a[href*="/card/"]')
        image = article.select_one("img.card-image") or article.select_one("img")
        if anchor is None or image is None:
            continue
        url = str(anchor.get("href") or "").strip()
        image_url = str(image.get("src") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(
            SetCardLink(
                set_slug=set_slug,
                set_name=set_info["name"],
                url=url,
                title=str(anchor.get("title") or "").strip(),
                image_url=image_url,
            )
        )
    return result


def parse_number_out_of(value: str) -> tuple[str, str]:
    match = re.search(r"#\s*([A-Za-z0-9]+)\s*/\s*([A-Za-z0-9]+)", value)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def parse_release_meta(value: str) -> dict[str, str]:
    number, total = parse_number_out_of(value)
    rarity = ""
    date_text = ""
    if ":" in value:
        tail = value.split(":", 1)[1]
        rarity = tail.split("·", 1)[0].strip()
    if "↘" in value:
        date_text = value.rsplit("↘", 1)[1].strip()
    return {
        "official_number": number,
        "official_total": total,
        "rarity": rarity,
        "release_date_text": date_text,
    }


def split_weak_resist_retreat(value: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "weakness_text": "",
        "resistance_text": "",
        "retreat_cost": None,
    }
    for part in [item.strip() for item in value.split("|") if item.strip()]:
        if part.startswith("weak:"):
            result["weakness_text"] = part.removeprefix("weak:").strip()
        elif part.startswith("resist:"):
            result["resistance_text"] = part.removeprefix("resist:").strip()
        elif part.startswith("retreat:"):
            raw = part.removeprefix("retreat:").strip()
            try:
                result["retreat_cost"] = int(raw)
            except ValueError:
                result["retreat_cost"] = raw
    return result


def parse_card_page(session: requests.Session, link: SetCardLink) -> dict[str, Any]:
    response = request_with_retries(session, link.url)
    soup = BeautifulSoup(response.text, "html.parser")
    root = soup.select_one(".card-tabs .tab.text") or soup.select_one(".card-text-area") or soup
    title = text_of(soup.select_one("h1.card-title") or soup.find("h1"))
    display_title = title or link.title
    official_name = text_of(root.select_one(".name")) if hasattr(root, "select_one") else ""
    hp_text = text_of(root.select_one(".hp")) if hasattr(root, "select_one") else ""
    color = text_of(root.select_one(".color")) if hasattr(root, "select_one") else ""
    pokemon_species = text_of(root.select_one(".pokemon")) if hasattr(root, "select_one") else ""
    stage = text_of(root.select_one(".stage")) if hasattr(root, "select_one") else ""
    evolves = text_of(root.select_one(".evolves")) if hasattr(root, "select_one") else ""
    evolves_from = ""
    evolves_into = ""
    if evolves.startswith("Evolves from "):
        evolves_from = evolves.removeprefix("Evolves from ").strip()
    elif evolves.startswith("Evolves into "):
        evolves_into = evolves.removeprefix("Evolves into ").strip()
    attacks_text = multiline_text_of(root.select_one(".text")) if hasattr(root, "select_one") else ""
    weak_resist_retreat = split_weak_resist_retreat(
        text_of(root.select_one(".weak-resist-retreat")) if hasattr(root, "select_one") else ""
    )
    illustrator_text = text_of(root.select_one(".illus")) if hasattr(root, "select_one") else ""
    release_meta_text = text_of(root.select_one(".release-meta")) if hasattr(root, "select_one") else ""
    release_meta = parse_release_meta(release_meta_text)
    flavor_text = text_of(root.select_one(".flavor")) if hasattr(root, "select_one") else ""
    image = soup.select_one("img.card-image")
    image_url = str(image.get("src") or "").strip() if image else link.image_url
    official_set_code = ""
    code_match = re.search(r"\(\s*([A-Z0-9]+)\s*\)", display_title)
    if code_match:
        official_set_code = code_match.group(1)
    if not official_set_code:
        code_match = re.search(r"\(\s*([A-Z0-9]+)\s*\)", release_meta_text)
        official_set_code = code_match.group(1) if code_match else ""
    official_id = ""
    if official_set_code and release_meta["official_number"]:
        official_id = f"{official_set_code}-{release_meta['official_number']}"
    return {
        "source": SOURCE,
        "source_url": link.url,
        "source_card_slug": link.url.rstrip("/").rsplit("/", 1)[-1],
        "set_name": link.set_name,
        "set_slug": link.set_slug,
        "official_set_code": official_set_code,
        "official_number": release_meta["official_number"],
        "official_total": release_meta["official_total"],
        "official_id": official_id,
        "official_name": official_name,
        "display_title": display_title,
        "pokemon_species": pokemon_species,
        "hp": hp_text.removesuffix(" HP").strip(),
        "color": color,
        "stage": stage,
        "evolves_from": evolves_from,
        "evolves_into": evolves_into,
        "is_dark": official_name.startswith("Dark "),
        "attacks_text": attacks_text,
        "weakness_text": weak_resist_retreat["weakness_text"],
        "resistance_text": weak_resist_retreat["resistance_text"],
        "retreat_cost": weak_resist_retreat["retreat_cost"],
        "illustrator_text": illustrator_text,
        "rarity": release_meta["rarity"],
        "release_date_text": release_meta["release_date_text"],
        "release_meta_text": release_meta_text,
        "flavor_text": flavor_text,
        "image_url": image_url,
        "local_image_path": "",
        "image_sha256": "",
        "image_bytes": 0,
        "raw_text": text_of(root),
        "fetched_at": utc_now_iso(),
    }


def extension_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return ".jpg"


def image_relative_path(card: dict[str, Any]) -> Path:
    set_slug = str(card["set_slug"])
    card_slug = str(card["source_card_slug"])
    return Path("images") / set_slug / f"{card_slug}{extension_from_url(str(card['image_url']))}"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def download_image(
    session: requests.Session,
    root: Path,
    card: dict[str, Any],
    *,
    force: bool,
) -> dict[str, Any]:
    relative_path = image_relative_path(card)
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if force or not path.exists():
        response = request_with_retries(session, str(card["image_url"]), timeout=45.0)
        path.write_bytes(response.content)
    raw = path.read_bytes()
    card["local_image_path"] = relative_path.as_posix()
    card["image_sha256"] = sha256_bytes(raw)
    card["image_bytes"] = len(raw)
    return card


def species_key(card: dict[str, Any]) -> str:
    value = str(card.get("pokemon_species") or card.get("official_name") or card["source_card_slug"]).strip()
    if value.startswith("Dark "):
        value = value.removeprefix("Dark ").strip()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def build_default_policy(cards: list[dict[str, Any]]) -> dict[str, Any]:
    set_priority = {"base-set": 0, "jungle": 1, "fossil": 2, "team-rocket": 3}
    sorted_cards = sorted(
        cards,
        key=lambda item: (
            species_key(item),
            set_priority.get(str(item["set_slug"]), 99),
            int(item["official_number"]) if str(item["official_number"]).isdigit() else 999,
            str(item["source_card_slug"]),
        ),
    )
    catalog_no_by_species: dict[str, int] = {}
    variant_count_by_species: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    for card in sorted_cards:
        key = species_key(card)
        if not key:
            continue
        if key not in catalog_no_by_species:
            catalog_no_by_species[key] = len(catalog_no_by_species) + 1
        variant_count_by_species[key] = variant_count_by_species.get(key, 0) + 1
        variant_no = variant_count_by_species[key]
        catalog_no = catalog_no_by_species[key]
        display_no = str(catalog_no) if variant_no == 1 else f"{catalog_no}.{variant_no}"
        entries.append(
            {
                "catalog_no": catalog_no,
                "variant_no": variant_no,
                "display_no": display_no,
                "species_key": key,
                "source_card_slug": card["source_card_slug"],
                "official_id": card["official_id"],
                "official_name": card["official_name"],
                "set_name": card["set_name"],
                "is_dark": card["is_dark"],
            }
        )
    return {
        "policy_id": "default_species_with_variants_v1",
        "description": "按宝可梦物种分组；同物种下保留不同官方卡版本；variant_no=1 展示为主编号。",
        "generated_at": utc_now_iso(),
        "entry_count": len(entries),
        "species_count": len(catalog_no_by_species),
        "entries": entries,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download early Pokemon TCG card catalog snapshots from PkmnCards.")
    parser.add_argument(
        "--sets",
        nargs="+",
        choices=sorted(DEFAULT_SETS),
        default=sorted(DEFAULT_SETS),
        help="Set slugs to download.",
    )
    parser.add_argument("--force-images", action="store_true", help="Re-download images even if cached.")
    parser.add_argument("--no-images", action="store_true", help="Only download structured card data.")
    parser.add_argument("--output", type=Path, default=None, help="Override output directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.output.resolve(strict=False) if args.output else dataset_root()
    root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    selected_sets = {slug: DEFAULT_SETS[slug] for slug in args.sets}
    progress_path = root / "progress.json"
    write_json(
        progress_path,
        {
            "status": "running",
            "started_at": utc_now_iso(),
            "sets": list(selected_sets),
            "done_count": 0,
            "target_count": None,
        },
    )

    links: list[SetCardLink] = []
    for set_slug, set_info in selected_sets.items():
        set_links = parse_set_links(session, set_slug, set_info)
        links.extend(set_links)
        print(f"{set_info['name']}: {len(set_links)} cards")

    raw_cards_path = root / "raw_cards.json"
    cards: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, link in enumerate(links, start=1):
        try:
            card = parse_card_page(session, link)
            if not args.no_images:
                card = download_image(session, root, card, force=args.force_images)
            cards.append(card)
        except Exception as exc:  # noqa: BLE001 - keep partial snapshot useful.
            errors.append({"url": link.url, "error": str(exc)})
        if index % 20 == 0 or index == len(links):
            cards.sort(
                key=lambda item: (
                    str(item["set_slug"]),
                    int(item["official_number"]) if str(item["official_number"]).isdigit() else 999,
                )
            )
            write_json(raw_cards_path, cards)
            write_json(
                progress_path,
                {
                    "status": "running",
                    "updated_at": utc_now_iso(),
                    "sets": list(selected_sets),
                    "done_count": index,
                    "success_count": len(cards),
                    "error_count": len(errors),
                    "target_count": len(links),
                    "errors": errors[-10:],
                },
            )
            print(f"parsed {index}/{len(links)} cards, success={len(cards)}, errors={len(errors)}")

    cards.sort(key=lambda item: (str(item["set_slug"]), int(item["official_number"]) if str(item["official_number"]).isdigit() else 999))
    policy = build_default_policy(cards)
    fetched_at = utc_now_iso()
    manifest = {
        "dataset_id": DATASET_ID,
        "source": SOURCE,
        "fetched_at": fetched_at,
        "sets": [
            {
                "slug": slug,
                "name": info["name"],
                "url": info["url"],
            }
            for slug, info in selected_sets.items()
        ],
        "card_count": len(cards),
        "error_count": len(errors),
        "image_count": sum(1 for card in cards if card.get("local_image_path")),
        "image_bytes": sum(int(card.get("image_bytes") or 0) for card in cards),
        "files": {
            "raw_cards": "raw_cards.json",
            "catalog_policy_default": "catalog_policy_default.json",
            "progress": "progress.json",
        },
        "errors": errors,
    }
    write_json(raw_cards_path, cards)
    write_json(root / "catalog_policy_default.json", policy)
    write_json(root / "manifest.json", manifest)
    write_json(
        progress_path,
        {
            "status": "done" if not errors else "done_with_errors",
            "finished_at": fetched_at,
            "done_count": len(links),
            "success_count": len(cards),
            "error_count": len(errors),
            "target_count": len(links),
            "errors": errors,
        },
    )
    print(f"wrote {root}")
    print(f"cards={len(cards)} images={manifest['image_count']} image_bytes={manifest['image_bytes']}")
    if errors:
        print(f"errors={len(errors)}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
