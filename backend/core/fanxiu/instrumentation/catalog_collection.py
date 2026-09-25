"""图鉴采集的 Kernel 入口。

HTTP 调用方指定图鉴名称；模块加载、兼容刷新及诊断摘要由所属适配器负责。
collect_catalog_snapshot 必须在已有的普通 Kernel Cell 内执行，沿用各采集器
的只读游戏采集与快照落盘流程，不创建第二个执行器。
"""

from typing import Any, Literal

CatalogCollectionKind = Literal["wardrobe", "magic_treasure", "xianyuan", "gongfa"]


def _collect_wardrobe() -> dict[str, Any]:
    import importlib
    import backend.core.fanxiu.instrumentation.wardrobe as wardrobe_runtime
    import backend.core.fanxiu.instrumentation.wardrobe_collector as wardrobe_collector
    importlib.reload(wardrobe_runtime)
    importlib.reload(wardrobe_collector)
    snapshot = wardrobe_collector.collect_wardrobe_snapshot_once()
    return {'runtime_item_count': snapshot.get('runtime_item_count'), 'runtime_owned_count': snapshot.get('runtime_owned_count'), 'runtime_updated_at': snapshot.get('runtime_updated_at')}


def _collect_xianyuan() -> dict[str, Any]:
    import importlib
    import backend.core.fanxiu.instrumentation.xianyuan_atlas as atlas
    importlib.reload(atlas)
    snapshot = atlas.collect_xianyuan_atlas_snapshot_once()
    return {'people': snapshot.get('runtime_item_count'), 'summary': snapshot.get('summary')}


def _collect_gongfa() -> dict[str, Any]:
    import importlib
    import backend.core.fanxiu.instrumentation.gongfa_equipment as gongfa_equipment
    import backend.core.fanxiu.instrumentation.gongfa_atlas as atlas
    importlib.reload(gongfa_equipment)
    importlib.reload(atlas)
    snapshot = atlas.collect_gongfa_atlas_snapshot_once()
    return {'books': snapshot.get('runtime_item_count'), 'summary': snapshot.get('summary')}


def _collect_magic_treasure() -> dict[str, Any]:
    import importlib
    import backend.core.fanxiu.catalog.item as magic_treasure_item_catalog
    import backend.core.fanxiu.instrumentation.magic_treasure as magic_treasure_runtime
    import backend.core.fanxiu.instrumentation.magic_treasure_collector as magic_treasure_collector
    if not hasattr(magic_treasure_item_catalog, 'load_fanxiu_talisman_item_knowledge'):
        importlib.reload(magic_treasure_item_catalog)
    importlib.reload(magic_treasure_runtime)
    importlib.reload(magic_treasure_collector)
    snapshot = magic_treasure_collector.collect_magic_treasure_snapshot_once()
    return {'runtime_item_count': snapshot.get('runtime_item_count'), 'runtime_updated_at': snapshot.get('runtime_updated_at')}

_COLLECTORS = {
    "wardrobe": _collect_wardrobe,
    "magic_treasure": _collect_magic_treasure,
    "xianyuan": _collect_xianyuan,
    "gongfa": _collect_gongfa,
}


def collect_catalog_snapshot(kind: CatalogCollectionKind) -> dict[str, Any]:
    """采集并持久化指定图鉴，返回其现有诊断摘要；未知类型在采集前报错。"""
    try:
        collect = _COLLECTORS[kind]
    except KeyError as exc:
        raise ValueError(f"不支持的图鉴采集类型：{kind}") from exc
    return collect()


def build_catalog_collection_code(kind: CatalogCollectionKind) -> str:
    """生成提交给普通 Kernel Cell 的调用代码，不在 HTTP 进程中执行采集。"""
    if kind not in _COLLECTORS:
        raise ValueError(f"不支持的图鉴采集类型：{kind}")
    return (
        "from backend.core.fanxiu.instrumentation.catalog_collection "
        "import collect_catalog_snapshot\n"
        f"print(collect_catalog_snapshot({kind!r}))"
    )
