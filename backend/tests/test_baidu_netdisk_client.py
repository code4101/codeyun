"""NetdiskClient against a scripted fake of the projection/OCR seam (no GUI input)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from backend.core.baidu_netdisk import TransferLedger
import json

from backend.core.baidu_netdisk.client import (
    DOWNLOAD_BUTTON_RE, FILE_DIALOG_TITLE, NetdiskClient, Screen, TransferResult, breadcrumb, context_menu_item,
    StepFailed, read_quota, list_rows, parse_share_link, blank_band_right_of_crumb, context_menu_column, header_checkbox, inspect_client, list_header, loaded_count, row_mtime_cell, screen_path, selection_count, selection_evidence, toolbar_download, top_tab_home,
)

W, H = 1213, 864


def line(text, x, y, w=80, h=20):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


class FakeClient:
    """Minimal official-client model: remote tree, file list, native dialog."""

    def __init__(self, tmp: Path, *, dirs=None, merged_crumbs=False, row_click_sets_value=True,
                 enter_closes_dialog=False):
        self.dirs = dirs or {"/": ["新建文件夹"], "/新建文件夹": []}
        self.cwd = "/"
        self.dialog = None  # {"cwd":..., "value":...}
        self.selected = None
        self.menu = None  # (x, y) of an open in-page context menu
        self.selected_count = 1
        self.checkbox_works = True  # real 8.1.5.103 header checkbox: partial -> all -> none
        self.loaded_marker = True
        self.menu_top = 195  # first menu item y; 120 puts 下载 above the list header (upward menu)
        self.blank_click_closes_menu = True
        self.inputs: list[dict] = []
        self.merged = merged_crumbs
        self.row_click_sets_value = row_click_sets_value
        self.enter_closes_dialog = enter_closes_dialog
        self.download_dir = tmp / "client-dl"
        self.download_dir.mkdir()
        self.remote_bytes = {"/新建文件夹/probe.txt": b"remote-content"}
        self.submitted = []
        self.page = "file"
        self.last_lines = []
        self.selection_size = None  # e.g. a kept multi-selection total
        self.schemes: list[str] = []
        self.scheme_window = False
        self.root_home_mark = True  # 8.1.5.103 root home: recent-files strip, no crumb
        self.root_home_delay = 0  # observations of the root before the recent-files strip renders
        self.editing = None  # index of the inline new-folder row being named
        self.quota_label = "1.8T / 11.1T"  # real 8.1.5.103 top bar
        self.visible_rows = None  # e.g. 3: only that many list rows render; scrolling moves the window
        self.scroll_offset = 0
        self.new_folder_delay = 0
        self.folder_row_sets_value = True
        self.share = None  # open 「分享： <name>」 window: {"name", "days", "created"}
        self.shares: list[str] = []  # 有效期 label of every link created
        self.share_link_text = "https://pan.baidu.com/s/1AbCdEf-gh?pwd=x7k2"
        self.clipboard = "old clipboard"
        self.share_copies_to_clipboard = False  # dialog: a folder row fills the name field (folder upload)  # observations before the inline row renders
        self.root_list_view = False  # 全部文件 root list: no crumb band, header right under the toolbar
        self.merged_header = False  # OCR joins 文件 with the marker on its row
        self.underscore_as_space = False  # OCR reads '_' as ' '
        self.new_folder_works = True
        self.inline_selected = False  # 8.1.5.103: the open edit row is selected (已选中1…) and shows 修改时间 "-"
        self.inline_split = False  # OCR splits the highlighted default name into tight fragments
        self.empty_cta = False  # hypothetical empty-folder call to action repeating 新建文件夹 in the list body
        self.crumb_view = None  # parts -> crumb segments as OCR reads them (e.g. an extra space)

    # seam -----------------------------------------------------------------------
    def list_windows(self):
        rows = [{"id": "m", "application": "BaiduNetdiskUnite.exe", "title": "百度网盘", "width": W, "height": H},
                {"id": "x", "application": "notepad.exe", "title": "x", "width": W, "height": H}]
        if self.scheme_window:
            rows.append({"id": "s", "application": "BaiduNetdiskUnite.exe", "title": "分享", "width": 400, "height": 300})
        if self.share is not None:
            rows.append({"id": "sw", "application": "BaiduNetdiskUnite.exe", "title": f"分享： {self.share['name']}",
                         "width": 782, "height": 575})
        if self.dialog is not None:
            rows.append({"id": "d", "application": "BaiduNetdiskUnite.exe", "title": FILE_DIALOG_TITLE, "width": 567, "height": 435})
        return rows

    def open_scheme(self, url):
        self.schemes.append(url)

    def activate(self, window_id):
        return {"activated": True}

    def frame(self, window_id):
        rows = len(self.dirs.get(self.cwd, []))
        state = "empty" if not self.selected else ("all" if self.selected_count >= rows else "partial")
        return {"window_id": window_id, "width": W, "height": H, "input_token": "tok", "token": "f", "full": True,
                "patches": [{"x": 0, "y": 0, "width": W, "height": H, "image": checkbox_frame_url(state)}]}

    def ocr(self, frame):
        lines = self.dialog_lines() if frame["window_id"] == "d" else (
            self.share_lines() if frame["window_id"] == "sw" else self.main_lines())
        for i, item in enumerate(lines):
            item["line_id"] = f"l{i}"
            for token in item.pop("_tokens", []):
                token["parent_line_id"] = item["line_id"]
        self.last_lines = lines
        tokens = [t for item in lines for t in item.get("_kept", [])]
        return {"lines": lines, "tokens": tokens}

    def crumb_line(self, parts):
        """Layout of client 8.1.5.103: one merged OCR line "全部文件/a/b" with per-char tokens."""
        text = "/".join(["全部文件", *parts])
        tokens, x, spans = [], 112, []
        for seg_index, seg in enumerate(["全部文件", *parts]):
            if seg_index:
                tokens.append({"text": "/", "x": x + 8, "y": 228, "w": 2, "h": 14, "order": len(tokens)})
                x += 12
            start = x
            for ch in seg:
                tokens.append({"text": ch, "x": x, "y": 228, "w": 15, "h": 14, "order": len(tokens)})
                x += 14
            spans.append((start, x, seg_index))
        item = line(text, 112, 228, x - 112, 14)
        item["_kept"] = tokens
        item["_tokens"] = tokens
        item["_spans"] = spans
        return item

    def main_lines(self):
        if self.page == "share_tab" or (self.page == "file" and getattr(self, "own_view_delay", 0) > 0):
            if self.page == "file":
                self.own_view_delay -= 1  # own tab still repainting after the top-tab click
            # 资源分享/extraction tab: top tabs only, no side nav, no list header.
            return [line(t, x, y, w, h) for t, x, y, w, h in getattr(self, "share_tab_lines", None) or
                    [("首页", 25, 7, 33, 11), ("资源分享", 80, 7, 60, 11), ("请输入提取码", 500, 400, 120, 16)]]
        if self.page == "transfer":
            return [line("首页", 65, 10, 32, 19), line("首页", 12, 150, 30, 16), line("传输", 17, 211, 28, 16),
                    line("上传中(0)", 300, 120), line("已完成(1)", 400, 120)]
        parts = [p for p in self.cwd.split("/") if p]
        lines = [line("首页", 65, 10, 32, 19), line("全部", 110, 119, 33, 19), line("上传", 126, 171, 58, 21),
                 line("会员中心", 938, 56, 58, 17), line(self.quota_label, 1013, 56, 61, 14), line("¥0.04/G起", 1077, 57, 62, 11),
                 line("新建文件夹", 244, 173, 76, 17), line("传输", 17, 211, 28, 16), line("消息", 15, 349, 31, 19),
                 line("文件", 140, 274, 35, 18), line("大小", 801, 274, 31, 19), line("类型", 932, 274, 31, 19),
                 line("修改时间", 1095, 275, 64, 17)]
        if parts:
            shown = self.crumb_view(parts) if self.crumb_view else parts
            lines.append(self.crumb_line(shown) if self.merged else line("全部文件", 112, 228, 57, 14))
            if not self.merged:
                x = 112 + 57
                for p in shown:
                    lines.append(line(p, x + 20, 228, 15 * len(p), 14))
                    x += 20 + 15 * len(p)
        else:
            # Root home: recent-files strip above the header may repeat a file name.
            late = self.root_home_delay > 0
            self.root_home_delay -= late
            lines += [line("最近文件", 112, 228, 60, 14)] if self.root_home_mark and not late else []
            lines += [line(n, 189, 250, 150, 14) for n in self.dirs["/"][:1]]
        if self.selected:
            size = self.selection_size if self.selection_size is not None else len(
                self.remote_bytes.get(self.cwd.rstrip("/") + "/" + self.selected, b""))
            label = f"下载({size}B)" if size < 1024 else f"下载({size / 1024:.1f}K)"
            lines += [line(label, 785, 173, 90, 17), line(f"已选中{self.selected_count}个文件/文件夹", 205, 277, 141, 14)]
        elif self.editing is not None and self.inline_selected:
            lines.append(line("已选中1个文件/文件夹", 205, 277, 141, 14))
        elif self.loaded_marker and self.page == "file":
            lines.append(line(f"已全部加载，共{len(self.dirs.get(self.cwd, []))}个", 205, 277, 120, 14))
        hide = None
        if self.editing is not None and self.new_folder_delay > 0:
            hide, self.new_folder_delay = self.editing, self.new_folder_delay - 1
        names = self.dirs.get(self.cwd, [])
        window = range(len(names)) if self.visible_rows is None else \
            range(self.scroll_offset, min(len(names), self.scroll_offset + self.visible_rows))
        for i in window:
            name = names[i]
            y = 333 + 61 * (i - window.start)
            if i != hide:
                child = self.cwd.rstrip("/") + "/" + name
                folder = child in self.dirs
                size = len(self.remote_bytes.get(child, b""))
                label = "-" if folder else (f"{size}B" if size < 1024 else f"{size / 1024:.2f}KB")
                if i == self.editing and self.inline_split and name.startswith("新建文件夹"):
                    lines += [line(name[:2], 189, y, 30, 14), line(name[2:], 221, y, 15 * len(name[2:]), 14)]
                else:
                    lines.append(dict(line(name, 189, y, 15 * len(name), 14), _name=name))
                lines += [line(label, 806, y, 8 * len(label), 14), line("文件夹" if folder else "TXT", 932, y, 45, 14)]
                if i != self.editing:  # the inline edit row has no 修改时间 yet (8.1.5.103)
                    lines.append(dict(line("2026.10.16 16:06", 1066, y, 101, 14), _row=name))
                elif self.inline_selected:
                    lines.append(line("-", 1066, y, 8, 14))
        if not names and self.empty_cta and self.page == "file":
            lines += [line("上传文件", 520, 500, 64, 16), line("新建文件夹", 620, 500, 76, 16)]
        if self.menu:  # 8.1.5.103 opens it leftwards, in one column, from above the header
            items = ["打开", "打开方式", "文档历史版本", "本地编辑", "下载", "分享", "复制", "剪切", "重命名", "删除"]
            lines += [line(text, 1055, self.menu_top + 36 * i, 40, 18) for i, text in enumerate(items)]
        return self._ocr_quirks(lines, root=not parts)

    def _ocr_quirks(self, lines, *, root):
        if self.root_list_view and root:
            lines = [item for item in lines if item["text"] != "最近文件" and item["y"] != 250]
            for item in lines:
                if 270 <= item["y"] <= 280:
                    item["y"] = 228
        if self.merged_header:
            hy = 228 if self.root_list_view and root else 274
            head = next(item for item in lines if item["text"] == "文件" and abs(item["y"] - hy) < 6)
            mark = [item for item in lines if abs(item["y"] - hy) < 6 and item["text"].startswith(("已全部加载", "已选中"))]
            if mark:
                lines.remove(mark[0])
                head["text"], head["w"] = "文件" + mark[0]["text"], mark[0]["x"] + mark[0]["w"] - head["x"]
        if self.underscore_as_space:
            for item in lines:
                item["text"] = item["text"].replace("_", " ")
                for token in item.get("_tokens", []):
                    token["text"] = token["text"].replace("_", " ")
        return lines

    def dialog_lines(self):
        lines = [line("文件名(N):", 20, 368, 90), line("存入百度网盘", 450, 369, 100), line("取消", 485, 393, 30)]
        if self.dialog["value"]:
            lines.append(line(self.dialog["value"], 150, 368, 200))
        cwd = self.dialog["cwd"]
        if cwd and Path(cwd).is_dir():
            lines += [line(p.name, 220, 100 + 21 * i, 200) for i, p in enumerate(sorted(Path(cwd).iterdir()))]
        return lines

    def input(self, frame, **action):
        self.inputs.append({"window": frame["window_id"], **action})
        kind = action["action"]
        hit = None
        if "x" in action and kind in {"click", "double_click"}:
            px, py = action["x"] * W, action["y"] * H
            for item in self.last_lines:
                if item["x"] <= px <= item["x"] + item["w"] and item["y"] <= py <= item["y"] + item["h"]:
                    hit = item.get("_row", item["text"]) if action.get("button") == "right" else item.get("_name", item["text"])
        if frame["window_id"] == "m":
            px = action.get("x", 0) * W
            crumb = next((item for item in self.last_lines if "_spans" in item and item["text"] == hit), None)
            if crumb is not None:  # clicking a merged crumb lands on whichever segment is under the point
                seg = next(i for start, end, i in crumb["_spans"] if start <= px <= end + 12)
                self.cwd = "/" + "/".join([p for p in self.cwd.split("/") if p][:seg])
                return {"accepted": True}
            self._main(kind, hit, action)
        elif frame["window_id"] == "sw":
            self._share(kind, hit)
        else:
            self._dialog(kind, hit, action)
        return {"accepted": True}

    def clipboard_text(self):
        return self.clipboard

    def close_window(self, window_id, expected_title):
        if window_id == "sw" and self.share and expected_title == f"分享： {self.share['name']}":
            self.share = None
            return True
        return False

    def share_lines(self):
        if self.share["created"]:
            return [line("链接分享", 46, 18, 68, 22), line(self.share_link_text, 46, 120, 420, 18),
                    line("提取码：x7k2", 46, 160, 100, 18), line("复制链接", 640, 484, 80, 18)]
        return [line("链接分享", 46, 18, 68, 22), line("有效期：", 44, 129, 54, 18), line("1天", 203, 130, 29, 20),
                line("7天", 294, 129, 28, 21), line("30天", 386, 130, 34, 19), line("1年", 483, 130, 29, 20),
                line("永久有效", 576, 131, 55, 18), line("提取码：", 44, 187, 55, 22), line("随机生成", 203, 186, 57, 22),
                line("自定义", 324, 185, 44, 23), line("生成二维码", 518, 484, 92, 17), line("②创建链接", 665, 484, 79, 18)]

    def _share(self, kind, hit):
        if kind == "click" and hit in {"1天", "7天", "30天", "1年", "永久有效"}:
            self.share["days"] = hit
        elif kind == "click" and hit and hit.endswith("创建链接"):
            self.share["created"] = True
            self.shares.append(self.share["days"])
            if self.share_copies_to_clipboard:
                self.clipboard = (f"通过网盘分享的文件：{self.share['name']}\n链接: https://pan.baidu.com/s/1ySpxdsAqZeApC4nvEZCAJw"
                                  "?pwd=hhmg 提取码: hhmg \n--来自百度网盘超级会员v5的分享")

    def _main(self, kind, hit, action):
        if kind == "click" and hit == "分享" and self.menu:
            self.share, self.menu = {"name": self.selected, "days": "1年", "created": False}, None
            return
        rows = self.dirs.setdefault(self.cwd, [])
        if self.editing is not None and kind in {"type", "key"}:
            if kind == "type":
                rows[self.editing] = action["text"]
            elif action["key"] == "enter":
                name = rows.pop(self.editing)  # renamed: re-sorts to its place (here: the end)
                rows.append(name)
                self.editing = None
                self.dirs.setdefault(self.cwd.rstrip("/") + "/" + name, [])
            return
        if kind == "scroll":
            if self.visible_rows is not None:
                step = 1 if action["delta"] < 0 else -1
                self.scroll_offset = max(0, min(len(rows) - self.visible_rows, self.scroll_offset + step))
            return
        if kind == "click" and hit == "新建文件夹" and action["y"] * H < 250:
            if self.new_folder_works:  # the inline row opens at the top of the list
                rows.insert(0, next(n for n in ["新建文件夹", *(f"新建文件夹({k})" for k in range(1, 9))] if n not in rows))
                self.editing = 0
                self.scroll_offset = 0
            return
        if kind == "click" and hit == "首页" and self.page == "share_tab" and action["y"] * H < 50:
            self.page = "file"  # the top tab returns to the own (main) tab
        elif kind == "click" and hit == "首页" and action["y"] * H > 100:
            self.page = "file"
        elif kind == "click" and hit == "首页":
            self.page = "top_tab"  # wrong target: the top tab, not the side nav
        elif kind == "click" and hit == "全部文件":
            self.cwd = "/"
        elif kind == "double_click" and hit:
            self.cwd = (self.cwd.rstrip("/") + "/" + hit)
        elif kind == "click" and hit == "上传":
            self.dialog = {"cwd": None, "value": ""}
        elif kind == "click" and hit is None and action.get("button") == "left" and 220 <= action["y"] * H <= 250:
            if self.blank_click_closes_menu:  # blank breadcrumb row: dismisses the menu, keeps the selection
                self.menu = None
        elif kind == "click" and action.get("button") == "left" and 112 <= action["x"] * W <= 127 \
                and 276 <= action["y"] * H <= 291:  # the header checkbox (measured box)
            rows = self.dirs.get(self.cwd, [])
            if not self.checkbox_works:
                return
            if self.selected and self.selected_count < len(rows):  # partial -> all
                self.selected_count, self.menu = len(rows), None
                self.selection_size = sum(len(self.remote_bytes.get(self.cwd.rstrip("/") + "/" + n, b"")) for n in rows)
            else:  # all -> none
                self.selected = self.menu = self.selection_size = None
                self.selected_count = 1
        elif kind == "click" and action.get("button") == "right" and hit:
            self.selected, self.menu = hit, (action["x"] * W, action["y"] * H)
        elif kind == "click" and hit and (hit.startswith("下载(") or (hit == "下载" and self.menu)):
            remote = self.cwd.rstrip("/") + "/" + self.selected
            if remote in self.dirs:  # folder download: the client recreates the tree under its own dir
                for path, data in self.remote_bytes.items():
                    if path.startswith(remote + "/"):
                        target = self.download_dir / self.selected / path[len(remote) + 1:]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
            else:
                (self.download_dir / self.selected).write_bytes(self.remote_bytes[remote])
            self.menu = None

    def _dialog(self, kind, hit, action):
        d = self.dialog
        if kind == "type":
            d["value"] = action["text"]
        elif kind == "key" and action["key"] == "enter":
            if self.enter_closes_dialog:
                self.dialog = None
            elif Path(d["value"]).is_dir():
                d["cwd"], d["value"] = d["value"], ""
        elif kind == "click" and hit == "存入百度网盘":
            self.submitted.append(d["value"])
            self.dirs.setdefault(self.cwd, []).append(d["value"])
            local = Path(d["cwd"] or ".") / d["value"]
            if d["cwd"] and local.is_dir():  # folder upload: the whole tree appears remotely
                base = self.cwd.rstrip("/") + "/" + d["value"]
                self.dirs.setdefault(base, [])
                for item in sorted(local.rglob("*")):
                    rel = item.relative_to(local).as_posix()
                    parent = base + ("/" + rel.rsplit("/", 1)[0] if "/" in rel else "")
                    self.dirs.setdefault(parent, []).append(item.name)
                    if item.is_dir():
                        self.dirs.setdefault(base + "/" + rel, [])
                    else:
                        self.remote_bytes[base + "/" + rel] = item.read_bytes()
            self.dialog = None
        elif kind == "click" and hit == "取消":
            self.dialog = None
        elif kind == "click" and hit and d["cwd"] and self.row_click_sets_value and (
                (Path(d["cwd"]) / hit).is_file() or (Path(d["cwd"]) / hit).is_dir() and self.folder_row_sets_value):
            d["value"] = hit


FIXTURES = Path(__file__).parent / "fixtures"
_FRAMES: dict[str, str] = {}


def checkbox_frame_url(state: str, *, extra=None) -> str:
    """A 1213x864 white frame carrying the real header-checkbox crop (partial/all/empty) at its measured place."""
    if extra is None and state in _FRAMES:
        return _FRAMES[state]
    import base64
    import io
    from PIL import Image
    image = Image.new("RGB", (W, H), "white")
    image.paste(Image.open(FIXTURES / f"baidu_netdisk_header_checkbox_{state}.png").convert("RGB"), (96, 264))
    if extra:
        extra(image)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    if extra is None:
        _FRAMES[state] = url
    return url


def make(tmp_path: Path, fake: FakeClient, lease_check=lambda: None):
    return NetdiskClient(ledger=TransferLedger(tmp_path / "state" / "ledger.json"), lease_check=lease_check,
                         driver=fake, settle=0, sleep=lambda _s: None, max_scrolls=1, poll_attempts=3)


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "codeyun-transfer-probe.txt").write_bytes(b"x" * 1091)
    (src / "other.txt").write_bytes(b"o")
    return src / "codeyun-transfer-probe.txt"


@pytest.mark.parametrize("merged", [False, True])
def test_upload_navigates_selects_row_and_submits_once(tmp_path, source, merged):
    fake = FakeClient(tmp_path, merged_crumbs=merged)
    result = make(tmp_path, fake).upload_file(source, "/新建文件夹")
    assert result.status == "remote_present", result.to_dict()
    assert result.facts["content_verified"] is False and result.facts["source_sha256"]
    assert fake.submitted == ["codeyun-transfer-probe.txt"]
    typed = [i["text"] for i in fake.inputs if i["action"] == "type"]
    assert typed == [str(source.parent) + os.sep]  # directory, never the full file path
    confirm_clicks = [i for i in fake.inputs if i["window"] == "d" and i["action"] == "click" and abs(i["x"] * W - 500) < 2]
    assert len(confirm_clicks) == 1
    assert TransferLedger(tmp_path / "state" / "ledger.json").get(result.key)["state"] == "verified"
    again = make(tmp_path, fake).upload_file(source, "/新建文件夹")
    assert again.status == "already_remote_present" and again.to_dict()["content_verified"] is False
    assert again.to_dict()["verification"] == "remote_present" and "record" not in again.to_dict()
    assert fake.submitted == ["codeyun-transfer-probe.txt"]


def test_upload_without_name_field_evidence_never_confirms(tmp_path, source):
    fake = FakeClient(tmp_path, row_click_sets_value=False)
    result = make(tmp_path, fake).upload_file(source, "/新建文件夹")
    assert result.status == "not_submitted" and result.facts["reason"] == "file_name_value_mismatch"
    assert fake.submitted == []
    record = TransferLedger(tmp_path / "state" / "ledger.json").get(result.key)
    assert record["state"] == "failed"  # pre-submit failure: retry allowed


def test_dialog_closing_on_directory_enter_is_reported(tmp_path, source):
    fake = FakeClient(tmp_path, enter_closes_dialog=True)
    result = make(tmp_path, fake).upload_file(source, "/新建文件夹")
    assert result.status == "state_unknown" and result.facts["reason"] == "dialog_closed_after_directory_enter"
    assert TransferLedger(tmp_path / "state" / "ledger.json").get(result.key)["state"] == "submitted"
    assert fake.submitted == []


def test_missing_remote_dir_and_existing_remote_name_stop(tmp_path, source):
    fake = FakeClient(tmp_path)
    assert make(tmp_path, fake).upload_file(source, "/不存在").facts["reason"] == "remote_dir_not_found"
    (tmp_path / "b").mkdir()
    fake2 = FakeClient(tmp_path / "b", dirs={"/": ["新建文件夹"], "/新建文件夹": ["codeyun-transfer-probe.txt"]})
    r = make(tmp_path / "b", fake2).upload_file(source, "/新建文件夹")
    assert r.facts["reason"] == "remote_name_exists" and fake2.dialog is None


def test_lost_lease_sends_no_input(tmp_path, source):
    fake = FakeClient(tmp_path)

    def lost():
        raise RuntimeError("lease_lost")

    result = make(tmp_path, fake, lease_check=lost).upload_file(source, "/新建文件夹")
    assert result.status == "not_submitted" and fake.inputs == []


def test_download_selects_by_right_click_and_collects_with_hash(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["新建文件夹"], "/新建文件夹": ["probe.txt"]})
    data = fake.remote_bytes["/新建文件夹/probe.txt"]
    biz = tmp_path / "biz"
    result = make(tmp_path, fake).download_file("/新建文件夹/probe.txt", biz, client_download_dir=fake.download_dir,
                                                 expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest())
    assert result.status == "downloaded", result.to_dict()
    assert (biz / "probe.txt").read_bytes() == data
    kinds = [(i["action"], i.get("button"), i.get("key")) for i in fake.inputs]
    # Navigate, one right-click on the row, one click on the context menu's 下载; never Escape.
    assert kinds[-2:] == [("click", "right", None), ("click", "left", None)]
    assert ("key", None, "escape") not in kinds
    right, submit = fake.inputs[-2:]
    assert 1066 <= right["x"] * W <= 1167 and abs(right["y"] * H - 340) < 2  # the row's 修改时间 cell
    assert submit["y"] * H > 292 and abs(submit["x"] * W - 1075) < 2  # leftward menu's 下载, not the toolbar
    record = TransferLedger(tmp_path / "state" / "ledger.json").get(result.key)
    assert record["state"] == "verified" and Path(record["facts"]["manifest"]).exists()


def test_merged_crumb_root_click_uses_token_span_not_line_center(tmp_path, source):
    fake = FakeClient(tmp_path, merged_crumbs=True,
                      dirs={"/": ["新建文件夹", "other"], "/新建文件夹": [], "/other": []})
    fake.cwd = "/新建文件夹"
    result = make(tmp_path, fake).upload_file(source, "/other")
    assert result.status == "remote_present", result.to_dict()
    root_click = fake.inputs[0]
    assert 112 <= root_click["x"] * W <= 169 and fake.dirs["/other"] == ["codeyun-transfer-probe.txt"]


def test_transfer_page_goes_home_via_side_nav_not_top_tab(tmp_path, source):
    fake = FakeClient(tmp_path, merged_crumbs=True)
    fake.page = "transfer"
    result = make(tmp_path, fake).upload_file(source, "/新建文件夹")
    assert result.status == "remote_present", result.to_dict()
    assert fake.inputs[0]["y"] * H > 100  # side nav 首页, not the y=10 top tab


def test_share_tab_goes_home_via_unique_top_tab_and_polls_own_view(tmp_path):
    fake = FakeClient(tmp_path, merged_crumbs=True)
    fake.page, fake.own_view_delay = "share_tab", 2  # own view renders two observations late
    _, screen = make(tmp_path, fake).open_remote_dir("/新建文件夹")
    assert screen_path(screen) == ["新建文件夹"]
    top = fake.inputs[0]
    assert top["action"] == "click" and top["y"] * H < 50 and top["x"] * W < 60
    assert [i["action"] for i in fake.inputs] == ["click", "double_click"]  # one home click, no retries


EX, EX_SPACED = "Example01示例", "Example01 示例"


def spaced_crumb(parts):
    """OCR of the opened crumb adds one ASCII space inside Example01示例, every other character equal."""
    return [EX_SPACED if p == EX else p for p in parts]


@pytest.mark.parametrize("merged", [False, True])
def test_exact_crumb_still_opens_without_variant(tmp_path, merged):
    fake = FakeClient(tmp_path, merged_crumbs=merged, dirs={"/": [EX, "b"], f"/{EX}": [], "/b": []})
    _, screen = make(tmp_path, fake).open_remote_dir(f"/{EX}")
    assert screen_path(screen) == [EX]
    assert [i["action"] for i in fake.inputs] == ["double_click"]  # one open from root, no extra scroll


@pytest.mark.parametrize("merged", [False, True])
def test_space_only_crumb_variant_after_unique_open_in_complete_parent(tmp_path, merged):
    fake = FakeClient(tmp_path, merged_crumbs=merged, dirs={"/": [EX, "b"], f"/{EX}": [], "/b": []})
    fake.crumb_view = spaced_crumb
    _, screen = make(tmp_path, fake).open_remote_dir(f"/{EX}")
    assert screen_path(screen) == [EX_SPACED] and fake.cwd == f"/{EX}"
    assert [i["action"] for i in fake.inputs] == ["double_click"]


@pytest.mark.parametrize("merged", [False, True])
def test_nested_child_verifies_under_carried_canonical_parent(tmp_path, merged):
    dirs = {"/": ["P"], "/P": [EX, "z"], f"/P/{EX}": ["Sub", "Sub2"], f"/P/{EX}/Sub": ["Leaf"],
            f"/P/{EX}/Sub/Leaf": [], f"/P/{EX}/Sub2": [], "/P/z": []}
    fake = FakeClient(tmp_path, merged_crumbs=merged, dirs=dirs)
    fake.crumb_view = spaced_crumb
    _, screen = make(tmp_path, fake).open_remote_dir(f"/P/{EX}/Sub/Leaf")
    assert screen_path(screen) == ["P", EX_SPACED, "Sub", "Leaf"] and fake.cwd == f"/P/{EX}/Sub/Leaf"


def test_space_fold_collision_in_parent_is_refused(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": [EX, EX_SPACED], f"/{EX}": [], f"/{EX_SPACED}": []})
    fake.crumb_view = spaced_crumb
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir(f"/{EX}")
    assert err.value.code == "folder_not_verified" and err.value.facts["variant"] == "name_fold_collision"


def test_underscore_fold_collision_in_parent_is_refused(tmp_path):
    other = "Example01_示例"
    fake = FakeClient(tmp_path, dirs={"/": [EX, other], f"/{EX}": [], f"/{other}": []})
    fake.crumb_view = spaced_crumb
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir(f"/{EX}")
    assert err.value.facts["variant"] == "name_fold_collision"


@pytest.mark.parametrize("trait", ["no_marker", "rows_unread"])
def test_incomplete_parent_listing_leaves_variant_unproven(tmp_path, trait):
    names = [EX, "b", "c", "d"]
    fake = FakeClient(tmp_path, dirs={"/": names, **{f"/{n}": [] for n in names}})
    fake.crumb_view = spaced_crumb
    if trait == "no_marker":
        fake.loaded_marker = False
    else:
        fake.visible_rows = 2  # the row is on the first frame; c and d are never read
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir(f"/{EX}")
    assert err.value.code == "folder_not_verified" and err.value.facts["variant"] == "parent_listing_incomplete"
    assert [i["action"] for i in fake.inputs] == ["double_click"]  # nothing after the unproven open


@pytest.mark.parametrize("view, variant", [
    (lambda parts: ["Q", *parts[1:]] if len(parts) == 2 else parts, "parent_not_verified"),  # wrong parent
    (lambda parts: [*parts[:-1], "Example02 示例"] if len(parts) == 2 else parts, "not_space_variant"),  # wrong text
    (lambda parts: [*parts[:-1], "Example01.示例"] if len(parts) == 2 else parts, "not_space_variant"),  # no dot fold
    (lambda parts: [*parts[:-1], "Example01 示"] if len(parts) == 2 else parts, "not_space_variant"),  # no prefix
    (lambda parts: [*parts, "x"] if len(parts) == 2 else parts, "parent_not_verified"),  # extra segment
])
def test_wrong_parent_or_text_after_open_is_refused(tmp_path, view, variant):
    fake = FakeClient(tmp_path, dirs={"/": ["P"], "/P": [EX], f"/P/{EX}": []})
    fake.crumb_view = view
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir(f"/P/{EX}")
    assert err.value.code == "folder_not_verified" and err.value.facts["variant"] == variant


def test_space_variant_never_widens_list_name_match(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": [EX_SPACED], f"/{EX_SPACED}": []})
    with pytest.raises(StepFailed) as err:  # the row lookup itself stays exact
        make(tmp_path, fake).open_remote_dir(f"/{EX}")
    assert err.value.code == "remote_dir_not_found"


DEEP = {"/": ["P"], "/P": [EX, "z"], f"/P/{EX}": ["Sub", "Sub2"], f"/P/{EX}/Sub": ["Leaf"],
        f"/P/{EX}/Sub/Leaf": [], f"/P/{EX}/Sub2": [], "/P/z": []}


def elided_crumb(parts, mark=".", tail=None):
    """Measured shape: depth >= 3 shows "全部文件/. /<parent>/<child>" with the parent's OCR space dropped."""
    if len(parts) < 3:
        return spaced_crumb(parts)
    return [mark, *(tail(parts) if tail else parts[-2:])]


