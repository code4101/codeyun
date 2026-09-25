"""APK 静态资料的共同输入契约，不依赖索引构建或元数据解析器。

显式路径优先于环境变量，最后使用本机默认解包目录；解析只验证目录，
不触发下载、解包或索引构建。
"""
from __future__ import annotations

import os
from pathlib import Path

from .resources import FanxiuResourceError


FANXIU_APK_UNPACKED_ROOT_ENV = "FANXIU_APK_UNPACKED_ROOT"


DEFAULT_FANXIU_APK_UNPACKED_ROOT = Path(
    r"C:\TapTap\Support\android_emulator\games\308550\apk\1023295_unpacked"
)


APK_INDEX_DEFAULT_KEYWORDS = (
    "UnityPlayerActivity",
    "UnityPlayer",
    "loadLibrary",
    "il2cpp",
    "tolua",
    "Lua",
    "AssetBundle",
    "filelist",
    "resdownload",
    "download",
    "hotfix",
    "patch",
    "version",
    "md5",
    "encrypt",
    "decrypt",
    "http",
    "https",
    "cdn",
    "frxx",
    "gongfa",
    "resource",
    "config",
    "功法",
    "玄魔",
    "法宝",
    "仙侣",
    "资源",
    "下载",
    "加密",
    "解密",
    "热更",
)


def resolve_fanxiu_apk_unpacked_root(apk_root: str | os.PathLike[str] | None = None) -> Path:
    value = apk_root or os.environ.get(FANXIU_APK_UNPACKED_ROOT_ENV) or DEFAULT_FANXIU_APK_UNPACKED_ROOT
    root = Path(value).expanduser().resolve()
    if not root.exists():
        raise FanxiuResourceError(f"APK 解包目录不存在：{root}")
    if not root.is_dir():
        raise FanxiuResourceError(f"APK 解包路径不是目录：{root}")
    has_dex = any(root.glob("classes*.dex"))
    has_manifest = (root / "AndroidManifest.xml").exists()
    if not has_dex and not has_manifest:
        raise FanxiuResourceError(f"目录不像 APK 解包目录，缺少 classes*.dex 或 AndroidManifest.xml：{root}")
    return root
