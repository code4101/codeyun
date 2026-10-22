"""Deterministic capability checks for AssetNetdiskClient (static synthetic frame, no GUI, no UI flow)."""

import base64
import io
import time

import pytest
from PIL import Image

from backend.core.baidu_netdisk.asset_client import AssetNetdiskClient, AssetProjectionDriver
from backend.core.baidu_netdisk.assets import AssetStore
from backend.core.baidu_netdisk.client import StepFailed
from backend.core.baidu_netdisk.ledger import TransferLedger
from backend.core.baidu_netdisk.ownership import NativeAccountHandle

W, H, PROFILE, WID = 200, 120, "win-test", "11:22:33.0"
FACTS = {"client_version": "8.1.5.103", "width": W, "height": H, "dpi": 96}


SW, SH, SID = 180, 100, "44:55:66.0"  # settings: an owned window of its own (client area only)
MAIN_ROW = {"id": WID, "title": "百度网盘", "application": "BaiduNetdisk.exe", "width": 800, "height": 600}
SETTINGS_ROW = {"id": SID, "title": "设置", "application": "BaiduNetdisk.exe", "width": 725, "height": 477}
SPOTS = {(W, H): ((40, 30), (120, 70)), (SW, SH): ((21, 20), (103, 20), (61, 60))}


WORKAREA = [0, 0, 1920, 1040]


def client_facts(w, h, visible=True, minimized=False, left=100, top=100):
    """Static public-geometry facts of a window (list rows carry outer sizes only); canonical keys."""
    outer = [left - 8, top - 31, left + w + 8, top + h + 8]
    inside = outer[0] >= WORKAREA[0] and outer[1] >= WORKAREA[1] and outer[2] <= WORKAREA[2] \
        and outer[3] <= WORKAREA[3]
    return {"width": w, "height": h, "visible": visible, "minimized": minimized,
            "client_screen_rect": [left, top, left + w, top + h], "outer_screen_rect": outer,
            "workarea_rect": list(WORKAREA), "virtual_desktop_rect": [0, 0, 1920, 1080],
            "outer_in_workarea": inside}


def _png(w=W, h=H):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    for x0, y0 in SPOTS[(w, h)]:
        for by in range(4):
            for bx in range(4):
                shade = (0, 0, 0) if (bx * 3 + by * 5 + x0) % 4 < 2 else (200, 60, 30)
                img.paste(shade, (x0 + bx * 4, y0 + by * 4, x0 + bx * 4 + 4, y0 + by * 4 + 4))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Seams:
    """Static frame + recorded inputs; only the driver seams, no screen transitions."""

    def __init__(self):
        self.inputs, self.dpi, self.frames = [], 96, 0
        self.data = "data:image/png;base64," + base64.b64encode(_png()).decode()
        self.settings_data = "data:image/png;base64," + base64.b64encode(_png(SW, SH)).decode()
        self.windows, self.captured, self.closes = [dict(MAIN_ROW)], [], []
        self.on_input = self.close_result = None
        self.fits, self.activations = [], []
        self.geometry = {WID: client_facts(W, H), SID: client_facts(SW, SH)}  # absent: window gone

    def window_geometry(self, window_id):
        if window_id not in self.geometry:
            raise RuntimeError("window gone")
        return {"window_id": window_id, **self.geometry[window_id]}

    def list_windows(self):
        return [dict(w) for w in self.windows]

    def activate(self, window_id):
        self.activations.append(window_id)
        return {}

    def fit_window(self, window_id):
        """Static delegate contract: the recorded window is moved inside, client size unchanged."""
        self.fits.append(window_id)
        old = self.geometry[window_id]
        self.geometry[window_id] = client_facts(old["width"], old["height"])
        return {"window_id": window_id, "moved": True, "old_rect": old["outer_screen_rect"],
                "new_rect": self.geometry[window_id]["outer_screen_rect"], "workarea_rect": list(WORKAREA),
                "client_screen_rect": self.geometry[window_id]["client_screen_rect"],
                "width": old["width"], "height": old["height"]}

    def frame(self, window_id):
        self.frames += 1
        self.captured.append(window_id)
        small = self.geometry.get(window_id, {}).get("width") == SW
        w, h, data = (SW, SH, self.settings_data) if small else (W, H, self.data)
        return {"token": f"f{self.frames}", "window_id": window_id, "width": w, "height": h, "full": True,
                "input_token": "tok", "patches": [{"width": w, "height": h, "image": data}]}

    def ocr(self, frame):
        return {"lines": [{"line_id": 1, "text": "机密文本", "x": 5, "y": 5, "w": 40, "h": 12}], "tokens": []}

    def input(self, frame, **action):
        self.inputs.append(action)
        if self.on_input:
            self.on_input()

    def close_window(self, window_id, expected_title):
        self.closes.append((window_id, expected_title))
        if isinstance(self.close_result, Exception):
            raise self.close_result
        if self.close_result is not False:
            self.windows = [w for w in self.windows if w["id"] != window_id]
        return self.close_result is not False

    def client_version(self, window_id):
        return "8.1.5.103"

    def window_dpi(self, window_id):
        return self.dpi


class Leases:
    def check(self, resource, token):
        return {"token": token}


@pytest.fixture
def env(tmp_path):
    return _build(tmp_path)[:5]


def _build(tmp_path, action_gap=0.0):
    store = AssetStore(tmp_path / "store")
    ref = store.capture(_png())["image_id"]
    shape = {"role": "action", "threshold": 0.9, "match": "image"}
    store.publish_scene({"scene_id": "home", "title": "home", "profile": PROFILE, "reference_image_id": ref, **FACTS,
                         "shapes": [{"shape_id": "mark", "role": "identity", "bbox": [40, 30, 16, 16],
                                     "threshold": 0.9, "match": "image"},
                                    {"shape_id": "nav", "bbox": [120, 70, 16, 16], **shape,
                                     "effect": "navigation", "action": "click", "sceneJumpTarget": "home"},
                                    {"shape_id": "loose", "bbox": [120, 70, 16, 16], **shape, "action": "click"},
                                    {"shape_id": "submit", "bbox": [120, 70, 16, 16], **shape,
                                     "effect": "upload_submit", "action": "click"},
                                    {"shape_id": "peek", "bbox": [120, 70, 16, 16], **shape,
                                     "effect": "inspection", "action": "hover", "sceneJumpTarget": "home"},
                                    {"shape_id": "open_settings", "bbox": [120, 70, 16, 16], **shape,
                                     "effect": "navigation", "action": "click", "sceneJumpTarget": "settings"}]}, 0)
    store.publish_scene(_settings_scene(store.capture(_png(SW, SH))["image_id"]), 0)
    now, waits, hooks = [100.0], [], []

    def wait(seconds):  # account pacing only; the client's settle sleep is a no-op
        waits.append(round(seconds, 6))
        now[0] += seconds
        while hooks:  # something changes while the account waits out its gap
            hooks.pop(0)()

    seams = Seams()
    account = NativeAccountHandle(Leases(), "t", external=True, action_gap=action_gap, clock=lambda: now[0],
                                  sleep=wait)
    client = AssetNetdiskClient(ledger=TransferLedger(tmp_path / "ledger.json"), store=store, profile=PROFILE,
                                scene_ids=["home", "settings"], account=account, driver=seams, clock=lambda: now[0],
                                sleep=lambda s: None, poll_attempts=2)
    client.hooks = hooks
    return client, seams, store, now, ref, account, waits


