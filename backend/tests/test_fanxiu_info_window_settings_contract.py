"""信息窗设置的接口契约：Pydantic 模型必须覆盖全部设置键。

`/fanxiu/kernel-scheduler/info-window/settings` 的请求体与
`/fanxiu/kernel-scheduler/info-window` 的响应体共用 FanxiuInfoWindowSettings。
Pydantic 默认忽略未声明字段，于是"只改默认值 + 前端开关、忘了改模型"会让
接口两侧同时静默失效：POST 丢字段后 normalize 回填默认值，GET 又把字段过滤
掉，开关看起来接好了却什么也不改。

#699 的"累计魔晶"开关就是这样漏过一次，所以这里用结构断言把整类问题钉住：
任何新增设置都必须同时出现在模型里。
"""

from __future__ import annotations


def test_settings_models_cover_every_default_setting() -> None:
    from backend.core.fanxiu.data_annotation.models import (
        FanxiuInfoWindowSettings,
        FanxiuInfoWindowSettingsRequest,
    )
    from backend.core.fanxiu.info_window import FANXIU_INFO_WINDOW_DEFAULT_SETTINGS

    expected = set(FANXIU_INFO_WINDOW_DEFAULT_SETTINGS)
    assert set(FanxiuInfoWindowSettings.model_fields) == expected
    assert set(FanxiuInfoWindowSettingsRequest.model_fields) == expected | {"entry_id"}


def test_settings_survive_the_request_model_round_trip() -> None:
    from backend.core.fanxiu.data_annotation.models import (
        FanxiuInfoWindowSettingsRequest,
    )
    from backend.core.fanxiu.info_window import (
        FANXIU_INFO_WINDOW_DEFAULT_SETTINGS,
        normalize_fanxiu_info_window_settings,
    )

    # 每个键都取反，这样"字段被丢掉后回填默认值"会立刻暴露成断言失败。
    payload = {
        key: not bool(default)
        for key, default in FANXIU_INFO_WINDOW_DEFAULT_SETTINGS.items()
    }
    request = FanxiuInfoWindowSettingsRequest(**payload)
    body = request.model_dump(exclude={"entry_id"})
    assert body == payload
    assert normalize_fanxiu_info_window_settings(body) == payload
