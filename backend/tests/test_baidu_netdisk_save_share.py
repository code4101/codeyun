"""NetdiskClient.save_share against a scripted share tab modelled on the observed native ordering (no GUI input).

Observed (sanitized): the share bottom bar shows a remembered destination label and a folder icon
next to 保存到网盘; the icon opens an in-page 保存到 picker that starts at the root; a single click
on a folder name opens it and the crumb updates asynchronously; 确定 only chooses the destination;
保存到网盘 copies immediately into whatever the label shows and ends in 保存成功 / 查看.
One-root shares open with 已选择1个文件 already.
"""

from __future__ import annotations

import json

import pytest

from backend.core.baidu_netdisk import TransferLedger
from backend.core.baidu_netdisk.client import (NetdiskClient, Screen, StepFailed, destination_icon, destination_path,
                                               parse_share_url, save_picker_path)

W, H = 1200, 800
SHARE = "https://pan.baidu.com/s/1AbCdEfGh"
PWD = "x7k9"
SAVE = (700, 600, 80)  # 保存到网盘 text box: centre x 740
ICON = (740 - 102, 610)  # where the folder icon sits in this fake (next to Save, not to the label)


def line(text, x, y, w=80, h=20):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


class FakeShare:
    """Share tab + bottom destination bar + picker over a remote tree.  Only 保存到网盘 copies."""

    def __init__(self, roots=("a.zip", "资料"), *, dest="/", label_prefix="保存到:", preselected=0,
                 icon_opens=True, picker_window=False, dirs=None, lag=0, label_updates=True,
                 picker_closes=True, keep_selection=True, save_mode="success", extra_hidden=False):
        self.roots = list(roots)
        self.selected: set[str] = set(self.roots[:preselected])
        self.dest = dest  # remembered across share tabs
        self.label_prefix = label_prefix
        self.icon_opens = icon_opens
        self.picker_window = picker_window
        self.dirs = dirs or {"/": ["data"], "/data": ["m"], "/data/m": []}
        self.lag = lag  # observations before a picker crumb change becomes visible
        self.label_updates = label_updates
        self.picker_closes = picker_closes
        self.keep_selection = keep_selection
        self.save_mode = save_mode
        self.extra_hidden = extra_hidden
        self.page = "file"
        self.picker = None  # {"cwd", "shown", "pending"}
        self.dialog = None
        self.schemes: list[str] = []
        self.inputs: list[dict] = []
        self.events: list[tuple] = []  # ("icon",), ("confirm", cwd), ("save", dest), ("view",)

    # -- driver surface ---------------------------------------------------------------
    def list_windows(self):
        rows = [{"id": "m", "application": "BaiduNetdiskUnite.exe", "title": "百度网盘", "width": W, "height": H}]
        if self.picker is not None and self.picker_window:
            rows.append({"id": "p", "application": "BaiduNetdiskUnite.exe", "title": "保存到", "width": 500, "height": 450})
        return rows

    def activate(self, window_id):
        return {}

    def open_scheme(self, url):
        self.schemes.append(url)
        self.page = "share"

    def frame(self, window_id):
        return {"window_id": window_id, "width": W, "height": H, "input_token": "tok", "token": "f"}

    def ocr(self, frame):
        if self.picker is not None and self.picker["pending"]:
            self.picker["pending"][1] -= 1
            if self.picker["pending"][1] < 0:
                self.picker["shown"] = self.picker["pending"][0]
                self.picker["pending"] = None
        lines = []
        if frame["window_id"] == "m":
            lines += self.share_lines() if self.page == "share" else []
            lines += self.dialog_lines()
        if self.picker is not None and (frame["window_id"] == "p" or not self.picker_window):
            lines += self.picker_lines()  # in-page picker overlays the share tab
        return {"lines": lines, "tokens": []}

    def share_lines(self):
        shown = len(self.selected) + (1 if self.extra_hidden and self.selected else 0)
        crumb = "/".join(["我的网盘", *[p for p in self.dest.split("/") if p]]) + ("/" if self.dest == "/" else "")
        lines = [line(f"已选择{shown}个文件", 200, 300, 120), line("大小", 600, 300, 40),
                 line(self.label_prefix + crumb, 300, 600, 200), line("保存到网盘", *SAVE), line("下载", 800, 600, 40)]
        for i, name in enumerate(self.roots):
            lines += [line(name, 200, 340 + 40 * i, 120), line("1.2M" if "." in name else "-", 600, 340 + 40 * i, 40)]
        return lines

    def picker_lines(self):
        cwd = self.picker["shown"]
        crumb = "/".join(["全部文件", *[p for p in cwd.split("/") if p]])
        lines = [line(crumb, 50, 100, 300), line("新建文件夹", 60, 450, 80), line("取消", 200, 450, 40),
                 line("确定", 300, 450, 40)]
        for i, child in enumerate(self.dirs.get(cwd, [])):
            lines.append(line(child, 60, 150 + 40 * i, 100))
        return lines

    def dialog_lines(self):
        if self.dialog is None:
            return []
        return [line(self.dialog, 500, 200, 80), line("查看", 560, 250, 40)]

    def _hit(self, lines, x, y):
        for item in lines:
            if item["x"] <= x * W <= item["x"] + item["w"] and item["y"] <= y * H <= item["y"] + item["h"]:
                return item
        return None

    def input(self, frame, **action):
        self.inputs.append({"window": frame["window_id"], **action})
        x, y, kind = action.get("x"), action.get("y"), action.get("action")
        if self.picker is not None and (frame["window_id"] == "p" or not self.picker_window):
            return self._picker_input(x, y, kind)
        if self.dialog is not None:
            if (self._hit(self.dialog_lines(), x, y) or {}).get("text") == "查看":
                self.events.append(("view",))
                self.dialog, self.page = None, "file"
            return {}
        if self.page != "share" or kind != "click":
            return {}
        px, py = x * W, y * H
        if abs(px - ICON[0]) < 10 and abs(py - ICON[1]) < 10:
            self.events.append(("icon",))
            if self.icon_opens:
                self.picker = {"cwd": "/", "shown": "/", "pending": None}  # observed: picker opens at root
            return {}
        hit = self._hit(self.share_lines(), x, y)
        if hit and hit["text"] == "保存到网盘":
            return self._save()
        if abs(py - 310) < 12:  # header checkbox: partial -> all, all -> none, none -> all
            self.selected = set() if len(self.selected) == len(self.roots) else set(self.roots)
            return {}
        for i, name in enumerate(self.roots):
            if abs(py - (350 + 40 * i)) < 12:
                self.selected ^= {name}
        return {}

    def _picker_input(self, x, y, kind):
        hit = self._hit(self.picker_lines(), x, y)
        text = hit and hit["text"]
        cwd = self.picker["cwd"]
        if text == "确定":
            self.events.append(("confirm", cwd))  # destination choice only: never copies
            if self.label_updates:
                self.dest = cwd
            if not self.keep_selection:
                self.selected = set()
            if self.picker_closes:
                self.picker = None
        elif text == "取消":
            self.picker = None
        elif text and text.startswith("全部文件"):
            self._navigate("/")
        elif text in self.dirs.get(self.picker["shown"], []):
            child = f"{cwd.rstrip('/')}/{text}"
            if kind == "double_click":  # observed: second click lands on a row of the new folder
                child = f"{child}/{self.dirs.get(child, [text])[0]}"
            self._navigate(child)
        return {}

    def _navigate(self, cwd):
        self.picker["cwd"] = cwd
        self.picker["pending"] = [cwd, self.lag]

    def _save(self):
        self.events.append(("save", self.dest))
        if self.save_mode == "raise":
            raise RuntimeError("input channel broke after press")
        if self.save_mode == "success":
            self.dirs.setdefault(self.dest, []).extend(sorted(self.selected))
            self.dialog = "保存成功"
        elif self.save_mode == "failure":
            self.dialog = "保存失败"
        return {}

    # -- assertions helpers ---------------------------------------------------------------
    def saves(self):
        return [e for e in self.events if e[0] == "save"]


