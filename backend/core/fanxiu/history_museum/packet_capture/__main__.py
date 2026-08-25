from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="凡修历史博物馆：人工抓包守护")
    parser.add_argument(
        "--i-understand-this-is-retired",
        action="store_true",
        help="确认这是独立人工取证，不属于 CodeYun 生产或作业链路",
    )
    parser.add_argument("--state-interval", type=float, default=15.0)
    args = parser.parse_args()
    if not args.i_understand_this_is_retired:
        parser.error("必须显式传入 --i-understand-this-is-retired")

    from backend.core.fanxiu.history_museum.packet_capture.service_runtime import (
        run_fanxiu_packet_service_loop,
    )

    run_fanxiu_packet_service_loop(state_interval_seconds=args.state_interval)


if __name__ == "__main__":
    main()
