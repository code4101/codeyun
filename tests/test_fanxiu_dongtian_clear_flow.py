import threading

from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner


class _Runtime:
    def __init__(self, battle_scenes=None):
        self.calls = []
        self.battle_scenes = iter(battle_scenes or [346])

    def wait_click_then_view(self, scene, shape, target):
        self.calls.append(("wait_click_then_view", scene, shape, target))
        yield

    def wait_click(self, scene, shape):
        self.calls.append(("wait_click", scene, shape))
        yield

    def wait_view(self, *scenes, label):
        self.calls.append(("wait_view", scenes, label))
        yield

    def wait_action_settle(self, seconds):
        self.calls.append(("wait_action_settle", seconds))
        yield

    def current_scene(self, scenes, *, update):
        scene = next(self.battle_scenes)
        self.calls.append(("current_scene", scenes, update, scene))
        return scene, 100.0, "frame"


def test_daily_dongtian_known_occupation_chain():
    runtime = _Runtime([345, 346])

    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    list(runner._daily_dongtian_continue_enemy_occupation(runtime))

    assert runtime.calls == [
        ("wait_click_then_view", 341, "\u4f4d\u7f6e1", 342),
        ("wait_click_then_view", 342, "\u5360\u9886", 343),
        ("wait_click_then_view", 343, "\u5360\u9886", 344),
        ("wait_click", 344, "\u6218\u6597"),
        ("wait_action_settle", 1.0),
        ("current_scene", [345, 346], True, 345),
        ("wait_click", 345, "\u8df3\u8fc7"),
        ("wait_action_settle", 1.0),
        ("current_scene", [345, 346], True, 346),
        ("wait_click", 346, "\u7ee7\u7eed"),
        ("wait_view", (341, 279), "\u6d1e\u5929_\u884c\u52a8\u529b\uff1a\u786e\u8ba4\u6218\u6597\u540e\u7684\u6b63\u5e38\u843d\u70b9"),
    ]


def test_daily_dongtian_battle_can_finish_without_optional_skip_scene():
    runtime = _Runtime([346])
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)

    list(runner._daily_dongtian_finish_battle(runtime))

    assert runtime.calls == [
        ("wait_action_settle", 1.0),
        ("current_scene", [345, 346], True, 346),
        ("wait_click", 346, "\u7ee7\u7eed"),
        ("wait_view", (341, 279), "\u6d1e\u5929_\u884c\u52a8\u529b\uff1a\u786e\u8ba4\u6218\u6597\u540e\u7684\u6b63\u5e38\u843d\u70b9"),
    ]


def test_daily_dongtian_action_power_loop_can_start_at_341_and_stop_below_100():
    class Runtime:
        def __init__(self):
            self.scenes = iter([341, 341])
            self.action_power = iter([100, 0])

        def current_scene(self, scenes, *, update):
            return next(self.scenes), 100.0, "frame"

        def cur_frame(self, *, update):
            return "frame"

        def ocr_numbers_in_shapes(self, scene, shapes, *, padding, frame_data_url):
            value = next(self.action_power)
            return [value], str(value)

    runtime = Runtime()
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None
    occupation_calls = []

    def occupy(_runtime):
        occupation_calls.append(True)
        yield

    runner._daily_dongtian_continue_enemy_occupation = occupy
    result = None
    action = runner._daily_dongtian_clear_action_power_loop(runtime, threading.Event(), {})
    while True:
        try:
            next(action)
        except StopIteration as done:
            result = done.value
            break

    assert result == "success"
    assert occupation_calls == [True]


def test_daily_dongtian_action_power_reuses_279_hud_shape_on_current_frame():
    class Runtime:
        def cur_frame(self, *, update):
            assert update is True
            return "current-341-frame"

        def ocr_numbers_in_shapes(self, scene, shapes, *, padding, frame_data_url):
            assert scene == 279
            assert shapes == ("行动力",)
            assert frame_data_url == "current-341-frame"
            return [80], "80"

    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)

    assert runner._daily_dongtian_action_power(Runtime()) == (80, "80")


def test_daily_dongtian_wrong_or_own_detail_returns_before_occupation():
    class Runtime:
        def __init__(self):
            self.calls = []

        def wait_view(self, scene, *, label):
            self.calls.append(("wait_view", scene))
            yield

        def ocr_text(self, *, update):
            return "白玉京 玉清道宗 详情"

        def wait_click_then_view(self, scene, shape, target):
            self.calls.append(("wait_click_then_view", scene, shape, target))
            yield

    runtime = Runtime()
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None
    action = runner._daily_dongtian_validate_enemy_detail(
        runtime,
        "大罗天墟",
        {"own_union_name": "玉清道宗"},
    )

    try:
        while True:
            next(action)
    except RuntimeError as exc:
        assert "已返回洞天主页" in str(exc)

    assert runtime.calls == [
        ("wait_view", 341),
        ("wait_click_then_view", 341, "返回", 279),
    ]