class Client(NetdiskClient):
    """Target navigation/listing are the existing tested provider steps; here they read the fake tree."""

    def __init__(self, fake, ledger, *, complete=True, **kw):
        super().__init__(ledger=ledger, lease_check=lambda: None, driver=fake, settle=0, sleep=lambda s: None,
                         poll_attempts=4, **kw)
        self.fake = fake
        self.complete = complete
        self.listings = 0

    def open_remote_dir(self, remote_dir, *, create_missing=False):
        self.created_dirs = []
        parent = "/"
        for part in [p for p in remote_dir.split("/") if p]:
            path = parent.rstrip("/") + "/" + part
            if part not in self.fake.dirs.setdefault(parent, []):
                if not create_missing:
                    raise RuntimeError("remote_dir_not_found")
                self.fake.dirs[parent].append(part)
                self.created_dirs.append(path)
            self.fake.dirs.setdefault(path, [])
            parent = path
        self._target = remote_dir
        return "m", Screen({"window_id": "m", "width": W, "height": H, "input_token": "t", "token": "f"}, [])

    def _list_dir_entries(self, main, screen):
        self.listings += 1
        names = self.fake.dirs.get(self._target, [])
        rows = [{"name": n, "kind": "file" if "." in n else "dir", "size": "1.2M" if "." in n else "-"} for n in names]
        return screen, rows, self.complete