def deep_fail(tmp_path, view, target=f"/P/{EX}/Sub", **traits):
    fake = FakeClient(tmp_path, dirs={k: list(v) for k, v in DEEP.items()})
    fake.crumb_view = view
    for key, value in traits.items():
        setattr(fake, key, value)
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir(target)
    assert err.value.code == "folder_not_verified"
    return fake, err.value.facts["variant"]


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("mark, tail", [(".", None), ("…", None), (".", lambda parts: [EX_SPACED, parts[-1]])])
def test_elided_deep_crumb_proven_from_full_parent_after_unique_open(tmp_path, merged, mark, tail):
    fake = FakeClient(tmp_path, merged_crumbs=merged, dirs={k: list(v) for k, v in DEEP.items()})
    fake.crumb_view = lambda parts: elided_crumb(parts, mark, tail)
    _, screen = make(tmp_path, fake).open_remote_dir(f"/P/{EX}/Sub")
    assert screen_path(screen)[0] == mark and screen_path(screen)[-1] == "Sub" and fake.cwd == f"/P/{EX}/Sub"
    assert [i["action"] for i in fake.inputs] == ["double_click"] * 3  # navigation only, no extra input


def test_elided_crumb_chains_to_deeper_level(tmp_path):
    fake = FakeClient(tmp_path, dirs={k: list(v) for k, v in DEEP.items()})
    fake.crumb_view = elided_crumb
    _, screen = make(tmp_path, fake).open_remote_dir(f"/P/{EX}/Sub/Leaf")
    assert screen_path(screen) == [".", "Sub", "Leaf"] and fake.cwd == f"/P/{EX}/Sub/Leaf"


