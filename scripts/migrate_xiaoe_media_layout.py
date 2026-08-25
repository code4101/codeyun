from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.xiaoe_media_layout import migrate_legacy_video_layout


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移小鹅通视频/音频分层归档结构")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate_legacy_video_layout(args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
