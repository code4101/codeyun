import ast
import subprocess
import sys

import pytest

from backend.core.fanxiu.instrumentation.catalog_collection import (
    build_catalog_collection_code,
    collect_catalog_snapshot,
)


@pytest.mark.parametrize("kind", ["wardrobe", "magic_treasure", "xianyuan", "gongfa"])
def test_catalog_cell_calls_one_public_collector(kind):
    code = build_catalog_collection_code(kind)
    compile(code, "catalog-cell", "exec")
    tree = ast.parse(code)
    assert len(tree.body) == 2
    assert isinstance(tree.body[0], ast.ImportFrom)
    assert tree.body[0].module == "backend.core.fanxiu.instrumentation.catalog_collection"
    call = tree.body[1].value.args[0]
    assert call.func.id == "collect_catalog_snapshot"
    assert ast.literal_eval(call.args[0]) == kind


def test_unknown_catalog_is_rejected_before_build_or_collection():
    for operation in (build_catalog_collection_code, collect_catalog_snapshot):
        with pytest.raises(ValueError, match="不支持的图鉴采集类型"):
            operation("unknown'); arbitrary_code()")


def test_import_does_not_load_game_collectors_or_http():
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
import backend.core.fanxiu.instrumentation.catalog_collection
for name in (
    'backend.api.fanxiu',
    'backend.core.fanxiu.instrumentation.wardrobe_collector',
    'backend.core.fanxiu.instrumentation.magic_treasure_collector',
    'backend.core.fanxiu.instrumentation.xianyuan_atlas',
    'backend.core.fanxiu.instrumentation.gongfa_atlas',
):
    assert name not in sys.modules, name
"""],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