def test_elided_crumb_never_short_circuits_the_full_target(tmp_path):
    fake = FakeClient(tmp_path, dirs={k: list(v) for k, v in DEEP.items()})
    fake.crumb_view = elided_crumb
    client = make(tmp_path, fake)
    client.open_remote_dir(f"/P/{EX}/Sub")
    fake.inputs.clear()
    client.open_remote_dir(f"/P/{EX}/Sub")  # an elided crumb is no full path: back to root, re-proven
    assert [i["action"] for i in fake.inputs] == ["click", "double_click", "double_click", "double_click"]


@pytest.mark.parametrize("tail, variant", [
    (lambda parts: ["Example02示例", parts[-1]], "elided_tail_mismatch"),  # other parent behind the mark
    (lambda parts: ["Example 01示例", parts[-1]], "elided_tail_mismatch"),  # space form never proven for it
    (lambda parts: ["Example01.示例", parts[-1]], "elided_tail_mismatch"),  # no dot folding
    (lambda parts: [EX, "Sub2"], "elided_child_mismatch"),  # wrong row opened
    (lambda parts: [EX, "Sub", "x"], "elided_span_unproven"),  # changed tail: extra segment
    (lambda parts: [parts[-1]], "elided_span_unproven"),  # hidden parent: one visible segment proves nothing
    (lambda parts: ["P", EX, "Sub"], "elided_span_unproven"),  # the mark hides nothing
    (lambda parts: [".", EX, "Sub"], "elision_not_unique"),
    (lambda parts: ["a..b", "Sub"], "elision_not_unique"),  # partial ellipsis beside the mark
])
def test_elided_crumb_wrong_or_ambiguous_tail_is_refused(tmp_path, tail, variant):
    fake, got = deep_fail(tmp_path, lambda parts: elided_crumb(parts, ".", tail))
    assert got == variant
    assert [i["action"] for i in fake.inputs] == ["double_click"] * 3  # nothing after the unproven open


def test_elided_head_must_match_proven_parent(tmp_path):
    view = lambda parts: ["Q", ".", *parts[-2:]] if len(parts) == 4 else elided_crumb(parts)
    _, got = deep_fail(tmp_path, view, target=f"/P/{EX}/Sub/Leaf")
    assert got == "elided_head_mismatch"


def test_elided_crumb_replay_of_parent_state_is_refused(tmp_path):
    # The Leaf open leaves the crumb of Sub on screen: an unchanged crumb proves nothing.
    view = lambda parts: elided_crumb(parts[:3] if len(parts) == 4 else parts)
    _, got = deep_fail(tmp_path, view, target=f"/P/{EX}/Sub/Leaf")
    assert got == "stale_parent_state"


@pytest.mark.parametrize("drift", [False, True])
def test_elided_crumb_needs_listing_read_under_the_parent_crumb(tmp_path, drift):
    dirs = {k: list(v) for k, v in DEEP.items()}
    dirs["/P"], dirs[f"/P/{EX}"] = [EX], ["Sub2", "Sub"]  # Sub only on the second (scrolled) frame
    fake = FakeClient(tmp_path, dirs=dirs)
    fake.visible_rows = 1

    def view(parts):  # drift: the scrolled listing frame no longer shows the proven parent crumb
        if drift and len(parts) == 2 and fake.scroll_offset > 0:
            return ["P", "z"]
        return elided_crumb(parts)
    fake.crumb_view = view
    if not drift:
        make(tmp_path, fake).open_remote_dir(f"/P/{EX}/Sub")
        assert fake.cwd == f"/P/{EX}/Sub"
        return
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir(f"/P/{EX}/Sub")
    assert err.value.facts["variant"] == "stale_parent_state"


