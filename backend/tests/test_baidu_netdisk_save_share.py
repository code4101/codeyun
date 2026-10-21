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
from backend.core.baidu_netdisk import client as client_module
from backend.core.baidu_netdisk.client import (NetdiskClient, Screen, StepFailed, address_bar_field, destination_icon,
                                               destination_path, parse_share_url, picker_rows, save_picker, save_picker_path,
                                               share_checkbox_anchor,
                                               share_checkbox_state, share_extraction_state)

W, H = 1920, 1152  # measured native frame (8.1.5.103)
# Measured share checkbox column: header box x42..58 / y304..320 (centre 50,312) with the
# 已选择N个文件 text at x~98; root-row boxes on the same column, rows 47 px apart (row 1 centre y359).
HEADER_X, HEADER_CY, BOX_X, ROW_CY, ROW_STEP = 98, 312, 50, 359, 47
BLUE, GREY = (6, 145, 255), (200, 200, 200)
_PIXELS: dict = {}
SHARE = "https://pan.baidu.com/s/1AbCdEfGh"
PWD = "x7k9"
BAR_Y = 1073  # measured share bottom-bar row (below the picker modal)
SAVE = (700, BAR_Y, 80)  # 保存到网盘 text box: centre x 740
ICON = (740 - 102, BAR_Y + 10)
PRIOR = "https://pan.baidu.com/share/init?surl=ZzPrior00&pwd=q1w2"  # another share left in the field  # where the folder icon sits in this fake (next to Save, not to the label)
# Measured in-page 保存到 modal (1920x1152, sanitized): bounds x655..1265 y355..795; title near 680,383;
# root crumb near 665,428 with 已加载前20个 near 1163,428 on its row; bottom row 新建文件夹 near 732,759,
# 取消 near 1045,759, 确定 near 1188,759.  The share page behind keeps 全部文件 near 38,265.
PK_TITLE, PK_CRUMB, PK_MARKER = (664, 373, 40), (657, 418), (1125, 418, 80)
PK_NEW, PK_CANCEL, PK_CONFIRM = (700, 749, 70), (1030, 749, 32), (1172, 749, 32)
MERGED, MERGED_MARKER = (664, 420, 186, 14), (1170, 419, 82, 18)  # measured merged Back crumb + 已加载前1个
SPLIT_ROOT_X = 750  # synthetic: root path line right of a separately read 返回上一级 > pair
PK_ROW_X, PK_ROW_Y, PK_ROW_STEP, PK_ROWS = 700, 460, 40, 7  # 7 whole rows fit above the button row
BG_ROOT = (10, 255, 60)


def line(text, x, y, w=80, h=20):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


