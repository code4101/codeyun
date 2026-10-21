"""Refresh the CodeHub online rejection table, without Excel or WeChat.

Run from CodeYun: ``uv run python scripts/frontierbench_sync.py``.
``--collect-only`` retains a validated snapshot without publishing it;
``--snapshot FILE`` retries the exact saved payload after a network failure.
The paired CodeHub node owns import validation, idempotency and comparisons.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / "xlproject" / "src")]
import xlproject.loadenv  # noqa: E402,F401

from backend.core.frontierbench import (  # noqa: E402
    collect_snapshot, SERIES, SCOPE, CODEHUB_SNAPSHOT_API,
)
from backend.core.settings import get_settings  # noqa: E402


def publish(snapshot: dict, *, peer_name: str, api_path: str) -> dict:
    """Publish once through a paired node's public API; never retry writes blindly."""
    from lingnet.config import ConfigStore
    from lingnet.peers import PeerClient

    target = urlsplit(api_path)
    if target.scheme or target.netloc or target.query or target.fragment or not api_path.startswith("/api/"):
        raise ValueError("同步地址须为 CodeHub 同源的 /api/ 路径")
    peer = ConfigStore(get_settings().data_dir / "lingnet").peer(peer_name)
    origin = urlsplit(peer["url"])
    # Keep the operator's configured network route and certificate validation.
    # A failed POST is never automatically replayed on a different route.
    manifest = PeerClient(peer["url"], peer["token"], timeout=20).manifest()
    if not manifest.get("enabled") or manifest.get("device_id") != peer.get("node_id"):
        raise RuntimeError("CodeHub 节点身份或启用状态不匹配，未上传数据")
    client = PeerClient(f"{origin.scheme}://{origin.netloc}", peer["token"], timeout=40)
    return client.request("POST", api_path, snapshot)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--peer", default="CodeHub")
    parser.add_argument("--sync-api", default=CODEHUB_SNAPSHOT_API, help="CodeHub 公共快照导入接口路径")
    args = parser.parse_args()
    snapshot = (json.loads(args.snapshot.read_text(encoding="utf-8"))
                if args.snapshot else collect_snapshot())
    if (snapshot.get("source") != "skyvendor" or snapshot.get("series") != SERIES
            or snapshot.get("scope") != SCOPE or snapshot.get("complete") is not True
            or not isinstance(snapshot.get("rows"), list) or not snapshot.get("projects")):
        raise ValueError("输入须为完整、已核验的 frontier-bench-fw 抓取快照")
    captured = datetime.fromisoformat(snapshot["captured_at"])
    if captured.tzinfo is None:
        raise ValueError("快照抓取时间须包含时区")
    directory = get_settings().data_dir / "frontierbench"
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / f"snapshot-{captured.strftime('%Y%m%dT%H%M%S%z')}.json"
    file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"snapshot": str(file), "captured_at": snapshot["captured_at"],
              "rows": len(snapshot["rows"])}
    if not args.collect_only:
        try:
            result = publish(snapshot, peer_name=args.peer, api_path=args.sync_api)
        except Exception as exc:
            raise RuntimeError(f"同步未确认；快照已保存在 {file}。先核查在线表状态，再用 --snapshot 重试。{exc}") from exc
        receipt = file.with_suffix(".receipt.json")
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        remote = result.get("snapshot") or {}
        report["sync"] = {"created": result.get("created"), "stale": result.get("stale"),
                          "is_latest": result.get("is_latest"),
                          "snapshot_id": remote.get("id"), "counts": result.get("counts"),
                          "last_checked_at": remote.get("last_checked_at") or remote.get("last_seen_at")}
        report["receipt"] = str(receipt)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FrontierBench 更新失败（未自动重试）：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