@pytest.mark.parametrize("trait", ["no_marker", "collision"])
def test_elided_crumb_needs_complete_fold_free_parent_listing(tmp_path, trait):
    dirs = {k: list(v) for k, v in DEEP.items()}
    traits = {}
    if trait == "no_marker":
        traits["loaded_marker"] = False
    else:
        dirs[f"/P/{EX}"] = ["Sub", "S ub"]
        dirs[f"/P/{EX}/S ub"] = []
    fake = FakeClient(tmp_path, dirs=dirs)
    fake.crumb_view = elided_crumb
    for key, value in traits.items():
        setattr(fake, key, value)
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir(f"/P/{EX}/Sub")
    want = "parent_listing_incomplete" if trait == "no_marker" else "name_fold_collision"
    assert err.value.code == "folder_not_verified" and err.value.facts["variant"] == want


def test_elided_crumb_never_accepts_a_created_row(tmp_path):
    dirs = {k: list(v) for k, v in DEEP.items()}
    dirs[f"/P/{EX}"] = ["Sub2"]
    fake = FakeClient(tmp_path, dirs=dirs)
    fake.crumb_view = elided_crumb
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir(f"/P/{EX}/Sub", create_missing=True)
    assert err.value.facts["variant"] == "created_row"


def test_share_tab_top_home_missing_own_view_fails_after_one_click(tmp_path):
    fake = FakeClient(tmp_path)
    fake.page, fake.own_view_delay = "share_tab", 99
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir("/新建文件夹")
    assert err.value.code == "own_file_view_not_verified" and len(fake.inputs) == 1


@pytest.mark.parametrize("lines, code", [
    ([("首页", 25, 7, 33, 11), ("首页", 300, 7, 33, 11)], "top_tab_home_ambiguous"),
    ([("资源分享", 80, 7, 60, 11), ("首页", 400, 7, 33, 11)], "top_tab_home_not_proven"),  # not leftmost
    ([("资源分享", 80, 7, 60, 11), ("首页", 600, 300, 33, 11), ("首页", 600, 500, 33, 11)], "side_nav_home_not_found"),
    ([("资源分享", 80, 7, 60, 11)], "side_nav_home_not_found"),
])
def test_share_tab_without_proven_top_home_fails_closed_without_input(tmp_path, lines, code):
    fake = FakeClient(tmp_path)
    fake.page, fake.share_tab_lines = "share_tab", lines
    with pytest.raises(StepFailed) as err:
        make(tmp_path, fake).open_remote_dir("/新建文件夹")
    assert err.value.code == code and fake.inputs == []


def test_top_tab_home_accepts_measured_1920_frame():
    screen = Screen({"width": 1920, "height": 1152}, [line("首页", 39, 10, 52, 18), line("资源分享", 120, 10, 70, 18)])
    assert top_tab_home(screen)["x"] == 39
    beside = Screen({"width": 1920, "height": 1152}, [line("首页", 39, 10, 52, 18), line("首页", 900, 30, 52, 18)])
    with pytest.raises(StepFailed, match="top_tab_home_ambiguous"):
        top_tab_home(beside)


def test_create_missing_makes_each_absent_segment_and_skips_existing(tmp_path):
    fake = FakeClient(tmp_path)
    client = make(tmp_path, fake)
    _, screen = client.open_remote_dir("/新建文件夹/a/b", create_missing=True)
    assert client.current_path(screen) == ["新建文件夹", "a", "b"]
    assert client.created_dirs == ["/新建文件夹/a", "/新建文件夹/a/b"]
    assert fake.dirs["/新建文件夹"] == ["a"] and fake.dirs["/新建文件夹/a"] == ["b"]
    typed = [i["text"] for i in fake.inputs if i["action"] == "type"]
    assert typed == ["a", "b"]
    # second run: everything exists, nothing typed
    fake.inputs.clear()
    _, screen = client.open_remote_dir("/新建文件夹/a/b", create_missing=True)
    assert client.created_dirs == [] and not [i for i in fake.inputs if i["action"] in {"type", "key"}]


def test_create_missing_at_root_with_same_named_default_row(tmp_path):
    fake = FakeClient(tmp_path)  # root already holds a folder literally named 新建文件夹
    client = make(tmp_path, fake)
    _, screen = client.open_remote_dir("/m2610skilldependency", create_missing=True)
    assert client.current_path(screen) == ["m2610skilldependency"]
    assert fake.dirs["/"] == ["新建文件夹", "m2610skilldependency"]


def test_create_missing_without_inline_row_types_nothing(tmp_path):
    fake = FakeClient(tmp_path)
    fake.new_folder_works = False
    with pytest.raises(StepFailed) as exc:
        make(tmp_path, fake).open_remote_dir("/x", create_missing=True)
    assert exc.value.code == "new_folder_row_not_verified"
    assert not [i for i in fake.inputs if i["action"] in {"type", "key"}]


def test_missing_dir_without_create_flag_still_stops(tmp_path):
    fake = FakeClient(tmp_path)
    with pytest.raises(StepFailed) as exc:
        make(tmp_path, fake).open_remote_dir("/x")
    assert exc.value.code == "remote_dir_not_found" and fake.dirs["/"] == ["新建文件夹"]


def test_result_status_is_not_overridden_by_collect_facts():
    data = TransferResult("downloaded", "done", "k", {"status": "collected", "next": "x"}).to_dict()
    assert data["status"] == "downloaded" and data["next"] == "done"


def test_download_reports_public_status_and_collect_status(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["新建文件夹"], "/新建文件夹": ["probe.txt"]})
    data = fake.remote_bytes["/新建文件夹/probe.txt"]
    result = make(tmp_path, fake).download_file("/新建文件夹/probe.txt", tmp_path / "biz", client_download_dir=fake.download_dir,
                                                 expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest())
    out = result.to_dict()
    assert out["status"] == "downloaded" and out["collect_status"] == "collected" and out["content_verified"] is True


REAL = Path(__file__).parent / "fixtures" / "baidu_netdisk_file_view_ocr.json"


def real_screen() -> Screen:
    doc = json.loads(REAL.read_text(encoding="utf-8"))
    return Screen({"width": 1213, "height": 864, "window_id": "m", "input_token": "t"}, doc["lines"], doc["tokens"])


def test_real_ocr_header_crumb_span_and_rows():
    screen = real_screen()
    header = list_header(screen)
    assert header["text"] == "文件" and (header["x"], header["y"]) == (140.0, 274.0)
    root_line, segments = breadcrumb(screen, header)
    assert root_line["text"] == "全部文件/新建文件夹" and segments == ["新建文件夹"]
    box = screen.span(root_line, "全部文件")
    assert (box["x"], box["x"] + box["w"]) == (112.0, 169.0)
    assert screen.span(root_line, "全部文") is not None and screen.span(root_line, "部文件/新") is not None
    assert screen.span(root_line, "文件夹X") is None
    rows = screen.find("codeyun-module-probe.txt", below=header["y"] + header["h"], right_of=header["x"] - 60)
    assert [(r["x"], r["y"]) for r in rows] == [(189.0, 333.0)]
    assert screen.find("文件", below=header["y"] + header["h"]) == []


def test_real_ocr_path_is_exact(tmp_path):
    client = make(tmp_path, FakeClient(tmp_path))
    screen = real_screen()
    assert client.current_path(screen) == ["新建文件夹"]
    assert client.current_path(screen) != ["新建"]
    _, row = client._find_in_list("m", screen, "codeyun-transfer-probe.txt")
    assert row["y"] == 394.0


def test_inspect_is_read_only_and_redacts(tmp_path):
    fake = FakeClient(tmp_path, merged_crumbs=True)
    fake.cwd = "/新建文件夹"
    checks = []
    report = inspect_client(fake, lambda: checks.append(1), redact=True, with_tokens=True)
    assert fake.inputs == [] and len(checks) == 1
    (window,) = report["windows"]
    assert window["path"] == "/新建文件夹" and window["list_header"]["text"] == "文件"
    texts = [line["text"] for line in window["lines"]]
    assert "上传" in texts and all("新建文件夹" != t for t in texts if t.startswith("全部文件"))


# ---- prior records, resume, selection, share (experimental) -------------------------

from backend.core.baidu_netdisk.client import _selection_size_matches, artifact_status  # noqa: E402

REMOTE = "/新建文件夹/probe.txt"


def dl_fake(tmp_path):
    return FakeClient(tmp_path, dirs={"/": ["新建文件夹"], "/新建文件夹": ["probe.txt"]})


def legacy(tmp_path, biz, state, **facts):
    """A record written before content expectations joined the key."""
    ledger = TransferLedger(tmp_path / "state" / "ledger.json")
    record, _ = ledger.begin("download", remote_path=REMOTE, local_dir=os.fspath(biz.resolve()))
    ledger.update(record["key"], "submitted", name="probe.txt")
    if state == "verified":
        ledger.update(record["key"], "verified", **facts)
    return record["key"]


def download(tmp_path, fake, biz, data, sha=True, client=None):
    client = client or make(tmp_path, fake)
    return client.download_file(REMOTE, biz, client_download_dir=fake.download_dir, expected_size=len(data),
                                expected_sha256=hashlib.sha256(data).hexdigest() if sha else None)


def test_unfinished_legacy_record_is_never_resubmitted(tmp_path):
    fake, biz = dl_fake(tmp_path), tmp_path / "biz"
    key = legacy(tmp_path, biz, "submitted")
    result = download(tmp_path, fake, biz, fake.remote_bytes[REMOTE])
    assert result.status == "already_submitted" and result.key == key and fake.inputs == []


def test_verified_legacy_record_with_same_hash_answers_from_artifact(tmp_path):
    fake, biz = dl_fake(tmp_path), tmp_path / "biz"
    data = fake.remote_bytes[REMOTE]
    biz.mkdir()
    (biz / "probe.txt").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest().upper()
    legacy(tmp_path, biz, "verified", expected_size=len(data), verification="local_hash",
           target_path=os.fspath(biz / "probe.txt"), sha256=digest)
    result = download(tmp_path, fake, biz, data)
    assert result.status == "already_verified" and result.to_dict()["content_verified"] is True and fake.inputs == []


def test_verified_legacy_record_with_other_size_allows_new_version(tmp_path):
    fake, biz = dl_fake(tmp_path), tmp_path / "biz"
    data = fake.remote_bytes[REMOTE]
    legacy(tmp_path, biz, "verified", expected_size=len(data) + 5, verification="local_size",
           target_path=os.fspath(tmp_path / "old.txt"), sha256="AA")
    result = download(tmp_path, fake, biz, data)
    assert result.status == "downloaded", result.to_dict()
    assert (biz / "probe.txt").read_bytes() == data


def test_verified_legacy_record_without_comparable_facts_needs_review(tmp_path):
    fake, biz = dl_fake(tmp_path), tmp_path / "biz"
    legacy(tmp_path, biz, "verified", verification="local_size", target_path=os.fspath(tmp_path / "old.txt"))
    result = download(tmp_path, fake, biz, fake.remote_bytes[REMOTE])
    assert result.status == "needs_review" and result.facts["reason"] == "prior_expectation_unknown" and fake.inputs == []


def test_hashless_download_then_hashed_request_reuses_proven_artifact(tmp_path):
    fake, biz = dl_fake(tmp_path), tmp_path / "biz"
    data = fake.remote_bytes[REMOTE]
    first = download(tmp_path, fake, biz, data, sha=False)
    assert first.status == "downloaded" and first.facts["content_verified"] is False
    assert artifact_status(TransferLedger(tmp_path / "state" / "ledger.json").get(first.key)).status == "already_downloaded"
    clicks = len(fake.inputs)
    again = download(tmp_path, fake, biz, data)
    assert again.status == "already_verified" and again.key == first.key and len(fake.inputs) == clicks


def test_kept_multi_selection_blocks_download_before_submit(tmp_path):
    fake, biz = dl_fake(tmp_path), tmp_path / "biz"
    fake.selection_size = 4096
    result = download(tmp_path, fake, biz, fake.remote_bytes[REMOTE])
    assert result.status == "not_submitted" and result.facts["reason"] == "download_selection_size_mismatch"
    assert list(fake.download_dir.iterdir()) == []


def test_selection_size_label():
    assert _selection_size_matches("下载(1.1K)", 1091) and _selection_size_matches("下载（14B）", 14)
    assert _selection_size_matches("下载(2.5MB)", int(2.5 * 1024 ** 2))
    assert not _selection_size_matches("下载(2.1K)", 1091) and not _selection_size_matches("下载", 1091)