class FakeShare:
    """Share tab + bottom destination bar + picker over a remote tree.  Only 保存到网盘 copies."""

    def __init__(self, roots=("a.zip", "资料"), *, dest="/", label_prefix="保存到:", preselected=0,
                 icon_opens=True, picker_window=False, dirs=None, lag=0, label_updates=True,
                 picker_closes=True, keep_selection=True, save_mode="success", extra_hidden=False, extract=None,
                 field="placeholder", open_delay=1, decoy=False, glyph=None, marker="已加载前20个",
                 crumb_split=False, twin=False, title="保存到", back=None, opens_at="/", back_tokens=True):
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
        self.extract = extract  # None: list shown; 'rejected'/'pending': stuck on the extraction page
        self.page = "file"
        self.picker = None  # {"cwd", "shown", "pending"}
        self.dialog = None
        self.schemes: list[str] = []
        self.field = field  # top address field: 'placeholder' | 'link' (prior share) | 'both' | 'none'
        self.open_delay = open_delay  # observations after typing before the share tab shows
        self.decoy = decoy  # a link-looking file name in the list, below the top band
        self.glyph = glyph  # OCR text read off the folder icon itself (observed: "D")
        self.marker = marker  # loaded-count text at the right end of the picker crumb row
        self.crumb_split = crumb_split  # crumb OCR'd as one line per segment instead of one merged line
        self.twin = twin  # a second complete modal layout on the same frame
        self.title = title  # modal title text (None: not shown)
        self.elide = None  # crumb text override (e.g. a shortened 全部文件/…/m)
        self.back = back  # below the root: None | 'merged' (measured one OCR line) | 'split' 返回上一级 control
        self.back_tokens = back_tokens  # the merged line carries per-token OCR boxes
        self.opens_at = opens_at  # folder the picker opens on (observed: root)
        self.bg: list[dict] = []  # extra share-page lines behind the modal
        self.picker_inputs: list[tuple] = []  # (kind, px, py, hit text) of every input the picker received
        self.focused = self.all_selected = False
        self.typed = None
        self.pending_open = None
        self.opens: list[str] = []  # every share open, either route
        self.inputs: list[dict] = []
        self.events: list[tuple] = []  # ("icon",), ("confirm", cwd), ("save", dest), ("view",)
        self.box_x = BOX_X  # where the client really draws (and hit-tests) the checkbox column
        self.paint = None  # extra pixels (decoys) drawn on every share frame
        self.render_checks = True  # False: the frame never shows a box turning blue

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
        self.opens.append(url)
        self.page = "share"

    def field_lines(self):
        if self.typed is not None:
            return [line(self.typed, 300, 40, 600)]
        lines = []
        if self.field in ("placeholder", "both"):
            lines.append(line("搜网盘文件、全网资源资讯", 450, 40, 300))
        if self.field in ("link", "both"):
            lines.append(line(PRIOR, 300, 40, 600))
        if self.field in ("q_link", "q_both", "x_link"):  # magnifier merged into the line (measured bbox)
            lines += [line(("Q " if self.field != "x_link" else "Z ") + PRIOR, 226, 56, 406, 17),
                      line("C", 186, 57, 16, 14)]  # separate refresh glyph
        if self.field == "q_both":
            lines.append(line(PRIOR, 900, 40, 600))
        return lines

    def _address_input(self, x, y, kind, text):
        """True when the input belonged to the address field (click on it, or keys/typing while focused)."""
        if kind == "click":
            self.focused = bool(self._hit(self.field_lines(), x, y))
            self.all_selected = False
            return self.focused
        if not self.focused or kind not in ("key", "type"):
            return False
        if kind == "key":
            self.all_selected = True
        else:
            self.typed = text if self.all_selected else (self.typed or "") + text
            self.opens.append(self.typed)
            if self.typed == f"{SHARE}?pwd={PWD}":
                self.pending_open = self.open_delay
        return True

    def share_inputs(self):
        """Inputs other than the address-bar opener (click field, Ctrl+A, type)."""
        return [i for i in self.inputs if not i.get("address")]

    def frame(self, window_id):
        if self.pending_open is not None:  # the client opens the share tab asynchronously
            self.pending_open -= 1
            if self.pending_open < 0:
                self.page, self.pending_open, self.focused = "share", None, False
        frame = {"window_id": window_id, "width": W, "height": H, "input_token": "tok", "token": "f"}
        if window_id == "m" and self.page == "share" and not self.extract:
            frame.update(full=True, patches=[{"x": 0, "y": 0, "width": W, "height": H, "image": self.pixels()}])
        return frame

    def pixels(self):
        """Share-tab checkboxes: blue fill with a white tick when checked, grey outline when empty."""
        shown = len(self.selected) + (1 if self.extra_hidden and self.selected else 0)
        checks = (shown > 0, *(name in self.selected for name in self.roots)) if self.render_checks \
            else (False,) * (len(self.roots) + 1)
        key = (self.box_x, checks, self.paint)
        if key not in _PIXELS:
            import base64
            import io
            from PIL import Image, ImageDraw
            image = Image.new("RGB", (W, H), "white")
            draw = ImageDraw.Draw(image)
            for i, checked in enumerate(checks):
                cy = HEADER_CY if i == 0 else ROW_CY + ROW_STEP * (i - 1)
                box = (self.box_x - 8, cy - 8, self.box_x + 8, cy + 8)  # 17 px, as measured
                if checked:
                    draw.rectangle(box, fill=BLUE)
                    draw.line((box[0] + 4, cy, box[0] + 7, cy + 3, box[0] + 12, cy - 3), fill="white", width=2)
                else:
                    draw.rectangle(box, outline=GREY)
            if self.paint:
                self.paint(draw)
            buf = io.BytesIO()
            image.save(buf, "PNG")
            _PIXELS[key] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return _PIXELS[key]

    def ocr(self, frame):
        if self.picker is not None and self.picker["pending"]:
            self.picker["pending"][1] -= 1
            if self.picker["pending"][1] < 0:
                self.picker["shown"] = self.picker["pending"][0]
                self.picker["pending"] = None
        lines = []
        if frame["window_id"] == "m":
            lines += self.field_lines()
            lines += self.share_lines() if self.page == "share" else []
            lines += [line(PRIOR, 200, 300, 600)] if self.decoy and self.page == "file" else []
            lines += self.dialog_lines()
        if self.picker is not None and (frame["window_id"] == "p" or not self.picker_window):
            lines += self.picker_lines()  # in-page picker overlays the share tab
        shown = self.picker is not None and (frame["window_id"] == "p" or not self.picker_window)
        return {"lines": lines, "tokens": self.crumb_tokens() if shown else []}

    def crumb_tokens(self):
        """OCR tokens of the merged 返回上一级>全部文件/... line (measured line bbox 664,420,186,14 at /data),
        or of the root path line right of a split control."""
        if self.back is None or self.picker["shown"] == "/" or self.elide or not self.back_tokens:
            return []
        merged = self.back == "merged"
        pieces = ["返回上一级", ">"] if merged else []
        for i, seg in enumerate(["全部文件", *[p for p in self.picker["shown"].split("/") if p]]):
            pieces += (["/"] if i else []) + [seg]
        widths = {"返回上一级": 70, ">": 10, "全部文件": 56, "/": 8}
        tokens, x = [], MERGED[0] if merged else SPLIT_ROOT_X
        for order, piece in enumerate(pieces):
            w = widths.get(piece, 10 * len(piece) + 2)
            tokens.append({"text": piece, "x": x, "y": MERGED[1] if merged else PK_CRUMB[1], "w": w,
                           "h": MERGED[3] if merged else 20, "order": order, "parent_line_id": "pk-crumb"})
            x += w
        return tokens

    def share_lines(self):
        if self.extract:  # observed native page; the box renders the code letter-spaced
            lines = [line("向你分享了文件", 200, 200, 120), line(" ".join(PWD), 200, 260, 120),
                     line("提取文件", 200, 320, 80)]
            return lines + ([line("提取码格式错误", 200, 290, 100)] if self.extract == "rejected" else [])
        shown = len(self.selected) + (1 if self.extra_hidden and self.selected else 0)
        crumb = "/".join(["我的网盘", *[p for p in self.dest.split("/") if p]]) + ("/" if self.dest == "/" else "")
        lines = [line("全部文件", *BG_ROOT), line(f"已选择{shown}个文件", HEADER_X, HEADER_CY - 10, 120),
                 line("大小", 521, HEADER_CY - 10, 38), *self.bg,
                 line(self.label_prefix + crumb, 300, BAR_Y, 200), line("保存到网盘", *SAVE), line("下载", 800, BAR_Y, 40)]
        if self.glyph:  # measured 23x23 icon glyph box, centred on the icon point
            lines.append(line(self.glyph, ICON[0] - 11, ICON[1] - 11, 23, 23))
        for i, name in enumerate(self.roots):
            y = ROW_CY + ROW_STEP * i - 10
            lines += [line(name, HEADER_X, y, 120), line("1.2M" if "." in name else "-", 521, y, 40)]
        return lines

    def picker_lines(self):
        cwd = self.picker["shown"]
        segments = ["全部文件", *[p for p in cwd.split("/") if p]]
        lines = [line(self.title, *PK_TITLE)] if self.title else []
        if self.elide:
            lines.append(line(self.elide, *PK_CRUMB, 200))
        elif self.back == "merged" and cwd != "/":
            w = sum(t["w"] for t in self.crumb_tokens()) or MERGED[2]
            lines.append({**line("返回上一级>" + "/".join(segments), MERGED[0], MERGED[1], w, MERGED[3]),
                          "line_id": "pk-crumb"})
        elif self.back == "split" and cwd != "/":  # control, separator and root path as separate lines
            lines += [line("返回上一级", 664, PK_CRUMB[1], 70), line(">", 738, PK_CRUMB[1], 8),
                      {**line("/".join(segments), SPLIT_ROOT_X, PK_CRUMB[1], 200), "line_id": "pk-crumb"}]
        elif self.crumb_split:  # one OCR line per segment with ">" separators between
            x = PK_CRUMB[0]
            for i, seg in enumerate(segments):
                if i:
                    lines.append(line(">", x, PK_CRUMB[1], 8))
                    x += 14
                lines.append(line(seg, x, PK_CRUMB[1], 16 * len(seg)))
                x += 16 * len(seg) + 6
        else:
            lines.append(line("/".join(segments), *PK_CRUMB, 300))
        if self.marker:
            lines.append(line(self.marker, *(MERGED_MARKER if self.back == "merged" and cwd != "/" else PK_MARKER)))
        lines += [line("新建文件夹", *PK_NEW), line("取消", *PK_CANCEL), line("确定", *PK_CONFIRM)]
        top = self.picker.get("top", 0)
        for i, child in enumerate(self.dirs.get(cwd, [])[top:top + PK_ROWS + 1]):
            lines.append(line(child, PK_ROW_X, PK_ROW_Y + PK_ROW_STEP * i, 100))  # 8th row is clipped by the buttons
        if self.twin:  # a second full layout (e.g. a stacked duplicate modal), shifted left
            lines += [line("保存到", 120, 373, 40), line("全部文件", 113, 418, 60), line("取消", 486, 749, 32),
                      line("确定", 628, 749, 32)]
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
            return self._picker_input(x, y, kind, action.get("delta", 0))
        if frame["window_id"] == "m" and self._address_input(x, y, kind, action.get("text")):
            self.inputs[-1]["address"] = True
            return {}
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
                self.picker = {"cwd": self.opens_at, "shown": self.opens_at, "pending": None}  # observed: root
            return {}
        hit = self._hit(self.share_lines(), x, y)
        if hit and hit["text"] == "保存到网盘":
            return self._save()
        if abs(px - self.box_x) > 8:  # only the 17 px boxes toggle (a miss changes nothing)
            return {}
        if abs(py - HEADER_CY) <= 8:  # header checkbox: partial -> all, all -> none, none -> all
            self.selected = set() if len(self.selected) == len(self.roots) else set(self.roots)
            return {}
        for i, name in enumerate(self.roots):
            if abs(py - (ROW_CY + ROW_STEP * i)) <= 8:
                self.selected ^= {name}
        return {}

    def _picker_input(self, x, y, kind, delta=0):
        hit = self._hit(self.picker_lines(), x, y)
        if hit and hit.get("line_id") == "pk-crumb":  # the merged line: the click lands on one drawn glyph run
            hit = next((t for t in self.crumb_tokens() if t["x"] <= x * W <= t["x"] + t["w"]), None)
        text = hit and hit["text"]
        self.picker_inputs.append((kind, x * W, y * H, text))
        cwd = self.picker["cwd"]
        if kind == "scroll":  # wheel inside the list viewport moves it 3 rows, clamped at the end
            if PK_CRUMB[0] <= x * W <= PK_CONFIRM[0] + PK_CONFIRM[2] and PK_CRUMB[1] + 20 <= y * H <= PK_CANCEL[1]:
                rows = len(self.dirs.get(self.picker["shown"], []))
                step = 3 if delta < 0 else -3
                self.picker["top"] = max(0, min(self.picker.get("top", 0) + step, max(0, rows - PK_ROWS)))
                self.events.append(("scroll", self.picker["top"]))
            return {}
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
        elif text == "返回上一级":
            self._navigate(cwd.rsplit("/", 1)[0] or "/")
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
        self.picker["top"] = 0

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
    assert len(fake.inputs) == clicks and len(fake.opens) == 1


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
    assert len(fake.share_inputs()) == 1  # no growth: the second header click is never made blind


