import base64
import io

from PIL import Image, ImageDraw

from backend.core.fanxiu.choice_knowledge.activity_quiz import (
    fixed_option_click_point,
    option_panel_visible,
    resolve_activity_quiz_target,
)
from backend.core.fanxiu.choice_knowledge.model import ChoiceContext, ChoiceOption, ChoiceQuestion


def _frame(*, buttons: bool) -> str:
    image = Image.new("RGB", (900, 1600), (238, 238, 225))
    if buttons:
        draw = ImageDraw.Draw(image)
        for center_y in (1059, 1136, 1213):
            draw.rounded_rectangle((260, center_y - 28, 640, center_y + 28), radius=15, fill=(184, 164, 112), outline=(80, 70, 45), width=4)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def test_option_panel_gate_distinguishes_reading_from_choices():
    assert option_panel_visible(_frame(buttons=False)) is False
    assert option_panel_visible(_frame(buttons=True)) is True


def test_fixed_position_target_avoids_option_ocr():
    question = ChoiceQuestion(
        domain="quiz",
        prompt="测试题",
        contexts=[ChoiceContext("activity_quiz", "choice_click", options_order_fixed=True)],
        options=[
            ChoiceOption("甲", status=-1, position=0),
            ChoiceOption("乙", status=1, position=1),
            ChoiceOption("丙", status=-1, position=2),
        ],
    )

    target = resolve_activity_quiz_target(question)

    assert target is not None
    assert (target.position, target.answer, target.reason) == (1, "乙", "fixed_position")
    assert fixed_option_click_point(target.position) == (450.0, 1136.0)


def test_text_answer_falls_back_to_current_option_mapping():
    question = ChoiceQuestion(
        domain="quiz",
        prompt="测试题",
        options=[ChoiceOption("狐狸", status=1)],
    )

    target = resolve_activity_quiz_target(question, ["老虎", "狐狸", "狸猫"])

    assert target is not None
    assert (target.position, target.answer, target.reason) == (1, "狐狸", "observed_option_match")


def test_lingquan_fill_in_answer_can_drive_activity_quiz_click_without_saved_positions():
    question = ChoiceQuestion(
        domain="quiz",
        prompt="韩立的本命法宝是什么？",
        contexts=[ChoiceContext("lingquan", "text_input")],
        options=[ChoiceOption("青竹蜂云剑", status=1, position=None)],
    )

    target = resolve_activity_quiz_target(
        question,
        ["掌天瓶", "青竹蜂云剑。", "玄天斩灵剑"],
    )

    assert target is not None
    assert (target.position, target.reason) == (1, "observed_option_match")


def test_unknown_question_has_no_automatic_guess_target():
    assert resolve_activity_quiz_target(
        None,
        ["未知选项一", "未知选项二", "未知选项三"],
    ) is None
