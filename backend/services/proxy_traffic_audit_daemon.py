from __future__ import annotations

import argparse
import os
from pathlib import Path

from backend.core.runtime.proxy_traffic_audit import (
    DEFAULT_MIHOMO_PIPE,
    ProxyTrafficAuditCollector,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Mihomo proxy traffic deltas.")
    parser.add_argument("--interval", type=float, default=float(os.getenv("CODEYUN_PROXY_TRAFFIC_AUDIT_INTERVAL") or 2.0))
    parser.add_argument("--db", default=os.getenv("CODEYUN_PROXY_TRAFFIC_AUDIT_DB") or "")
    parser.add_argument("--pipe", default=os.getenv("CODEYUN_PROXY_TRAFFIC_AUDIT_PIPE") or DEFAULT_MIHOMO_PIPE)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve(strict=False) if args.db else None
    collector = ProxyTrafficAuditCollector(db_path=db_path, pipe_path=args.pipe)
    collector.run_loop(interval_seconds=args.interval, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