def _settings_scene(ref, **override):
    ident = {"role": "identity", "threshold": 0.9, "match": "image"}
    return {"scene_id": "settings", "title": "settings", "profile": PROFILE, "reference_image_id": ref,
            "client_version": FACTS["client_version"], "width": SW, "height": SH, "dpi": 96,
            "window_role": "settings", "window_title": "设置",
            "shapes": [{"shape_id": "id_a", "bbox": [21, 20, 16, 16], **ident},
                       {"shape_id": "id_b", "bbox": [103, 20, 16, 16], **ident},
                       {"shape_id": "peek", "bbox": [61, 60, 16, 16], "role": "action", "threshold": 0.9,
                        "match": "image", "effect": "inspection", "action": "hover"}], **override}


def test_capability_is_bound_single_use_and_clicks_matched_bbox(env):
    client, seams, store, _, _ = env
    prepared = client.prepare_action("home", "nav", discovery=True)
    ev = prepared["evidence"]
    assert ev["bbox"] == [120, 70, 16, 16] and ev["revision"] == 1
    assert store.get_image(ev["capture_id"])["sha256"] == ev["capture_sha256"]
    receipt = client.execute_action(prepared["action_id"])
    assert receipt["status"] == "acted_unverified" and receipt["after"]
    assert seams.inputs == [{"action": "click", "x": 128 / W, "y": 78 / H, "button": "left"}]
    with pytest.raises(StepFailed, match="action_unknown_or_consumed"):
        client.execute_action(prepared["action_id"])
    assert len(seams.inputs) == 1


def test_expired_and_revision_changed_capabilities_are_refused(env):
    client, seams, store, now, _ = env
    stale = client.prepare_action("home", "nav")
    now[0] += 5.01
    with pytest.raises(StepFailed, match="action_expired"):
        client.execute_action(stale["action_id"])
    pending = client.prepare_action("home", "nav")
    body = dict(store.get_scene("home")["scene"], title="home v2")
    store.publish_scene(body, 1)
    with pytest.raises(StepFailed, match="asset_revision_changed"):
        client.execute_action(pending["action_id"])
    assert seams.inputs == []


def test_dpi_change_after_prepare_is_refused(env):
    client, seams, _, _, _ = env
    prepared = client.prepare_action("home", "nav")
    seams.dpi = 144  # the live profile no longer selects the main window
    with pytest.raises(StepFailed, match="client_main_window_not_unique"):
        client.execute_action(prepared["action_id"])
    assert seams.inputs == []


@pytest.mark.parametrize("shape_id", ["loose", "submit"])
def test_undeclared_or_business_effect_is_refused_before_any_frame(env, shape_id):
    client, seams, _, _, _ = env
    with pytest.raises(StepFailed, match="action_not_declared"):
        client.action_shape("home", shape_id, expected_scene_ids=["home"])
    assert seams.frames == 0 and seams.inputs == []


def test_normal_mode_requires_expected_scene(env):
    client, seams, store, _, ref = env
    body = dict(store.get_scene("home")["scene"])
    body["shapes"] = [dict(s) for s in body["shapes"]]
    del body["shapes"][1]["sceneJumpTarget"]
    store.publish_scene(body, 1)
    with pytest.raises(StepFailed, match="expected_scene_required"):
        client.prepare_action("home", "nav")
    assert seams.frames == 0


def test_observation_receipt_has_ids_only_and_live_facts_gate_scenes(env):
    client, seams, _, _, _ = env
    receipt = client.observe_assets()
    assert receipt["recognition"]["status"] == "matched" and receipt["scene_id"] == "home"
    assert receipt["facts"] == {"window_id": WID, **FACTS}
    assert "机密文本" not in repr(receipt) and "base64" not in repr(receipt)
    seams.dpi = 120  # caller strings cannot override the live window facts
    assert client.observe_assets(WID)["recognition"]["reason"] == "profile_facts_mismatch"
    with pytest.raises(StepFailed, match="client_main_window_not_unique"):  # no declared profile selects it
        client.prepare_action("home", "nav")
    assert seams.inputs == []


def test_ocr_failure_keeps_first_raw_frame_with_typed_ids(env):
    client, seams, store, _, _ = env

    def broken_ocr(frame):
        raise ValueError("机密文本 C:/private/path")

    seams.ocr = broken_ocr
    with pytest.raises(StepFailed, match="observation_failed") as info:
        client.observe_assets()
    facts = info.value.facts
    assert set(facts) == {"error", "observation_id", "capture_id", "capture_sha256"}
    assert facts["error"] == "ValueError" and "机密文本" not in repr(facts) and info.value.__cause__ is None
    record = store.get_image(facts["capture_id"])
    assert record["sha256"] == facts["capture_sha256"] and record["source"]["captured_at"].endswith("+00:00")
    assert store.read_image(facts["capture_id"]) == base64.b64decode(seams.data.split(",", 1)[1])
    assert seams.frames == 1 and seams.inputs == []  # no replacement capture


def test_inherited_input_is_profile_gated_by_live_role_selection(env):
    client, seams, _, _, _ = env
    screen = client.observe(WID)
    seams.dpi = 120  # no configured scene declares 120 dpi: the window belongs to no declared role
    with pytest.raises(StepFailed, match="input_window_not_declared") as info:
        client.act(screen, WID, action="key", key="enter")
    assert seams.inputs == [] and seams.frames == 2  # one fresh raw evidence frame, no OCR
    facts = info.value.facts
    assert client.store.get_image(facts["evidence_capture_id"])["sha256"] == facts["evidence_capture_sha256"]
    assert "base64" not in repr(facts)
    seams.dpi = 96  # dynamic legacy screens need declared facts only, not a scene match
    client.act(screen, WID, action="key", key="enter")
    assert seams.inputs == [{"action": "key", "key": "enter"}]


@pytest.mark.parametrize("title", ["设置", "请选择文件/文件夹", "分享：某文件"])
def test_inherited_main_input_refused_while_owned_window_open_before_or_after_pacing(tmp_path, title):
    client, seams, _, _, _, account, waits = _build(tmp_path, action_gap=1.0)
    screen = client.observe(WID)
    seams.geometry["77:1:1.0"] = client_facts(SW, SH)
    seams.windows.append({**SETTINGS_ROW, "id": "77:1:1.0", "title": title})
    with pytest.raises(StepFailed, match="interfering_window_open") as info:
        client.act(screen, WID, action="key", key="enter")
    assert waits == [] and "某文件" not in repr(info.value.facts)
    seams.windows.pop()
    client.hooks.append(lambda: seams.windows.append({**SETTINGS_ROW, "id": "77:1:1.0", "title": title}))
    with pytest.raises(StepFailed, match="interfering_window_open"):
        client.driver.park_pointer(screen.frame)
    assert seams.inputs == [] and client.driver.generation == 0 and waits == [1.0]


def test_inherited_input_needs_the_unique_window_of_a_declared_role(env):
    client, seams, _, _, _ = env
    screen = client.observe(WID)
    seams.geometry["78:1:1.0"] = client_facts(W, H)
    seams.windows.append({**MAIN_ROW, "id": "78:1:1.0"})  # a second main-profile window: main is not unique
    with pytest.raises(StepFailed, match="input_window_not_declared"):
        client.act(screen, WID, action="key", key="enter")
    seams.windows = [dict(MAIN_ROW), {**SETTINGS_ROW, "id": "79:1:1.0", "title": "关于"}]
    with pytest.raises(StepFailed, match="input_window_not_declared"):  # an undeclared window's frame
        client.driver.input({**screen.frame, "window_id": "79:1:1.0"}, action="key", key="enter")
    assert seams.inputs == []
    seams.windows.append(dict(SETTINGS_ROW))  # settings role: its own facts, and main is not "interfering" for it
    settings = client.observe(SID)
    seams.dpi = 120  # no declared settings profile selects it any more
    with pytest.raises(StepFailed, match="input_window_not_declared"):
        client.act(settings, SID, action="key", key="enter")
    assert seams.inputs == []
    seams.dpi = 96
    client.act(settings, SID, action="key", key="enter")
    assert seams.inputs == [{"action": "key", "key": "enter"}]