# ---- checkbox column measured on the native 1920x1152 frame, never the provisional CSS offset ----

def share_screen(fake):
    """The share tab as the client observes it: one fresh frame plus its OCR."""
    fake.page = "share"
    frame = fake.frame("m")
    return Screen(frame, fake.ocr(frame)["lines"])


def share_header_line(screen):
    return next(l for l in screen.lines if l["text"].startswith("已选择"))


def test_measured_1920_checkbox_is_the_anchor_and_old_offset_misses_it():
    fake = FakeShare(roots=("only.zip",), preselected=1)  # native one-root share: 已选择1个文件
    screen = share_screen(fake)
    header = share_header_line(screen)
    anchor = share_checkbox_anchor(screen, header, 1)
    assert anchor["measured"] and abs(anchor["x"] - BOX_X) <= 1 and 42 <= anchor["x"] <= 58
    old = header["x"] - 37  # the former SHARE_CHECKBOX_OFFSET: x 61, right of the x42..58 box
    assert not 42 <= old <= 58
    fake.input(screen.frame, action="click", x=old / W, y=HEADER_CY / H, button="left")
    assert fake.selected == {"only.zip"}  # the incident: two such clicks left 已选择1个文件
    fake.input(screen.frame, action="click", x=anchor["x"] / W, y=HEADER_CY / H, button="left")
    assert fake.selected == set()
    after = share_screen(fake)
    assert share_checkbox_state(after, anchor, share_header_line(after), HEADER_CY) == "empty"


@pytest.mark.parametrize("roots,pre,items,boxes", [
    (("only.zip",), 1, None, 2),                         # clear 1 -> 0, select all
    (("a.zip", "b.zip", "资料"), 1, ["b.zip", "资料"], 4),  # partial -> all -> none, then two rows
    (("a.zip", "b.zip"), 0, None, 1)])                   # nothing preselected: profile column, proven blue after
def test_header_and_row_clicks_share_one_anchor_inside_the_box(tmp_path, ledger, roots, pre, items, boxes):
    fake = FakeShare(roots=roots, preselected=pre)
    _, result = run(fake, ledger, items=items)
    assert result.status == "remote_present", result.to_dict()
    left = [i for i in fake.share_inputs() if i["x"] * W < HEADER_X]  # everything left of the header text
    assert len(left) == boxes and all(42 <= i["x"] * W <= 58 for i in left)
    assert len({round(i["x"] * W, 3) for i in left}) == 1  # header and rows: one coherent column
    assert_saved_only_into(fake, "/data/m/t")


def blue_rect(box):
    return lambda draw: draw.rectangle(box, fill=BLUE)


DECOY = blue_rect((62, 306, 66, 318))  # a second blue blob in the checkbox column's search region
HEADER_BLUE = blue_rect((42, 304, 58, 320))  # header box painted checked whatever the selection


@pytest.mark.parametrize("pre,setup,reason", [
    (1, lambda f: setattr(f, "paint", DECOY), "share_checkbox_ambiguous"),
    (0, lambda f: setattr(f, "paint", DECOY), "share_checkbox_state_mismatch"),
    (1, lambda f: setattr(f, "box_x", 36), "share_checkbox_off_profile"),       # box 12+ px off the profile column
    (1, lambda f: setattr(f, "render_checks", False), "share_checkbox_not_found"),  # count 1 but no blue box
    (0, lambda f: setattr(f, "paint", HEADER_BLUE), "share_checkbox_state_mismatch"),  # count 0 yet checked
])
def test_bad_or_ambiguous_checkbox_anchor_gets_no_input(tmp_path, ledger, pre, setup, reason):
    fake = FakeShare(roots=("only.zip",), preselected=pre)
    setup(fake)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == reason, result.to_dict()
    assert fake.share_inputs() == [] and fake.saves() == []


