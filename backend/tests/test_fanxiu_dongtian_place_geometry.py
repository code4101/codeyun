from backend.core.fanxiu.data_annotation.dongtian_place_geometry import (
    dongtian_geometry_scroll_direction,
    estimate_dongtian_target_center,
    resolve_dongtian_ocr_name,
    dongtian_label_positions,
    normalize_dongtian_place_name,
)


def _normalize(value):
    return str(value or "").replace("[洞天]", "").replace("[福地]", "")


def test_fixed_map_geometry_projects_occluded_month_rainbow_from_visible_neighbors():
    # Real #279 frame: 青冥台 / 星岩廊 are visible while 月虹梁 can be
    # covered by the fixed roster. Geometry may predict, never authorize click.
    places = {
        "青冥台": (-25, -1409),
        "月虹梁": (246, -1609),
        "星岩廊": (-177, -1777),
    }
    lines = [
        {"text": "[洞天]青冥台", "x": 345, "y": 783, "w": 152, "h": 28},
        {"text": "[洞天]星岩廊", "x": 161, "y": 1224, "w": 155, "h": 28},
    ]
    center = estimate_dongtian_target_center("[洞天]月虹梁", lines, places, _normalize)
    assert center is not None
    assert abs(center[0] - 746) < 20
    assert abs(center[1] - 1036) < 20


def test_geometry_requires_multiple_consistent_anchors():
    places = {"甲": (0, 0), "乙": (100, -100), "丙": (200, -200)}
    one = [{"text": "甲", "x": 10, "y": 10, "w": 20, "h": 20}]
    assert estimate_dongtian_target_center("乙", one, places, _normalize) is None


def test_roster_occlusion_guides_scroll_instead_of_click():
    window = {"x": 12, "y": 160, "w": 872, "h": 1162}
    roster = {"x": 662, "y": 355, "w": 222, "h": 405}
    assert dongtian_geometry_scroll_direction((745, 600), window, roster) == "up"
    assert dongtian_geometry_scroll_direction((745, 1036), window, roster) is None
    assert dongtian_geometry_scroll_direction((745, 1500), window, roster) == "down"


def test_ocr_edit_requires_unique_catalog_identity():
    names = ['紫琅阕', '月虹梁', '月虹窟', '蛰龙窟']
    assert resolve_dongtian_ocr_name('[洞天]紫琅', names) == '紫琅阕'
    assert resolve_dongtian_ocr_name('[福地]垫龙窟', names) == '蛰龙窟'
    assert resolve_dongtian_ocr_name('[福地]垫龙窟', names + ['蟠龙窟']) is None
    assert resolve_dongtian_ocr_name('月虹', names) is None
    assert resolve_dongtian_ocr_name('联盟占领紫琅阕', names) is None


def test_multitier_real_label_offsets_predict_truncated_purple_title():
    configs = [
        {'name':'白玉京','group':1,'pos':[0,-326]},
        {'name':'太明玉墟','group':2,'pos':[209,-607]},
        {'name':'大罗天墟','group':2,'pos':[-228,-637]},
        {'name':'[洞天]紫琅阕','group':3,'pos':[105,-889]},
    ]
    lines = [
        {'text':'白玉京','x':451,'y':651.5,'w':0,'h':0},
        {'text':'太明玉墟','x':701,'y':913.5,'w':0,'h':0},
        {'text':'大罗天墟','x':177.5,'y':950,'w':0,'h':0},
    ]
    center = estimate_dongtian_target_center('紫琅阕', lines, dongtian_label_positions(configs), normalize_dongtian_place_name)
    assert center is not None
    assert abs(center[0]-577)<5 and abs(center[1]-1202.5)<5