@pytest.fixture
def ledger(tmp_path):
    return TransferLedger(tmp_path / "ledger.json")


def run(fake, ledger, target="/data/m/t", **kw):
    client = Client(fake, ledger)
    return client, client.save_share(SHARE, target, pwd=PWD, **kw)


# ---- ordering: destination chosen and proven before the one 保存到网盘 press ----------------

def assert_saved_only_into(fake, target):
    """Every save happened with the label on ``target`` and after the destination choice."""
    assert fake.saves() == [("save", target)]
    i = fake.events.index(("save", target))
    assert all(e[0] != "save" for e in fake.events[:i])
    assert fake.events[i + 1:] in ([], [("view",)])


def test_remembered_wrong_destination_is_replaced_before_save(tmp_path, ledger):
    fake = FakeShare(dest="/old", dirs={"/": ["data", "old"], "/data": ["m"], "/data/m": [], "/old": []})
    client, result = run(fake, ledger)
    assert result.status == "remote_present", result.to_dict()
    assert_saved_only_into(fake, "/data/m/t")
    assert fake.dirs["/old"] == []
    assert fake.events[:2] == [("icon",), ("confirm", "/data/m/t")]
    assert result.facts["destination_before"] == ["old"]
    assert not [i for i in fake.inputs if i.get("action") == "double_click"]  # one single click per folder


def test_all_root_items_saved_into_created_target(tmp_path, ledger):
    fake = FakeShare()
    _, result = run(fake, ledger, "/data/m/GA/x/sd-1")
    assert result.status == "remote_present", result.to_dict()
    assert_saved_only_into(fake, "/data/m/GA/x/sd-1")
    assert result.facts["root_items"] == 2 and result.facts["selection"] == "all_root_items"
    assert {p["name"] for p in result.facts["present"]} == {"a.zip", "资料"}
    assert result.facts["content_verified"] is False and result.facts["client_reported"] == "success"
    assert result.facts["created_dirs"] == ["/data/m/GA", "/data/m/GA/x", "/data/m/GA/x/sd-1"]


def test_label_already_on_target_skips_picker(tmp_path, ledger):
    fake = FakeShare(dest="/data/m/t", dirs={"/": ["data"], "/data": ["m"], "/data/m": ["t"], "/data/m/t": []})
    _, result = run(fake, ledger)
    assert result.status == "remote_present"
    assert fake.events[0] == ("save", "/data/m/t") and ("icon",) not in fake.events


def test_async_breadcrumb_is_polled_before_next_click(tmp_path, ledger):
    fake = FakeShare(lag=2)
    _, result = run(fake, ledger)
    assert result.status == "remote_present", result.to_dict()
    assert_saved_only_into(fake, "/data/m/t")


def test_crumb_never_settling_fails_before_any_save(tmp_path, ledger):
    fake = FakeShare(lag=50)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_folder_not_verified"
    assert fake.saves() == [] and result.facts["cancel"] == "verified"
    assert ledger.get(result.key)["state"] == "failed"


