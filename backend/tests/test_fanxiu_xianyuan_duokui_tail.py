from backend.core.fanxiu.data_annotation.tasks.xianyuan_duokui_tail import (
    read_xianyuan_shop_wallet_from_ocr,
)


def test_exact_shop_header_reads_activity_local_wallet() -> None:
    class Runtime:
        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "a", "text": "当前拥有夺魁灵玉", "x": 10, "y": 100, "w": 120, "h": 20},
                {"parent_line_id": "a", "text": "12080", "x": 140, "y": 100, "w": 50, "h": 20},
                {"parent_line_id": "b", "text": "活动期间累计夺魁灵玉", "x": 10, "y": 130, "w": 160, "h": 20},
                {"parent_line_id": "b", "text": "136080", "x": 180, "y": 130, "w": 60, "h": 20},
            ]

    assert read_xianyuan_shop_wallet_from_ocr(Runtime()) == (12_080, 136_080)