def test_resume_download_collects_without_gui(tmp_path):
    fake, biz = dl_fake(tmp_path), tmp_path / "biz"
    data = fake.remote_bytes[REMOTE]
    client = make(tmp_path, fake)
    client.poll_attempts = 1  # one observation: candidate seen but not yet stable
    first = download(tmp_path, fake, biz, data, client=client)
    assert first.status == "submitted" and first.next == "resume_download_later_do_not_resubmit", first.to_dict()
    inputs = len(fake.inputs)

    def no_gui():
        raise AssertionError("resume must not touch the GUI")
    resumed = make(tmp_path, fake, lease_check=no_gui).resume_download(first.key)
    assert resumed.status == "downloaded" and resumed.facts["content_verified"] is True, resumed.to_dict()
    assert (biz / "probe.txt").read_bytes() == data and len(fake.inputs) == inputs
    assert make(tmp_path, fake).resume_download(first.key).status == "already_verified"
    assert make(tmp_path, fake).resume_download("nope").status == "unknown_key"


def test_upload_record_status_never_claims_content(tmp_path, source):
    fake = FakeClient(tmp_path)
    result = make(tmp_path, fake).upload_file(source, "/新建文件夹")
    status = artifact_status(TransferLedger(tmp_path / "state" / "ledger.json").get(result.key))
    assert status.status == "already_remote_present" and status.to_dict()["content_verified"] is False
    assert status.next == "download_roundtrip_to_verify_content"


SHARE = "https://pan.baidu.com/s/1AbCdEfGh?pwd=ab12"


def test_share_list_not_visible_opens_scheme_once_and_clicks_nothing(tmp_path):
    fake = dl_fake(tmp_path)
    result = make(tmp_path, fake).download_share(SHARE, "probe.txt", tmp_path / "biz", client_download_dir=fake.download_dir,
                                                 expected_size=14)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_list_not_visible"
    assert len(fake.schemes) == 1 and fake.inputs == [] and "ab12" not in json.dumps(result.to_dict())


def test_share_extra_window_is_reported_before_any_click(tmp_path):
    fake = dl_fake(tmp_path)
    fake.open_scheme = lambda url: setattr(fake, "scheme_window", True)
    result = make(tmp_path, fake).download_share(SHARE, "probe.txt", tmp_path / "biz", client_download_dir=fake.download_dir,
                                                 expected_size=14)
    assert result.facts["reason"] == "share_opened_extra_window" and fake.inputs == []


@pytest.mark.parametrize("name", ["C:evil.txt", "a.txt:stream", "CON", "nul.txt", "x.", " x", "a/b", ".."])
def test_unsafe_item_names_are_rejected_before_ledger(tmp_path, name):
    fake = dl_fake(tmp_path)
    with pytest.raises(ValueError):
        make(tmp_path, fake).download_share(SHARE, name, tmp_path / "biz", client_download_dir=fake.download_dir,
                                            expected_size=1)
    assert not (tmp_path / "state" / "ledger.json").exists() and fake.schemes == []


# ---- root evidence and literal names ------------------------------------------------

def test_root_without_crumb_or_root_home_mark_fails_closed(tmp_path, source):
    fake = FakeClient(tmp_path)
    fake.root_home_mark = False  # e.g. OCR missed the crumb: never assume root
    result = make(tmp_path, fake).upload_file(source, "/新建文件夹")
    assert result.status == "not_submitted" and result.facts["reason"] == "current_path_unknown"
    assert fake.inputs == [] and fake.submitted == []


def test_unparseable_crumb_is_not_root():
    base = [line("文件", 140, 274, 35, 18), line("大小", 801, 274, 31, 19)]
    frame = {"width": W, "height": H}
    twice = Screen(frame, base + [line("全部文件", 112, 228, 57, 14), line("全部文件", 600, 228, 57, 14),
                                  line("最近文件", 112, 200, 60, 14)])
    assert screen_path(twice) is None
    assert screen_path(Screen(frame, base + [line("全部文件", 112, 228, 57, 14)])) == []
    assert screen_path(Screen(frame, base + [line("全部 文件/a b", 112, 228, 120, 14)])) == ["a b"]
    assert screen_path(Screen(frame, base)) is None


@pytest.mark.parametrize("merged", [False, True])
def test_folder_names_keep_internal_spaces(tmp_path, source, merged):
    fake = FakeClient(tmp_path, merged_crumbs=merged, dirs={"/": ["ab", "a b"], "/ab": [], "/a b": []})
    result = make(tmp_path, fake).upload_file(source, "/a b")
    assert result.status == "remote_present", result.to_dict()
    assert fake.dirs["/a b"] == ["codeyun-transfer-probe.txt"] and fake.dirs["/ab"] == []


def test_file_name_with_space_never_selects_spaceless_twin(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["新建文件夹"], "/新建文件夹": ["ab.txt"]})
    fake.remote_bytes["/新建文件夹/ab.txt"] = b"twin"
    result = make(tmp_path, fake).download_file("/新建文件夹/a b.txt", tmp_path / "biz",
                                                client_download_dir=fake.download_dir, expected_size=4)
    assert result.status == "not_submitted" and result.facts["reason"] == "remote_file_not_found"
    assert not any(i.get("button") == "right" for i in fake.inputs) and fake.selected is None


# ---- download selection: one right-click, proven on the next frame -----------------

def selected_folder(tmp_path, **attrs):
    """Client left on /新建文件夹 with a leftover selection (earlier run or upload)."""
    fake = FakeClient(tmp_path, dirs={"/": ["新建文件夹"], "/新建文件夹": ["other.txt", "probe.txt"]})
    fake.remote_bytes["/新建文件夹/other.txt"] = b"other-content-xyz"
    fake.cwd, fake.selected = "/新建文件夹", "other.txt"
    for name, value in attrs.items():
        setattr(fake, name, value)
    return fake


def checkbox_clicks(fake):
    return sum(i["action"] == "click" and i.get("button") == "left" and 112 <= i["x"] * W <= 127
               and 276 <= i["y"] * H <= 291 for i in fake.inputs)


@pytest.mark.parametrize("attrs, clicks", [
    ({}, 2),                                                    # one of two selected: partial -> all -> none
    ({"selected_count": 2, "selection_size": 4096}, 1),          # all selected: one click clears
])
def test_leftover_selection_is_cleared_by_header_checkbox_then_only_target_downloads(tmp_path, attrs, clicks):
    fake = selected_folder(tmp_path, **attrs)
    data = fake.remote_bytes[REMOTE]
    result = download(tmp_path, fake, tmp_path / "biz", data)
    assert result.status == "downloaded", result.to_dict()
    assert checkbox_clicks(fake) == clicks and not any(i["action"] == "key" for i in fake.inputs)
    assert list(fake.download_dir.iterdir()) == [] and sorted(p.name for p in (tmp_path / "biz").iterdir()) == ["probe.txt"]
    assert (tmp_path / "biz" / "probe.txt").read_bytes() == data
    first_right = next(i for i, x in enumerate(fake.inputs) if x.get("button") == "right")
    assert first_right == clicks  # only checkbox clicks precede the right-click


def test_all_selected_is_never_clicked_back_to_all(tmp_path):
    fake = selected_folder(tmp_path, selected_count=2, selection_size=4096, checkbox_works=False)
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.status == "not_submitted" and result.facts["reason"] == "selection_not_cleared"
    assert checkbox_clicks(fake) == 1  # no growth after the first click: no second click


def test_ineffective_checkbox_stops_with_zero_submission(tmp_path):
    fake = selected_folder(tmp_path, checkbox_works=False)
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.status == "not_submitted" and result.facts["reason"] == "selection_not_cleared"
    assert checkbox_clicks(fake) == 1 and not any(i.get("button") == "right" for i in fake.inputs)
    assert list(fake.download_dir.iterdir()) == []
    assert TransferLedger(tmp_path / "state" / "ledger.json").get(result.key)["state"] == "failed"


def test_clear_needs_positive_empty_marker(tmp_path):
    fake = selected_folder(tmp_path, loaded_marker=False, selected_count=2)
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.facts["reason"] == "selection_clear_unproven" and checkbox_clicks(fake) == 1
    assert not any(i.get("button") == "right" for i in fake.inputs) and list(fake.download_dir.iterdir()) == []


def test_no_checkbox_click_with_extra_window_dialog_or_unknown_profile(tmp_path):
    fake = selected_folder(tmp_path, scheme_window=True)  # e.g. a txt preview window left open
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.facts["reason"] == "clear_selection_extra_window" and fake.inputs == []
    (tmp_path / "b").mkdir()
    fake = selected_folder(tmp_path / "b", dialog={"cwd": None, "value": ""})
    result = download(tmp_path / "b", fake, tmp_path / "b" / "biz", fake.remote_bytes[REMOTE])
    assert result.status == "not_submitted" and fake.inputs == []
    (tmp_path / "c").mkdir()
    fake = selected_folder(tmp_path / "c")
    original = fake.frame
    fake.frame = lambda window_id: {**original(window_id), "width": 1280}
    result = download(tmp_path / "c", fake, tmp_path / "c" / "biz", fake.remote_bytes[REMOTE])
    assert result.facts["reason"] == "checkbox_profile_unverified" and fake.inputs == []


def test_unselected_start_clicks_no_checkbox(tmp_path):
    fake = dl_fake(tmp_path)
    assert download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE]).status == "downloaded"
    assert checkbox_clicks(fake) == 0 and not any(i["action"] == "key" for i in fake.inputs)


def test_right_click_that_selects_nothing_stops_before_submit(tmp_path):
    fake = dl_fake(tmp_path)
    original = fake._main

    def ignore_right_click(kind, hit, action):
        if action.get("button") != "right":
            original(kind, hit, action)
    fake._main = ignore_right_click
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.status == "not_submitted" and result.facts["reason"] == "download_button_not_found"
    assert sum(i.get("button") == "right" for i in fake.inputs) == 1 and list(fake.download_dir.iterdir()) == []


def test_selection_count_must_read_one(tmp_path):
    fake = dl_fake(tmp_path)
    original = fake.main_lines

    def two_selected():
        return [dict(item, text="已选中2个文件/文件夹") if item["text"] == "已选中1个文件/文件夹" else item
                for item in original()]
    fake.main_lines = two_selected
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.status == "not_submitted" and result.facts["reason"] == "selection_count_unverified"


def test_missing_context_menu_never_falls_back_to_toolbar(tmp_path):
    fake = dl_fake(tmp_path)
    original = fake._main

    def no_menu(kind, hit, action):
        original(kind, hit, action)
        fake.menu = None
    fake._main = no_menu
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.status == "not_submitted" and result.facts["reason"] == "context_menu_download_not_found"
    assert list(fake.download_dir.iterdir()) == []


# ---- real 8.1.5.103 selection frame (right-click on the 修改时间 cell) -------------------

SELECTED = Path(__file__).parent / "fixtures" / "baidu_netdisk_selection_menu_ocr.json"


def selected_screen(**edits) -> Screen:
    doc = json.loads(SELECTED.read_text(encoding="utf-8"))
    lines = [dict(item, text=edits.get(item["text"], item["text"])) for item in doc["lines"]]
    return Screen({"width": doc["width"], "height": doc["height"], "window_id": "m", "input_token": "t"}, lines)


def test_real_selection_count_is_not_a_second_header():
    screen = selected_screen()
    header = list_header(screen)
    assert header is not None and header["text"] == "文件" and header["x"] == 140
    assert selection_count(screen) == 1 and screen_path(screen) == ["新建文件夹"]
    top = screen.find(DOWNLOAD_BUTTON_RE, above=header["y"])
    assert [t["text"] for t in top] == ["下载(2.1K)"] and _selection_size_matches(top[0]["text"], 2105)


@pytest.mark.parametrize("clipped", ["已选中1", "已选中1个", "已选中10个文件/文件夹"])
def test_real_clipped_or_other_count_never_reads_as_one(clipped):
    assert selection_count(selected_screen(**{"已选中1个文件/文件夹": clipped})) != 1


def test_real_leftward_menu_download_is_found_without_click_x_filter():
    before = real_screen()  # same folder, nothing selected, no menu
    items = context_menu_item(before, selected_screen(), "下载")
    assert [(i["text"], i["x"], i["y"]) for i in items] == [("下载", 1055.0, 361.0)]
    assert context_menu_item(selected_screen(), selected_screen(), "下载") == []  # nothing new: not a menu


def test_real_mtime_cell_is_the_right_click_anchor():
    screen = real_screen()
    row = screen.find("codeyun-transfer-probe.txt", literal=True)[0]
    cell = row_mtime_cell(screen, row)
    assert cell is not None and cell["text"].startswith("2026.10.16") and cell["x"] <= 1127 <= cell["x"] + cell["w"]