def test_daily_dongtian_packet_builds_enemy_place_list(monkeypatch):
    from backend.core.fanxiu.packet import current_facts

    def fake_facts(*_args, **_kwargs):
        return {
            "decoded_records": {
                "records": [
                    {"payload": {"parsed": {"mines": {"_count": 0, "items": []}}}},
                    {
                    "payload": {
                        "parsed": {
                            "mines": {
                                "_count": 39,
                                "items": [
                                    {"id": 1, "crossUnion": {"id": 11, "name": "enemy"}},
                                    {"id": 7, "crossUnion": {"id": 22, "name": "own"}},
                                ],
                            }
                        }
                    }
                    },
                ]
            }
        }

    monkeypatch.setattr(current_facts, "catch_up_and_list_fanxiu_packet_decoded_records", fake_facts)
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    assert runner._daily_dongtian_enemy_places_from_latest_packet({"own_union_name": "own"}) == ["\u767d\u7389\u4eac"]


def test_daily_dongtian_packet_level_two_ids_match_real_map_order(monkeypatch):
    from backend.core.fanxiu.packet import current_facts

    def fake_facts(*_args, **_kwargs):
        return {
            "decoded_records": {
                "records": [{
                    "payload": {
                        "parsed": {
                            "mines": {
                                "_count": 8,
                                "items": [
                                    {"id": 2, "crossUnion": {"id": 22, "name": "own"}},
                                    {"id": 3, "crossUnion": {"id": 11, "name": "enemy"}},
                                ],
                            }
                        }
                    }
                }]
            }
        }

    monkeypatch.setattr(current_facts, "catch_up_and_list_fanxiu_packet_decoded_records", fake_facts)
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    assert runner._daily_dongtian_enemy_places_from_latest_packet({"own_union_name": "own"}) == ["\u592a\u660e\u7389\u589f"]


def test_daily_dongtian_enemy_place_uses_dynamic_icon_offset_and_avoids_roster():
    class Shape:
        def __init__(self, box):
            self._box = box

        def box(self):
            return self._box

    class Runtime:
        payload = {}

        def __init__(self):
            self.clicked = []

        def view(self, scene):
            return scene

        def shape(self, scene, title):
            return {
                "窗口": Shape({"x": 13, "y": 160, "w": 872, "h": 1162}),
                "我的编队": Shape({"x": 662, "y": 355, "w": 222, "h": 405}),
                "地点名称": Shape({"x": 457, "y": 1187, "w": 237, "h": 36}),
                "地点图标": Shape({"x": 540, "y": 1080, "w": 74, "h": 50}),
            }[title]

        def wait_view(self, scene, *, label):
            yield

        def cur_frame(self, *, update):
            return "frame"

        def ocr_lines_in_shapes(self, scene, shapes, *, frame_data_url):
                return [
                    {"text": "\u6211\u7684\u7f16\u961f", "x": 723, "y": 363, "w": 153, "h": 42},
                    {"text": "\u6218\u62a5\u76df\u7389\u6e05\u9053\u5b9712/12\u5927\u7f57\u5929\u589f\u5360\u9886\u4e2d", "x": 34, "y": 571, "w": 815, "h": 58},
                    {"text": "\u592a\u660e\u7389\u589f", "x": 750, "y": 691, "w": 99, "h": 29},
                    {"text": "\u5927\u7f57\u5929\u589f", "x": 116, "y": 935, "w": 118, "h": 31},
                ]

        def click_frame_point(self, scene, x, y):
            self.clicked.append((scene, x, y))

        def wait_action_settle(self, seconds):
            yield

    runtime = Runtime()
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None
    action = runner._daily_dongtian_click_first_enemy_place(
        runtime,
        threading.Event(),
        ["\u592a\u660e\u7389\u589f", "\u5927\u7f57\u5929\u589f"],
        max_scrolls=0,
    )

    while True:
        try:
            next(action)
        except StopIteration as done:
            result = done.value
            break

    assert result == "\u5927\u7f57\u5929\u589f"
    assert runtime.clicked == [(279, 176.5, 850.5)]