def test_unprofiled_frame_size_gets_no_input(tmp_path, ledger, monkeypatch):
    monkeypatch.setattr(client_module, "SHARE_CHECKBOX_PROFILES", {(1213, 864): 48})
    fake = FakeShare(roots=("only.zip",), preselected=1)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_checkbox_profile_unverified"
    assert fake.share_inputs() == [] and fake.saves() == []


@pytest.mark.parametrize("pre,items,setup,reason", [
    # Profile column misses a box drawn elsewhere: the next frame proves nothing changed; no second try.
    (0, None, lambda f: setattr(f, "box_x", 36), "share_select_all_unverified"),
    (0, ["a.zip"], lambda f: setattr(f, "box_x", 36), "share_selection_unverified"),
    # Count says selected but the clicked box never shows checked on the anchor.
    (0, ["a.zip"], lambda f: setattr(f, "render_checks", False), "share_selection_unverified"),
    (0, None, lambda f: setattr(f, "render_checks", False), "share_select_all_unverified"),
    # Count reaches 0 but the header box still shows checked: not a proven clear.
    (2, ["a.zip"], lambda f: setattr(f, "paint", HEADER_BLUE), "share_selection_clear_unverified"),
])
def test_every_checkbox_click_is_proven_on_the_next_frame_without_retry(tmp_path, ledger, pre, items, setup, reason):
    fake = FakeShare(roots=("a.zip", "b.zip"), preselected=pre)
    setup(fake)
    _, result = run(fake, ledger, items=items)
    assert result.status == "not_submitted" and result.facts["reason"] == reason, result.to_dict()
    assert len(fake.share_inputs()) == 1 and fake.saves() == [] and ("icon",) not in fake.events


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


# Sanitized 1920x1152 PUBLIC frame: OCR reads the folder icon as "D" around the Save-anchored point.
MEASURED_BAR = [line("保存到:我的网盘/", 52, 1075, 556, 17), line("D", 1355, 1071, 23, 23),
                line("保存到网盘", 1430, 1073, 74, 18), line("下载", 1593, 1071, 36, 23)]


def measured_bar(*extra, glyph=None, size=(W, H)):
    lines = [dict(l) for l in MEASURED_BAR] + list(extra)
    if glyph is not None:
        lines[1].update(glyph)
    return Screen({"width": size[0], "height": size[1]}, lines)


def test_measured_icon_glyph_d_is_the_icon_not_text():
    icon = destination_icon(measured_bar())
    assert (icon["x"] + 1, icon["y"] + 1) == (1467 - 102, 1082)
    assert 1355 <= icon["x"] + 1 <= 1378 and icon["x"] + 1 < 1430 - 24  # inside the glyph, clear of Save


@pytest.mark.parametrize("screen", [
    measured_bar(glyph={"text": "O"}), measured_bar(glyph={"text": "d"}), measured_bar(glyph={"text": "DD"}),
    measured_bar(glyph={"text": "保存"}), measured_bar(glyph={"text": "D", "w": 60, "x": 1335}),  # too wide
    measured_bar(glyph={"w": 23, "h": 12, "y": 1076}),  # not square
    measured_bar(glyph={"y": 1060}),  # off the Save row (anchor not in it -> must still be refused below)
    measured_bar(glyph={"x": 1345}),  # anchor 8.5 px off the glyph centre
    measured_bar(size=(2560, 1440)),  # unmeasured frame profile
    measured_bar(line("D", 1360, 1076, 12, 12)),  # second overlapping hit: ambiguous
    measured_bar(line("D", 1200, 1071, 23, 23)),  # second D glyph on the bar: ambiguous
])
def test_icon_glyph_outside_measured_profile_refused(screen):
    hit = [l for l in screen.lines if l["x"] <= 1365 <= l["x"] + l["w"] and l["y"] <= 1082 <= l["y"] + l["h"]]
    if not hit:  # glyph moved off the anchor: the point is plain background, nothing to prove here
        assert destination_icon(screen)["x"] + 1 == 1365
        return
    with pytest.raises(StepFailed) as exc:
        destination_icon(screen)
    assert exc.value.code == "destination_icon_on_text"


def test_icon_glyph_profile_needs_geometry_inputs():
    no_label = Screen({"width": W, "height": H}, [l for l in MEASURED_BAR if l["text"] != "保存到:我的网盘/"])
    with pytest.raises(StepFailed) as exc:
        destination_icon(no_label)
    assert exc.value.code == "destination_label_not_found"
    with pytest.raises(StepFailed) as exc:
        destination_icon(Screen({"width": W, "height": H}, []))
    assert exc.value.code == "destination_label_not_found"


@pytest.mark.parametrize("glyph,reason", [("O", "destination_icon_on_text"), ("Dl", "destination_icon_on_text")])
def test_unmeasured_icon_glyph_gets_no_input(ledger, glyph, reason):
    fake = FakeShare(dest="/old", glyph=glyph)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == reason, result.to_dict()
    assert ("icon",) not in fake.events and fake.saves() == [] and fake.picker is None


def test_measured_icon_glyph_opens_picker_and_saves_once(ledger):
    fake = FakeShare(dest="/old", glyph="D", dirs={"/": ["data", "old"], "/data": ["m"], "/data/m": [], "/old": []})
    _, result = run(fake, ledger)
    assert result.status == "remote_present", result.to_dict()
    assert fake.events[:2] == [("icon",), ("confirm", "/data/m/t")]
    assert_saved_only_into(fake, "/data/m/t")


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
            i = next(i for i, item in enumerate(out) if item["text"].startswith("保存到:"))
            out[i] = line("保存到:我的网盘/.../t", 300, BAR_Y, 200)
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
    assert len(fake.saves()) == 1 and len(fake.opens) == 1


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
    assert fake.saves() == [] and len(fake.opens) == 1
    retry = client.save_share(SHARE + "?pwd=" + PWD, "/data/m/t")  # failed before submit stays retryable
    # ...but the echoed link now competes with the field: fail closed, nothing typed again.
    assert retry.status == "not_submitted" and retry.facts["reason"] == "share_address_field_ambiguous"
    assert len(fake.opens) == 1 and fake.share_inputs()[-1]["action"] != "type"
    assert_no_share_secret(tmp_path, retry)


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
    assert fake.opens == [] and fake.inputs == [] and not (tmp_path / "ledger.json").exists()


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
    assert fake.share_inputs() == [] and fake.saves() == []


def test_incomplete_target_listing_does_not_open_share(tmp_path, ledger):
    fake = FakeShare()
    result = Client(fake, ledger, complete=False).save_share(SHARE, "/data/m/t", pwd=PWD)
    assert result.status == "not_submitted" and result.facts["reason"] == "target_listing_incomplete"
    assert fake.opens == []


