"""Pure-algorithm checks for recognition.py on small synthetic frames (no GUI, no real assets)."""

import io
import json

import pytest
from PIL import Image

from backend.core.baidu_netdisk.assets import AssetStore
from backend.core.baidu_netdisk.recognition import RecognitionError, match_shape, recognize

W, H = 200, 120
PROFILE = "win-8.1.5-100pct"


def _icon(img, x, y):
    """16x16 blocky non-flat pattern."""
    for by in range(4):
        for bx in range(4):
            shade = (0, 0, 0) if (bx * 3 + by * 5) % 4 < 2 else (200, 60, 30)
            img.paste(shade, (x + bx * 4, y + by * 4, x + bx * 4 + 4, y + by * 4 + 4))


def _png(icons=(), size=(W, H)):
    img = Image.new("RGB", size, (255, 255, 255))
    for x, y in icons:
        _icon(img, x, y)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def store(tmp_path):
    return AssetStore(tmp_path / "store")


def _publish(store, scene_id, shapes, *, icons=((40, 30),), profile=PROFILE):
    ref = store.capture(_png(icons))["image_id"]
    scene = {"scene_id": scene_id, "title": scene_id, "profile": profile,
             "reference_image_id": ref, "shapes": shapes}
    store.publish_scene(scene, 0)
    return ref


def _img_shape(sid="icon", bbox=(40, 30, 16, 16), **kw):
    return {"shape_id": sid, "role": "identity", "bbox": list(bbox), "threshold": 0.9, "match": "image", **kw}


def _ocr_shape(sid, text, bbox, mode="exact", role="identity", **kw):
    return {"shape_id": sid, "role": role, "bbox": list(bbox), "ocr": text, "ocr_mode": mode, **kw}


def _line(lid, text, x, y, w, h=14, score=0.95, chars=False):
    line = {"line_id": lid, "text": text, "x": x, "y": y, "w": w, "h": h, "score": score}
    tokens = []
    if chars:
        step = w / len(text)
        tokens = [{"text": c, "x": x + i * step, "y": y, "w": step, "h": h, "parent_line_id": lid, "order": i}
                  for i, c in enumerate(text)]
    return line, tokens


def _doc(*items):
    return {"lines": [i[0] for i in items], "tokens": [t for i in items for t in i[1]]}


# -- image template matching ---------------------------------------------------
def test_image_match_within_padding_and_nms_merges_neighbour_peaks(store):
    _publish(store, "home", [_img_shape(threshold=0.5)])
    got = recognize(store, _png([(43, 33)]), scene_ids=["home"], profile=PROFILE)
    assert got["status"] == "matched" and got["scene_id"] == "home" and got["candidates"] == ["home"]
    assert got["shapes"]["icon"]["bbox"] == [43, 33, 16, 16] and got["shapes"]["icon"]["score"] > 0.99
    far = recognize(store, _png([(70, 30)]), scene_ids=["home"], profile=PROFILE)
    assert far["status"] == "unknown" and far["shapes"] == {}
    assert far["scenes"]["home"]["shapes"]["icon"]["status"] == "unmatched"


def test_image_id_template_and_colour_fidelity(store):
    ref = store.capture(_png([(40, 30)]))["image_id"]
    crop = store.derive(ref, [40, 30, 16, 16])["image_id"]
    store.publish_scene({"scene_id": "home", "title": "t", "profile": PROFILE, "reference_image_id": ref,
                         "shapes": [_img_shape(image_id=crop)]}, 0)
    assert recognize(store, _png([(38, 28)]), scene_ids=["home"], profile=PROFILE)["status"] == "matched"
    inverted = Image.open(io.BytesIO(_png([(38, 28)]))).point(lambda v: 255 - v)
    buf = io.BytesIO()
    inverted.save(buf, format="PNG")
    assert recognize(store, buf.getvalue(), scene_ids=["home"], profile=PROFILE)["status"] == "unknown"


