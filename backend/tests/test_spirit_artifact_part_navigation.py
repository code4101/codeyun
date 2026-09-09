import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_part_navigation import (
    locate_spirit_artifact_part,
)


NAMES = ('卷', '瑚', '海', '轴', '灵', '山')


def test_visible_last_card_with_missing_name():
    # 7-6 实际 OCR：末端“山”漏字，但第三、第五名称和第六卡片阶字可见。
    tokens = [
        dict(text='海', x=115., y=403., w=44., h=34., parent_line_id='line-8'),
        dict(text='灵', x=476., y=388., w=41., h=52., parent_line_id='line-7'),
        dict(text='阶', x=662., y=343., w=26., h=30., parent_line_id='line-6'),
    ]
    point, direction = locate_spirit_artifact_part(tokens, NAMES, 6)
    assert direction is None
    assert point[0] == pytest.approx(676.25)
    assert locate_spirit_artifact_part(tokens[:2], NAMES, 6) == (None, 'right')


def test_announcement_word_is_not_a_part_anchor():
    tokens = [dict(text=text, x=100.+index*30, y=397., w=30., h=35.,
                   parent_line_id='announcement')
              for index, text in enumerate('八汛玄功效果轴发动')]
    with pytest.raises(ValueError, match='无已知名称'):
        locate_spirit_artifact_part(tokens, NAMES, 6)