def test_real_empty_and_selected_evidence():
    assert selection_evidence(real_screen()) == [] and loaded_count(real_screen()) == 2  # "已全部加载，共2个"
    selected = selected_screen()
    assert {line["text"] for line in selection_evidence(selected)} == {"下载(2.1K)", "已选中1个文件/文件夹"}
    assert loaded_count(selected) is None
    assert selection_evidence(selected_screen(**{"已选中1个文件/文件夹": "已选中1", "下载(2.1K)": "x"}))  # clipped still counts


# ---- header checkbox pixels (real 8.1.5.103 crops) -------------------------------------

def checkbox_screen(state="partial", extra=None, size=(W, H)) -> Screen:
    frame = {"width": size[0], "height": size[1], "full": True, "window_id": "m", "input_token": "t",
             "patches": [{"x": 0, "y": 0, "width": size[0], "height": size[1],
                          "image": checkbox_frame_url(state, extra=extra)}]}
    return Screen(frame, [line("文件", 140, 274, 35, 18), line("大小", 801, 274, 32, 20)])


@pytest.mark.parametrize("state", ["partial", "all"])
def test_real_header_checkbox_is_measured_in_pixels(state):
    box = header_checkbox(checkbox_screen(state))
    assert box == {"x": 112, "y": 276, "w": 16, "h": 16}  # centre (120, 284): the click point Codex measured


def blue(box):
    def paint(image):
        from PIL import ImageDraw
        ImageDraw.Draw(image).rectangle(box, fill=(37, 138, 255))
    return paint


@pytest.mark.parametrize("state, extra, code", [
    ("empty", None, "header_checkbox_not_found"),                          # grey outline only
    ("empty", blue((104, 278, 135, 289)), "header_checkbox_not_found"),     # a blue button bar, not a square
    ("partial", blue((131, 278, 136, 283)), "header_checkbox_ambiguous"),   # a second blue blob in the ROI
    ("empty", blue((112, 296, 127, 311)), "header_checkbox_not_found"),     # a square off the header row
])
def test_header_checkbox_rejects_non_checkboxes(state, extra, code):
    with pytest.raises(StepFailed) as err:
        header_checkbox(checkbox_screen(state, extra))
    assert err.value.code == code


def test_header_checkbox_requires_measured_profile():
    with pytest.raises(StepFailed) as err:
        header_checkbox(checkbox_screen("partial", size=(1280, 864)))
    assert err.value.code == "checkbox_profile_unverified"


# ---- upward / leftover context menu (real 8.1.5.103 frame) ------------------------------

RESIDUAL = FIXTURES / "baidu_netdisk_menu_residual_ocr.json"


def residual_screen(*, band_image=True, extra_lines=()) -> Screen:
    """Selection cleared, upward menu still open (its plain 下载 at y263, above the header)."""
    import base64
    import io
    from PIL import Image
    doc = json.loads(RESIDUAL.read_text(encoding="utf-8"))
    image = Image.new("RGB", (doc["width"], doc["height"]), "white")
    if band_image:
        image.paste(Image.open(FIXTURES / "baidu_netdisk_crumb_band.png").convert("RGB"), (96, 216))
    else:
        image.paste((40, 40, 40), (300, 228, 480, 240))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    frame = {"width": doc["width"], "height": doc["height"], "full": True, "window_id": "m", "input_token": "t",
             "patches": [{"x": 0, "y": 0, "width": doc["width"], "height": doc["height"],
                          "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}]}
    return Screen(frame, doc["lines"] + list(extra_lines))


def test_real_upward_menu_download_is_not_toolbar_or_selection():
    screen = residual_screen()
    header = list_header(screen)
    plain = [l for l in screen.lines if l["text"] == "下载"]
    assert plain and plain[0]["y"] < header["y"]  # the menu item sits above the header
    assert toolbar_download(screen) == [] and selection_evidence(screen) == [] and loaded_count(screen) == 3
    assert any(l["text"] == "下载" for l in context_menu_column(screen))
    assert context_menu_item(real_screen(), screen, "下载") == plain  # found although above the header


def test_real_toolbar_download_is_only_the_sized_button_on_upload_row():
    selected = selected_screen()
    menu_item_above_header = line("下载", 1054, 263, 35, 19)
    selected.lines.append(menu_item_above_header)
    assert [t["text"] for t in toolbar_download(selected)] == ["下载(2.1K)"]


def test_real_blank_band_right_of_crumb_is_verified():
    band = blank_band_right_of_crumb(residual_screen())
    assert band is not None and band["x"] == 110 + 149 + 30 and abs(band["y"] + 8 - 235) <= 1
    assert band["x"] + band["w"] < 1053  # left of the menu column
    assert blank_band_right_of_crumb(residual_screen(band_image=False)) is None  # not blank in pixels
    assert blank_band_right_of_crumb(residual_screen(extra_lines=[line("x", 350, 228, 30, 14)])) is None  # OCR text there


def test_root_has_no_menu_close_anchor(tmp_path, source):
    fake = FakeClient(tmp_path)
    fake.menu = (1100.0, 340.0)
    screen = make(tmp_path, fake).observe("m")
    assert context_menu_column(screen) and blank_band_right_of_crumb(screen) is None


def blank_clicks(fake):
    return sum(i["action"] == "click" and i.get("button") == "left" and 220 <= i["y"] * H <= 250 for i in fake.inputs)


def test_upward_context_menu_download_above_header_submits_from_empty_start(tmp_path):
    fake = dl_fake(tmp_path)
    fake.menu_top = 120  # 下载 at y264, above the header at y274
    data = fake.remote_bytes[REMOTE]
    result = download(tmp_path, fake, tmp_path / "biz", data)
    assert result.status == "downloaded", result.to_dict()
    submit = fake.inputs[-1]
    assert submit["y"] * H < 274 and abs(submit["x"] * W - 1075) < 2 and blank_clicks(fake) == 0


def test_leftover_menu_is_closed_then_selection_cleared_then_target_downloads(tmp_path):
    fake = selected_folder(tmp_path, menu=(1100.0, 340.0), menu_top=120)
    data = fake.remote_bytes[REMOTE]
    result = download(tmp_path, fake, tmp_path / "biz", data)
    assert result.status == "downloaded", result.to_dict()
    assert blank_clicks(fake) == 1 and checkbox_clicks(fake) == 2
    kinds = ["blank" if 220 <= i["y"] * H <= 250 else "box" if 276 <= i["y"] * H <= 291 and i["x"] * W < 130
             else i.get("button") for i in fake.inputs]
    assert kinds == ["blank", "box", "box", "right", "left"]
    assert sorted(p.name for p in (tmp_path / "biz").iterdir()) == ["probe.txt"]


def test_menu_that_does_not_close_stops_with_zero_submission(tmp_path):
    fake = selected_folder(tmp_path, menu=(1100.0, 340.0), blank_click_closes_menu=False)
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.status == "not_submitted" and result.facts["reason"] == "context_menu_not_closed"
    assert blank_clicks(fake) == 1 and checkbox_clicks(fake) == 0 and len(fake.inputs) == 1
    assert list(fake.download_dir.iterdir()) == []


def test_menu_close_needs_no_extra_window(tmp_path):
    fake = selected_folder(tmp_path, menu=(1100.0, 340.0), scheme_window=True)
    result = download(tmp_path, fake, tmp_path / "biz", fake.remote_bytes[REMOTE])
    assert result.facts["reason"] == "close_menu_extra_window" and fake.inputs == []


def test_mkdir_cli_outputs_created_then_existed(tmp_path, monkeypatch, capsys):
    import contextlib
    import importlib.util
    import backend.core.baidu_netdisk.client as client_mod
    spec = importlib.util.spec_from_file_location("bnt_cli", Path(__file__).resolve().parents[2] / "scripts" / "baidu_netdisk_transfer.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    fake = FakeClient(tmp_path)
    monkeypatch.setattr(cli, "_lease", lambda args: contextlib.nullcontext(lambda: None))
    monkeypatch.setattr(client_mod, "NetdiskClient", lambda **kw: make(tmp_path, fake))
    argv = ["mkdir", "/m2610skilldependency/sources", "--ledger", str(tmp_path / "l.json")]
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["created"] == ["/m2610skilldependency", "/m2610skilldependency/sources"]
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["existed"] is True and out["created"] == []


def test_root_home_rendering_late_is_polled_without_input(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["a"], "/a": [], "/b": []})
    fake.cwd = "/b"
    fake.root_home_delay = 2
    client = make(tmp_path, fake)
    _, screen = client.open_remote_dir("/a")
    assert client.current_path(screen) == ["a"]
    assert [i["action"] for i in fake.inputs] == ["click", "double_click"]  # crumb once, no extra clicks


def test_root_home_never_rendering_fails_closed(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["a"], "/a": [], "/b": []})
    fake.cwd = "/b"
    fake.root_home_delay = 99
    with pytest.raises(StepFailed) as exc:
        make(tmp_path, fake).open_remote_dir("/a")
    assert exc.value.code == "root_not_verified" and len(fake.inputs) == 1


def test_root_list_without_crumb_band_is_root_after_crumb_click(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["a"], "/a": [], "/b": []})
    fake.cwd, fake.root_list_view = "/b", True
    client = make(tmp_path, fake)
    _, screen = client.open_remote_dir("/a")
    assert client.current_path(screen) == ["a"]


def test_crumbless_screen_with_a_crumb_band_gap_is_not_root():
    lines = [line("上传", 126, 171, 58, 21), line("文件", 140, 274, 35, 18), line("大小", 801, 274, 31, 19)]
    assert screen_path(Screen({"width": W, "height": H}, lines, [])) is None


@pytest.mark.parametrize("marker", ["已全部加载，共0个", "已选中1个文件/文件夹"])
def test_merged_header_line_is_the_list_header(marker):
    lines = [line("全部文件/m", 110, 226, 200, 14), line("文件" + marker, 141, 275, 200, 18),
             line("大小", 801, 274, 31, 19), line("修改时间", 1095, 275, 64, 17)]
    screen = Screen({"width": W, "height": H}, lines, [])
    header = list_header(screen)
    assert header["text"] == "文件" and header["x"] == 141 and header["w"] < 40
    assert screen_path(screen) == ["m"]
    assert loaded_count(screen) == (0 if marker.startswith("已全部") else None)


def test_create_missing_in_empty_folder_with_merged_header_and_late_inline_row(tmp_path):
    fake = FakeClient(tmp_path)
    fake.merged_header, fake.new_folder_delay = True, 2
    client = make(tmp_path, fake)
    _, screen = client.open_remote_dir("/新建文件夹/a", create_missing=True)
    assert client.created_dirs == ["/新建文件夹/a"] and fake.dirs["/新建文件夹"] == ["a"]
    assert [i["text"] for i in fake.inputs if i["action"] == "type"] == ["a"]


# ---- inline new-folder proof (synthetic technical frames; no real OCR) ------------------------

def _typed_or_keyed(fake):
    return [i for i in fake.inputs if i["action"] in {"type", "key"}]


def _toolbar_new_folder_clicks(fake):
    return [i for i in fake.inputs if i["action"] == "click" and i["y"] * H < 250 and 244 <= i["x"] * W <= 320]


def test_new_folder_rows_need_a_list_cell_no_date_and_join_split_names(tmp_path):
    client = make(tmp_path, FakeClient(tmp_path))
    head = [line("上传", 126, 171, 58, 21), line("新建文件夹", 244, 173, 76, 17), line("文件", 140, 274, 35, 18),
            line("已选中1个文件/文件夹", 205, 277, 141, 14), line("大小", 801, 274, 31, 19),
            line("类型", 932, 274, 31, 19), line("修改时间", 1095, 275, 64, 17)]
    rows = [line("新建", 189, 333, 30, 14), line("文件夹(2)", 221, 333, 60, 14), line("-", 806, 333, 8, 14),
            line("文件夹", 932, 333, 45, 14), line("-", 1066, 333, 8, 14),  # open edit, name OCR-split
            line("新建文件夹", 189, 394, 75, 14), line("-", 806, 394, 8, 14), line("文件夹", 932, 394, 45, 14),
            line("2026.10.16 16:06", 1066, 394, 101, 14),  # committed folder of the default name
            line("新建文件夹(1)", 189, 455, 95, 14), line("-", 806, 455, 8, 14),
            line("2026.10.16 16:07", 1066, 455, 101, 14),
            line("新建文件夹", 620, 600, 76, 16), line("新建文件夹", 189, 700, 75, 14)]  # no list cells: no rows
    found = client._new_folder_rows(Screen({"width": W, "height": H}, head + rows, []))
    assert [(r["joined"], r["y"]) for r in found] == [("新建文件夹(2)", 333)]


@pytest.mark.parametrize("split, cta, merged", [(False, False, False), (False, False, True), (True, False, False),
                                                (False, True, False), (True, True, True)])
def test_create_missing_in_empty_category_with_selected_inline_row(tmp_path, split, cta, merged):
    """Parent made, then the final folder inside the still-empty parent: header shows 已选中1, row 修改时间 "-"."""
    fake = FakeClient(tmp_path, dirs={"/": ["x"], "/x": []})
    fake.inline_selected, fake.inline_split, fake.empty_cta, fake.merged_header = True, split, cta, merged
    client = make(tmp_path, fake)
    _, screen = client.open_remote_dir("/x/cat/final", create_missing=True)
    assert client.current_path(screen) == ["x", "cat", "final"]
    assert client.created_dirs == ["/x/cat", "/x/cat/final"] and fake.dirs["/x/cat"] == ["final"]
    assert [i["text"] for i in fake.inputs if i["action"] == "type"] == ["cat", "final"]


def test_create_missing_beside_committed_default_named_folders(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["x"], "/x": ["新建文件夹", "新建文件夹(1)"]})
    fake.inline_selected = fake.inline_split = True
    client = make(tmp_path, fake)
    _, screen = client.open_remote_dir("/x/final", create_missing=True)
    assert fake.dirs["/x"] == ["新建文件夹", "新建文件夹(1)", "final"] and client.created_dirs == ["/x/final"]
    assert [i["text"] for i in fake.inputs if i["action"] == "type"] == ["final"]