def test_every_input_paced_once_and_prepare_only_waits(tmp_path):
    client, seams, _, now, _, account, waits = _build(tmp_path, action_gap=3.0)
    client.act(client.observe(WID), WID, action="key", key="enter")
    assert waits == [3.0] and client.driver.generation == 1
    client.driver.park_pointer(client.observe(WID).frame)
    assert waits == [3.0, 3.0] and seams.inputs[-1]["action"] == "move"
    prepared = client.prepare_action("home", "nav", discovery=True)
    assert waits == [3.0, 3.0, 3.0] and account.actions_taken  # wait_ready: not itself an action
    client.execute_action(prepared["action_id"])
    assert waits == [3.0, 3.0, 3.0] and len(seams.inputs) == 3


def test_intervening_input_supersedes_other_capabilities(env):
    client, seams, _, _, _ = env
    first = client.prepare_action("home", "nav", discovery=True)
    second = client.prepare_action("home", "nav", discovery=True)
    client.execute_action(second["action_id"])
    with pytest.raises(StepFailed, match="action_superseded"):
        client.execute_action(first["action_id"])
    assert len(seams.inputs) == 1


def test_capability_expiry_rechecked_after_pacing(tmp_path):
    client, seams, _, now, _, account, waits = _build(tmp_path, action_gap=3.0)
    prepared = client.prepare_action("home", "nav")
    account.action_gap = 10.0  # the execute-time pace now waits past the 5 s capability
    with pytest.raises(StepFailed, match="action_expired"):
        client.execute_action(prepared["action_id"])
    assert waits == [3.0, 7.0] and seams.inputs == []


@pytest.mark.parametrize("ttl", [0, -1, 5.01, float("inf"), float("nan")])
def test_capability_ttl_must_be_finite_and_at_most_five_seconds(tmp_path, ttl):
    client, *_ = _build(tmp_path)
    with pytest.raises(ValueError, match="capability_ttl"):
        AssetNetdiskClient(ledger=client.ledger, store=client.store, profile=PROFILE, scene_ids=["home"],
                           account=client.account, driver=Seams(), capability_ttl=ttl)


# -- declared window roles ---------------------------------------------------------


def test_roles_select_exact_unique_client_app_title_and_live_client_facts(env):
    client, seams, _, _, _ = env
    seams.geometry["9:9:9.0"] = client_facts(W, H)
    seams.windows += [dict(SETTINGS_ROW), {**MAIN_ROW, "id": "9:9:9.0", "application": "explorer.exe"}]
    assert client.main_window() == WID and client.role_window("settings") == SID
    seams.geometry["77:1:1.0"] = client_facts(SW, SH)  # outer row size differs; live client size decides
    seams.windows.append({**SETTINGS_ROW, "id": "77:1:1.0", "width": 900, "height": 700})
    with pytest.raises(StepFailed, match="role_window_not_unique"):
        client.role_window("settings")
    seams.geometry["78:1:1.0"] = client_facts(W, H)
    seams.windows.append({**MAIN_ROW, "id": "78:1:1.0", "width": 300, "height": 200})
    with pytest.raises(StepFailed, match="client_main_window_not_unique"):
        client.main_window()
    with pytest.raises(StepFailed, match="window_role_unknown"):
        client.role_window("about")
    assert seams.frames == 0


@pytest.mark.parametrize("override, error", [
    ({"window_role": "Bad Role"}, "malformed window_role"),
    ({"window_role": "about", "window_title": ""}, "non-empty exact window_title"),
    ({"window_title": "设置 "}, "window_title must be exactly"),
    ({"window_title": "百度网盘"}, "window_title must be exactly"),
    ({"window_role": "file-dialog"}, "window_title must be exactly"),
    ({"dpi": None}, "must declare"),
])
def test_nonmain_scene_role_title_and_facts_validated_at_construction(tmp_path, override, error):
    client, *_ = _build(tmp_path)
    store = client.store
    record = store.get_scene("settings")
    store.publish_scene(_settings_scene(record["scene"]["reference_image_id"], **override), record["revision"])
    with pytest.raises(ValueError, match=error):
        AssetNetdiskClient(ledger=client.ledger, store=store, profile=PROFILE, scene_ids=["home", "settings"],
                           account=client.account, driver=Seams())


@pytest.mark.parametrize("title", ["设置", "请选择文件/文件夹", "分享：某文件"])
def test_main_action_refused_while_owned_window_open(env, title):
    client, seams, _, _, _ = env
    seams.windows.append({**SETTINGS_ROW, "title": title})
    with pytest.raises(StepFailed, match="interfering_window_open") as info:
        client.action_shape("home", "nav")
    assert seams.frames == 0 and seams.inputs == [] and "某文件" not in repr(info.value.facts)


def test_owned_window_opening_after_prepare_or_while_pacing_is_refused(tmp_path):
    client, seams, _, _, _, account, waits = _build(tmp_path, action_gap=1.0)
    prepared = client.prepare_action("home", "nav")
    seams.windows.append(dict(SETTINGS_ROW))
    with pytest.raises(StepFailed, match="interfering_window_open"):
        client.execute_action(prepared["action_id"])
    seams.windows.pop()
    prepared = client.prepare_action("home", "nav")
    account.action_gap = 3.0  # the execute-time pace waits (within the capability ttl)
    client.hooks.append(lambda: seams.windows.append(dict(SETTINGS_ROW)))
    with pytest.raises(StepFailed, match="interfering_window_open"):
        client.execute_action(prepared["action_id"])
    assert seams.inputs == [] and client.driver.generation == 0 and waits[-1] == 2.0


def test_revision_change_while_pacing_is_refused_at_gate(tmp_path):
    client, seams, store, _, _, account, _ = _build(tmp_path, action_gap=1.0)
    prepared = client.prepare_action("home", "nav")
    account.action_gap = 3.0  # the execute-time pace waits (within the capability ttl)
    client.hooks.append(lambda: store.publish_scene(dict(store.get_scene("home")["scene"], title="v2"), 1))
    with pytest.raises(StepFailed, match="asset_revision_changed"):
        client.execute_action(prepared["action_id"])
    assert seams.inputs == []


def test_navigation_to_settings_reads_back_fresh_settings_window(env):
    client, seams, _, _, _ = env
    seams.on_input = lambda: seams.windows.append(dict(SETTINGS_ROW))
    receipt = client.action_shape("home", "open_settings")
    assert receipt["status"] == "verified" and receipt["recognition"]["scene_id"] == "settings"
    assert receipt["after"][-1]["window_role"] == "settings" and seams.captured[-1] == SID
    assert receipt["before"]["window_role"] == "main" and "机密文本" not in repr(receipt)


def test_settings_frame_never_recognised_as_main_scene(env):
    client, seams, _, _, _ = env
    seams.windows.append(dict(SETTINGS_ROW))
    assert client.observe_assets(role="settings")["scene_id"] == "settings"
    assert client.observe_assets(SID, role="main")["recognition"]["status"] == "rejected"


