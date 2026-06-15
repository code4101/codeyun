from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.catalog.audio import build_fanxiu_wwise_mp3_export


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Fanxiu Wwise BNK/WEM audio to MP3.")
    parser.add_argument("--resource-root", default=None)
    parser.add_argument("--export-root", default=None)
    parser.add_argument("--vgmstream-cli", default=None)
    parser.add_argument("--ffmpeg", dest="ffmpeg_path", default=None)
    parser.add_argument("--max-banks", type=int, default=None)
    parser.add_argument("--max-entries", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--mp3-quality", type=int, default=4)
    args = parser.parse_args()

    result = build_fanxiu_wwise_mp3_export(
        resource_root=args.resource_root,
        export_root=args.export_root,
        vgmstream_cli=args.vgmstream_cli,
        ffmpeg_path=args.ffmpeg_path,
        max_banks=args.max_banks,
        max_entries=args.max_entries,
        overwrite=args.overwrite,
        mp3_quality=args.mp3_quality,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