def test_missing_explicit_item_clicks_nothing(tmp_path, ledger):
    fake = FakeShare()
    _, result = run(fake, ledger, items=["nope.zip"])
    assert result.facts["reason"] == "share_item_not_found" and fake.share_inputs() == []


def test_select_all_with_rows_outside_frame_refuses(tmp_path, ledger):
    fake = FakeShare(extra_hidden=True)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_select_all_unverified"
    assert fake.picker is None and fake.saves() == []


def test_save_picker_path_parses_merged_crumb_and_ignores_bottom_bar():
    screen = picker_screen(FakeShare(), "/data/m")
    assert save_picker_path(screen) == ["data", "m"]
    assert save_picker_path(Screen({"width": W, "height": H}, [line("确定", 1, 1)])) is None
    assert save_picker_path(bar(40)) is None  # 我的网盘/ on the Save row is the destination label


# ---- picker modal: measured layout, modal-scoped lookups, bounded scrolling -----------------

MODAL = (655, 355, 1265, 795)  # measured modal bounds


def picker_screen(fake, cwd="/"):
    """The main-window frame (share page behind + in-page modal) the provider observes."""
    fake.page = "share"
    fake.picker = {"cwd": cwd, "shown": cwd, "pending": None}
    frame = fake.frame("m")
    return Screen(frame, fake.ocr(frame)["lines"])


def in_modal(px, py):
    return MODAL[0] <= px <= MODAL[2] and MODAL[1] <= py <= MODAL[3]


def test_measured_modal_ignores_background_root_and_loaded_marker():
    fake = FakeShare(roots=("a.zip", "data"), dest="/old")  # a background share row named like the folder
    screen = picker_screen(fake)
    bar_y = [l for l in screen.lines if l["text"] == "保存到网盘"][0]["y"]
    roots = [l for l in screen.lines if l["text"].startswith("全部文件") and abs(l["y"] - bar_y) > 20]
    assert len(roots) == 2  # the old whole-frame crumb scan refused exactly this frame (crumb_not_unique)
    picker = save_picker(screen)
    assert picker["path"] == [] and save_picker_path(screen) == []
    assert [m["text"] for m in picker["marker"]] == ["已加载前20个"]  # never a path segment
    region = picker["region"]
    assert MODAL[0] - 40 <= region["x"] and region["x"] + region["w"] <= MODAL[2] + 40
    inside = {l["text"] for l in screen.lines if region["x"] <= l["x"] and l["x"] + l["w"] <= region["x"] + region["w"]
              and region["y"] <= l["y"] and l["y"] + l["h"] <= region["y"] + region["h"]}
    assert inside == {"保存到", "全部文件", "已加载前20个", "data", "新建文件夹", "取消", "确定"}
    assert [l["text"] for l in picker_rows(screen, picker)] == ["data"]
    assert all(in_modal(r["x"], r["y"]) for r in picker_rows(screen, picker))  # not the share row at x98
    assert (picker["cancel"]["x"], picker["confirm"]["x"]) == (PK_CANCEL[0], PK_CONFIRM[0])


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("marker", ["已加载前20个", "已加载20个", None])
def test_navigated_crumb_path_excludes_loaded_marker(split, marker):
    fake = FakeShare(crumb_split=split, marker=marker, dirs={"/": ["data"], "/data": ["m"], "/data/m": ["x"]})
    assert save_picker_path(picker_screen(fake, "/data/m")) == ["data", "m"]


@pytest.mark.parametrize("crumb", ["全部文件/…/m", "全部文件/data/m...", "全部文件/da…"])
def test_elided_picker_crumb_fails_closed(crumb):
    fake = FakeShare()
    fake.elide = crumb
    screen = picker_screen(fake)
    with pytest.raises(StepFailed) as exc:
        save_picker(screen)
    assert exc.value.code == "save_picker_crumb_elided" and save_picker_path(screen) is None


@pytest.mark.parametrize("setup, code", [
    (dict(title=None), "save_picker_layout_not_found"),
    (dict(title="保存至网盘"), "save_picker_layout_not_found"),
    (dict(twin=True), "save_picker_ambiguous"),
])
def test_picker_layout_must_be_unique_and_titled(setup, code):
    with pytest.raises(StepFailed) as exc:
        save_picker(picker_screen(FakeShare(**setup)))
    assert exc.value.code == code


def test_two_root_crumbs_inside_the_modal_fail_closed():
    fake = FakeShare()
    fake.dirs["/"] = ["全部文件夹副本"]  # a folder row itself starting with the root label
    with pytest.raises(StepFailed) as exc:
        save_picker(picker_screen(fake))
    assert exc.value.code == "save_picker_crumb_not_unique"


@pytest.mark.parametrize("setup, elide, why", [
    (dict(title=None), None, "save_picker_layout_not_found"),
    (dict(twin=True), None, "save_picker_ambiguous"),
    ({}, "全部文件/…", "save_picker_crumb_elided"),
])
def test_unrecognised_open_modal_gets_no_input_and_no_cancel(ledger, setup, elide, why):
    fake = FakeShare(dest="/old", **setup)
    fake.elide = elide
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_not_recognized"
    assert result.facts["picker"] == why and result.facts["cancel"] == "not_attempted"
    assert fake.picker_inputs == [] and fake.picker is not None and fake.saves() == []


def picker_clicks(fake):
    return [(k, t) for k, _x, _y, t in fake.picker_inputs if k != "scroll"]


@pytest.mark.parametrize("lag", [0, 2])
def test_offscreen_folder_is_reached_by_bounded_scroll_then_one_click_per_segment(ledger, lag):
    fillers = [f"f{i:02d}" for i in range(10)]
    fake = FakeShare(roots=("a.zip", "data"), lag=lag,
                     dirs={"/": [*fillers, "data"], "/data": ["m"], "/data/m": []})
    _, result = run(fake, ledger, target="/data/m")
    assert result.status == "remote_present", result.to_dict()
    assert_saved_only_into(fake, "/data/m")
    scrolls = [(x, y) for k, x, y, _t in fake.picker_inputs if k == "scroll"]
    # first scroll shows 'data' only clipped under the last whole row: never clicked, scrolled again
    assert len(scrolls) == 2 and all(in_modal(x, y) and PK_CRUMB[1] + 20 < y < PK_CANCEL[1] for x, y in scrolls)
    assert picker_clicks(fake) == [("click", "data"), ("click", "m"), ("click", "确定")]
    assert all(in_modal(x, y) for _k, x, y, _t in fake.picker_inputs)