def test_hover_is_one_pointer_move_with_same_role_readback(env):
    client, seams, _, _, _ = env
    receipt = client.action_shape("home", "peek")
    assert seams.inputs == [{"action": "move", "x": 128 / W, "y": 78 / H}]
    assert receipt["status"] == "verified" and seams.captured[-1] == WID and seams.closes == []


def test_hover_requires_inspection_and_same_role_expected_scene(env):
    client, seams, store, _, _ = env
    with pytest.raises(StepFailed, match="hover_readback_role_mismatch"):
        client.action_shape("home", "peek", expected_scene_ids=["settings"])
    body = dict(store.get_scene("home")["scene"])
    body["shapes"] = [dict(s) for s in body["shapes"]]
    del body["shapes"][4]["sceneJumpTarget"]
    store.publish_scene(body, 1)
    with pytest.raises(StepFailed, match="expected_scene_required"):
        client.action_shape("home", "peek", discovery=True)
    body["shapes"][4]["effect"] = "navigation"
    store.publish_scene(body, 2)
    with pytest.raises(StepFailed, match="action_not_declared"):
        client.action_shape("home", "peek", expected_scene_ids=["home"])
    assert seams.frames == 0 and seams.inputs == []


def test_dismiss_settings_is_one_paced_exact_close_then_fresh_main_readback(tmp_path):
    client, seams, _, _, _, account, waits = _build(tmp_path, action_gap=2.0)
    seams.windows.append(dict(SETTINGS_ROW))
    receipt = client.dismiss_auxiliary("settings", ["home"])
    assert seams.closes == [(SID, "设置")] and seams.inputs == [] and client.driver.generation == 1
    assert waits == [2.0] and receipt["status"] == "verified" and receipt["recognition"]["scene_id"] == "home"
    assert seams.captured == [SID, WID]  # the closed window is never captured again
    assert "机密文本" not in repr(receipt)


def test_dismiss_main_refused_and_close_needs_dismiss_capability(env):
    client, seams, _, _, _ = env
    seams.windows.append(dict(SETTINGS_ROW))
    with pytest.raises(StepFailed, match="main_dismiss_refused"):
        client.dismiss_auxiliary("home", ["home"])
    frame = client.observe(SID).frame
    with pytest.raises(StepFailed, match="close_window_not_declared"):
        client.driver.close_window(SID, "设置", frame=frame)
    with pytest.raises(StepFailed, match="close_window_not_declared"):
        client.driver.close_window(SID, "设置")
    assert seams.closes == [] and client.driver.generation == 0


def test_dismiss_needs_two_declared_identities(env):
    client, seams, store, _, _ = env
    seams.windows.append(dict(SETTINGS_ROW))
    record = store.get_scene("settings")
    body = dict(record["scene"], shapes=[s for s in record["scene"]["shapes"] if s["shape_id"] != "id_b"])
    store.publish_scene(body, record["revision"])
    with pytest.raises(StepFailed, match="dismiss_identity_not_declared"):
        client.dismiss_auxiliary("settings", ["home"])
    assert seams.frames == 0 and seams.closes == []


def test_dismiss_title_mismatch_or_unknown_outcome_never_replayed(env):
    client, seams, _, _, _ = env
    seams.windows.append(dict(SETTINGS_ROW))
    seams.close_result = False
    with pytest.raises(StepFailed, match="close_title_mismatch"):
        client.dismiss_auxiliary("settings", ["home"])
    assert len(seams.closes) == 1
    seams.close_result = OSError("gone")
    receipt = client.dismiss_auxiliary("settings", ["home"])
    assert receipt["status"] == "close_outcome_unknown" and receipt["error"] == "OSError"
    assert len(seams.closes) == 2 and seams.inputs == []


def test_dismiss_refused_when_settings_window_absent_or_dpi_changed(env):
    client, seams, _, _, _ = env
    with pytest.raises(StepFailed, match="role_window_not_unique"):
        client.dismiss_auxiliary("settings", ["home"])
    seams.windows.append(dict(SETTINGS_ROW))
    seams.dpi = 144  # live profile no longer selects the settings window
    with pytest.raises(StepFailed, match="role_window_not_unique"):
        client.dismiss_auxiliary("settings", ["home"])
    assert seams.closes == []


# -- live-profile role selection over public window geometry ---------------------------

AUX_ID = "80:1:1.0"


def _with_aux(tmp_path):
    """Client with a custom ``transfer-panel`` role titled like main but with its own client profile."""
    client, seams, store, *_ = _build(tmp_path)
    store.publish_scene(_settings_scene(store.get_scene("settings")["scene"]["reference_image_id"], scene_id="aux",
                                        title="aux", window_role="transfer-panel", window_title="百度网盘"), 0)
    client = AssetNetdiskClient(ledger=client.ledger, store=store, profile=PROFILE,
                                scene_ids=["home", "settings", "aux"], account=client.account, driver=seams,
                                sleep=lambda s: None)
    seams.geometry[AUX_ID] = client_facts(SW, SH)
    seams.windows.append({**MAIN_ROW, "id": AUX_ID})  # same title and outer row size as main
    return client, seams


def test_same_title_windows_split_by_live_client_profile(tmp_path):
    client, seams = _with_aux(tmp_path)
    assert client.main_window() == WID and client.role_window("transfer-panel") == AUX_ID
    seams.geometry["81:1:1.0"] = client_facts(SW, SH)
    seams.windows.append({**MAIN_ROW, "id": "81:1:1.0"})  # two exact aux profiles stay ambiguous
    with pytest.raises(StepFailed, match="role_window_not_unique"):
        client.role_window("transfer-panel")
    assert client.main_window() == WID and seams.frames == 0


def test_visible_aux_blocks_main_and_aux_ignores_background_main(tmp_path):
    client, seams = _with_aux(tmp_path)
    with pytest.raises(StepFailed, match="interfering_window_open") as info:
        client.action_shape("home", "nav")
    assert info.value.facts["windows"] == ["transfer-panel"] and seams.frames == 0
    client.act(client.observe(AUX_ID), AUX_ID, action="key", key="enter")  # main stays open behind
    assert seams.inputs == [{"action": "key", "key": "enter"}]
    seams.geometry[AUX_ID] = client_facts(SW, SH, minimized=True)
    client.act(client.observe(WID), WID, action="key", key="enter")
    assert len(seams.inputs) == 2


def test_undeclared_same_title_popup_and_missing_facts_fail_closed(env):
    client, seams, _, _, _ = env
    seams.geometry["82:1:1.0"] = client_facts(90, 60)  # same title, no declared profile
    seams.windows.append({**MAIN_ROW, "id": "82:1:1.0"})
    assert client.main_window() == WID
    with pytest.raises(StepFailed, match="interfering_window_open") as info:
        client.action_shape("home", "nav")
    assert info.value.facts["windows"] == ["undeclared"]
    del seams.geometry["82:1:1.0"]  # unreadable facts: never selected, reported as unknown
    with pytest.raises(StepFailed, match="interfering_window_open") as info:
        client.action_shape("home", "nav")
    assert info.value.facts["windows"] == ["unknown"]
    seams.geometry[WID] = {"visible": True, "minimized": False}  # no live client size: main never matches
    with pytest.raises(StepFailed, match="client_main_window_not_unique"):
        client.main_window()
    assert seams.frames == 0 and seams.inputs == []


def test_projection_driver_geometry_is_the_public_core_read(monkeypatch):
    from backend.core.devices import window_projection
    calls = []
    monkeypatch.setattr(window_projection, "projection_window_geometry",
                        lambda window_id: calls.append(window_id) or {"window_id": window_id, "width": 1},
                        raising=False)
    assert AssetProjectionDriver().window_geometry(WID) == {"window_id": WID, "width": 1} and calls == [WID]