@pytest.mark.parametrize("picker_window", [False, True])
@pytest.mark.parametrize("prefix", ["保存到:", ""])
def test_in_page_or_window_picker_with_or_without_label_prefix(tmp_path, ledger, picker_window, prefix):
    fake = FakeShare(picker_window=picker_window, label_prefix=prefix)
    _, result = run(fake, ledger)
    assert result.status == "remote_present", result.to_dict()
    assert_saved_only_into(fake, "/data/m/t")


def test_confirm_is_not_a_copy_label_must_follow(tmp_path, ledger):
    fake = FakeShare(label_updates=False)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "destination_label_mismatch"
    assert ("confirm", "/data/m/t") in fake.events and fake.saves() == []
    assert fake.dirs["/data/m/t"] == [] and ledger.get(result.key)["state"] == "failed"


def test_picker_not_closing_after_confirm_never_saves(tmp_path, ledger):
    fake = FakeShare(picker_closes=False)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_not_closed_after_confirm"
    assert fake.saves() == []


def test_selection_lost_during_picker_never_saves(tmp_path, ledger):
    fake = FakeShare(keep_selection=False)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_selection_changed_after_destination"
    assert fake.saves() == []


def test_icon_not_opening_picker_is_retryable_not_submitted(tmp_path, ledger):
    fake = FakeShare(icon_opens=False)
    client, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_not_recognized"
    assert fake.saves() == [] and ledger.get(result.key)["state"] == "failed"
    fake.icon_opens = True
    again = client.save_share(SHARE, "/data/m/t", pwd=PWD)
    assert again.status == "remote_present" and fake.saves() == [("save", "/data/m/t")]


def test_picker_target_missing_cancels_and_never_saves(tmp_path, ledger):
    fake = FakeShare()
    client = Client(fake, ledger)
    real_open = client.open_remote_dir

    def open_then_hide(remote_dir, *, create_missing=False):
        out = real_open(remote_dir, create_missing=create_missing)
        fake.dirs["/data/m"] = []  # picker no longer shows the folder
        return out
    client.open_remote_dir = open_then_hide
    result = client.save_share(SHARE, "/data/m/t", pwd=PWD)
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_folder_not_found"
    assert result.facts["cancel"] == "verified" and fake.picker is None and fake.saves() == []


def test_stray_save_dialog_after_icon_click_blocks_retry(tmp_path, ledger):
    fake = FakeShare(icon_opens=False)
    real = fake.input

    def icon_hits_save(frame, **action):
        out = real(frame, **action)
        if fake.events and fake.events[-1] == ("icon",):
            fake.dialog = "保存成功"  # as if the click had landed on the Save control
        return out
    fake.input = icon_hits_save
    client, result = run(fake, ledger)
    assert result.status == "state_unknown" and result.facts["reason"] == "save_dialog_before_save_click"
    clicks = len(fake.inputs)
    assert client.save_share(SHARE, "/data/m/t", pwd=PWD).status == "already_recorded"
    assert len(fake.inputs) == clicks and len(fake.schemes) == 1


# ---- native preselection (one-root shares open with 已选择1个文件) ------------------------

@pytest.mark.parametrize("roots,pre,items", [(("only.zip",), 1, None), (("only.zip",), 1, ["only.zip"]),
                                             (("a.zip", "b.zip", "资料"), 1, ["b.zip"]),
                                             (("a.zip", "b.zip"), 2, ["a.zip"])])
def test_preselection_is_cleared_then_exact_set_selected(tmp_path, ledger, roots, pre, items):
    fake = FakeShare(roots=roots, preselected=pre)
    _, result = run(fake, ledger, items=items)
    assert result.status == "remote_present", result.to_dict()
    want = sorted(items or roots)
    assert sorted(fake.dirs["/data/m/t"]) == want and result.facts["preselected"] == pre
    assert_saved_only_into(fake, "/data/m/t")


