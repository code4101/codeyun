from __future__ import annotations

import argparse
import os

from backend.core.services.launcher import install_child_process_no_window_default

install_child_process_no_window_default()

from backend.core.fanxiu.history_museum.packet_capture.service_runtime import run_fanxiu_packet_service_loop


def _interval_seconds() -> float:
    try:
        return float(os.getenv("FX_PACKET_SERVICE_STATE_INTERVAL_SECONDS") or 15.0)
    except ValueError:
        return 15.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the standalone Fanxiu packet capture and decode service.")
    parser.add_argument("--state-interval", type=float, default=_interval_seconds())
    args = parser.parse_args()
    run_fanxiu_packet_service_loop(state_interval_seconds=args.state_interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