def test_projection_driver_fit_is_the_public_core_move(monkeypatch):
    from backend.core.devices import window_projection
    calls = []
    monkeypatch.setattr(window_projection, "fit_projection_window",
                        lambda window_id: calls.append(window_id) or {"window_id": window_id, "moved": False})
    assert AssetProjectionDriver().fit_window(WID) == {"window_id": WID, "moved": False} and calls == [WID]


# -- guarded window preparation ------------------------------------------------------


def test_prepare_window_inside_workarea_is_a_read_only_noop(tmp_path):
    client, seams, _, _, _, account, waits = _build(tmp_path, action_gap=2.0)
    assert client.prepare_window() == {"status": "ready", "moved": False, "window_id": WID,
                                       "window_role": "main", "restored": False}
    assert seams.frames == 0 and seams.fits == [] and seams.activations == [] and waits == []
    assert client.driver.generation == 0 and not account.actions_taken


def test_offscreen_window_fitted_once_through_guard_before_fresh_capability(tmp_path):
    client, seams, store, _, _, account, waits = _build(tmp_path, action_gap=2.0)
    seams.geometry[WID] = client_facts(W, H, left=-500)
    receipt = client.prepare_window()
    assert receipt["status"] == "moved" and receipt["old_rect"][0] < 0 and receipt["new_rect"][0] >= 0
    assert seams.fits == [WID] and client.driver.generation == 1 and waits == [2.0] and seams.inputs == []
    assert store.get_image(receipt["evidence_capture_id"])["sha256"] == receipt["evidence_capture_sha256"]
    seams.geometry[WID] = client_facts(W, H, left=-500)
    prepared = client.prepare_action("home", "nav", discovery=True)  # fit, then wait_ready before the frame
    assert seams.fits == [WID, WID] and waits == [2.0, 2.0, 2.0]
    client.execute_action(prepared["action_id"])  # the capability is not aged by the fit's pacing
    assert len(seams.inputs) == 1 and waits == [2.0, 2.0, 2.0]


def test_prepare_window_refuses_wrong_profile_interference_or_minimized_without_retry(env):
    client, seams, _, _, _ = env
    seams.geometry[WID] = client_facts(W, H, left=-500)
    seams.dpi = 120
    with pytest.raises(StepFailed, match="client_main_window_not_unique"):
        client.prepare_window()
    seams.dpi = 96
    seams.windows.append(dict(SETTINGS_ROW))
    with pytest.raises(StepFailed, match="interfering_window_open"):
        client.prepare_window()
    seams.windows.pop()
    seams.geometry[WID] = client_facts(W, H, minimized=True)  # activate does not restore it
    with pytest.raises(StepFailed, match="window_not_visible"):
        client.prepare_window()
    assert seams.activations == [WID] and seams.fits == [] and seams.frames == 0 and seams.inputs == []
    assert client.driver.generation == 0


def test_fit_gate_refuses_undeclared_frame_with_fresh_evidence(env):
    client, seams, _, _, _ = env
    frame = client.observe(WID).frame
    with pytest.raises(StepFailed, match="fit_window_not_declared"):
        client.driver.fit_window(SID, frame=frame)
    seams.dpi = 120
    with pytest.raises(StepFailed, match="input_window_not_declared") as info:
        client.driver.fit_window(WID, frame=frame)
    assert "evidence_capture_id" in info.value.facts and seams.fits == [] and client.driver.generation == 0


def test_capability_window_changed_while_pacing_carries_fresh_evidence(tmp_path):
    client, seams, store, _, _, account, _ = _build(tmp_path, action_gap=1.0)
    prepared = client.prepare_action("home", "nav")
    account.action_gap = 3.0
    seams.geometry["78:1:1.0"] = client_facts(W, H)
    client.hooks.append(lambda: seams.windows.append({**MAIN_ROW, "id": "78:1:1.0"}))
    frames = seams.frames
    with pytest.raises(StepFailed, match="window_changed") as info:
        client.execute_action(prepared["action_id"])
    facts = info.value.facts
    assert store.get_image(facts["evidence_capture_id"])["sha256"] == facts["evidence_capture_sha256"]
    assert seams.frames == frames + 1 and seams.inputs == [] and "base64" not in repr(facts)


def test_inherited_open_remote_dir_prepares_main_before_base_flow(env, monkeypatch):
    from backend.core.baidu_netdisk.client import NetdiskClient
    client, seams, _, _, _ = env
    seams.geometry[WID] = client_facts(W, H, left=-500)
    calls = []
    monkeypatch.setattr(NetdiskClient, "open_remote_dir",
                        lambda self, remote_dir, *, create_missing=False:
                        calls.append((list(seams.fits), remote_dir, create_missing)) or (WID, None))
    assert client.open_remote_dir("/a/b", create_missing=True) == (WID, None)
    assert calls == [([WID], "/a/b", True)]


# -- exact full-path file selection (pure validators + provider refusal contracts) ---------------------

from backend.core.baidu_netdisk.client import FILE_DIALOG_TITLE  # noqa: E402
from backend.core.baidu_netdisk.file_selection import (filename_band, filename_control, path_value,  # noqa: E402
                                                        typed_path)

DW, DH, DID = 190, 110, "77:88:99.0"
SPOTS[(DW, DH)] = ((21, 20), (103, 20))
DIALOG_ROW = {"id": DID, "title": FILE_DIALOG_TITLE, "application": "BaiduNetdisk.exe", "width": 555, "height": 398}
LABEL = {"line_id": 1, "text": "文件名(N): 机密长名…", "x": 10, "y": 60, "w": 120, "h": 12}  # merged with field
TYPE_ROW = {"line_id": 2, "text": "文件类型(T):", "x": 10, "y": 84, "w": 60, "h": 12}
CONFIRM = {"line_id": 3, "text": "存入百度网盘", "x": 150, "y": 58, "w": 36, "h": 16}  # on the filename row
EDIT = {"type": "EditControl", "name": "文件名(N):", "help_text": "", "bbox": [60, 57, 80, 18], "is_readonly": False}
LONG = "很长 的 文件名 'quoted' — " + "x" * 120 + ".bin"
ELLIPSIZED = {"line_id": 4, "text": LONG[:16] + "…", "x": 10, "y": 30, "w": 120, "h": 12}  # list pixels: clipped
ROW = {"type": "ListItemControl", "name": LONG, "help_text": "", "value": None, "is_readonly": None,
       "bbox": [8, 28, 150, 16]}  # full native name of the same row
DECOYS = [{**ROW, "name": LONG + ".bak", "bbox": [8, 10, 150, 16]}, {**ROW, "name": LONG[:-4], "bbox": [8, 44, 150, 8]}]


def _read(controls, **override):
    return {"window_id": DID, "width": DW, "height": DH, "read_at": 10.0, "source": "uia",
            "coordinate_space": "window_client_pixels", "truncated": False, "controls": controls, **override}


@pytest.mark.parametrize("path", ["C:\\data\\" + LONG, "D:\\空 格\\ü\\a.txt", "\\\\host\\share\\a b.txt"])
def test_typed_path_quotes_exact_full_absolute_path(path):
    assert typed_path(path) == f'"{path}"'


@pytest.mark.parametrize("path", ["rel\\a.txt", "C:\\a\nb.txt", "C:\\a\r.txt", 'C:\\a"b.txt', "C:\\a\\",
                                  " C:\\a.txt", "C:\\a*.txt", "\\\\?\\C:\\a.txt", "C:\\a\tb.txt"])