def test_folder_absent_after_list_end_stops_scrolling_and_cancels(ledger):
    fake = FakeShare()
    client = Client(fake, ledger)
    real_open = client.open_remote_dir

    def open_then_hide(remote_dir, *, create_missing=False):
        out = real_open(remote_dir, create_missing=create_missing)
        fake.dirs["/"] = [f"f{i:02d}" for i in range(12)]  # the picker list never shows 'data'
        return out
    client.open_remote_dir = open_then_hide
    result = client.save_share(SHARE, "/data/m", pwd=PWD)
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_folder_not_found"
    scrolls = [e for e in fake.events if e[0] == "scroll"]
    assert scrolls == [("scroll", 3), ("scroll", 5), ("scroll", 5)]  # unchanged rows = end of list
    assert picker_clicks(fake) == [("click", "取消")] and result.facts["cancel"] == "verified"
    assert fake.picker is None and fake.saves() == []


def test_endless_list_scrolls_at_most_max_scrolls(ledger):
    fake = FakeShare(dirs={"/": [*(f"f{i:03d}" for i in range(100)), "data"], "/data": ["m"], "/data/m": []})
    _, result = run(fake, ledger, target="/data/m")
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_folder_not_found"
    assert len([e for e in fake.events if e[0] == "scroll"]) == 8 and fake.saves() == []


def test_two_visible_rows_with_the_folder_name_click_nothing(ledger):
    fake = FakeShare(dirs={"/": ["data", "data"], "/data": ["m"], "/data/m": []})
    _, result = run(fake, ledger, target="/data/m")
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_folder_ambiguous"
    assert picker_clicks(fake) == [("click", "取消")] and fake.saves() == []


def test_scroll_that_changes_the_crumb_fails_without_a_click(ledger):
    fake = FakeShare(dirs={"/": [*(f"f{i:02d}" for i in range(10)), "data"], "/data": ["m"], "/data/m": []})
    real = fake._picker_input

    def scroll_navigates(x, y, kind, delta=0):
        out = real(x, y, kind, delta)
        if kind == "scroll":
            fake.picker.update(cwd="/f00", shown="/f00", top=0)
        return out
    fake._picker_input = scroll_navigates
    _, result = run(fake, ledger, target="/data/m")
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_scroll_changed_path"
    assert picker_clicks(fake) == [("click", "取消")] and fake.saves() == []


def test_background_buttons_never_take_the_modal_cancel(ledger):
    fake = FakeShare(dest="/old")
    fake.bg = [line("取消", 300, 900, 32), line("确定", 400, 900, 32)]  # share-page controls left of the modal
    client = Client(fake, ledger)
    real_open = client.open_remote_dir

    def open_then_hide(remote_dir, *, create_missing=False):
        out = real_open(remote_dir, create_missing=create_missing)
        fake.dirs["/data/m"] = []
        return out
    client.open_remote_dir = open_then_hide
    result = client.save_share(SHARE, "/data/m/t", pwd=PWD)
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_folder_not_found"
    assert result.facts["cancel"] == "verified" and fake.picker is None and fake.saves() == []
    kind, x, y, text = fake.picker_inputs[-1]
    assert (kind, text) == ("click", "取消") and in_modal(x, y)


def test_cli_request_file_keeps_secrets_out_of_argv(tmp_path, monkeypatch, capsys):
    import scripts.baidu_netdisk_transfer as cli
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"url": SHARE, "pwd": PWD, "target_dir": "/data/m/t", "items": None}), encoding="utf-8")
    seen = {}

    class Stub:
        def __init__(self, **kw):
            pass

        def save_share(self, url, target, *, pwd, items, opener):
            seen.update(url=url, target=target, pwd=pwd, items=items, opener=opener)
            from backend.core.baidu_netdisk.client import TransferResult
            return TransferResult("remote_present", "business_verify_content", "k", {"target_dir": target})
    monkeypatch.setattr("backend.core.baidu_netdisk.client.NetdiskClient", Stub)
    argv = ["save-share", "--request-file", str(req), "--lease-token", "T", "--ledger", str(tmp_path / "l.json")]
    monkeypatch.setattr(cli, "_lease", lambda args: _null_lease())
    cli.main(argv)
    out = capsys.readouterr().out
    assert seen == {"url": SHARE, "target": "/data/m/t", "pwd": PWD, "items": None, "opener": "address_bar"}
    assert PWD not in out and "1AbCdEfGh" not in out and PWD not in " ".join(argv)
    req.write_text(json.dumps({"url": SHARE, "pwd": PWD, "target_dir": "/data/m/t", "opener": "scheme"}), encoding="utf-8")
    cli.main(argv)
    assert seen["opener"] == "scheme"  # request file may choose the scheme explicitly
    cli.main(argv + ["--opener", "address-bar"])
    assert seen["opener"] == "address_bar"  # the flag overrides the request file


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
    assert len(fake.opens) == 1 and "AbCdEfGh" not in (tmp_path / "ledger.json").read_text(encoding="utf-8")


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


# ---- extraction page: recognised explicitly, never typed into or pressed ---------------------

OPENER_READS = 3  # fresh field proof, after the click, after Ctrl+A


def counting(fake):
    real, seen = fake.ocr, []
    fake.ocr = lambda frame: seen.append(1) or real(frame)
    return seen


@pytest.mark.parametrize("state, reason", [("rejected", "share_extraction_rejected"),
                                           ("pending", "share_extraction_not_passed")])
def test_extraction_page_is_explicit_not_submitted_without_input(tmp_path, ledger, state, reason):
    fake = FakeShare(extract=state, open_delay=0)
    seen = counting(fake)
    result = Client(fake, ledger).save_share(SHARE + "?pwd=" + PWD, "/data/m/t")
    assert result.status == "not_submitted" and result.facts["reason"] == reason
    assert result.facts["code_submitted_by_flow"] is False and "texts" not in result.facts
    assert fake.share_inputs() == [] and fake.saves() == [] and len(fake.opens) == 1  # no typing, no 提取文件, no retry
    if state == "rejected":
        assert result.facts["hint"] == "提取码格式错误" and len(seen) == OPENER_READS + 1  # stops at the first rejection
    else:
        assert len(seen) == OPENER_READS + 4 + 1  # polls exhausted, one closing read
    dumped = json.dumps(result.to_dict(), ensure_ascii=False) + (tmp_path / "ledger.json").read_text(encoding="utf-8")
    for secret in (PWD, " ".join(PWD), "1AbCdEfGh"):
        assert secret not in dumped
    assert_no_share_secret(tmp_path, result)


def test_download_share_reports_extraction_rejection(tmp_path, ledger):
    fake = FakeShare(extract="rejected")
    (tmp_path / "dl").mkdir()
    result = Client(fake, ledger).download_share(SHARE, "a.zip", tmp_path / "out", client_download_dir=tmp_path / "dl",
                                                 expected_size=1, pwd=PWD)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_extraction_rejected"
    assert fake.inputs == [] and len(fake.schemes) == 1 and PWD not in json.dumps(result.to_dict())


