from __future__ import annotations

import json
from typing import Any

from backend.core.settings import get_settings


HKEX_SECURITIES_URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"


def load_hkex_board_lots(*, refresh: bool = False) -> dict[str, int]:
    cache_path = get_settings().data_dir / "stock" / "hkex_board_lots.json"
    if not refresh:
        cached = _read_board_lot_cache(cache_path)
        if cached:
            return cached
    try:
        import pandas as pd

        frame = pd.read_excel(HKEX_SECURITIES_URL, header=2, dtype={"Stock Code": str})
        board_lots: dict[str, int] = {}
        for row in frame.to_dict("records"):
            symbol = _normalize_hk_symbol(row.get("Stock Code"))
            lot_size = _parse_board_lot(row.get("Board Lot"))
            if symbol and lot_size:
                board_lots[symbol] = lot_size
        if board_lots:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(board_lots, ensure_ascii=False, indent=2), encoding="utf-8")
        return board_lots
    except Exception:
        return _read_board_lot_cache(cache_path)


def _read_board_lot_cache(path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in data.items():
        symbol = _normalize_hk_symbol(key)
        lot_size = _parse_board_lot(value)
        if symbol and lot_size:
            result[symbol] = lot_size
    return result


def _normalize_hk_symbol(value: Any) -> str:
    text = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
    return text.zfill(5) if text else ""


def _parse_board_lot(value: Any) -> int | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        lot_size = int(float(text))
    except (TypeError, ValueError):
        return None
    return lot_size if lot_size > 0 else None