def test_stale_open_inline_edit_is_never_typed_into(tmp_path):
    """An earlier, still uncommitted inline row: no toolbar click, no keys, no typing."""
    fake = FakeClient(tmp_path, dirs={"/": ["x"], "/x": ["新建文件夹"]})
    fake.cwd, fake.inline_selected = "/x", True
    client = make(tmp_path, fake)
    fake.editing = 0
    with pytest.raises(StepFailed) as exc:
        client.open_remote_dir("/x/final", create_missing=True)
    assert exc.value.code == "stale_inline_folder_row"
    assert not _typed_or_keyed(fake) and not _toolbar_new_folder_clicks(fake) and fake.dirs["/x"] == ["新建文件夹"]


def test_inline_row_that_never_appears_in_empty_folder_types_nothing(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["x"], "/x": []})
    fake.empty_cta, fake.new_folder_works = True, False
    with pytest.raises(StepFailed) as exc:
        make(tmp_path, fake).open_remote_dir("/x/final", create_missing=True)
    assert exc.value.code == "new_folder_row_not_verified"
    assert not _typed_or_keyed(fake) and len(_toolbar_new_folder_clicks(fake)) == 1


def test_ocr_underscore_as_space_still_matches_and_skips_existing(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["p"], "/p": ["MA_x"], "/p/MA_x": []})
    fake.underscore_as_space = True
    client = make(tmp_path, fake)
    _, screen = client.open_remote_dir("/p/MA_x", create_missing=True)
    assert client.created_dirs == [] and fake.cwd == "/p/MA_x"
    _, screen = client.open_remote_dir("/p/FIN_y", create_missing=True)
    assert client.created_dirs == ["/p/FIN_y"] and fake.dirs["/p"] == ["MA_x", "FIN_y"]


def quota_screen(*lines_):
    return Screen({"width": W, "height": H}, [line("会员中心", 938, 56, 58, 17), *lines_], [])


@pytest.mark.parametrize("label", ["1.8T / 11.1T", "1.8T/11.1T", "1.8T/ 11.1T", "1.8TB / 11.1TB"])
def test_quota_label_variants_seen_on_8_1_5_103(label):
    got = read_quota(quota_screen(line(label, 1013, 56, 61, 14), line("¥0.04/G起", 1077, 57, 62, 11)))
    assert (got["used"], got["total"]) == ("1.8T", "11.1T") and got["raw"] == label
    assert got["used_bytes"] == int(1.8 * 1024 ** 4) and got["total_bytes"] == int(11.1 * 1024 ** 4)


def test_quota_merged_with_anchor_and_mixed_units():
    got = read_quota(Screen({"width": W, "height": H}, [line("会员中心 512G/2T", 938, 56, 120, 17)], []))
    assert (got["used"], got["total"], got["used_bytes"], got["total_bytes"]) == ("512G", "2T", 512 * 1024 ** 3, 2 * 1024 ** 4)


@pytest.mark.parametrize("lines_", [
    [],  # label missing
    [line("¥0.04/G起", 1077, 57, 62, 11)],  # the price is never a quota
    [line("1.8T / 11.1T", 1013, 120, 61, 14)],  # not on the anchor row
    [line("1.8T / 11.1T", 800, 56, 61, 14)],  # left of the anchor
    [line("1.8T / 11.1T", 1013, 56, 61, 14), line("2T/3T", 1100, 56, 40, 14)],  # ambiguous
])
def test_quota_unproven_label_reads_none(lines_):
    assert read_quota(quota_screen(*lines_)) is None


def test_quota_observes_once_without_input(tmp_path):
    fake = FakeClient(tmp_path)
    got = make(tmp_path, fake).quota(evidence_dir=tmp_path / "ev")
    assert got["status"] == "ok" and got["used"] == "1.8T" and got["total"] == "11.1T"
    assert fake.inputs == [] and not (tmp_path / "ev").exists()


def test_quota_failure_keeps_screenshot(tmp_path):
    fake = FakeClient(tmp_path)
    fake.quota_label = "加载中"
    got = make(tmp_path, fake).quota(evidence_dir=tmp_path / "ev")
    assert got["status"] == "failed" and got["reason"] == "quota_label_not_found"
    assert Path(got["screenshot"]).is_file() and "会员中心" in got["top_texts"] and fake.inputs == []



# ---- directory transfers ------------------------------------------------------------

def local_tree(tmp_path: Path) -> Path:
    root = tmp_path / "src" / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "a.txt").write_bytes(b"a" * 10)
    (root / "sub" / "b_c.txt").write_bytes(b"b" * 2000)
    return root


def test_list_rows_kinds_sizes_and_chrome_excluded():
    lines = [line("上传", 126, 171, 58, 21), line("文件", 140, 274, 35, 18), line("大小", 801, 274, 31, 19),
             line("类型", 932, 274, 31, 19), line("修改时间", 1095, 275, 64, 17),
             line("新建文件夹", 201, 331, 75, 14), line("文件夹", 932, 331, 45, 14),  # inline edit row: no mtime
             line("MA 市场营销", 188, 392, 90, 14), line("-", 806, 392, 8, 14), line("文件夹", 932, 391, 45, 14),
             line("2026.10.20 19:21", 1065, 393, 101, 14),
             line("a.txt", 188, 452, 50, 14), line("1.07KB", 806, 452, 40, 14), line("TXT", 932, 452, 30, 14),
             line("2026.10.20 19:22", 1065, 452, 101, 14),
             line("知识搜索/问答/创作/AI全网搜索~", 376, 818, 200, 14)]
    rows = [{k: v for k, v in r.items() if k != "line"} for r in list_rows(Screen({"width": W, "height": H}, lines, []))]
    assert rows == [{"name": "MA 市场营销", "kind": "dir", "size": "-"}, {"name": "a.txt", "kind": "file", "size": "1.07KB"}]


def test_upload_dir_native_submits_once_and_verifies_every_level(tmp_path):
    src = local_tree(tmp_path)
    fake = FakeClient(tmp_path, dirs={"/": ["dst"], "/dst": []})
    client = make(tmp_path, fake)
    result = client.upload_dir(src, "/dst")
    assert result.status == "remote_present", result.to_dict()
    facts = result.facts
    assert facts["mode"] == "native" and facts["files_total"] == 2 and facts["uploaded"] == 2
    assert facts["missing"] == [] and facts["missing_dirs"] == [] and facts["listing_complete"]
    assert fake.submitted == ["proj"] and fake.dirs["/dst/proj/empty"] == []
    again = client.upload_dir(src, "/dst")  # rerun: verify only, never resubmit
    assert again.status == "remote_present" and fake.submitted == ["proj"]


def test_upload_dir_auto_falls_back_to_per_file_when_folder_name_unproven(tmp_path):
    src = local_tree(tmp_path)
    fake = FakeClient(tmp_path, dirs={"/": ["dst"], "/dst": []})
    fake.folder_row_sets_value = False
    result = make(tmp_path, fake).upload_dir(src, "/dst")
    assert result.status == "remote_present", result.to_dict()
    facts = result.facts
    assert facts["mode"] == "per_file" and facts["native_fallback_reason"] == "file_name_value_mismatch"
    assert fake.dialog is None and sorted(fake.submitted) == ["a.txt", "b_c.txt"]
    assert facts["uploaded"] == 2 and facts["missing"] == [] and "/dst/proj/empty" in fake.dirs


def test_upload_dir_into_existing_folder_skips_existing_names(tmp_path):
    src = local_tree(tmp_path)
    fake = FakeClient(tmp_path, dirs={"/": ["dst"], "/dst": ["proj"], "/dst/proj": ["a.txt"]})
    result = make(tmp_path, fake).upload_dir(src, "/dst")
    assert result.status == "remote_present", result.to_dict()
    assert result.facts["skipped_existing"] == ["a.txt"] and result.facts["uploaded"] == 1
    assert fake.submitted == ["b_c.txt"]


def test_upload_dir_native_mode_never_merges_into_existing_folder(tmp_path):
    src = local_tree(tmp_path)
    fake = FakeClient(tmp_path, dirs={"/": ["dst"], "/dst": ["proj"], "/dst/proj": []})
    result = make(tmp_path, fake).upload_dir(src, "/dst", mode="native")
    assert result.status == "not_submitted" and result.facts["reason"] == "remote_dir_exists" and fake.submitted == []


def test_upload_dir_reports_missing_when_remote_lacks_a_file(tmp_path):
    src = local_tree(tmp_path)
    fake = FakeClient(tmp_path, dirs={"/": ["dst"], "/dst": []})
    client = make(tmp_path, fake)
    original = fake._dialog

    def lossy(kind, hit, action):
        original(kind, hit, action)
        if kind == "click" and hit == "存入百度网盘":
            fake.dirs["/dst/proj/sub"].remove("b_c.txt")  # e.g. still transferring
    fake._dialog = lossy
    result = client.upload_dir(src, "/dst")
    assert result.status == "incomplete" and result.facts["missing"] == ["sub/b_c.txt"]
    assert result.facts["uploaded"] == 1


def remote_proj(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["src"], "/src": ["proj"], "/src/proj": ["a.txt", "sub"],
                                      "/src/proj/sub": ["b_c.txt"]})
    fake.remote_bytes = {"/src/proj/a.txt": b"a" * 10, "/src/proj/sub/b_c.txt": b"b" * 2000}
    return fake


def test_download_dir_collects_tree_checks_names_sizes_and_resumes_from_ledger(tmp_path):
    fake = remote_proj(tmp_path)
    client = make(tmp_path, fake)
    local = tmp_path / "biz"
    local.mkdir()
    result = client.download_dir("/src/proj", local, client_download_dir=fake.download_dir)
    assert result.status == "downloaded", result.to_dict()
    assert (local / "proj" / "a.txt").read_bytes() == b"a" * 10
    assert (local / "proj" / "sub" / "b_c.txt").stat().st_size == 2000
    assert result.facts["files_total"] == 2 and len(result.facts["files"]) == 2
    clicks = len(fake.inputs)
    again = client.download_dir("/src/proj", local, client_download_dir=fake.download_dir)
    assert again.status == "already_downloaded" and len(fake.inputs) == clicks  # ledger answer, no GUI


def test_download_dir_never_overwrites_a_different_local_file(tmp_path):
    fake = remote_proj(tmp_path)
    local = tmp_path / "biz"
    (local / "proj").mkdir(parents=True)
    (local / "proj" / "a.txt").write_bytes(b"local!")
    result = make(tmp_path, fake).download_dir("/src/proj", local, client_download_dir=fake.download_dir)
    assert result.status == "needs_review" and result.facts["conflicts"] == ["a.txt"]
    assert (local / "proj" / "a.txt").read_bytes() == b"local!" and not (local / "proj" / "sub").exists()