def test_flat_template_refused_and_flat_frame_never_scores(store):
    _publish(store, "blank", [_img_shape(bbox=(120, 80, 16, 16))])
    got = recognize(store, _png([(40, 30)]), scene_ids=["blank"], profile=PROFILE)
    assert got["status"] == "unknown"
    assert got["scenes"]["blank"]["shapes"]["icon"]["reason"] == "flat_template"
    _publish(store, "home", [_img_shape()])
    flat = recognize(store, _png(), scene_ids=["home"], profile=PROFILE)
    assert flat["status"] == "unknown" and flat["scenes"]["home"]["shapes"]["icon"]["score"] < 0.9


def test_separated_hits_in_floating_region_are_ambiguous(store):
    _publish(store, "home", [_img_shape(floating=True, search_padding=60)])
    two = recognize(store, _png([(40, 30), (80, 30)]), scene_ids=["home"], profile=PROFILE)
    assert two["status"] == "unknown"
    icon = two["scenes"]["home"]["shapes"]["icon"]
    assert icon["status"] == "ambiguous" and icon["bbox"] is None
    with pytest.raises(RecognitionError):
        _publish(store, "wide", [_img_shape(search_padding=60)])
        recognize(store, _png([(40, 30)]), scene_ids=["wide"], profile=PROFILE)


# -- OCR bbox modes ------------------------------------------------------------
def test_ocr_contains_locates_short_word_inside_long_line(store):
    _publish(store, "root", [_ocr_shape("crumb", "全部文件", (10, 20, 40, 14), mode="contains")])
    long_line = _line("l1", "全部文件 / 我的资源", 10, 20, 110, chars=True)
    got = recognize(store, _png(), scene_ids=["root"], profile=PROFILE, ocr_document=_doc(long_line))
    assert got["status"] == "matched"
    assert got["shapes"]["crumb"]["bbox"] == [10, 20, 40, 14]  # 4 of 11 chars of 110px, never the whole line
    no_tokens = _doc(_line("l1", "全部文件 / 我的资源", 10, 20, 110))
    miss = recognize(store, _png(), scene_ids=["root"], profile=PROFILE, ocr_document=no_tokens)
    assert miss["status"] == "unknown"
    assert miss["scenes"]["root"]["shapes"]["crumb"]["reason"] == "ocr_not_found"


def test_ocr_is_local_exact_regex_score_and_duplicates(store):
    _publish(store, "s", [_ocr_shape("up", "上传", (100, 10, 30, 14)),
                          _ocr_shape("n", r"共\d+项", (100, 60, 50, 14), mode="regex")])

    def run(*items):
        r = recognize(store, _png(), scene_ids=["s"], profile=PROFILE, ocr_document=_doc(*items))
        return r["status"], {k: v["reason"] for k, v in r["scenes"]["s"]["shapes"].items()}

    n = _line("n", "共12项", 100, 60, 50)
    assert run(_line("u", "上 传", 100, 10, 30), n)[0] == "matched"
    assert run(_line("u", "上传", 10, 100, 30), n) == ("unknown", {"up": "ocr_not_found", "n": "ocr"})
    assert run(_line("u", "上传文件", 100, 10, 30), n)[1]["up"] == "ocr_not_found"
    assert run(_line("u", "上传", 100, 10, 30, score=0.3), n)[1]["up"] == "ocr_low_score"
    assert run(_line("u", "上传", 100, 10, 30), _line("n", "共x项", 100, 60, 50))[1]["n"] == "ocr_not_found"
    dup = run(_line("u", "上传", 100, 10, 14), _line("v", "上传", 118, 10, 14), n)
    assert dup == ("unknown", {"up": "ocr_hits:2", "n": "ocr"})
    assert run(_line("u", "上传", 100, 10, 30))[1]["n"] == "ocr_not_found"
    no_doc = recognize(store, _png(), scene_ids=["s"], profile=PROFILE)
    assert no_doc["scenes"]["s"]["shapes"]["up"]["reason"] == "ocr_unavailable"


