import pytest
"""Pure geometry regression from the real aligned mail OCR frame."""
from backend.core.fanxiu.data_annotation.tasks.mail import MailTaskMixin
from backend.core.fanxiu.runtime_gui.mail import MailWindowGeometry


def geometry():
    return MailWindowGeometry(frame_width=900, frame_height=1600,
        first_center_y=409, second_center_y=599, row_pitch=190, title_center_offset=-35)


def test_aligned_ocr_variant_refines_the_exact_runtime_row():
    fragments = [{"text":"陀舍古帝致婪天传人","x":254,"y":1002,"w":332,"h":31}]
    args = dict(title="陀舍古帝致焚天传人", fallback_y=979, geometry=geometry(), max_row_distance_ratio=.8)
    assert MailTaskMixin._precise_mail_observed_title_point(fragments, **args) is None
    assert MailTaskMixin._precise_mail_observed_title_point(fragments,
        observed_title="陀舍古帝致婪天传人", **args) == (420, 1017.5)


def test_aligned_spelling_cannot_authorize_a_different_row():
    fragments = [{"text":"陀舍古帝致婪天传人","x":254,"y":1192,"w":332,"h":31}]
    assert MailTaskMixin._precise_mail_observed_title_point(fragments,
        title="陀舍古帝致焚天传人", observed_title="陀舍古帝致婪天传人",
        fallback_y=979, geometry=geometry(), max_row_distance_ratio=.48) is None


def test_absent_observed_spelling_preserves_exact_title_behavior():
    fragments = [{"text":"神木馈赠","x":254,"y":810,"w":150,"h":31}]
    assert MailTaskMixin._precise_mail_observed_title_point(fragments,
        title="神木馈赠", fallback_y=789, geometry=geometry(),
        max_row_distance_ratio=.8) == (329,825.5)


@pytest.mark.parametrize("control", ["D", "锁定", "已读", "领取"])
def test_mapped_control_cannot_replace_a_runtime_title(control):
    fragments = [{"text":control,"x":680,"y":670,"w":55,"h":30},
        {"text":"虚天礼敬赠","x":254,"y":810,"w":175,"h":31}]
    point = MailTaskMixin._precise_mail_observed_title_point(fragments,
        title="虚天礼敬赠", observed_title=control, fallback_y=789,
        geometry=geometry(), max_row_distance_ratio=.8)
    assert point == (341.5,825.5)


def test_runtime_title_wins_over_a_similar_neighbour_observation():
    fragments = [
        {"text":"天枢神雷传承长篇宝匣","x":254,"y":1002,"w":332,"h":31},
        {"text":"天璇神雷传承长篇宝匣","x":254,"y":810,"w":332,"h":31},
    ]
    assert MailTaskMixin._precise_mail_observed_title_point(fragments,
        title="天枢神雷传承长篇宝匣", observed_title="天璇神雷传承长篇宝匣",
        fallback_y=979, geometry=geometry(), max_row_distance_ratio=.8) == (420,1017.5)