def test_typed_path_refuses_relative_control_or_reserved_characters(path):
    with pytest.raises(StepFailed, match="source_path_not_typable") as info:
        typed_path(path)
    assert path.strip() not in repr(info.value.facts)


def test_path_value_accepts_exact_value_with_optional_surrounding_quotes():
    full = "C:\\data\\" + LONG
    path_value(full, full)
    path_value(f'"{full}"', full)


@pytest.mark.parametrize("value, reason", [
    (None, "no_value"), ("C:\\data\\" + LONG[:40] + "…", "value_differs"), (LONG, "value_differs"),
    ('"C:\\data\\a.bin" "C:\\data\\b.bin"', "multiple_or_malformed_paths"), ('"C:\\data\\' + LONG, "multiple_or_malformed_paths"),
    ("c:\\DATA\\" + LONG, "value_differs"), ("C:\\data\\" + LONG + " ", "value_differs")])
def test_path_value_refuses_clipped_multiple_or_mismatched_values(value, reason):
    full = "C:\\data\\" + LONG
    with pytest.raises(StepFailed, match="file_name_value_mismatch") as info:
        path_value(value, full)
    assert info.value.facts == {"reason": reason}


@pytest.mark.parametrize("scale", [1, 2.5])
def test_filename_band_follows_confirm_row_and_left_edge_excluding_type_row(scale):
    def s(box):
        return {**box, **{k: box[k] * scale for k in ("x", "y", "w", "h")}}
    label, confirm, type_row = s(LABEL), s(CONFIRM), s(TYPE_ROW)
    band = filename_band(confirm, [label, type_row, confirm], DW * scale, DH * scale)
    x, y, w, h = band
    assert (x, x + w) == (0, confirm["x"])
    assert y <= confirm["y"] + confirm["h"] / 2 < y + h <= type_row["y"] + type_row["h"] / 2
    # the OCR filename label is not an input: missing, or merged with the path text, the band is the same
    assert filename_band(confirm, [type_row, confirm], DW * scale, DH * scale) == band
    merged = {**label, "text": "文件名(N):C:\\机密\\a.bin", "w": confirm["x"] - label["x"]}
    assert filename_band(confirm, [merged, type_row, confirm], DW * scale, DH * scale) == band


def test_filename_band_refuses_degenerate_confirm_row():
    with pytest.raises(StepFailed, match="file_name_band_invalid"):
        filename_band({**CONFIRM, "x": 4}, [CONFIRM], DW, DH)


BAND = filename_band(CONFIRM, [LABEL, TYPE_ROW, CONFIRM], DW, DH)


def test_filename_control_returns_the_single_editable_edit():
    other = {**EDIT, "is_readonly": True, "value": "x"}  # read-only/no-value/out-of-band are not candidates
    found = filename_control(_read([{**EDIT, "value": ""}, other, {**EDIT, "value": None},
                                    {**EDIT, "bbox": [60, 80, 80, 18], "value": ""}]),
                             window_id=DID, width=DW, height=DH, not_before=9.0, band=BAND)
    assert found["bbox"] == EDIT["bbox"]
    assert filename_control(_read([{**EDIT, "name": "文件名（N）：", "value": ""}]), window_id=DID, width=DW,
                            height=DH, not_before=9.0, band=BAND)["bbox"] == EDIT["bbox"]


@pytest.mark.parametrize("name", [None, "", " ", "文件名:", "文件类型(T):", "地址: C:\\机密", "文件名(N): C:\\机密\\a.bin",
                                  "搜索"])
def test_filename_control_refuses_blank_or_unrelated_native_name(name):
    with pytest.raises(StepFailed, match="file_name_control_invalid") as info:
        filename_control(_read([{**EDIT, "name": name, "value": "C:\\secret\\a.bin"}]), window_id=DID, width=DW,
                         height=DH, not_before=9.0, band=BAND)
    assert info.value.facts == {"reason": "control_name_mismatch"}


@pytest.mark.parametrize("override, controls, reason", [
    ({"window_id": WID}, None, "wrong_window"), ({"source": "ocr"}, None, "wrong_provenance"),
    ({"coordinate_space": "screen"}, None, "wrong_provenance"), ({"width": DW + 1}, None, "frame_size_mismatch"),
    ({"read_at": 8.0}, None, "stale_read"), ({"read_at": None}, None, "stale_read"),
    ({"truncated": True}, None, "truncated_read"), ({"truncated": None}, None, "truncated_read"),
    ({}, [], "editable_control_not_unique"), ({}, "two", "editable_control_not_unique")])
def test_filename_control_refuses_wrong_stale_truncated_or_ambiguous_reads(override, controls, reason):
    edit = {**EDIT, "value": "C:\\secret\\a.bin"}
    controls = [edit] if controls is None else [edit, {**edit, "bbox": [70, 58, 60, 16]}] if controls == "two" \
        else controls
    with pytest.raises(StepFailed, match="file_name_control_invalid") as info:
        filename_control(_read(controls, **override), window_id=DID, width=DW, height=DH, not_before=9.0, band=BAND)
    assert info.value.facts["reason"] == reason and "secret" not in repr(info.value.facts)


class DialogSeams(Seams):
    """Static file-dialog frame + programmed bounded UIA reads; no dialog behaviour is simulated."""

    def __init__(self):
        super().__init__()
        self.dialog_data = "data:image/png;base64," + base64.b64encode(_png(DW, DH)).decode()
        self.geometry[DID] = client_facts(DW, DH)
        self.windows.append(dict(DIALOG_ROW))
        self.values, self.reads, self.read_at, self.name = ["", ""], [], None, EDIT["name"]
        self.lines = [ELLIPSIZED, LABEL, TYPE_ROW, CONFIRM]
        self.rows, self.row_read, self.close_on_enter = [], {}, False

    def input(self, frame, **action):
        super().input(frame, **action)
        if action.get("key") == "enter" and self.close_on_enter:  # the dialog accepted something and closed
            self.windows = [w for w in self.windows if w["id"] != DID]

    def frame(self, window_id):
        if window_id != DID:
            return super().frame(window_id)
        self.frames += 1
        self.captured.append(window_id)
        return {"token": f"f{self.frames}", "window_id": DID, "width": DW, "height": DH, "full": True,
                "input_token": "tok", "patches": [{"width": DW, "height": DH, "image": self.dialog_data}]}

    def ocr(self, frame):
        if frame["window_id"] != DID:
            return super().ocr(frame)
        return {"lines": [dict(line) for line in self.lines], "tokens": []}

    def native_controls(self, window_id, *, bounds_xywh, control_types):
        self.reads.append((window_id, list(bounds_xywh), tuple(control_types)))
        read_at = time.monotonic() if self.read_at is None else self.read_at
        if tuple(control_types) == ("ListItemControl",):
            return _read([dict(r) for r in self.rows], **{"window_id": window_id, "read_at": read_at, **self.row_read})
        edits = sum(1 for r in self.reads if r[2] == ("EditControl",))
        value = self.values[min(edits, len(self.values)) - 1]
        return _read([{**EDIT, "name": self.name, "value": value}], window_id=window_id, read_at=read_at)


