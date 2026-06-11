from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.settings import get_settings
from backend.core.stock.akshare_market import fetch_akshare_stock_history
from backend.core.stock.qlib_bridge import QLIB_EXPORT_START_DATE, QlibWatchTarget, _analyze_rows, _cache_daily_rows
from backend.core.stock.qlib_screening import HK_POOL, QlibScreenTarget, _load_hk_pool_rows, _normalize_hk_symbol


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync HK stock pool daily history and Qlib factor scores.")
    parser.add_argument("--limit", type=int, default=0, help="Limit target count for debugging; 0 means all.")
    parser.add_argument("--sleep", type=float, default=0.15, help="Sleep seconds between symbols.")
    parser.add_argument("--start-date", default=QLIB_EXPORT_START_DATE)
    args = parser.parse_args()

    data_dir = get_settings().data_dir / "stock" / "qlib"
    progress_path = data_dir / "hk_pool_sync_progress.json"
    score_path = data_dir / "hk_pool_scores.json"
    data_dir.mkdir(parents=True, exist_ok=True)

    rows, source = _load_hk_pool_rows(refresh=True)
    targets = _targets_from_rows(rows, start_date=args.start_date)
    if args.limit > 0:
        targets = targets[: args.limit]

    scores: list[dict] = []
    started_at = dt.datetime.now().isoformat(timespec="seconds")
    _write_json(
        progress_path,
        {
            "status": "running",
            "source": source,
            "started_at": started_at,
            "updated_at": started_at,
            "total": len(targets),
            "done": 0,
            "success": 0,
            "failed": 0,
            "current": "",
            "error": "",
        },
    )

    success = 0
    failed = 0
    for index, target in enumerate(targets, start=1):
        current = f"{target.market}.{target.symbol} {target.name}"
        try:
            history = fetch_akshare_stock_history(
                market=target.market,
                symbol=target.symbol,
                name=target.name,
                period="daily",
                start_date=target.start_date,
                end_date=dt.date.today().isoformat(),
                adjust="",
            )
            if history.rows:
                _cache_daily_rows(target.qlib_target, history.rows)
            item = type("_SyncItem", (), {"source": "akshare", "error": "" if history.rows else "没有日线数据"})()
            analysis = _analyze_rows(target=target.qlib_target, item=item, rows=history.rows)
            scores.append(_score_row(target, analysis))
            success += 1 if history.rows else 0
            failed += 0 if history.rows else 1
        except Exception as exc:
            item = type("_SyncItem", (), {"source": "akshare", "error": str(exc)})()
            analysis = _analyze_rows(target=target.qlib_target, item=item, rows=())
            scores.append(_score_row(target, analysis))
            failed += 1

        _write_json(
            progress_path,
            {
                "status": "running",
                "source": source,
                "started_at": started_at,
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "total": len(targets),
                "done": index,
                "success": success,
                "failed": failed,
                "current": current,
                "error": "",
            },
        )
        _write_json(score_path, sorted(scores, key=lambda row: (row["score"] is not None, row["score"] or -1), reverse=True))
        if args.sleep > 0:
            time.sleep(args.sleep)

    _write_json(
        progress_path,
        {
            "status": "completed",
            "source": source,
            "started_at": started_at,
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "total": len(targets),
            "done": len(targets),
            "success": success,
            "failed": failed,
            "current": "",
            "error": "",
        },
    )


def _targets_from_rows(rows, *, start_date: str) -> list[QlibScreenTarget]:
    unique: dict[str, QlibScreenTarget] = {}
    for row in rows:
        symbol = _normalize_hk_symbol(row.get("代码"))
        if not symbol:
            continue
        name = str(row.get("名称") or row.get("中文名称") or symbol).strip() or symbol
        unique.setdefault(symbol, QlibScreenTarget(market="HK", symbol=symbol, name=name, pool=HK_POOL, start_date=start_date))
    return sorted(unique.values(), key=lambda target: target.symbol)


def _score_row(target: QlibScreenTarget, analysis) -> dict:
    return {
        "pool": target.pool,
        "market": target.market,
        "symbol": target.symbol,
        "name": target.name,
        "score": analysis.score,
        "signal": analysis.signal,
        "row_count": analysis.row_count,
        "start_date": analysis.start_date,
        "end_date": analysis.end_date,
        "return_5": analysis.return_5,
        "return_20": analysis.return_20,
        "return_60": analysis.return_60,
        "ma_20_distance": analysis.ma_20_distance,
        "volume_ratio_5_20": analysis.volume_ratio_5_20,
        "source": analysis.source,
        "error": analysis.error,
    }


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