# -- scene identity ------------------------------------------------------------
def test_required_and_any_of_optional_identity(store):
    _publish(store, "s", [_ocr_shape("a", "A", (10, 10, 20, 14)),
                          _ocr_shape("b", "B", (60, 10, 20, 14), required=False),
                          _ocr_shape("c", "C", (110, 10, 20, 14), required=False),
                          _ocr_shape("go", "GO", (10, 80, 20, 14), role="action")])
    a, b, c = (_line(t, t, x, 10, 20) for t, x in (("A", 10), ("B", 60), ("C", 110)))
    go = _line("go", "GO", 10, 80, 20)

    def status(*items):
        return recognize(store, _png(), scene_ids=["s"], profile=PROFILE, ocr_document=_doc(*items))["status"]

    assert status(a, c) == "matched" and status(a, b) == "matched"
    assert status(a, go) == "unknown"          # optional group needs one hit
    assert status(b, c, go) == "unknown"       # required A missing; action GO proves nothing
    full = recognize(store, _png(), scene_ids=["s"], profile=PROFILE, ocr_document=_doc(a, b, go))
    assert full["shapes"]["go"]["bbox"] == [10, 80, 20, 14] and full["shapes"]["c"]["status"] == "unmatched"


def test_profile_and_size_rejection_and_multi_scene_ambiguity(store):
    shape = [_ocr_shape("a", "A", (10, 10, 20, 14))]
    _publish(store, "one", shape)
    _publish(store, "two", shape)
    _publish(store, "other", shape, profile="mac")
    doc = _doc(_line("a", "A", 10, 10, 20))
    both = recognize(store, _png(), scene_ids=["one", "two", "other"], profile=PROFILE, ocr_document=doc)
    assert both["status"] == "ambiguous" and both["scene_id"] is None
    assert both["candidates"] == ["one", "two"] and both["scenes"]["other"]["reason"] == "profile_mismatch"
    bad = recognize(store, _png(), scene_ids=["one"], profile="unknown", ocr_document=doc)
    assert bad["status"] == "rejected" and bad["candidates"] == []
    small = recognize(store, _png(size=(W, H + 1)), scene_ids=["one"], profile=PROFILE, ocr_document=doc)
    assert small["status"] == "rejected" and small["scenes"]["one"]["reason"] == "size_mismatch"
    _publish(store, "acts", [_ocr_shape("go", "A", (10, 10, 20, 14), role="action")])
    acts = recognize(store, _png(), scene_ids=["acts"], profile=PROFILE, ocr_document=doc)
    assert acts["status"] == "rejected" and acts["scenes"]["acts"]["reason"] == "no_identity_shape"


def test_match_shape_proves_scene_first_and_leaks_no_text(store):
    _publish(store, "s", [_ocr_shape("a", "身份标记", (10, 10, 60, 14)), _img_shape("btn", role="action")])
    doc = _doc(_line("a", "身份标记", 10, 10, 60))
    got = match_shape(store, _png([(42, 31)]), scene_id="s", shape_id="btn", profile=PROFILE, ocr_document=doc)
    assert got["status"] == "matched" and got["bbox"] == [42, 31, 16, 16] and got["candidates"] == ["s"]
    assert got["revision"] == 1 and len(got["frame_sha256"]) == 64
    assert "身份标记" not in json.dumps(got, ensure_ascii=False)
    unproven = match_shape(store, _png([(42, 31)]), scene_id="s", shape_id="btn", profile=PROFILE)
    assert unproven["status"] == "unknown" and unproven["bbox"] is None and "btn" not in unproven["shapes"]
    with pytest.raises(RecognitionError):
        match_shape(store, _png(), scene_id="s", shape_id="nope", profile=PROFILE, ocr_document=doc)


# -- explicit parent scenes (overlay specificity) ------------------------------
_ROOT = [_ocr_shape("root", "全部文件", (10, 10, 60, 14))]
_MENU = [_ocr_shape("menu", "菜单", (100, 60, 40, 14))]
_ROOT_LINE = _line("r", "全部文件", 10, 10, 60)
_MENU_LINE = _line("m", "菜单", 100, 60, 40)


def _publish_tree(store, scene_id, shapes, parents=None):
    ref = store.capture(_png())["image_id"]
    scene = {"scene_id": scene_id, "title": scene_id, "profile": PROFILE, "reference_image_id": ref,
             "shapes": shapes}
    if parents is not None:
        scene["parent_scene_ids"] = parents
    store.publish_scene(scene, 0)


