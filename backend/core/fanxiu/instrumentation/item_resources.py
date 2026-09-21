from __future__ import annotations

"""按客户端 ``BackpackData.GetItemNum2`` 语义组合只读 Runtime 计数。

客户端规则：先在 ``BackpackData.ItemVoDic[baseId]`` 统计已加载实例的 ``num``；
仅当该 baseId 不在索引中时，才用 ``Item.Item[baseId]`` 配置判定，若其
``subType == 1`` 则读 ``WalletMgr`` 对应币种余额。本模块只组合现有公共读取接口，
不调用游戏 Lua、不写游戏状态、不初始化或刷新任何配置表。

``effectValue`` 币种格式有两代，本解析器都支持但只接受严格形状：

- 旧导出 ``Backpack.GetItemNum2`` 的数字格式：取 ``StringProxy.Split(effectValue, '|')``
  的数字首段作为币种，因此 ``1002|6`` 仍返回 1002（尾随字段按客户端 Split 语义忽略）；
- 当前 Runtime 观察到的 tag 格式：``WALLET|<正整数>``，恰好两个字段，例如
  31001/32001 的 ``WALLET|31001`` / ``WALLET|32001``；多余字段在此格式拒绝。

这只是对已确认客户端规则与 Runtime ``Item.Item`` 观察的组合，并未逆向验证
``GameUtil``/``GetItemNum2`` 的全部语义，也不猜测未观察到的 tag 或币种。

钱包回退沿用客户端 ``WalletData.GetCurrencyByType`` 的空余额语义：已加载的钱包
字典中缺少该币种记录时视为 0（``missing_as_zero=True``）；钱包未初始化仍报错。
物品配置缺失，或 ``effectValue`` 为空/未知 tag/``WALLET`` 字段数不符/币种不是
正整数（0、负数）时明确报错，绝不猜测币种。非钱包物品保留“已加载背包索引中的 0 语义”。
"""

import re
import time
from collections.abc import Iterable
from typing import Any

from .backpack import read_backpack_item_counts
from .item_config import read_item_metadata_runtime
from .runtime_memory import FanxiuRuntimeMemoryError
from .wallet import read_wallet_currency_snapshot


def _currency_type_from_effect_value(effect_value: Any, item_id: int) -> int:
    """严格解析 ``effectValue`` 币种：接受 ``WALLET|<正整数>`` 或数字首段旧格式。

    旧格式严格保留客户端 ``StringProxy.Split(effectValue, '|')`` 取首段数字的语义，
    因此 ``1002|6`` 仍返回 1002；多余字段只在 ``WALLET`` tag 上拒绝。未知 tag、
    空串、非数字首段、0、负数、``WALLET`` 字段数不符一律拒绝，不猜币种。
    """

    if isinstance(effect_value, bool) or effect_value is None:
        raise FanxiuRuntimeMemoryError(f"物品 {item_id} 为钱包材料但缺少 effectValue")
    text = effect_value if isinstance(effect_value, str) else str(effect_value)
    if not text:
        raise FanxiuRuntimeMemoryError(f"物品 {item_id} 的 effectValue 为空")
    fields = text.split("|")
    if fields[0] == "WALLET":
        # 当前 Runtime 观察到的 tag 格式必须是恰好两个字段，多余字段在此拒绝。
        if len(fields) != 2:
            raise FanxiuRuntimeMemoryError(
                f"物品 {item_id} 的 WALLET effectValue 字段数不符：{text!r}"
            )
        head = fields[1]
    elif re.fullmatch(r"[0-9]+", fields[0]):
        # 旧数字首段：只校验首段，尾随字段按客户端 Split 语义忽略。
        head = fields[0]
    else:
        raise FanxiuRuntimeMemoryError(
            f"物品 {item_id} 的 effectValue tag 未知：{text!r}"
        )
    if not re.fullmatch(r"[0-9]+", head) or int(head) <= 0:
        raise FanxiuRuntimeMemoryError(
            f"物品 {item_id} 的 effectValue 币种不是正整数：{text!r}"
        )
    return int(head)


def _process_identity(pid: Any, ticks: Any, label: str) -> tuple[int, int]:
    if pid is None or ticks is None:
        raise FanxiuRuntimeMemoryError(f"{label}缺少进程身份，无法核对来源")
    return int(pid), int(ticks)