def test_extraction_state_reads_only_fixed_labels():
    frame = {"window_id": "m", "width": W, "height": H, "input_token": "t", "token": "f"}
    assert share_extraction_state(Screen(frame, [line("提取码格式错误", 1, 1), line("提取文件", 1, 30)])) == "rejected"
    assert share_extraction_state(Screen(frame, [line("提取文件", 1, 30)])) == "pending"
    assert share_extraction_state(Screen(frame, [line("已选择0个文件", 1, 1), line("大小", 300, 1)])) is None


# ---- address-bar opener (default): OCR-proven field, one typed link, no Enter, no fallback ------------

def opener_inputs(fake):
    return [{k: v for k, v in i.items() if k in ("action", "key", "text")} for i in fake.inputs if i.get("address")]


@pytest.mark.parametrize("field", ["placeholder", "link"])
def test_address_bar_types_full_link_once_without_enter(tmp_path, ledger, field):
    fake = FakeShare(field=field)
    client, result = run(fake, ledger)
    assert result.status == "remote_present", result.to_dict()
    assert opener_inputs(fake) == [{"action": "click"}, {"action": "key", "key": "select_all"},
                                   {"action": "type", "text": f"{SHARE}?pwd={PWD}"}]
    assert fake.opens == [f"{SHARE}?pwd={PWD}"] and fake.schemes == []
    assert not [i for i in fake.inputs if i.get("key") == "enter"]
    assert result.facts["opener"] == "address_bar" and ledger.get(result.key)["facts"]["opener"] == "address_bar"
    assert_saved_only_into(fake, "/data/m/t")
    assert_no_share_secret(tmp_path, result)
    for prior in ("ZzPrior00", "q1w2"):
        assert prior not in json.dumps(result.to_dict()) and prior not in (tmp_path / "ledger.json").read_text("utf-8")
    again = client.save_share(SHARE, "/data/m/t", pwd=PWD)  # recorded: never reopened or resubmitted
    assert again.status == "already_recorded" and len(fake.opens) == 1 and len(fake.saves()) == 1


def test_address_bar_waits_for_asynchronous_share_tab(tmp_path, ledger):
    fake = FakeShare(open_delay=3)
    _, result = run(fake, ledger)
    assert result.status == "remote_present", result.to_dict()
    assert len(fake.opens) == 1


def test_address_bar_share_never_showing_is_not_submitted_without_fallback(tmp_path, ledger):
    fake = FakeShare(open_delay=50)
    client, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_list_not_visible"
    assert len(fake.opens) == 1 and fake.schemes == [] and fake.share_inputs() == [] and fake.saves() == []
    assert ledger.get(result.key)["state"] == "failed"
    assert_no_share_secret(tmp_path, result)


@pytest.mark.parametrize("field, decoy, reason", [("none", False, "share_address_field_not_found"),
                                                  ("none", True, "share_address_field_not_found"),
                                                  ("both", False, "share_address_field_ambiguous")])
def test_missing_or_ambiguous_address_field_gets_no_input(tmp_path, ledger, field, decoy, reason):
    fake = FakeShare(field=field, decoy=decoy)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == reason
    assert fake.inputs == [] and fake.opens == [] and fake.schemes == []  # no guessed point, no scheme fallback
    assert "texts" not in result.facts and "matches" not in result.facts
    dumped = json.dumps(result.to_dict()) + (tmp_path / "ledger.json").read_text(encoding="utf-8")
    assert "ZzPrior00" not in dumped and "q1w2" not in dumped
    assert ledger.get(result.key)["state"] == "failed"


def test_address_field_with_merged_magnifier_q_prefix_is_the_field(tmp_path, ledger):
    fake = FakeShare(field="q_link")
    _, result = run(fake, ledger)
    assert result.status == "remote_present", result.to_dict()
    assert opener_inputs(fake) == [{"action": "click"}, {"action": "key", "key": "select_all"},
                                   {"action": "type", "text": f"{SHARE}?pwd={PWD}"}]
    assert fake.inputs[0]["x"] * W > 226 and fake.inputs[0]["y"] * H < 96
    assert_no_share_secret(tmp_path, result)
    dumped = json.dumps(result.to_dict()) + (tmp_path / "ledger.json").read_text(encoding="utf-8")
    assert "ZzPrior00" not in dumped and "q1w2" not in dumped


@pytest.mark.parametrize("field, reason", [("x_link", "share_address_field_not_found"),
                                           ("q_both", "share_address_field_ambiguous")])
def test_unsupported_prefix_or_several_fields_with_q_prefix_get_no_input(tmp_path, ledger, field, reason):
    fake = FakeShare(field=field)
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == reason
    assert fake.inputs == [] and fake.opens == [] and fake.schemes == []
    assert "texts" not in result.facts and "matches" not in result.facts
    dumped = json.dumps(result.to_dict()) + (tmp_path / "ledger.json").read_text(encoding="utf-8")
    assert "ZzPrior00" not in dumped and "q1w2" not in dumped and "pan.baidu.com/s/" not in dumped.replace(SHARE, "")


@pytest.mark.parametrize("text, ok", [("Q https://pan.baidu.com/s/1AbCdEfGh?pwd=x7k9", True),
                                      ("Q	pan.baidu.com/share/init?surl=x", True),
                                      ("Qhttps://pan.baidu.com/s/1AbCdEfGh", False),
                                      ("q https://pan.baidu.com/s/1AbCdEfGh", False),
                                      ("QQ https://pan.baidu.com/s/1AbCdEfGh", False),
                                      ("Q 搜网盘文件、全网资源资讯", False),
                                      ("Q https://evil.example/pan.baidu.com/s/x", False),
                                      ("打开 https://pan.baidu.com/s/1AbCdEfGh", False)])
def test_address_field_icon_prefix_is_exact(text, ok):
    screen = Screen(frame={"width": W, "height": H}, lines=[line(text, 226, 56, 406, 17)])
    if ok:
        assert address_bar_field(screen)["text"] == text
    else:
        with pytest.raises(StepFailed) as err:
            address_bar_field(screen)
        assert err.value.code == "share_address_field_not_found" and text not in json.dumps(err.value.facts)


def test_link_like_text_below_the_top_band_is_ignored(tmp_path, ledger):
    fake = FakeShare(decoy=True)
    _, result = run(fake, ledger)
    assert result.status == "remote_present" and fake.inputs[0]["y"] * H < 96