def test_download_dir_submitted_then_resumed_without_gui(tmp_path):
    fake = remote_proj(tmp_path)
    client = make(tmp_path, fake)
    local = tmp_path / "biz"
    local.mkdir()
    original = fake._main
    held = {}

    def slow(kind, hit, action):  # the client writes a partial first
        original(kind, hit, action)
        if kind == "click" and hit == "下载" and not held:
            done = fake.download_dir / "proj" / "sub" / "b_c.txt"
            held["data"] = done.read_bytes()
            done.unlink()
            (done.parent / "b_c.txt.baiduyun.p.downloading").write_bytes(b"x")
    fake._main = slow
    first = client.download_dir("/src/proj", local, client_download_dir=fake.download_dir)
    assert first.status == "submitted" and "sub/b_c.txt" in first.facts["missing"]
    partial = fake.download_dir / "proj" / "sub" / "b_c.txt.baiduyun.p.downloading"
    partial.unlink()
    (partial.parent / "b_c.txt").write_bytes(held["data"])

    def no_gui():
        raise AssertionError("resume must not touch the GUI")
    resumer = NetdiskClient(ledger=client.ledger, lease_check=no_gui, driver=None, settle=0, sleep=lambda _s: None,
                            poll_attempts=3)
    resumed = resumer.resume_download_dir(first.key)
    assert resumed.status == "downloaded" and (local / "proj" / "sub" / "b_c.txt").stat().st_size == 2000


def test_download_dir_requires_a_folder_row(tmp_path):
    fake = remote_proj(tmp_path)
    result = make(tmp_path, fake).download_dir("/src/proj/a.txt", tmp_path, client_download_dir=fake.download_dir)
    assert result.status == "not_submitted"
    assert not [i for i in fake.inputs if i.get("button") == "right"]



def test_list_rows_real_8_1_5_103_hover_icons_and_search_overlay():
    """Measured on /m2610skilldependency: hover icons on a row, AI search box over the last row."""
    lines = [line("上传", 126, 171, 58, 21), line("文件", 141, 275, 35, 18), line("已全部加载，共9个", 205, 277, 120, 14),
             line("大小", 801, 274, 31, 19), line("类型", 932, 274, 31, 19), line("修改时间", 1095, 275, 64, 17),
             line("GA 游戏", 189, 572, 60, 14), line("8", 580, 572, 12, 14), line("↓", 615, 572, 12, 14),
             line("回", 650, 572, 12, 14), line("...", 720, 572, 20, 14), line("文件夹", 932, 572, 45, 14),
             line("2026.10.20 19:25", 1065, 573, 101, 14),
             line("DA 数据分析", 189, 812, 80, 14), line("知识搜索/问答/创作/AI全网搜索~", 376, 818, 200, 14),
             line("2026.10.20 19:25", 1065, 818, 101, 14)]
    rows = [r["name"] for r in list_rows(Screen({"width": W, "height": H}, lines, []))]
    assert rows == ["GA 游戏"]


def test_listing_key_folds_dropped_underscores():
    from backend.core.baidu_netdisk.client import _list_key
    assert _list_key("FIN_金融") == _list_key("FIN金融") == _list_key("FIN 金融")



def test_scrolled_frame_ocr_variant_of_known_row_is_not_a_new_entry(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["p"], "/p": ["MA_市场营销", "GA_游戏", "DA_数据分析"]})
    client = make(tmp_path, fake)
    main, screen = client.open_remote_dir("/p")
    frames = iter([
        [("MA_市场营销", 333), ("GA_游戏", 394)],                       # DA hidden (under the overlay)
        [("MA_市场宫销", 300), ("GA_游戏", 361), ("DA_数据分析", 422)],  # scrolled: MA misread
    ])
    fake.loaded_marker = True

    def rows_of(spec):
        out = [line("上传", 126, 171, 58, 21), line("文件", 140, 274, 35, 18), line("已全部加载，共3个", 205, 277, 120, 14),
               line("大小", 801, 274, 31, 19), line("类型", 932, 274, 31, 19), line("修改时间", 1095, 275, 64, 17)]
        for name, y in spec:
            out += [line(name, 189, y, 90, 14), line("文件夹", 932, y, 45, 14), line("2026.10.20 19:25", 1065, y, 101, 14)]
        return Screen({"width": W, "height": H}, out, [])
    first = rows_of(next(frames))
    second = rows_of(next(frames))
    client.act = lambda s, w, **a: second if a.get("delta", 0) < 0 else first
    _, rows, complete = client._list_dir_entries(main, first)
    assert [r["name"] for r in rows] == ["MA_市场营销", "GA_游戏", "DA_数据分析"] and complete



def test_created_folder_resorted_out_of_view_is_found_by_scrolling(tmp_path):
    """Measured at the 26-item root: after Enter the new name re-sorts below the visible rows."""
    fake = FakeClient(tmp_path, dirs={"/": ["新建文件夹", "b", "c", "d", "e"], "/新建文件夹": [], "/b": [], "/c": [],
                                      "/d": [], "/e": []})
    fake.visible_rows = 3
    client = make(tmp_path, fake)
    client.max_scrolls = 5
    _, screen = client.open_remote_dir("/CodeYun自动化测试", create_missing=True)
    assert client.current_path(screen) == ["CodeYun自动化测试"]
    assert client.created_dirs == ["/CodeYun自动化测试"] and fake.dirs["/"][-1] == "CodeYun自动化测试"
    assert [i["text"] for i in fake.inputs if i["action"] == "type"] == ["CodeYun自动化测试"]



def test_per_file_pass_after_native_upload_skips_every_existing_name(tmp_path):
    src = local_tree(tmp_path)
    fake = FakeClient(tmp_path, dirs={"/": ["dst"], "/dst": []})
    client = make(tmp_path, fake)
    assert client.upload_dir(src, "/dst").facts["mode"] == "native"
    again = client.upload_dir(src, "/dst", mode="per_file")
    assert again.status == "remote_present", again.to_dict()
    assert sorted(again.facts["skipped_existing"]) == ["a.txt", "sub/b_c.txt"] and again.facts["uploaded"] == 0
    assert fake.submitted == ["proj"]  # nothing uploaded again



def test_list_rows_hover_badge_is_not_a_second_row():
    """Measured in /CodeYun自动化测试/probe-tree: hover shows a 常看 badge 12px right of the name."""
    lines = [line("上传", 126, 171, 58, 21), line("文件", 141, 275, 57, 17), line("已全部加载，共4个", 202, 277, 124, 14),
             line("大小", 801, 274, 33, 20), line("类型", 932, 274, 32, 19), line("修改时间", 1095, 275, 53, 17),
             line("sub", 187, 331, 29, 16), line("常看", 228, 332, 27, 18), line("8", 576, 333, 19, 13),
             line("文件夹", 932, 331, 44, 18), line("2026.10.20 20:35", 1065, 333, 100, 13),
             line("empty", 187, 393, 48, 18), line("文件夹", 932, 391, 44, 18), line("2026.10.20 20:35", 1065, 393, 100, 13),
             line("来自：MI", 187, 451, 60, 19), line("4LTE", 252, 451, 40, 19), line("文件夹", 932, 451, 44, 18),
             line("2026.10.20 20:35", 1065, 453, 100, 13),
             line("a.txt", 186, 511, 36, 19), line("8.00B", 803, 512, 36, 15), line("txt文件", 932, 511, 47, 18),
             line("2026.10.20 20:35", 1065, 513, 100, 13)]
    rows = [(r["name"], r["kind"], r["size"]) for r in list_rows(Screen({"width": W, "height": H}, lines, []))]
    assert rows == [("sub", "dir", None), ("empty", "dir", None), ("来自：MI 4LTE", "dir", None), ("a.txt", "file", "8.00B")]



# ---- own share creation ---------------------------------------------------------------

def share_fake(tmp_path):
    fake = FakeClient(tmp_path, dirs={"/": ["t"], "/t": ["probe-tree"], "/t/probe-tree": []})
    return fake


def test_share_create_picks_days_reads_link_and_closes_window_once(tmp_path):
    fake = share_fake(tmp_path)
    client = make(tmp_path, fake)
    result = client.share_create("/t/probe-tree", days=7)
    assert result.status == "shared", result.to_dict()
    assert (result.facts["url"], result.facts["pwd"], result.facts["days"]) == ("https://pan.baidu.com/s/1AbCdEf-gh", "x7k2", 7)
    assert fake.shares == ["7天"] and fake.share is None and result.facts["share_window_closed"] is True
    again = client.share_create("/t/probe-tree", days=7)  # ledger answer: no second link
    assert again.status == "already_shared" and again.facts["url"] == result.facts["url"] and fake.shares == ["7天"]


def test_open_share_window_does_not_break_main_window(tmp_path):
    fake = share_fake(tmp_path)
    fake.share = {"name": "probe-tree", "days": "1年", "created": False}
    assert make(tmp_path, fake).main_window() == "m"


def test_share_window_already_open_stops_before_any_input(tmp_path):
    fake = share_fake(tmp_path)
    fake.share = {"name": "probe-tree", "days": "1年", "created": False}
    result = make(tmp_path, fake).share_create("/t/probe-tree", days=7)
    assert result.status == "not_submitted" and result.facts["reason"] == "share_window_already_open"
    assert fake.inputs == [] and fake.shares == []


def test_unread_link_is_never_recreated(tmp_path):
    fake = share_fake(tmp_path)
    fake.share_link_text = "链接生成中"
    client = make(tmp_path, fake)
    first = client.share_create("/t/probe-tree", days=30)
    assert first.status == "submitted" and first.facts["url"] is None and fake.share is None
    again = client.share_create("/t/probe-tree", days=30)
    assert again.status == "state_unknown" and fake.shares == ["30天"]


def test_share_create_rejects_bad_days_and_pwd(tmp_path):
    client = make(tmp_path, share_fake(tmp_path))
    with pytest.raises(ValueError):
        client.share_create("/t/probe-tree", days=3)
    with pytest.raises(ValueError):
        client.share_create("/t/probe-tree", days=7, pwd="toolong")


@pytest.mark.parametrize("texts,expected", [
    (["https://pan.baidu.com/s/1AbC_dE?pwd=ab12", "提取码：ab12"], ("https://pan.baidu.com/s/1AbC_dE", "ab12")),
    (["链接：https://pan.baidu.com/s/1AbC_dE", "提取码: zz99"], ("https://pan.baidu.com/s/1AbC_dE", "zz99")),
    (["https://pan.baidu.com/s/1AbC_dE"], ("https://pan.baidu.com/s/1AbC_dE", None)),
    (["https://pan.baidu.com/s/1AbC_dE?pwd=ab12", "提取码：ab13"], None),  # disagreeing codes
    (["https://pan.baidu.com/s/1AbC_dE", "https://pan.baidu.com/s/2Other"], None),  # two links
    (["分享文件夹支持被订阅和回答"], None),
])
def test_parse_share_link(texts, expected):
    screen = Screen({"width": W, "height": H}, [line(t, 40, 100 + 30 * i, 300, 18) for i, t in enumerate(texts)], [])
    assert parse_share_link(screen) == expected



def real_tooltip_screen():
    """8.1.5.103 after 创建链接: the link wraps inside a tooltip bubble at x~652 (measured)."""
    texts = ["通过网盘分享的文", "件：probe-tree 链", "接: https://pan.bai", "du.com/s/1ySpxd", "sAqZeApC4nvEZC",
             "AJw?pwd=hhmg", "提取码: hhmg --来", "自百度网盘超级会", "员v5的分享"]
    lines = [line("访问人数限制：", 46, 309, 92, 17), line("不限制", 184, 309, 51, 22),
             line("可直接输入1-10人，或选择不限制", 315, 311, 212, 14), line("✓复制链接", 665, 484, 79, 18)]
    lines += [line(t, 652, 300 + 20 * i, 110, 16) for i, t in enumerate(texts)]
    return Screen({"width": W, "height": H}, lines, [])


def test_parse_share_link_joins_the_wrapped_tooltip_column():
    assert parse_share_link(real_tooltip_screen()) == ("https://pan.baidu.com/s/1ySpxdsAqZeApC4nvEZCAJw", "hhmg")


def test_share_create_prefers_fresh_clipboard_text(tmp_path):
    fake = share_fake(tmp_path)
    fake.share_link_text = "链接已复制"  # nothing readable by OCR
    fake.share_copies_to_clipboard = True
    result = make(tmp_path, fake).share_create("/t/probe-tree", days=7)
    assert result.status == "shared", result.to_dict()
    assert (result.facts["url"], result.facts["pwd"], result.facts["read_from"]) == (
        "https://pan.baidu.com/s/1ySpxdsAqZeApC4nvEZCAJw", "hhmg", "clipboard")


def test_stale_clipboard_is_ignored(tmp_path):
    fake = share_fake(tmp_path)
    fake.share_link_text = "链接已复制"
    fake.clipboard = "通过网盘分享的文件：probe-tree 链接: https://pan.baidu.com/s/1OldOld?pwd=aaaa"
    result = make(tmp_path, fake).share_create("/t/probe-tree", days=7)
    assert result.status == "submitted" and result.facts["url"] is None