def test_preselection_that_cannot_be_cleared_never_saves(tmp_path, ledger):
    fake = FakeShare(roots=("only.zip",), preselected=1)
    real = fake.input

    def sticky(frame, **action):
        out = real(frame, **action)
        fake.selected = {"only.zip"}  # header click has no effect
        return out
    fake.input = sticky
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_selection_not_cleared"
    assert fake.saves() == [] and ("icon",) not in fake.events


# ---- folder icon anchored on the Save control, never on the label's text edge -------------

def bar(label_w, save_x=700):
    return Screen({"width": W, "height": H}, [line("已选择1个文件", 200, 300, 120), line("大小", 600, 300, 40),
                                              line("我的网盘/", 300, 600, label_w), line("保存到网盘", save_x, 600, 80)])


def test_icon_point_follows_save_not_label_length():
    short, long_ = destination_icon(bar(40)), destination_icon(bar(300))
    assert short == long_ and short["x"] + 1 == 740 - 102 and destination_path(bar(40)) == []


@pytest.mark.parametrize("screen", [bar(380), bar(40, save_x=400)])
def test_icon_point_refused_when_bar_does_not_fit(screen):
    with pytest.raises(StepFailed) as exc:
        destination_icon(screen)
    assert exc.value.code == "destination_icon_out_of_bar"


def test_icon_point_on_text_refused():
    screen = bar(40)
    screen.lines.append(line("其他", 620, 600, 40))
    with pytest.raises(StepFailed) as exc:
        destination_icon(screen)
    assert exc.value.code == "destination_icon_on_text"


def test_destination_label_missing_never_clicks_bar(tmp_path, ledger):
    fake = FakeShare(label_prefix="目标:")
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "destination_label_not_found"
    assert fake.saves() == [] and ("icon",) not in fake.events


def test_elided_label_never_counts_as_target(tmp_path, ledger):
    fake = FakeShare()
    real = fake.share_lines

    def elided():
        out = real()
        if fake.dest == "/data/m/t":
            out[2] = line("保存到:我的网盘/.../t", 300, 600, 200)
        return out
    fake.share_lines = elided
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "destination_label_mismatch"
    assert fake.saves() == []


# ---- after the one press: unknown or failure never resubmits; verify always lists again -----

def test_save_click_error_is_unknown_and_blocks_retry(tmp_path, ledger):
    fake = FakeShare(save_mode="raise")
    client, result = run(fake, ledger)
    assert result.status == "state_unknown" and result.next.endswith("do_not_resubmit")
    assert ledger.get(result.key)["state"] == "submitted"
    clicks = len(fake.inputs)
    again = client.save_share(SHARE, "/data/m/t", pwd=PWD)
    assert again.status == "already_recorded" and len(fake.inputs) == clicks and len(fake.saves()) == 1


def test_client_reported_failure_is_recorded_not_retried(tmp_path, ledger):
    fake = FakeShare(save_mode="failure")
    client, result = run(fake, ledger)
    assert result.status == "submitted" and result.next.endswith("do_not_resubmit")
    assert result.facts["client_reported"] == "failure" and result.facts["missing"] == ["a.zip", "资料"]
    assert client.save_share(SHARE, "/data/m/t", pwd=PWD).status == "already_recorded"
    assert len(fake.saves()) == 1 and len(fake.schemes) == 1


def test_silent_save_stays_submitted_and_verify_relists_without_input(tmp_path, ledger):
    fake = FakeShare(save_mode="silent")
    client, result = run(fake, ledger)
    assert result.status == "submitted" and ledger.get(result.key)["state"] == "submitted"
    clicks, listed = len(fake.inputs), client.listings
    assert client.verify_save_share(result.key).status == "submitted"
    assert client.listings == listed + 1 and len(fake.inputs) == clicks
    fake.dirs["/data/m/t"] = ["a.zip"]
    partial = client.verify_save_share(result.key)
    assert partial.status == "partial_present" and client.listings == listed + 2 and len(fake.saves()) == 1


