from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner


class _Runtime:
    def __init__(self):
        self.calls = []

    def wait_click_then_view(self, scene, shape, target):
        self.calls.append(("wait_click_then_view", scene, shape, target))
        yield

    def wait_click(self, scene, shape):
        self.calls.append(("wait_click", scene, shape))
        yield


def test_daily_dongtian_known_occupation_chain():
    runtime = _Runtime()

    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    list(runner._daily_dongtian_continue_enemy_occupation(runtime))

    assert runtime.calls == [
        ("wait_click_then_view", 341, "\u4f4d\u7f6e1", 342),
        ("wait_click_then_view", 342, "\u5360\u9886", 343),
        ("wait_click_then_view", 343, "\u5360\u9886", 344),
        ("wait_click_then_view", 344, "\u6218\u6597", 346),
        ("wait_click", 346, "\u7ee7\u7eed"),
    ]


def test_daily_dongtian_packet_builds_enemy_place_list(monkeypatch):
    from backend.core.fanxiu.packet import current_facts

    def fake_facts(*_args, **_kwargs):
        return {
            "decoded_records": {
                "records": [{
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
                }]
            }
        }

    monkeypatch.setattr(current_facts, "catch_up_and_list_fanxiu_packet_decoded_records", fake_facts)
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    assert runner._daily_dongtian_enemy_places_from_latest_packet({"own_union_name": "own"}) == ["\u767d\u7389\u4eac"]