def test_lease_lost_before_typing_types_nothing(tmp_path, ledger):
    fake = FakeShare()

    def lease():
        if fake.all_selected:
            raise RuntimeError("lease expired")
    client = Client(fake, ledger)
    client.lease_check = lease
    result = client.save_share(SHARE, "/data/m/t", pwd=PWD)
    assert result.status == "not_submitted" and result.facts["reason"] == "RuntimeError"
    assert fake.opens == [] and not [i for i in fake.inputs if i["action"] == "type"]


def test_typed_link_and_letter_spaced_code_are_redacted_from_diagnostics(tmp_path, ledger):
    fake = FakeShare(label_prefix="目标:")
    real = fake.share_lines
    fake.share_lines = lambda: real() + [line(" ".join(PWD), 200, BAR_Y + 40, 120)]
    result = Client(fake, ledger).save_share(SHARE + "?pwd=" + PWD, "/data/m/t")
    assert result.status == "not_submitted" and result.facts["reason"] == "destination_label_not_found"
    texts = json.dumps(result.facts["texts"], ensure_ascii=False)
    assert texts.count("[redacted]") >= 2 and "保存到网盘" in texts  # field (typed link) and spaced code
    dumped = json.dumps(result.to_dict(), ensure_ascii=False) + (tmp_path / "ledger.json").read_text(encoding="utf-8")
    assert " ".join(PWD) not in dumped
    assert_no_share_secret(tmp_path, result)


def test_scheme_opener_is_explicit_only(tmp_path, ledger):
    fake = FakeShare(field="none")
    _, result = run(fake, ledger, opener="scheme")
    assert result.status == "remote_present" and len(fake.schemes) == 1 and opener_inputs(fake) == []
    with pytest.raises(ValueError):
        Client(FakeShare(), TransferLedger(tmp_path / "other.json")).save_share(SHARE, "/data/m/t", pwd=PWD, opener="auto")
    assert not (tmp_path / "other.json").exists()


# ---- picker crumb inside a folder: measured 返回上一级 control merged into (or split from) the root ------

def test_measured_merged_back_crumb_parses_path():
    fake = FakeShare(back="merged", marker="已加载前1个", dirs={"/": ["data"], "/data": ["proj"]})
    screen = picker_screen(fake, "/data")
    crumb = [l for l in screen.lines if l["text"] == "返回上一级>全部文件/data"]
    assert [(c["x"], c["y"], c["w"], c["h"]) for c in crumb] == [MERGED]  # the measured line, unchanged
    picker = save_picker(screen)
    assert picker["path"] == ["data"]  # the 已加载前1个 marker is never a segment
    assert [l["text"] for l in picker_rows(screen, picker)] == ["proj"]


@pytest.mark.parametrize("back", ["merged", "split"])
def test_back_control_is_never_a_path_segment(back):
    fake = FakeShare(back=back, dirs={"/": ["data"], "/data": ["m"], "/data/m": ["x"]})
    assert save_picker_path(picker_screen(fake, "/data/m")) == ["data", "m"]
    assert save_picker_path(picker_screen(fake)) == []  # root: no control drawn


@pytest.mark.parametrize("back", ["merged", "split"])
def test_root_reset_clicks_root_span_not_back_then_walks_deep_target(ledger, back):
    fake = FakeShare(back=back, dest="/old", opens_at="/old",
                     dirs={"/": ["data", "old"], "/data": ["m"], "/data/m": [], "/old": []})
    _, result = run(fake, ledger, "/data/m/GA/x/sd-1")
    assert result.status == "remote_present", result.to_dict()
    assert_saved_only_into(fake, "/data/m/GA/x/sd-1")
    assert picker_clicks(fake) == [("click", "全部文件"), ("click", "data"), ("click", "m"), ("click", "GA"),
                                   ("click", "x"), ("click", "sd-1"), ("click", "确定")]
    _kind, px, _py, _text = fake.picker_inputs[0]
    root = MERGED[0] + 70 + 10 if back == "merged" else SPLIT_ROOT_X  # root label starts right of 返回上一级>
    assert root <= px <= root + 56 and fake.events[:2] == [("icon",), ("confirm", "/data/m/GA/x/sd-1")]


def test_merged_back_crumb_without_root_token_boundary_gets_no_click(ledger):
    fake = FakeShare(back="merged", back_tokens=False, dest="/old", opens_at="/old",
                     dirs={"/": ["data", "old"], "/data": ["m"], "/data/m": [], "/old": []})
    _, result = run(fake, ledger)
    assert result.status == "not_submitted" and result.facts["reason"] == "save_picker_root_span_unknown"
    assert picker_clicks(fake) == [("click", "取消")] and fake.saves() == []  # never the whole line (Back)


@pytest.mark.parametrize("crumb, code", [
    ("返回上一级》全部文件/data", "save_picker_crumb_not_unique"),  # unobserved separator
    ("返回上一级 > 全部文件/data", None),  # OCR spacing inside the known label/separator only
    ("返回上级>全部文件/data", "save_picker_crumb_not_unique"),  # unknown prefix
    ("返回>全部文件/data", "save_picker_crumb_not_unique"),
    ("data>全部文件", "save_picker_crumb_not_unique"),  # reordered root
    ("返回上一级>返回上一级>全部文件/data", "save_picker_crumb_not_unique"),
    ("全部文件/data>返回上一级", "save_picker_crumb_unknown"),  # control after the root
    ("返回上一级>全部文件/…/m", "save_picker_crumb_elided"),
    ("返回上一级>全部文件/data...", "save_picker_crumb_elided"),
])
def test_back_prefixed_crumb_accepts_only_the_exact_control(crumb, code):
    fake = FakeShare()
    fake.elide = crumb
    screen = picker_screen(fake)
    if code is None:
        assert save_picker_path(screen) == ["data"]
        return
    with pytest.raises(StepFailed) as exc:
        save_picker(screen)
    assert exc.value.code == code and save_picker_path(screen) is None


@pytest.mark.parametrize("left", [["xyz"], ["返回上一级", "data"], ["<"], ["返回上一级"]])
def test_unknown_split_line_left_of_root_fails_closed(left):
    fake = FakeShare()
    fake.elide = "返回上一级>全部文件/data" if left == ["返回上一级"] else "全部文件/data"  # control twice
    fake.bg = [line(t, 626 + 14 * i, PK_CRUMB[1], 12) for i, t in enumerate(left)]
    with pytest.raises(StepFailed) as exc:
        save_picker(picker_screen(fake))
    assert exc.value.code == "save_picker_crumb_unknown"


def test_merged_back_crumb_twice_in_modal_fails_closed():
    fake = FakeShare(back="merged", dirs={"/": ["data"], "/data": ["返回上一级>全部文件/x"]})
    with pytest.raises(StepFailed) as exc:
        save_picker(picker_screen(fake, "/data"))
    assert exc.value.code == "save_picker_crumb_not_unique"