def test_verify_after_verified_lists_again_and_reports_deletion(tmp_path, ledger):
    fake = FakeShare()
    client, result = run(fake, ledger)
    assert result.status == "remote_present"
    listed = client.listings
    fake.dirs["/data/m/t"] = ["a.zip"]
    partial = client.verify_save_share(result.key)
    assert partial.status == "partial_present" and partial.facts["previously_verified"] is True
    fake.dirs["/data/m/t"] = []
    gone = client.verify_save_share(result.key)
    assert gone.status == "missing_after_verified" and client.listings == listed + 2
    assert ledger.get(result.key)["facts"]["last_check"] == "missing" and len(fake.saves()) == 1


def test_verify_after_verified_with_incomplete_listing_is_unknown(tmp_path, ledger):
    fake = FakeShare()
    client, result = run(fake, ledger)
    fake.dirs["/data/m/t"] = []
    client.complete = False
    assert client.verify_save_share(result.key).status == "state_unknown"


# ---- preserved validation, privacy and selection checks ------------------------------------

def test_secret_never_in_ledger_or_result(tmp_path, ledger):
    result = Client(FakeShare(), ledger).save_share(SHARE + f"?pwd={PWD}", "/data/m/t")
    raw = (tmp_path / "ledger.json").read_text(encoding="utf-8")
    dumped = json.dumps(result.to_dict(), ensure_ascii=False)
    for secret in (PWD, "1AbCdEfGh"):
        assert secret not in raw and secret not in dumped


def assert_no_share_secret(tmp_path, result):
    raw = (tmp_path / "ledger.json").read_text(encoding="utf-8")
    dumped = json.dumps(result.to_dict(), ensure_ascii=False)
    for secret in (PWD, PWD.upper(), "AbCdEfGh", "abcdefgh"):
        assert secret not in raw and secret not in dumped
    if "link" in result.facts:  # the link hash is an identifier, never redacted
        assert result.facts["link"] == ledger_link(tmp_path, result.key)


def ledger_link(tmp_path, key):
    return json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))[key]["inputs"]["link"]


def echo_share_link(fake, text):
    """OCR also reads the current share link from the client's navigation bar."""
    real = fake.share_lines
    fake.share_lines = lambda: real() + [line(text, 50, 50, 600)]


@pytest.mark.parametrize("echo", [SHARE + "?pwd=" + PWD, "pan.baidu.com/share/init?surl=abcdefgh&pwd=" + PWD.upper()])
def test_error_diagnostics_never_echo_current_link_or_code(tmp_path, ledger, echo):
    fake = FakeShare(label_prefix="目标:")
    echo_share_link(fake, echo)
    client = Client(fake, ledger)
    result = client.save_share(SHARE + "?pwd=" + PWD, "/data/m/t")
    assert result.status == "not_submitted" and result.facts["reason"] == "destination_label_not_found"
    assert "[redacted]" in json.dumps(result.facts["texts"], ensure_ascii=False) and "保存到网盘" in result.facts["texts"]
    assert_no_share_secret(tmp_path, result)
    assert fake.saves() == [] and len(fake.schemes) == 1
    retry = client.save_share(SHARE + "?pwd=" + PWD, "/data/m/t")  # failed before submit stays retryable
    assert retry.status == "not_submitted" and len(fake.schemes) == 2


def test_unknown_diagnostics_never_echo_current_link_or_code(tmp_path, ledger):
    fake = FakeShare(icon_opens=False)
    echo_share_link(fake, SHARE + "?pwd=" + PWD)
    real = fake.input

    def icon_hits_save(frame, **action):
        out = real(frame, **action)
        if fake.events and fake.events[-1] == ("icon",):
            fake.dialog = "保存成功"
        return out
    fake.input = icon_hits_save
    client = Client(fake, ledger)
    result = client.save_share(SHARE, "/data/m/t", pwd=PWD)
    assert result.status == "state_unknown" and result.facts["reason"] == "save_dialog_before_save_click"
    assert result.next == "inspect_client_do_not_resubmit"
    assert_no_share_secret(tmp_path, result)
    clicks = len(fake.inputs)
    assert client.save_share(SHARE, "/data/m/t", pwd=PWD).status == "already_recorded" and len(fake.inputs) == clicks


