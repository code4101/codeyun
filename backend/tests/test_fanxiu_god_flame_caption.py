"""Pure allowed-caption contract; no simulated game flow."""
import pytest
from backend.core.fanxiu.runtime_gui.god_flame import STRENGTH, MAIN, flame_action_caption_allowed

@pytest.mark.parametrize('caption,allowed', [('炼化',True),('突 破',True),('购买',False),('升阶',False),('',False),('突破购买',False)])
def test_strengthening_caption_transition(caption,allowed):
    assert flame_action_caption_allowed(STRENGTH,'炼化',caption) is allowed

def test_main_keeps_one_click_refinement_caption():
    assert flame_action_caption_allowed(MAIN,'炼化','一键炼化')