def _rec(store, ids, *lines):
    return recognize(store, _png(), scene_ids=ids, profile=PROFILE, ocr_document=_doc(*lines))


def test_explicit_descendant_wins_over_visible_ancestor(store):
    _publish_tree(store, "root", _ROOT)
    _publish_tree(store, "menu", _ROOT + _MENU, ["root"])
    got = _rec(store, ["root", "menu"], _ROOT_LINE, _MENU_LINE)
    assert got["status"] == "matched" and got["scene_id"] == "menu"
    assert got["candidates"] == ["root", "menu"] and got["suppressed"] == ["root"]
    closed = _rec(store, ["root", "menu"], _ROOT_LINE)
    assert closed["status"] == "matched" and closed["scene_id"] == "root" and closed["suppressed"] == []
    # menu not yet learned / not supplied: root alone matches as before
    assert _rec(store, ["root"], _ROOT_LINE, _MENU_LINE)["scene_id"] == "root"


def test_ancestor_suppression_is_transitive_through_unsupplied_parents(store):
    _publish_tree(store, "root", _ROOT)
    _publish_tree(store, "panel", _ROOT, ["root"])
    _publish_tree(store, "menu", _MENU, ["panel"])
    got = _rec(store, ["menu", "root"], _ROOT_LINE, _MENU_LINE)
    assert got["status"] == "matched" and got["scene_id"] == "menu" and got["suppressed"] == ["root"]
    assert "panel" not in got["scenes"]


def test_unrelated_descendants_stay_ambiguous_and_no_score_tiebreak(store):
    _publish_tree(store, "root", _ROOT)
    _publish_tree(store, "menu", _MENU, ["root"])
    _publish_tree(store, "dialog", _MENU, ["root"])
    _publish_tree(store, "other", _ROOT)  # unrelated, no relation declared
    got = _rec(store, ["root", "menu", "dialog"], _ROOT_LINE, _MENU_LINE)
    assert got["status"] == "ambiguous" and got["scene_id"] is None
    assert got["candidates"] == ["root", "menu", "dialog"] and got["suppressed"] == ["root"]
    plain = _rec(store, ["root", "other"], _ROOT_LINE)
    assert plain["status"] == "ambiguous" and plain["suppressed"] == []
    # siblings never suppress each other and a descendant that did not match suppresses nothing
    assert _rec(store, ["root", "other", "menu"], _ROOT_LINE)["status"] == "ambiguous"


@pytest.mark.parametrize("graph, needle", [
    ({"a": "root"}, "list"),
    ({"a": ["root", "root"]}, "duplicate"),
    ({"a": ["a"]}, "itself"),
    ({"a": [""]}, "list"),
    ({"a": ["ghost"]}, "unknown parent"),
    ({"a": ["b"], "b": ["a"]}, "cycle"),
    ({"a": ["b"], "b": ["c"], "c": ["b"]}, "cycle"),
])
def test_invalid_parent_graph_fails_closed(store, graph, needle):
    _publish_tree(store, "root", _ROOT)
    for sid, parents in graph.items():
        _publish_tree(store, sid, _MENU, parents)
    with pytest.raises(RecognitionError, match=needle):
        _rec(store, ["a", "root"], _ROOT_LINE, _MENU_LINE)
    with pytest.raises(RecognitionError, match=needle):
        match_shape(store, _png(), scene_id="a", shape_id="menu", profile=PROFILE,
                    ocr_document=_doc(_MENU_LINE))


def test_parent_graph_is_bounded(store, monkeypatch):
    import backend.core.baidu_netdisk.recognition as recognition
    monkeypatch.setattr(recognition, "MAX_SCENE_GRAPH", 3)
    _publish_tree(store, "s0", _ROOT)
    for i in range(1, 4):
        _publish_tree(store, f"s{i}", _ROOT, [f"s{i - 1}"])
    with pytest.raises(RecognitionError, match="exceeds"):
        _rec(store, ["s3"], _ROOT_LINE)
    assert _rec(store, ["s2"], _ROOT_LINE)["scene_id"] == "s2"
