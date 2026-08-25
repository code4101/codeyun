from __future__ import annotations

from typing import Any


# Safety lock requested by the user on 2026-08-03.  This is intentionally a
# source-controlled, fail-closed policy rather than an environment switch that
# a service restart or another caller could silently bypass.
STRICT_READ_ONLY_MODE = True


class FanxiuInstrumentationPolicyError(RuntimeError):
    """Raised before an instrumentation operation can alter live game state."""


def reject_active_instrumentation(operation: str) -> None:
    """Reject process injection, device writes, Lua execution and game commands."""

    if STRICT_READ_ONLY_MODE:
        raise FanxiuInstrumentationPolicyError(
            f"凡修动态插桩当前处于严格只读模式，已拒绝：{operation}"
        )


def instrumentation_policy_snapshot() -> dict[str, Any]:
    return {
        "mode": "strict-read-only",
        "locked": True,
        "allowed": [
            "读取设备与进程身份",
            "读取 /proc 映射和进程内存",
            "解析已保存的数据包",
            "读取 CodeYun 数据库快照",
        ],
        "blocked": [
            "Frida 附加或脚本注入",
            "启动或部署 Frida Server",
            "向设备推送动态插桩脚本或桥接库",
            "在游戏主 Lua 状态执行脚本",
            "Hook、替换或改写游戏运行时代码",
            "通过动态插桩发送游戏网络命令",
        ],
    }