def read_item_available_counts(
    item_ids: Iterable[int],
    *,
    manager_key: str = "item-available-counts",
) -> tuple[dict[int, int], dict[str, Any]]:
    """返回物品可用数量，并说明每个物品实际采用的来源。

    主路径固定为已加载的 ``ItemVoDic``：即使某 baseId 存在但计数为 0，也尊重
    该结果；只有 baseId 完全不在索引中时才按 ``GetItemNum2`` 回退到 ``Item.Item``
    配置。``subType == 1`` 的物品映射到钱包币种，多个物品映射同一币种时只读一次
    该币种；``subType != 1`` 的物品保持 0。一次调用内核对背包、配置、钱包三处
    观测的 ``pid``/``process_start_ticks``，来源进程不一致直接失败。

    全程只读；不调用 Lua、不打开界面、不写入或刷新游戏业务状态。
    """

    requested = sorted({int(item_id) for item_id in item_ids})
    if any(item_id <= 0 for item_id in requested):
        raise ValueError("需要正整数物品 base ID")
    if not requested:
        return {}, {
            "read_only": True,
            "source": "empty_request",
            "requested_item_ids": [],
            "items": [],
            "completed_at": time.time(),
        }

    counts, backpack = read_backpack_item_counts(
        requested, manager_key=manager_key, force_refresh=True
    )
    pid, ticks = _process_identity(
        backpack.get("pid"), backpack.get("process_start_ticks"), "背包观测"
    )

    # force_refresh=True 时 dictionaries 会为请求中每个命中 ItemVoDic 的 baseId
    # 生成一条，因此“key 不在此集合”等价于“ItemVoDic 缺少该 baseId”。
    index_ids = {int(row["base_id"]) for row in backpack.get("dictionaries") or []}
    missing = [item_id for item_id in requested if item_id not in index_ids]

    items: dict[int, dict[str, Any]] = {
        item_id: {
            "item_id": item_id,
            "source": "backpack_item_vo_dic",
            "count": int(counts[item_id]),
            "currency_type": None,
            "present_in_item_index": True,
        }
        for item_id in requested
    }
    wallet_snapshots: dict[int, dict[str, Any]] = {}
    metadata_evidence: dict[int, dict[str, Any]] = {}

    if missing:
        metadata = read_item_metadata_runtime(missing)
        meta_pid, meta_ticks = _process_identity(
            metadata.get("pid"), metadata.get("process_start_ticks"), "物品配置观测"
        )
        if (meta_pid, meta_ticks) != (pid, ticks):
            raise FanxiuRuntimeMemoryError("物品配置观测与背包观测不属于同一游戏进程")
        rows = metadata.get("items_by_id") or {}
        unresolved = [item_id for item_id in missing if item_id not in rows]
        if unresolved:
            raise FanxiuRuntimeMemoryError(f"物品配置未加载，禁止猜测币种：{unresolved}")

        currency_items: dict[int, list[int]] = {}
        for item_id in missing:
            row = rows[item_id]
            metadata_evidence[item_id] = {
                "item_sub_type_id": row.get("item_sub_type_id"),
                "effect_value": row.get("effect_value"),
            }
            sub_type = row.get("item_sub_type_id")
            if sub_type is None:
                raise FanxiuRuntimeMemoryError(f"物品 {item_id} 缺少 subType，无法判定钱包回退")
            if sub_type != 1:
                items[item_id] = {
                    "item_id": item_id,
                    "source": "backpack_item_vo_dic_absent_non_currency",
                    "count": 0,
                    "currency_type": None,
                    "present_in_item_index": False,
                }
                continue
            currency_type = _currency_type_from_effect_value(
                row.get("effect_value"), item_id
            )
            currency_items.setdefault(currency_type, []).append(item_id)

        for currency_type in sorted(currency_items):
            snapshot = read_wallet_currency_snapshot(
                currency_type, missing_as_zero=True
            )
            wallet_evidence = snapshot.get("evidence") or {}
            wallet_pid, wallet_ticks = _process_identity(
                wallet_evidence.get("pid"),
                wallet_evidence.get("process_start_ticks"),
                f"钱包观测 {currency_type}",
            )
            if (wallet_pid, wallet_ticks) != (pid, ticks):
                raise FanxiuRuntimeMemoryError("钱包观测与背包观测不属于同一游戏进程")
            balance = snapshot.get("exchange_currency")
            if balance is None:
                raise FanxiuRuntimeMemoryError(
                    f"钱包币种 {currency_type} 缺少 exchange_currency"
                )
            wallet_snapshots[currency_type] = snapshot
            for item_id in currency_items[currency_type]:
                counts[item_id] = int(balance)
                items[item_id] = {
                    "item_id": item_id,
                    "source": "wallet_currency",
                    "count": int(balance),
                    "currency_type": currency_type,
                    "present_in_item_index": False,
                }

    evidence = {
        "pid": pid,
        "process_start_ticks": ticks,
        "read_only": True,
        "source": "BackpackMgr.Model.BackpackData.ItemVoDic + Item.Item + WalletMgr",
        "requested_item_ids": requested,
        "observed_at": backpack.get("observed_at"),
        "completed_at": time.time(),
        "backpack": backpack,
        "item_metadata": metadata_evidence,
        "wallet_snapshots": {
            str(currency_type): snapshot
            for currency_type, snapshot in wallet_snapshots.items()
        },
        "items": [items[item_id] for item_id in requested],
    }
    return counts, evidence
