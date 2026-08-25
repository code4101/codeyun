from __future__ import annotations

import importlib
import os
import re
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from backend.core.fanxiu.runtime.adb_device import (
    fanxiu_adb_device_service,
)
from backend.core.fanxiu.instrumentation.policy import (
    instrumentation_policy_snapshot,
    reject_active_instrumentation,
)
from backend.core.services.launcher import run_quiet


DEFAULT_FANXIU_PACKAGE_NAME = "com.frxxcrjpwssc3.ggws"
DEFAULT_MODULE_NAMES = (
    "libil2cpp.so",
    "libunity.so",
    "libtolua.so",
    "libc.so",
)
DEFAULT_REMOTE_SERVER_GLOB = "/data/local/tmp/frida-server*"
DEFAULT_REMOTE_SERVER_LOG = "/data/local/tmp/codeyun-frida-server.log"
REMOTE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_./-]+$")


class FanxiuInstrumentationError(RuntimeError):
    pass


def _completed_text(process: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        part.strip()
        for part in (process.stdout, process.stderr)
        if part and part.strip()
    )


def _load_frida() -> Any:
    try:
        return importlib.import_module("frida")
    except ImportError as exc:
        raise FanxiuInstrumentationError(
            "CodeYun 后端未安装 Frida Python 绑定，请先同步项目依赖。"
        ) from exc


def _base_probe_source() -> str:
    return (
        Path(__file__).resolve().parent / "agents" / "base_probe.js"
    ).read_text(encoding="utf-8")


