from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from . import (
    VIDEO_REVIEW_LIMIT,
    download_bilibili_media,
    refill_video_review_batch,
    refresh_bilibili_result_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download explicit Bilibili URLs at the highest quality available to the current browser session.",
    )
    parser.add_argument("urls", nargs="+", help="One or more bilibili.com/video/BV... URLs")
    parser.add_argument("--root-dir", required=True, help="Root containing 1、video / 2、video / 3、video")
    parser.add_argument("--review-limit", type=int, default=VIDEO_REVIEW_LIMIT)
    args = parser.parse_args()

    results = [download_bilibili_media(url, root_dir=args.root_dir) for url in args.urls]
    refill = refill_video_review_batch(args.root_dir, limit=min(max(args.review_limit, 0), VIDEO_REVIEW_LIMIT))
    results = [refresh_bilibili_result_path(item, root_dir=args.root_dir) for item in results]
    print(json.dumps({"items": [asdict(item) for item in results], "refill": refill}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