def _dialog_client(tmp_path, scenes=("home", "dialog")):
    store = AssetStore(tmp_path / "store")
    ident = {"role": "identity", "threshold": 0.9, "match": "image"}
    store.publish_scene({"scene_id": "home", "title": "home", "profile": PROFILE, **FACTS,
                         "reference_image_id": store.capture(_png())["image_id"],
                         "shapes": [{"shape_id": "mark", "bbox": [40, 30, 16, 16], **ident}]}, 0)
    store.publish_scene({"scene_id": "dialog", "title": "dialog", "profile": PROFILE,
                         "reference_image_id": store.capture(_png(DW, DH))["image_id"],
                         "client_version": FACTS["client_version"], "width": DW, "height": DH, "dpi": 96,
                         "window_role": "file-dialog", "window_title": FILE_DIALOG_TITLE,
                         "shapes": [{"shape_id": "id_a", "bbox": [21, 20, 16, 16], **ident},
                                    {"shape_id": "id_b", "bbox": [103, 20, 16, 16], **ident}]}, 0)
    seams = DialogSeams()
    account = NativeAccountHandle(Leases(), "t", external=True, action_gap=0.0, clock=lambda: 0.0,
                                  sleep=lambda s: None)
    client = AssetNetdiskClient(ledger=TransferLedger(tmp_path / "ledger.json"), store=store, profile=PROFILE,
                                scene_ids=list(scenes), account=account, driver=seams, sleep=lambda s: None,
                                poll_attempts=2)
    source = tmp_path / LONG
    source.write_bytes(b"x")
    seams.values, seams.rows = ["", _dir(source), LONG], [*DECOYS, dict(ROW)]
    return client, seams, source


def _dir(source):
    return str(source.resolve().parent) + "\\"


def _no_secrets(facts, source):
    text = repr(facts)
    return str(source) not in text and LONG not in text and "机密" not in text and "x" * 40 not in text


MERGED_LABEL = {**LABEL, "text": "文件名(N):C:\\机密\\a.bin", "w": 135}


LIST_BOUNDS = [0.0, 0.0, float(DW), BAND[1]]


def _selection_inputs(source):
    edit = {"action": "click", "x": (60 + 40) / DW, "y": (57 + 9) / DH, "button": "left"}
    return [edit, {"action": "key", "key": "select_all"}, {"action": "type", "text": _dir(source)},
            {"action": "key", "key": "enter"},
            {"action": "click", "x": (8 + 75) / DW, "y": (28 + 8) / DH, "button": "left"}]  # the native ROW


@pytest.mark.parametrize("final", ["{name}", '"{name}"', "{full}", '"{full}"'])
@pytest.mark.parametrize("lines", [[ELLIPSIZED, LABEL, TYPE_ROW, CONFIRM], [TYPE_ROW, CONFIRM],
                                   [ELLIPSIZED, MERGED_LABEL, TYPE_ROW, CONFIRM]],
                         ids=["label", "label_missing", "label_merged_with_path"])
def test_long_ellipsized_name_selected_only_by_its_full_native_row(tmp_path, lines, final):
    client, seams, source = _dialog_client(tmp_path)
    seams.lines = lines
    seams.values[2] = final.format(name=LONG, full=source.resolve())
    screen = client._select_in_dialog(DID, source)
    assert seams.inputs == _selection_inputs(source)  # one row click; no double click, no confirm
    assert [(r[1], r[2]) for r in seams.reads] == [(BAND, ("EditControl",)), (BAND, ("EditControl",)),
                                                   (LIST_BOUNDS, ("ListItemControl",)), (BAND, ("EditControl",))]
    assert screen.frame["window_id"] == DID and screen.frame["token"] == f"f{seams.frames}"  # latest genuine frame
    assert screen.unique("存入百度网盘", "confirm_not_found") == CONFIRM


@pytest.mark.parametrize("after", ["", "C:\\x\\" + LONG, LONG[:40] + "…", '"{0}" "{0}"', "{0}.bak", LONG + " ",
                                   LONG.upper(), "{dir}"])
def test_final_readback_mismatch_refuses_after_the_single_row_click(tmp_path, after):
    client, seams, source = _dialog_client(tmp_path)
    seams.values[2] = after.format(LONG, dir=_dir(source))
    with pytest.raises(StepFailed, match="file_name_value_mismatch") as info:
        client._select_in_dialog(DID, source)
    assert seams.inputs == _selection_inputs(source)
    assert {"capture_id", "capture_sha256", "observation_id"} <= set(info.value.facts)
    assert _no_secrets(info.value.facts, source)


@pytest.mark.parametrize("typed", ["", "{dir}x", "C:\\other\\", '"{dir}" "{dir}"'])
def test_directory_readback_mismatch_refuses_before_enter(tmp_path, typed):
    client, seams, source = _dialog_client(tmp_path)
    seams.values[1] = typed.format(dir=_dir(source))
    with pytest.raises(StepFailed, match="file_name_value_mismatch") as info:
        client._select_in_dialog(DID, source)
    assert seams.inputs == _selection_inputs(source)[:3] and _no_secrets(info.value.facts, source)


@pytest.mark.parametrize("rows, override, reason", [
    ([], {}, "row_not_unique"), (DECOYS, {}, "row_not_unique"), ([ROW, {**ROW, "bbox": [8, 12, 150, 16]}], {}, "row_not_unique"),
    ([{**ROW, "name": LONG[:16] + "…"}], {}, "row_not_unique"), ([{**ROW, "name": " " + LONG}], {}, "row_not_unique"),
    ([{**ROW, "type": "HyperlinkControl"}], {}, "row_not_unique"), ([{**ROW, "bbox": [8, 60, 150, 16]}], {}, "row_not_unique"),
    ([ROW], {"truncated": True}, "truncated_read"), ([ROW], {"source": "ocr"}, "wrong_provenance"),
    ([ROW], {"window_id": WID}, "wrong_window"), ([ROW], {"width": DW + 1}, "frame_size_mismatch"),
    ([ROW], {"read_at": 0.0}, "stale_read")],
    ids=["none", "fuzzy_only", "ambiguous", "ellipsis", "padded", "link", "below_list", "truncated", "ocr",
         "other_window", "size", "stale"])
def test_row_ambiguity_provenance_truncation_or_mismatch_refuses_without_row_click(tmp_path, rows, override, reason):
    client, seams, source = _dialog_client(tmp_path)
    seams.rows, seams.row_read = [dict(r) for r in rows], override
    with pytest.raises(StepFailed, match="source_row_invalid") as info:
        client._select_in_dialog(DID, source)
    assert info.value.facts["reason"] == reason and _no_secrets(info.value.facts, source)
    assert {"capture_id", "capture_sha256", "observation_id"} <= set(info.value.facts)
    assert seams.inputs == _selection_inputs(source)[:4]  # navigated; no row click, nothing submitted


def test_unreadable_list_refuses_by_error_class_only(tmp_path):
    client, seams, source = _dialog_client(tmp_path)
    reads = seams.native_controls

    def native_controls(window_id, *, bounds_xywh, control_types):
        if tuple(control_types) == ("ListItemControl",):
            raise RuntimeError(f"UIA failed at {source}")
        return reads(window_id, bounds_xywh=bounds_xywh, control_types=control_types)
    seams.native_controls = native_controls
    with pytest.raises(StepFailed, match="source_row_unreadable") as info:
        client._select_in_dialog(DID, source)
    assert info.value.facts["error"] == "RuntimeError" and _no_secrets(info.value.facts, source)
    assert seams.inputs == _selection_inputs(source)[:4]


def test_dialog_closed_by_directory_enter_raises_the_base_code(tmp_path):
    client, seams, source = _dialog_client(tmp_path)
    seams.close_on_enter = True
    with pytest.raises(StepFailed, match="dialog_closed_after_directory_enter"):
        client._select_in_dialog(DID, source)
    assert seams.inputs == _selection_inputs(source)[:4] and len(seams.reads) == 2