class FanxiuInstrumentationService:
    """Execution-context-agnostic dynamic-instrumentation capability layer.

    Callers decide when and why to invoke a capability.  This service does not
    inspect or coordinate Scheduler, Kernel, Cell, or behavior-tree state.
    """

    def __init__(
        self,
        *,
        frida_loader: Callable[[], Any] = _load_frida,
    ) -> None:
        self._frida_loader = frida_loader
        self._lock = threading.RLock()

    def _run_adb(
        self,
        args: list[str],
        *,
        timeout: float = 8.0,
        check: bool = True,
    ) -> str:
        adb_path = fanxiu_adb_device_service.adb_path()
        process = run_quiet(
            [str(adb_path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = _completed_text(process)
        if check and process.returncode != 0:
            raise FanxiuInstrumentationError(
                output or f"adb 命令退出码 {process.returncode}"
            )
        return output

    def _shell(
        self,
        device_id: str,
        *args: str,
        timeout: float = 8.0,
        check: bool = True,
    ) -> str:
        return self._run_adb(
            ["-s", device_id, "shell", *args],
            timeout=timeout,
            check=check,
        )

    def adb_devices(self) -> list[str]:
        output = self._run_adb(["devices"])
        devices: list[str] = []
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def process_id(
        self,
        device_id: str,
        package_name: str = DEFAULT_FANXIU_PACKAGE_NAME,
    ) -> int | None:
        output = self._shell(
            device_id,
            "pidof",
            package_name,
            check=False,
        )
        for token in output.split():
            if token.isdigit():
                return int(token)
        return None

    def choose_device(
        self,
        *,
        device_id: str = "",
        package_name: str = DEFAULT_FANXIU_PACKAGE_NAME,
    ) -> str:
        explicit = (
            str(device_id or "").strip()
            or os.environ.get("FANXIU_INSTRUMENTATION_DEVICE_ID", "").strip()
        )
        devices = self.adb_devices()
        if explicit:
            if explicit not in devices:
                raise FanxiuInstrumentationError(
                    f"动态插桩设备未连接：{explicit}"
                )
            return explicit
        running = [
            candidate
            for candidate in devices
            if self.process_id(candidate, package_name) is not None
        ]
        if len(running) == 1:
            return running[0]
        if len(running) > 1:
            raise FanxiuInstrumentationError(
                "多个设备正在运行凡修，请显式指定 device_id。"
            )
        if len(devices) == 1:
            return devices[0]
        if not devices:
            raise FanxiuInstrumentationError("没有检测到已连接的 Android 设备。")
        raise FanxiuInstrumentationError(
            "检测到多个 Android 设备，但没有唯一的凡修进程。"
        )

    def _server_candidates(self, device_id: str) -> list[str]:
        command = f"ls -1 {DEFAULT_REMOTE_SERVER_GLOB} 2>/dev/null"
        output = self._shell(
            device_id,
            f"sh -c {shlex.quote(command)}",
            check=False,
        )
        candidates = [
            line.strip()
            for line in output.splitlines()
            if REMOTE_PATH_PATTERN.fullmatch(line.strip())
        ]
        candidates.sort(reverse=True)
        return candidates

    def _server_version(self, device_id: str, server_path: str) -> str:
        if not REMOTE_PATH_PATTERN.fullmatch(server_path):
            return ""
        output = self._shell(
            device_id,
            server_path,
            "--version",
            timeout=5.0,
            check=False,
        )
        return output.splitlines()[0].strip() if output.strip() else ""

    def _server_pid(self, device_id: str, server_path: str) -> int | None:
        process_name = Path(server_path).name
        output = self._shell(
            device_id,
            "pidof",
            process_name,
            check=False,
        )
        for token in output.split():
            if token.isdigit():
                return int(token)
        return None

    def _process_modules(self, device_id: str, pid: int | None) -> list[dict[str, Any]]:
        if pid is None:
            return []
        output = self._shell(
            device_id,
            "cat",
            f"/proc/{pid}/maps",
            check=False,
        )
        modules: dict[str, dict[str, Any]] = {}
        for line in output.splitlines():
            if "/" not in line:
                continue
            parts = line.split()
            if len(parts) < 6:
                continue
            address_range = parts[0]
            path = parts[-1]
            name = Path(path).name
            if name not in DEFAULT_MODULE_NAMES:
                continue
            row = modules.setdefault(
                name,
                {"name": name, "path": path, "ranges": []},
            )
            row["ranges"].append(address_range)
        return [modules[name] for name in sorted(modules)]

    def inspect(
        self,
        *,
        device_id: str = "",
        package_name: str = DEFAULT_FANXIU_PACKAGE_NAME,
    ) -> dict[str, Any]:
        selected = self.choose_device(
            device_id=device_id,
            package_name=package_name,
        )
        pid = self.process_id(selected, package_name)
        candidates = self._server_candidates(selected)
        server_path = candidates[0] if candidates else ""
        server_pid = (
            self._server_pid(selected, server_path)
            if server_path
            else None
        )
        try:
            frida = self._frida_loader()
            host_version = str(getattr(frida, "__version__", ""))
            host_available = True
        except FanxiuInstrumentationError:
            host_version = ""
            host_available = False
        return {
            "ok": bool(pid),
            "mode": "strict-read-only",
            "policy": instrumentation_policy_snapshot(),
            "device": {
                "id": selected,
                "abi_list": self._shell(
                    selected,
                    "getprop",
                    "ro.product.cpu.abilist",
                    check=False,
                ).strip(),
                "kernel_machine": self._shell(
                    selected,
                    "uname",
                    "-m",
                    check=False,
                ).strip(),
                "root": "uid=0" in self._shell(
                    selected,
                    "id",
                    check=False,
                ),
                "selinux": self._shell(
                    selected,
                    "getenforce",
                    check=False,
                ).strip(),
            },
            "target": {
                "package_name": package_name,
                "pid": pid,
                "running": pid is not None,
                "modules": self._process_modules(selected, pid),
            },
            "frida": {
                "host_available": host_available,
                "host_version": host_version,
                "server_path": server_path,
                "server_version": (
                    self._server_version(selected, server_path)
                    if server_path
                    else ""
                ),
                "server_pid": server_pid,
                "server_running": server_pid is not None,
                "server_candidates": candidates,
            },
            "capabilities": self.capabilities(),
        }

    def capabilities(self) -> list[dict[str, Any]]:
        capabilities = [
            {
                "name": "runtime.health",
                "kind": "query",
                "implemented": True,
                "side_effect": "none",
                "description": "读取目标进程架构、指针宽度和 Java 可用性。",
            },
            {
                "name": "runtime.modules",
                "kind": "query",
                "implemented": True,
                "side_effect": "none",
                "description": "读取目标进程已加载模块及基址。",
            },
            {
                "name": "inventory.snapshot",
                "kind": "query",
                "implemented": False,
                "side_effect": "none",
                "description": (
                    "读取游戏内 BackpackData 常驻全量储物袋模型；"
                    "业务模型已定位，MuMu ARM 转译进程的 Lua 桥接仍待完成。"
                ),
            },
            {
                "name": "inventory.refresh",
                "kind": "command",
                "implemented": False,
                "side_effect": "network-request",
                "description": (
                    "显式请求 CM_AllBagSyncInfo；与只读 snapshot 分离，"
                    "默认不调用。"
                ),
            },
            {
                "name": "chat.red_packet.pending",
                "kind": "query",
                "implemented": True,
                "side_effect": "none",
                "description": (
                    "从游戏进程 LuaJIT 内存读取 RedbagData 与 NpcData "
                    "的本地待领取红包候选；严格只读模式下不会调用"
                    "Inst_get 初始化未加载的 Manager。"
                ),
            },
            {
                "name": "chat.red_packet.claim",
                "kind": "command",
                "implemented": False,
                "side_effect": "network-request-and-reward",
                "description": (
                    "领取指定红包；必须与只读 pending 查询分离，"
                    "后续单独定义幂等和结果验证。"
                ),
            },
            {
                "name": "lingquan.question.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "pending-live-window",
                "side_effect": "none",
                "description": (
                    "从游戏 LuaJIT 常驻模型读取当前灵泉题目、题号、"
                    "阶段和倒计时；非阻塞缓存查询，失败时供业务降级 OCR。"
                ),
            },
            {
                "name": "final_camp_answer.question.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "pending-live-window",
                "side_effect": "none",
                "description": (
                    "从 FinalCampAnswerMgr 当前题目与本地 CampAnswer 配置读取"
                    "题干、四个选项 ID 和正确选项 ID；非阻塞缓存查询，"
                    "业务仍须用当前 OCR 题面和动态选项行做一致性校验。"
                ),
            },
            {
                "name": "camp_answer.question_plan.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "pending-live-window",
                "side_effect": "none",
                "description": (
                    "从 CampAnswerMgr 已下发的 questions 清单读取普通答题"
                    "题号、configId，并用本地 CampAnswer 配置解析三项与正确位置；"
                    "业务仍须用当前 OCR 题号和题面校验后才能点击。"
                ),
            },
            {
                "name": "dongtian.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从 XianLvMinesMgr 与 ClubMgr 的 LuaJIT 常驻模型读取"
                    "洞天行动力、矿位占领方和自己的跨服联盟身份；"
                    "不依赖 OCR 或历史网络事件。"
                ),
            },
            {
                "name": "lingmai.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从 UnionVenisMgr 的 LuaJIT 常驻模型读取联盟灵脉"
                    "剩余时间、体力、联盟分组、房间空位、自身座位和已加载座位名单；"
                    "不依赖 OCR 或历史网络事件。"
                ),
            },
            {
                "name": "xianfu.skill_draw.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从 RevenueMgr 的 LuaJIT 常驻模型读取仙品绝技免费状态、"
                    "下一次免费时间和剩余秒数；未到点时无需进入仙府或 OCR。"
                ),
            },
            {
                "name": "boss.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从 BossMgr 的 LuaJIT 常驻模型读取首领奖励剩余次数、"
                    "额外击杀奖励和当前大首领刷新时间；进入首领列表后"
                    "可替代易误读的小数字 OCR。"
                ),
            },
            {
                "name": "xuanhuang.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从 GodsoultowerMgr 的 LuaJIT 挑战页模型读取玄荒"
                    "剩余次数；仅在 #418 计数已加载时权威，失败时"
                    "由业务降级 OCR。"
                ),
            },
            {
                "name": "role.progression.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从 RoleMgr 常驻模型读取角色当前修为、下一境界所需修为，"
                    "直接判定经验是否还能继续使用；不可判定时由业务保留原流程。"
                ),
            },
            {
                "name": "lundao.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从 LundaoMgr 的 LuaJIT 常驻模型读取今日剩余闻道时间、"
                    "体力、房间空位、自身座位和已加载座位名单；"
                    "名单页尚未加载时明确返回不可判定，不把空缓存当成空房。"
                ),
            },
            {
                "name": "daofa.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "pending-live-window",
                "side_effect": "none",
                "description": (
                    "从 ImmortalRaceMgr 的 LuaJIT 常驻模型读取当前排名、"
                    "剩余挑战次数、自身战力和候选对手；数据不完整时安全失败。"
                ),
            },
            {
                "name": "xianyuan_duel.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "pending-live-window",
                "side_effect": "none",
                "description": (
                    "从 PartnerarenaMgr 的 LuaJIT 常驻模型读取挑战/刷新次数、"
                    "我方仙侣战力和三个候选；数据不完整时安全失败。"
                ),
            },
            {
                "name": "activity_rank.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "adapter-tested",
                "side_effect": "none",
                "description": (
                    "按 activity_id 从 ActivityrankMgr 读取任意已加载活动榜；"
                    "缓存缺失时快速失败，不在请求内启动全内存扫描。"
                ),
            },
            {
                "name": "activity_rank.lingzhuang_huadao.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从 ActivityrankMgr 的 LuaJIT 常驻模型读取灵装化道"
                    "玩家榜、总人数和自己的排名；不会请求游戏刷新榜单。"
                ),
            },
            {
                "name": "mail.snapshot",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "从主 lua_State 的全局 MailMgr 读取本地结构化邮件清单；"
                    "不会调用 Lua 方法或主动请求服务器。"
                ),
            },
            {
                "name": "mail.unclaimed",
                "kind": "query",
                "implemented": True,
                "validation_status": "live-validated",
                "side_effect": "none",
                "description": (
                    "筛选带附件且 rewardGetted=false 的本地未领取邮件；"
                    "不把未读状态等同于未领取，也不主动请求服务器。"
                ),
            },
            {
                "name": "mail.claim",
                "kind": "command",
                "implemented": False,
                "side_effect": "network-request-and-reward",
                "description": (
                    "领取指定邮件附件；与只读查询分离，"
                    "后续单独定义背包已满、过期和部分成功结果。"
                ),
            },
        ]
        for capability in capabilities:
            if capability.get("kind") == "command":
                capability["policy_disabled"] = True
        return capabilities

    def red_packet_pending(
        self,
        *,
        allow_discovery: bool = True,
        allow_runtime_initialization: bool = False,
        unavailable_cache_ttl_seconds: float = 120.0,
    ) -> dict[str, Any]:
        """Query the game-native Runtime without using the behavior-tree Kernel."""

        from backend.core.fanxiu.instrumentation.red_packet import (
            read_red_packet_pending,
        )

        return read_red_packet_pending(
            allow_discovery=allow_discovery,
            allow_runtime_initialization=allow_runtime_initialization,
            unavailable_cache_ttl_seconds=unavailable_cache_ttl_seconds,
        )

    def lingquan_question_snapshot(
        self,
        *,
        max_age_seconds: float = 2.0,
    ) -> dict[str, Any]:
        """Return a non-blocking read-only Lingquan Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.lingquan import (
            get_lingquan_question_snapshot,
        )

        return get_lingquan_question_snapshot(
            max_age_seconds=max_age_seconds,
        )

    def final_camp_answer_snapshot(
        self,
        *,
        max_age_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Return a non-blocking read-only final-round question snapshot."""

        from backend.core.fanxiu.instrumentation.final_camp_answer import (
            get_final_camp_answer_snapshot,
        )

        return get_final_camp_answer_snapshot(max_age_seconds=max_age_seconds)

    def camp_answer_snapshot(
        self,
        *,
        max_age_seconds: float = 2.0,
    ) -> dict[str, Any]:
        """Return the non-blocking ordinary-quiz question-plan snapshot."""

        from backend.core.fanxiu.instrumentation.camp_answer import (
            get_camp_answer_snapshot,
        )

        return get_camp_answer_snapshot(max_age_seconds=max_age_seconds)

    def dongtian_snapshot(self) -> dict[str, Any]:
        """Return the current read-only Dongtian Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.dongtian import (
            read_dongtian_snapshot,
        )

        return read_dongtian_snapshot()

    def lingmai_snapshot(self) -> dict[str, Any]:
        """Return the current read-only Lingmai Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.lingmai import (
            read_lingmai_snapshot,
        )

        return read_lingmai_snapshot()

    def xianfu_skill_draw_snapshot(self) -> dict[str, Any]:
        """Return the read-only Xianfu skill free-draw Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.xianfu import (
            read_xianfu_skill_draw_snapshot,
        )

        return read_xianfu_skill_draw_snapshot()

    def boss_snapshot(self) -> dict[str, Any]:
        """Return the current read-only boss Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.boss import (
            read_boss_snapshot,
        )

        return read_boss_snapshot()

    def mail_snapshot(self) -> dict[str, Any]:
        """Return the current read-only local mail-model snapshot."""

        from backend.core.fanxiu.instrumentation.mail import read_mail_snapshot

        return read_mail_snapshot()

    def xianqiao_snapshot(self) -> dict[str, Any]:
        """Return equipped仙纹 element counts from the read-only local model."""

        from backend.core.fanxiu.instrumentation.xianqiao import read_xianqiao_snapshot

        return read_xianqiao_snapshot()

    def beast_spirit_snapshot(self, *, optimize: bool = True) -> dict[str, Any]:
        """Return the loaded beast-soul inventory and optional fixed-shape optimum."""

        from backend.core.fanxiu.beast_spirit_optimizer import (
            board_placements,
            optimize_beast_soul_structured_layout,
            plan_first_fit_layout,
        )
        from backend.core.fanxiu.instrumentation.beast_spirit import (
            read_beast_spirit_snapshot,
        )

        snapshot = read_beast_spirit_snapshot()
        if optimize and snapshot.get("complete"):
            current_placements = board_placements(snapshot.get("boards") or [])
            layout = optimize_beast_soul_structured_layout(
                snapshot.get("items") or [],
                preferred_placements=current_placements,
            )
            layout["transition_plan"] = plan_first_fit_layout(
                current_placements,
                layout.get("selected") or [],
            )
            current_ids = {
                int(item_id)
                for board in snapshot.get("boards") or []
                for item_id in board.get("equipped_item_ids") or []
            }
            item_by_id = {
                int(item["item_id"]): item
                for item in snapshot.get("items") or []
            }
            current_score = sum(
                int(item_by_id[item_id].get("score") or 0)
                for item_id in current_ids
                if item_id in item_by_id
            )
            protected_ids = {
                int(item_id) for item_id in layout.get("protected_item_ids") or []
            }
            locked_ids = {
                int(item["item_id"])
                for item in snapshot.get("items") or []
                if item.get("locked")
            }
            layout.update(
                {
                    "current_score": current_score,
                    "score_gain": int(layout.get("score") or 0) - current_score,
                    "protected_item_ids": [str(item_id) for item_id in sorted(protected_ids)],
                    "unlocked_protected_item_ids": [
                        str(item_id) for item_id in sorted(protected_ids - locked_ids)
                    ],
                    "obsolete_locked_item_ids": [
                        str(item_id) for item_id in sorted(locked_ids - protected_ids)
                    ],
                    "safe_to_synthesize": protected_ids == locked_ids,
                }
            )
            snapshot["layout"] = layout
        return snapshot

    def beast_spirit_ui_projection(
        self, *, expected_item_ids: list[str | None]
    ) -> dict[str, Any]:
        """Return the active beast-bag UI projection without full optimization."""

        from backend.core.fanxiu.instrumentation.beast_spirit import (
            read_active_beast_bag_projection,
        )

        # ``v_showList`` may contain explicit empty padding slots.  They are
        # preserved in the returned order but are not inventory identities.
        return read_active_beast_bag_projection({
            str(item_id)
            for item_id in expected_item_ids
            if item_id is not None
        })

    def beast_spirit_ui_order(
        self, *, expected_item_ids: list[str | None]
    ) -> dict[str, Any]:
        """Return only v_showList order; never traverse ItemClassDic."""

        from backend.core.fanxiu.instrumentation.beast_spirit import (
            read_active_beast_bag_projection,
        )

        return read_active_beast_bag_projection(
            {
                str(item_id)
                for item_id in expected_item_ids
                if item_id is not None
            },
            include_materialized=False,
        )

    def backpack_ui_snapshot(self) -> dict[str, Any]:
        """Return the already-loaded ordinary backpack panel in exact UI order."""

        from backend.core.fanxiu.instrumentation.backpack_ui import (
            read_backpack_ui_snapshot,
        )

        return read_backpack_ui_snapshot()

    def backpack_quick_settings_snapshot(self) -> dict[str, Any]:
        """Read the four active quick-operation settings without invoking Lua."""

        from backend.core.fanxiu.instrumentation.backpack_quick_settings import (
            read_backpack_quick_settings_snapshot,
        )

        return read_backpack_quick_settings_snapshot()

    def beast_spirit_quick_synthesis_snapshot(self) -> dict[str, Any]:
        """Read the active quick-synthesis selection without invoking Lua."""

        from backend.core.fanxiu.instrumentation.beast_spirit_quick_synthesis import (
            read_beast_spirit_quick_synthesis_snapshot,
        )

        return read_beast_spirit_quick_synthesis_snapshot()

    def locate_backpack_ui_items(
        self,
        *,
        instance_id: str | int | None = None,
        base_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Locate loaded backpack UI rows without aggregating or reordering."""

        from backend.core.fanxiu.instrumentation.backpack_ui import (
            locate_backpack_ui_items,
        )

        return locate_backpack_ui_items(
            self.backpack_ui_snapshot(),
            instance_id=instance_id,
            base_id=base_id,
        )

    def lilian_event_catalog_snapshot(self) -> dict[str, Any]:
        """Return all loaded Lilian choice events, answers, and rewards."""

        from backend.core.fanxiu.instrumentation.lilian_event import (
            read_lilian_event_catalog_snapshot,
        )

        return read_lilian_event_catalog_snapshot()

    def xuanhuang_snapshot(self) -> dict[str, Any]:
        """Return the loaded Xuanhuang counter Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.xuanhuang import (
            read_xuanhuang_snapshot,
        )

        return read_xuanhuang_snapshot()

    def role_progression_snapshot(self) -> dict[str, Any]:
        """Return the current read-only role progression Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.role_progression import (
            read_role_progression_snapshot,
        )

        return read_role_progression_snapshot()

    def lundao_snapshot(self) -> dict[str, Any]:
        """Return the current read-only Lundao Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.lundao import (
            read_lundao_snapshot,
        )

        return read_lundao_snapshot()

    def daofa_snapshot(self) -> dict[str, Any]:
        """Return the current read-only DaoFa arena Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.arena import read_daofa_snapshot

        return read_daofa_snapshot()

    def xianyuan_duel_snapshot(self) -> dict[str, Any]:
        """Return the current read-only Xianyuan duel Runtime snapshot."""

        from backend.core.fanxiu.instrumentation.arena import (
            read_xianyuan_duel_snapshot,
        )

        return read_xianyuan_duel_snapshot()

    def lingzhuang_huadao_ranking_snapshot(self) -> dict[str, Any]:
        """Return the current read-only Lingzhuang Huadao ranking snapshot."""

        from backend.core.fanxiu.instrumentation.resource_ranking import (
            read_lingzhuang_huadao_snapshot,
        )

        return read_lingzhuang_huadao_snapshot()

    def activity_rank_snapshot(self, activity_id: int) -> dict[str, Any]:
        """Return one generic already-loaded ActivityrankMgr snapshot."""

        from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
            read_activity_rank_runtime_snapshot,
        )

        return read_activity_rank_runtime_snapshot(int(activity_id))

    def ensure_server(
        self,
        *,
        device_id: str = "",
        package_name: str = DEFAULT_FANXIU_PACKAGE_NAME,
    ) -> dict[str, Any]:
        reject_active_instrumentation("启动或部署 Frida Server")
        with self._lock:
            selected = self.choose_device(
                device_id=device_id,
                package_name=package_name,
            )
            candidates = self._server_candidates(selected)
            if not candidates:
                raise FanxiuInstrumentationError(
                    "设备端没有 Frida Server；请先部署与后端 Frida 版本一致的二进制。"
                )
            server_path = candidates[0]
            existing_pid = self._server_pid(selected, server_path)
            if existing_pid is None:
                self._shell(
                    selected,
                    "chmod",
                    "755",
                    server_path,
                    timeout=5.0,
                )
                try:
                    self._shell(
                        selected,
                        server_path,
                        "--daemonize",
                        timeout=3.0,
                    )
                except subprocess.TimeoutExpired:
                    # Some Android native-bridge environments keep the adb
                    # shell transport open even after Frida has daemonized.
                    # The process check below remains the source of truth.
                    pass
                for _ in range(10):
                    existing_pid = self._server_pid(selected, server_path)
                    if existing_pid is not None:
                        break
                    threading.Event().wait(0.2)
            if existing_pid is None:
                log_tail = self._shell(
                    selected,
                    f"sh -c {shlex.quote(f'tail -20 {DEFAULT_REMOTE_SERVER_LOG} 2>/dev/null')}",
                    check=False,
                )
                raise FanxiuInstrumentationError(
                    f"Frida Server 未保持运行。{log_tail}".strip()
                )
            return self.inspect(
                device_id=selected,
                package_name=package_name,
            )

    def _frida_device(self, frida: Any, device_id: str, timeout: float) -> Any:
        manager = frida.get_device_manager()
        devices = list(manager.enumerate_devices())
        for device in devices:
            if str(getattr(device, "id", "")) == device_id:
                return device
        usb_devices = [
            device
            for device in devices
            if str(getattr(device, "type", "")) == "usb"
        ]
        if len(usb_devices) == 1:
            return usb_devices[0]
        try:
            return frida.get_usb_device(timeout=timeout)
        except Exception as exc:
            raise FanxiuInstrumentationError(
                f"Frida 无法连接 Android 设备 {device_id}：{exc}"
            ) from exc

    def probe(
        self,
        *,
        device_id: str = "",
        package_name: str = DEFAULT_FANXIU_PACKAGE_NAME,
        module_names: list[str] | None = None,
        ensure_server: bool = True,
        timeout: float = 8.0,
    ) -> dict[str, Any]:
        """Attach briefly, read process facts, then unload and detach."""
        reject_active_instrumentation("Frida 附加与脚本注入")
        with self._lock:
            selected = self.choose_device(
                device_id=device_id,
                package_name=package_name,
            )
            if ensure_server:
                self.ensure_server(
                    device_id=selected,
                    package_name=package_name,
                )
            pid = self.process_id(selected, package_name)
            if pid is None:
                raise FanxiuInstrumentationError(
                    f"凡修进程未运行：{package_name}"
                )
            frida = self._frida_loader()
            device = self._frida_device(frida, selected, timeout)
            session = None
            script = None
            try:
                session = device.attach(pid)
                script = session.create_script(_base_probe_source())
                script.load()
                requested = list(module_names or DEFAULT_MODULE_NAMES)
                snapshot = script.exports_sync.snapshot(requested)
                return {
                    "ok": True,
                    "mode": "transient-read-only",
                    "device_id": selected,
                    "package_name": package_name,
                    "pid": pid,
                    "snapshot": snapshot,
                }
            except Exception as exc:
                raise FanxiuInstrumentationError(
                    f"Frida 只读探针失败：{exc}"
                ) from exc
            finally:
                if script is not None:
                    try:
                        script.unload()
                    except Exception:
                        pass
                if session is not None:
                    try:
                        session.detach()
                    except Exception:
                        pass


fanxiu_instrumentation_service = FanxiuInstrumentationService()
