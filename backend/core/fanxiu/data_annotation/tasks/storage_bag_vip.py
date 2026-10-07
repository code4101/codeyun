"""VIP experience's exact direct-use transaction, accepted on 2026-10-08."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from .storage_bag_direct_use import (
    StorageBagDirectUseBlocked, StorageBagDirectUseExecution,
    StorageBagDirectUseRequest, StorageBagSpiritStoneGuiAdapter,
    _parse_capture_time, _verify_direct_use_delta,
)

VIP_BASE_ID = 1010
VIP_ITEM_NAME = "VIP经验"
VIP_PROGRESS_SOURCE = "runtime_memory.role_charge+runtime_memory.ChargeData.vipLevelCfg"


def vip_experience_amount(snapshot: Mapping, *, process_identity: tuple[int, int]):
    """Validate the account's cumulative counter, never the level remainder."""
    evidence = snapshot.get("evidence") or {}
    identity = (evidence.get("pid"), evidence.get("process_start_ticks"))
    amount = snapshot.get("cumulative_vip_exp")
    if (snapshot.get("complete") is not True or snapshot.get("source") != VIP_PROGRESS_SOURCE
            or identity != process_identity or type(amount) is not int or amount < 0
            or type(evidence.get("role_id")) is not int or evidence["role_id"] <= 0):
        raise StorageBagDirectUseBlocked("VIP累计经验来源、账号或进程证据不完整")
    return amount, _parse_capture_time(snapshot.get("captured_at"))


@dataclass(frozen=True)
class StorageBagVipExecution(StorageBagDirectUseExecution):
    role_id: int = 0
    progress_before: Mapping | None = None
    progress_after: Mapping | None = None

    def receipt(self) -> dict:
        if (self.request.base_id != VIP_BASE_ID or self.request.name != VIP_ITEM_NAME
                or not self.request.instance_id or self.request.quantity <= 0
                or self.wallet_delta != self.request.quantity or min(self.process_identity) <= 0
                or self.role_id <= 0 or not self.progress_before or not self.progress_after
                or not self.wallet_captured_at or not self.before_fingerprint
                or not self.after_fingerprint or self.before_fingerprint == self.after_fingerprint):
            raise StorageBagDirectUseBlocked("VIP凭证缺少精确背包/累计经验双差值")
        evidence = {"instance_id": self.request.instance_id,
                    "role_id": self.role_id,
                    "progress_before": dict(self.progress_before),
                    "progress_after": dict(self.progress_after),
                    "process_identity": list(self.process_identity),
                    "progress_captured_at": self.wallet_captured_at,
                    "cumulative_vip_exp_before": self.wallet_before,
                    "cumulative_vip_exp_after": self.wallet_after,
                    "before_fingerprint": self.before_fingerprint,
                    "after_fingerprint": self.after_fingerprint}
        digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"action_key": "storage-bag-vip:" + digest, "base_id": VIP_BASE_ID,
                "operation_template": "direct_use", "opened_count": self.consumed_quantity,
                "rewards": [{"item_id": None, "name": VIP_ITEM_NAME,
                             "reward_key": "vip:cumulative_experience", "quantity": self.wallet_delta}],
                "runtime_before_fingerprint": self.before_fingerprint,
                "runtime_after_fingerprint": self.after_fingerprint, "evidence": evidence}


def verify_vip_direct_use_delta(before, after, *, request, wallet_before, wallet_after):
    if (request.base_id != VIP_BASE_ID or request.name != VIP_ITEM_NAME
            or not request.instance_id or type(request.quantity) is not int or request.quantity <= 0):
        raise StorageBagDirectUseBlocked("VIP事务仅允许base1010精确单叠")
    if ((wallet_before.get("evidence") or {}).get("role_id")
            != (wallet_after.get("evidence") or {}).get("role_id")):
        raise StorageBagDirectUseBlocked("VIP使用前后不是同一账号")
    result = _verify_direct_use_delta(
        before, after, request=request, progress_before=wallet_before,
        progress_after=wallet_after, amount_reader=vip_experience_amount,
        execution_type=StorageBagVipExecution)
    fields = ("source", "captured_at", "vip_level", "current_vip_exp",
              "completed_level_exp", "cumulative_vip_exp", "evidence")
    return replace(result, role_id=wallet_before["evidence"]["role_id"],
                   progress_before={key: wallet_before.get(key) for key in fields},
                   progress_after={key: wallet_after.get(key) for key in fields})


class StorageBagVipGuiAdapter(StorageBagSpiritStoneGuiAdapter):
    """Reuse the accepted detail/quantity chain, with an independent VIP counter."""
    BASE_ID = VIP_BASE_ID
    ITEM_NAME = VIP_ITEM_NAME
    PROGRESS_TYPE = VIP_BASE_ID
    amount_reader = staticmethod(vip_experience_amount)
    delta_verifier = staticmethod(verify_vip_direct_use_delta)


def read_vip_direct_use_progress(kind: int) -> dict:
    if kind != VIP_BASE_ID:
        raise StorageBagDirectUseBlocked("VIP进度读者拒绝其他产品")
    from backend.core.fanxiu.instrumentation.vip_progression import read_vip_progress_snapshot
    return read_vip_progress_snapshot(allow_discovery=True)