def test_linked_source_refused_before_any_input(tmp_path, monkeypatch):
    client, seams, source = _dialog_client(tmp_path)
    monkeypatch.setattr(type(source), "is_symlink", lambda self: self == source)  # no symlink privilege needed
    with pytest.raises(StepFailed, match="source_is_link"):
        client._select_in_dialog(DID, source)
    assert seams.inputs == [] and seams.reads == [] and seams.frames == 0


def _upload_client(tmp_path, monkeypatch):
    """Dialog client whose pre-dialog main-window navigation is stubbed; the base ``upload_file`` is real."""
    from backend.core.baidu_netdisk.client import Screen, TransferResult
    client, seams, source = _dialog_client(tmp_path)
    main = Screen({"window_id": WID, "width": W, "height": H}, [{"text": "上传", "x": 5, "y": 5, "w": 20, "h": 10}])
    monkeypatch.setattr(client, "open_remote_dir", lambda remote, create_missing=False: (WID, main))
    monkeypatch.setattr(client, "_find_in_list", lambda wid, screen, name: (screen, None))
    monkeypatch.setattr(client, "_header", lambda screen: {"y": 50})
    monkeypatch.setattr(client, "click_box", lambda screen, wid, box: screen)
    monkeypatch.setattr(client, "_wait_dialog", lambda: DID)
    verified = []
    monkeypatch.setattr(client, "_verify_upload", lambda key, wid, src: verified.append(
        client.ledger.get(key)["state"]) or TransferResult("verified", "done", key))
    return client, seams, source, verified


def test_upload_submits_once_after_row_selection_ledger_then_confirm(tmp_path, monkeypatch):
    client, seams, source, verified = _upload_client(tmp_path, monkeypatch)
    result = client.upload_file(source, "/备份")
    confirm = {"action": "click", "x": (150 + 18) / DW, "y": (58 + 8) / DH, "button": "left"}
    assert result.status == "verified" and verified == ["submitted"]
    assert seams.inputs == [*_selection_inputs(source), confirm]  # the base's single confirm click, nothing else


def test_upload_dialog_closed_after_navigation_blocks_the_key_without_submit(tmp_path, monkeypatch):
    client, seams, source, verified = _upload_client(tmp_path, monkeypatch)
    seams.close_on_enter = True
    result = client.upload_file(source, "/备份")
    assert (result.status, result.next) == ("state_unknown", "check_remote_and_transfer_page_do_not_resubmit")
    record = client.ledger.get(result.key)
    assert record["state"] == "submitted" and record["facts"]["unintended"] == "dialog_closed_after_directory_enter"
    assert seams.inputs == _selection_inputs(source)[:4] and verified == []
    again = client.upload_file(source, "/备份")  # the same key stays blocked; nothing is replayed
    assert again.status == "already_submitted" and len(seams.inputs) == 4


def test_list_row_and_directory_text_are_exact():
    from backend.core.baidu_netdisk.file_selection import directory_text, list_bounds, list_row
    assert directory_text("C:\\data\\" + LONG) == "C:\\data\\" and directory_text("C:\\a.txt") == "C:\\"
    assert directory_text("\\\\host\\share\\a b.txt") == "\\\\host\\share\\"
    with pytest.raises(StepFailed, match="source_path_not_typable"):
        directory_text("C:\\da\nta\\a.txt")
    bounds = list_bounds(BAND, DW)
    assert bounds == LIST_BOUNDS
    with pytest.raises(StepFailed, match="file_list_band_invalid"):
        list_bounds([0, 4, DW, 20], DW)
    row = list_row(_read([*DECOYS, ROW]), window_id=DID, width=DW, height=DH, not_before=9.0, bounds=bounds, name=LONG)
    assert row["bbox"] == ROW["bbox"]
    with pytest.raises(StepFailed, match="source_row_invalid") as info:
        list_row(_read([*DECOYS, ROW, ROW]), window_id=DID, width=DW, height=DH, not_before=9.0, bounds=bounds,
                 name=LONG)
    assert info.value.facts == {"reason": "row_not_unique", "count": 2, "rows": 4}


@pytest.mark.parametrize("name", ["", "文件类型(T):", "地址: C:\\机密"])
def test_unrelated_native_control_name_refuses_before_any_input(tmp_path, name):
    client, seams, source = _dialog_client(tmp_path)
    seams.lines, seams.name = [TYPE_ROW, CONFIRM], name  # OCR label missing does not lend the edit a name
    with pytest.raises(StepFailed, match="file_name_control_invalid") as info:
        client._select_in_dialog(DID, source)
    assert info.value.facts["reason"] == "control_name_mismatch" and seams.inputs == []
    assert _no_secrets(info.value.facts, source)


def test_stale_first_read_refuses_before_any_input(tmp_path):
    client, seams, source = _dialog_client(tmp_path)
    seams.read_at = 0.0  # read older than the frame
    with pytest.raises(StepFailed, match="file_name_control_invalid") as info:
        client._select_in_dialog(DID, source)
    assert info.value.facts["reason"] == "stale_read" and seams.inputs == [] and _no_secrets(info.value.facts, source)


def test_dialog_must_be_the_declared_file_dialog_role_window(tmp_path):
    client, seams, source = _dialog_client(tmp_path, scenes=("home",))
    with pytest.raises(StepFailed, match="window_role_unknown"):
        client._select_in_dialog(DID, source)
    client, seams, source = _dialog_client(tmp_path / "b")
    with pytest.raises(StepFailed, match="file_dialog_window_mismatch"):
        client._select_in_dialog("1:2:3.0", source)
    seams.geometry[DID] = client_facts(DW, DH + 1)  # live facts no longer equal the declared scene
    with pytest.raises(StepFailed, match="role_window_not_unique"):
        client._select_in_dialog(DID, source)
    assert seams.inputs == [] and seams.reads == []


def test_unrecognised_or_ambiguous_dialog_frame_refuses_before_input(tmp_path, monkeypatch):
    client, seams, source = _dialog_client(tmp_path)
    seams.ocr = lambda frame: {"lines": [dict(LABEL), dict(TYPE_ROW), dict(CONFIRM), dict(CONFIRM)], "tokens": []}
    with pytest.raises(StepFailed, match="file_dialog_anchors_not_unique") as info:
        client._select_in_dialog(DID, source)
    assert info.value.facts["confirms"] == 2 and _no_secrets(info.value.facts, source)
    buf = io.BytesIO()
    Image.new("RGB", (DW, DH), (255, 255, 255)).save(buf, format="PNG")  # identity anchors absent
    seams.dialog_data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    with pytest.raises(StepFailed, match="file_dialog_not_recognized"):
        client._select_in_dialog(DID, source)
    assert seams.inputs == [] and seams.reads == []


def test_directory_source_keeps_base_selection(tmp_path, monkeypatch):
    from backend.core.baidu_netdisk.client import NetdiskClient
    client, seams, source = _dialog_client(tmp_path)
    calls = []
    monkeypatch.setattr(NetdiskClient, "_select_in_dialog", lambda self, dialog, src: calls.append((dialog, src)))
    assert client._select_in_dialog(DID, tmp_path) is None and calls == [(DID, tmp_path)]
    assert seams.inputs == [] and seams.reads == [] and seams.frames == 0
    with pytest.raises(StepFailed, match="source_not_file"):
        client._select_in_dialog(DID, tmp_path / "missing.bin")
    assert len(calls) == 1
