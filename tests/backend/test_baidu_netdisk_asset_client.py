"""Deterministic capability checks for AssetNetdiskClient (static synthetic frame, no GUI, no UI flow)."""

import base64
import io

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


def client_facts(w, h, visible=True, minimized=False):
    """Static public-geometry facts of a window's client area (list rows carry outer sizes only)."""
    return {"width": w, "height": h, "visible": visible, "minimized": minimized}


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
        self.geometry = {WID: client_facts(W, H), SID: client_facts(SW, SH)}  # absent: window gone

    def window_geometry(self, window_id):
        if window_id not in self.geometry:
            raise RuntimeError("window gone")
        return {"window_id": window_id, **self.geometry[window_id]}

    def list_windows(self):
        return [dict(w) for w in self.windows]

    def activate(self, window_id):
        return {}

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
    with pytest.raises(StepFailed, match="input_window_not_declared"):
        client.act(screen, WID, action="key", key="enter")
    assert seams.inputs == [] and seams.frames == 1
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