def test_save_click_error_message_is_redacted_and_blocks_retry(tmp_path, ledger):
    fake = FakeShare(save_mode="raise")

    def broken():
        fake.events.append(("save", fake.dest))
        raise RuntimeError(f"input failed on {SHARE}?pwd={PWD}")
    fake._save = broken
    client = Client(fake, ledger)
    result = client.save_share(SHARE, "/data/m/t", pwd=PWD)
    assert result.status == "state_unknown" and result.next == "verify_save_share_do_not_resubmit"
    assert "[redacted]" in result.facts["error"]
    assert_no_share_secret(tmp_path, result)
    assert client.save_share(SHARE, "/data/m/t", pwd=PWD).status == "already_recorded" and len(fake.saves()) == 1


def test_explicit_items_select_exactly_those(tmp_path, ledger):
    fake = FakeShare(roots=("a.zip", "b.zip", "资料"))
    _, result = run(fake, ledger, items=["b.zip", "资料"])
    assert result.status == "remote_present" and fake.dirs["/data/m/t"] == ["b.zip", "资料"]


@pytest.mark.parametrize("url", ["https://pan.baidu.com.evil.cn/s/1AbCdEfGh", "http://pan.baidu.com/s/1AbCdEfGh",
                                 "https://yun.baidu.com/s/1AbCdEfGh"])
def test_foreign_share_domain_rejected_before_ledger(tmp_path, ledger, url):
    fake = FakeShare()
    with pytest.raises(ValueError):
        Client(fake, ledger).save_share(url, "/data/m/t", pwd=PWD)
    assert fake.schemes == [] and fake.inputs == [] and not (tmp_path / "ledger.json").exists()


@pytest.mark.parametrize("target,items", [("relative/dir", None), ("/a/../b", None), ("/", None),
                                          ("/data/m/t", []), ("/data/m/t", ["a.zip", "a.zip"]), ("/data/m/t", ["x/y"])])
def test_bad_target_or_items_rejected_before_ledger(tmp_path, ledger, target, items):
    fake = FakeShare()
    with pytest.raises(ValueError):
        Client(fake, ledger).save_share(SHARE, target, pwd=PWD, items=items)
    assert fake.inputs == [] and not (tmp_path / "ledger.json").exists()


@pytest.mark.parametrize("pre", [0, 1])
def test_existing_same_name_is_observed_existing_never_submitted(tmp_path, ledger, pre):
    fake = FakeShare(preselected=pre, dirs={"/": ["data"], "/data": ["m"], "/data/m": ["t"], "/data/m/t": ["a.zip"]})
    _, result = run(fake, ledger)
    assert result.status == "observed_existing" and result.facts["content_verified"] is False
    assert fake.inputs == [] and fake.saves() == []


def test_incomplete_target_listing_does_not_open_share(tmp_path, ledger):
    fake = FakeShare()
    result = Client(fake, ledger, complete=False).save_share(SHARE, "/data/m/t", pwd=PWD)
    assert result.status == "not_submitted" and result.facts["reason"] == "target_listing_incomplete"
    assert fake.schemes == []


def test_missing_explicit_item_clicks_nothing(tmp_path, ledger):
    fake = FakeShare()
    _, result = run(fake, ledger, items=["nope.zip"])
    assert result.facts["reason"] == "share_item_not_found" and fake.inputs == []


def test_select_all_with_rows_outside_frame_refuses(tmp_path, ledger):
    fake = FakeShare(extra_hidden=True)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_select_all_unverified"
    assert fake.picker is None and fake.saves() == []


def test_save_picker_path_parses_merged_crumb_and_ignores_bottom_bar():
    screen = Screen({"width": W, "height": H}, [line("全部文件/data/m", 50, 100, 300), line("确定", 300, 400)])
    assert save_picker_path(screen) == ["data", "m"]
    assert save_picker_path(Screen({"width": W, "height": H}, [line("确定", 1, 1)])) is None
    assert save_picker_path(bar(40)) is None  # 我的网盘/ on the Save row is the destination label


