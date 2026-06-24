from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.data_annotation.scene_identity_migration import migrate_scene_identity_levels_file
from backend.core.fanxiu.runtime.behavior_tree import DEFAULT_FANXIU_ENTRY_ID, data_annotation_asset_tree_path


def main() -> int:
    parser = argparse.ArgumentParser(description="把凡修资产树旧 shape 场景标识迁移为 frame.sceneIdentityLevel。")
    parser.add_argument("--entry-id", default=DEFAULT_FANXIU_ENTRY_ID, help="资产树 entry_id，默认使用当前凡修入口。")
    parser.add_argument("--asset-tree", type=Path, default=None, help="显式指定资产树 JSON 路径。")
    parser.add_argument("--write", action="store_true", help="实际写回资产树；默认只 dry-run 输出统计。")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有 sceneIdentityLevel；默认保留已有显式值。")
    parser.add_argument("--no-backup", action="store_true", help="写回时不创建 .bak 备份。")
    args = parser.parse_args()

    path = args.asset_tree or data_annotation_asset_tree_path(args.entry_id)
    result = migrate_scene_identity_levels_file(
        path,
        write=bool(args.write),
        overwrite=bool(args.overwrite),
        backup=not bool(args.no_backup),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
