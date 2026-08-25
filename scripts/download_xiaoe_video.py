from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.xiaoe_video_archive import download_hls_video


def main() -> None:
    parser = argparse.ArgumentParser(description="下载并校验一条小鹅通 HLS 视频")
    parser.add_argument("--playlist-url", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    result = download_hls_video(
        playlist_url=args.playlist_url,
        title=args.title,
        published_at=args.published_at,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