def test_cli_request_file_keeps_secrets_out_of_argv(tmp_path, monkeypatch, capsys):
    import scripts.baidu_netdisk_transfer as cli
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"url": SHARE, "pwd": PWD, "target_dir": "/data/m/t", "items": None}), encoding="utf-8")
    seen = {}

    class Stub:
        def __init__(self, **kw):
            pass

        def save_share(self, url, target, *, pwd, items):
            seen.update(url=url, target=target, pwd=pwd, items=items)
            from backend.core.baidu_netdisk.client import TransferResult
            return TransferResult("remote_present", "business_verify_content", "k", {"target_dir": target})
    monkeypatch.setattr("backend.core.baidu_netdisk.client.NetdiskClient", Stub)
    argv = ["save-share", "--request-file", str(req), "--lease-token", "T", "--ledger", str(tmp_path / "l.json")]
    monkeypatch.setattr(cli, "_lease", lambda args: _null_lease())
    cli.main(argv)
    out = capsys.readouterr().out
    assert seen == {"url": SHARE, "target": "/data/m/t", "pwd": PWD, "items": None}
    assert PWD not in out and "1AbCdEfGh" not in out and PWD not in " ".join(argv)


def _null_lease():
    from contextlib import contextmanager

    @contextmanager
    def cm():
        yield lambda: None
    return cm()


# ---- link forms and read-only picker diagnosis ------------------------------------------------

def test_share_init_route_canonicalises_to_s_form():
    assert parse_share_url("https://pan.baidu.com/share/init?surl=AbCdEfGh&pwd=x7k9") == (SHARE, PWD)
    assert parse_share_url("https://pan.baidu.com/share/init?surl=AbCdEfGh", PWD) == (SHARE, PWD)
    assert parse_share_url(SHARE + "?pwd=x7k9") == (SHARE, PWD)



def test_both_routes_share_one_ledger_identity(tmp_path, ledger):
    fake = FakeShare()
    client = Client(fake, ledger)
    first = client.save_share("https://pan.baidu.com/share/init?surl=AbCdEfGh&pwd=x7k9", "/data/m/t")
    again = client.save_share(SHARE, "/data/m/t", pwd=PWD)
    assert first.status == "remote_present" and again.status == "already_recorded" and again.key == first.key
    assert len(fake.schemes) == 1 and "AbCdEfGh" not in (tmp_path / "ledger.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("url", [
    "https://pan.baidu.com/share/init", "https://pan.baidu.com/share/init?surl=", "https://pan.baidu.com/share/init?surl=a/b",
    "https://pan.baidu.com/share/init?surl=Ab%3Cx%3E", "https://pan.baidu.com/share/init?surl=AbCd&surl=EfGh",
    "https://pan.baidu.com/share/init?surl=AbCdEfGh&next=https://evil.cn", "https://pan.baidu.com/share/other?surl=AbCdEfGh",
    "https://pan.baidu.com.evil.cn/share/init?surl=AbCdEfGh", "https://evil.cn/share/init?surl=AbCdEfGh",
    "https://u@pan.baidu.com/s/1AbCdEfGh", "https://pan.baidu.com:8443/s/1AbCdEfGh", "http://pan.baidu.com/share/init?surl=AbCdEfGh",
    "baiduyunguanjia://pan.baidu.com/s/1AbCdEfGh", "https://pan.baidu.com/s/1AbCdEfGh#x", "https://pan.baidu.com/s/1AbCdEfGh/../x",
])
def test_bad_share_links_rejected(url):
    with pytest.raises(ValueError):
        parse_share_url(url, PWD)


def test_observe_save_picker_is_read_only_and_reports_destination(tmp_path, ledger):
    fake = FakeShare(dest="/old")
    client = Client(fake, ledger)
    fake.page = "share"
    none = client.observe_save_picker()
    assert none["status"] == "picker_not_recognized" and none["submittable"] is False
    main = [s for s in none["surfaces"] if s["window"] == "m"][0]
    assert main["destination"] == ["old"] and main["destination_icon"]["x"] + 1 == ICON[0]
    fake.picker = {"cwd": "/data", "shown": "/data", "pending": None}
    seen = client.observe_save_picker()
    assert seen["status"] == "picker_recognized" and seen["submittable"] is True
    assert [s for s in seen["surfaces"] if s["recognized"]][0]["picker_path"] == ["data"]
    assert fake.inputs == []
