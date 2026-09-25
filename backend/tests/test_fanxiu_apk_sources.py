"""APK 输入解析的文件系统契约；不调用模拟器或反编译工具。"""
import subprocess
import sys

import pytest

from backend.core.fanxiu.catalog.apk_sources import (
    FANXIU_APK_UNPACKED_ROOT_ENV,
    resolve_fanxiu_apk_unpacked_root,
)
from backend.core.fanxiu.catalog.resources import FanxiuResourceError


@pytest.mark.parametrize("marker", ["classes.dex", "classes2.dex", "AndroidManifest.xml"])
def test_explicit_apk_root_takes_precedence_over_environment(tmp_path, monkeypatch, marker):
    (tmp_path / marker).touch()
    monkeypatch.setenv(FANXIU_APK_UNPACKED_ROOT_ENV, str(tmp_path / "absent"))
    assert resolve_fanxiu_apk_unpacked_root(tmp_path) == tmp_path.resolve()


def test_environment_apk_root_is_validated(tmp_path, monkeypatch):
    monkeypatch.setenv(FANXIU_APK_UNPACKED_ROOT_ENV, str(tmp_path))
    with pytest.raises(FanxiuResourceError, match="缺少"):
        resolve_fanxiu_apk_unpacked_root()
    (tmp_path / "AndroidManifest.xml").touch()
    assert resolve_fanxiu_apk_unpacked_root() == tmp_path.resolve()


def test_missing_or_file_path_is_rejected(tmp_path):
    with pytest.raises(FanxiuResourceError, match="不存在"):
        resolve_fanxiu_apk_unpacked_root(tmp_path / "missing")
    plain = tmp_path / "file"
    plain.touch()
    with pytest.raises(FanxiuResourceError, match="不是目录"):
        resolve_fanxiu_apk_unpacked_root(plain)


def test_metadata_and_path_consumers_do_not_load_apk_index_builder():
    code = """
import sys
from backend.core.fanxiu.catalog import il2cpp_metadata, lua_logic_index, visual
assert 'backend.core.fanxiu.catalog.apk_static' not in sys.modules
from backend.core.fanxiu.catalog import apk_static, apk_sources
assert apk_static.resolve_fanxiu_apk_unpacked_root is apk_sources.resolve_fanxiu_apk_unpacked_root
assert apk_static.APK_INDEX_DEFAULT_KEYWORDS is apk_sources.APK_INDEX_DEFAULT_KEYWORDS
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
