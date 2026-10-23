"""Drive the running official Baidu Netdisk client for one upload or download.

Everything visible goes through existing public services: window projection
(``list_projection_windows`` / ``projection_frame`` / ``projection_input``)
for capture and input, PaddleOCR (``run_paddle_ocr_preview`` +
``extract_ocr_spatial_document``) for text and coordinates.  No AI, no
private client APIs, no hard-coded desktop points: every click targets an OCR
line (or the exact token span inside one) found on a frame captured
immediately before, and is followed by a fresh capture that must show the
expected result.

Safety rules:
* every capture and every input is preceded by a resource-lease ``check``
  (capture may restore the window); input uses that frame's ``input_token``,
  which binds window identity and size;
* an unexpected screen raises :class:`StepFailed` (nothing was submitted, the
  ledger record is released as ``failed`` so a later run may retry);
* after the submit click nothing is ever clicked again; uncertainty is
  reported as ``state_unknown``/``submitted`` and the ledger keeps the record
  so the next run refuses to submit the same transfer twice.
* an upload is ``remote_present`` once its exact name appears in the target
  folder; nothing here compares remote content (that needs a download round
  trip with ``expected_sha256``).  A download is content-verified only when a
  hash was given (``content_verified``); otherwise only name and size matched.
* a ``submitted`` download is resumed with :meth:`NetdiskClient.resume_download`
  (filesystem only, no GUI input).

``download_share`` is EXPERIMENTAL: its scheme payload and table geometry come
from reading the official client's static ``main.js``/CSS and have not been
exercised end to end against a real share link.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from urllib.parse import parse_qs, unquote, urlsplit
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Callable

from .ledger import TransferLedger, transfer_key
from .local_files import (collect_download, collect_download_dir, describe_upload_source, describe_upload_tree,
                          load_manifest, sha256_file, snapshot_dir, validate_file_name, validate_remote_dir,
                          write_manifest)

CLIENT_APP_RE = re.compile(r"^baidunetdisk(unite)?\.exe$", re.I)
FILE_DIALOG_TITLE = "请选择文件/文件夹"
DIALOG_CONFIRM = "存入百度网盘"
DIALOG_CANCEL = "取消"
FILE_NAME_LABEL_RE = re.compile(r"^文件名\s*[(（]\s*N\s*[)）]\s*[:：]?")
ROOT_CRUMB = "全部文件"
CRUMB_SPLIT_RE = re.compile(r"[/>›》]")
# The file list header row: a name column ("文件" on 8.1.5.103) sharing its row
# with a size/time column, so "最近文件" cards or file names never qualify.
LIST_HEADER_NAMES = ("文件", "文件名")
LIST_HEADER_PEERS = ("大小", "修改时间")
# Empty list / inline edit: OCR merges the header name with the marker on its row into one line.
MERGED_HEADER_RE = re.compile(r"^(文件名?)(已全部加载[，,]共\d+个|已选中\d+个文件/文件夹)$")
QUOTA_ANCHOR = "会员中心"  # top bar: "会员中心 | 1.8T / 11.1T ¥0.04/G起" (8.1.5.103)
QUOTA_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMGTP]?)B?/(\d+(?:\.\d+)?)([KMGTP]?)B?(?![\d.])", re.I)
QUOTA_UNITS = "KMGTP"
ROOT_TOOLBAR_GAP = 80  # px toolbar->header with no crumb band between them (全部文件 root list)
# While items are selected the header row keeps "文件" and adds, to its right,
# "已选中1个文件/文件夹".  Full text only: a menu-clipped "已选中1" proves nothing.
SELECTION_RE = re.compile(r"^已选中(\d+)个文件/文件夹$")
# 8.1.5.103 file context menu (opens left of the cursor near the right edge).
CONTEXT_MENU_DOWNLOAD = "下载"
CONTEXT_MENU_PEERS = ("打开", "打开方式", "分享", "复制", "剪切", "重命名", "移动到", "详细信息", "删除")
MENU_LABELS = {CONTEXT_MENU_DOWNLOAD, *CONTEXT_MENU_PEERS, "文档历史版本", "本地编辑", "用手机打开"}
BLANK_BAND_WIDTH = 200  # px of verified-blank breadcrumb row clicked to dismiss a context menu
LIST_MTIME = "修改时间"
LIST_SIZE = "大小"
LIST_TYPE = "类型"
FOLDER_TYPE = "文件夹"
LIST_NAME_JOIN_GAP = 8  # px: a name OCR-split at a space; a wider gap is a hover badge such as 常看
LIST_NAME_SPAN = 100  # px right of the 文件 header where row names start (hover icons sit further right)
LIST_OVERLAY_RE = re.compile(r"^知识搜索")  # floating AI search box covering the bottom rows (8.1.5.103)
MTIME_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}\d{2}:\d{2}$")  # whitespace-free 2026.10.20 19:24
# Unselected header row shows "已全部加载，共2个" where the selection count would be.
LOADED_RE = re.compile(r"^已全部加载[，,]共(\d+)个$")
MAX_CLEAR_CLICKS = 2  # partial -> all -> none
# Header checkbox (8.1.5.103, measured): a ~16 px blue square ~20 px left of the
# "文件" header, centred on its row; empty state is a grey outline (no blue).
CHECKBOX_PROFILES = {(1213, 864)}
CHECKBOX_SIDE = (13, 19)
CHECKBOX_ROI_LEFT = 40  # px left of the header text searched for the box
SIDE_NAV_HOME = "首页"
# Top tab bar 首页 (back from e.g. the 资源分享/extraction tab, which has no side nav).  Measured
# 8.1.5.103: x39 y10 w52 h18 on a 1920x1152 frame (centre 3.4% / 1.6%); fake 1213x864 at 6.7% / 2.3%.
TOP_TAB_BAND = .06  # height fraction holding the tab bar; every 首页 in it counts toward uniqueness
TOP_TAB_HOME_LEFT = .1  # the 首页 tab is the leftmost tab
NEW_FOLDER_BUTTON = "新建文件夹"
NEW_FOLDER_ROW_RE = re.compile(r"^新建文件夹([(（]\d+[)）])?$")  # inline row the client adds, name preselected
ROOT_HOME_MARK = "最近文件"  # the root home shows recent files above the list and no crumb
DOWNLOAD_BUTTON_RE = re.compile(r"^下载\s*([(（][^)）]*[)）])?$")
# "下载(1.1K)": total size of the current selection, one display step of precision.
DOWNLOAD_SIZE_RE = re.compile(r"^下载[(（](\d+(?:\.(\d+))?)([KMGT]?)B?[)）]$", re.I)
# EXPERIMENTAL (not verified end to end).  Share links: the official client's own web-evoke scheme (main.js parseWebEvokedUrl,
# handler "evoked-sharelink") opens the "资源分享" tab (linkPreviewer) and, when
# pwd is given, extracts the file list automatically.  The payload carries no
# uk/checkUserInfo, so the handler's account-mismatch logout branch is unreachable.
SHARE_URL_RE = re.compile(r"^https://pan\.baidu\.com/s/([A-Za-z0-9_-]{5,64})$")
# Official share landing page https://pan.baidu.com/share/init?surl=<surl without the leading 1>;
# it names the same share as https://pan.baidu.com/s/1<surl>.
SHARE_SURL_RE = re.compile(r"^[A-Za-z0-9_-]{4,63}$")
SHARE_PWD_RE = re.compile(r"^[A-Za-z0-9]{4}$")
# linkPreviewer table: selection column left of the name column whose header reads
# "已选择N个文件"; row-click opens the file, so only the checkbox is clicked.
SHARE_HEADER_RE = re.compile(r"^已选择(\d+)个文件$")
# Share checkbox column (8.1.5.103, measured on a 1920x1152 frame): the header box spans x42..58 /
# y304..320 (centre 50,312) while the 已选择N个文件 text starts at x~98; root-row boxes share the
# column (row 1 centre 50,359).  Checked: blue fill; empty: grey outline (no blue).  Per profile,
# header-text x minus the offset predicts the column; a checked box in the frame is measured and
# used instead.  Unprofiled frames are never clicked (the old CSS guess of 37 px landed at x~61).
SHARE_CHECKBOX_PROFILES = {(1920, 1152): 48}
SHARE_CHECKBOX_TOLERANCE = 6  # px between predicted and measured column (OCR header x is approximate)
SHARE_SAVE = "保存到网盘"
# Extraction page of the 资源分享 tab (observed): code box, 提取文件 button and, on rejection, a red
# hint such as 提取码格式错误.  The flows only read it: 提取文件 is never pressed and the box never typed.
SHARE_EXTRACT_BUTTON = "提取文件"
# Share openers.  "address_bar" (default, manually verified on the native client): the main window's
# top address/search field (empty: placeholder 搜网盘文件、全网资源资讯; in the own file view it may hold a
# prior pan.baidu.com link) is clicked, Ctrl+A, and the full https://pan.baidu.com/s/<surl>?pwd=<code> typed
# once with no Enter; the client opens the share tab asynchronously (~1.2-1.5 s observed).  The field is
# only ever located from OCR on a fresh frame: exactly one placeholder-or-link line in the top band, else
# no input.  "scheme" (evoked-sharelink, see share_scheme_url) stays an explicit, never automatic, option.
SHARE_OPENERS = ("address_bar", "scheme")
ADDRESS_BAR_PLACEHOLDER_RE = re.compile(r"^搜网盘文件")
ADDRESS_BAR_LINK_RE = re.compile(r"^(?:https?://)?pan\.baidu\.com/(?:s/|share/init)", re.I)
# The field's magnifier glyph, when OCR merges it into the link line, reads as a literal "Q" then a space
# (observed 1920x1152: "Q https://pan.baidu.com/s/..." as one top-band line).  Only this exact prefix,
# and only before a link, is stripped; any other prefix is not the field.
ADDRESS_BAR_ICON_PREFIX_RE = re.compile(r"^Q\s+(?=\S)")
ADDRESS_BAR_BAND = 0.12  # field centre in the top 12% of the client area (measured centre y 65 of 1152)
SHARE_EXTRACT_ERROR_RE = re.compile(r"提取码(?:格式)?(?:错误|不正确|有误)")
# Own-share creation (8.1.5.103): context menu 分享 opens a separate window 「分享： <name>」 (782x575)
# whose 有效期 segment row and 创建链接 button live in its client area; its × is in the native title
# bar (outside the capture) and Escape does not close it, so it is closed with WM_CLOSE by title.
SHARE_WINDOW_RE = re.compile(r"^分享[:：]\s*(.+)$")
SHARE_DAYS = {1: "1天", 7: "7天", 30: "30天", 365: "1年", 0: "永久有效"}
SHARE_CREATE = "创建链接"
SHARE_VALIDITY_LABEL = re.compile(r"^有效期[:：]?$")
SHARE_PWD_LABEL = re.compile(r"^提取码[:：]?$")
SHARE_PWD_CUSTOM = "自定义"
SHARE_URL_TEXT_RE = re.compile(r"https?://pan\.baidu\.com/s/([0-9A-Za-z_-]{5,64})(?:\?pwd=([0-9A-Za-z]{4}))?")
SHARE_PWD_TEXT_RE = re.compile(r"提取码[:：]?\s*([0-9A-Za-z]{4})(?![0-9A-Za-z])")
# Observed share-tab ordering (official client): the bottom bar shows a destination label
# ("我的网盘/..." optionally prefixed 保存到) and a folder icon next to 保存到网盘.  The icon opens the in-page
# 保存到 picker (crumb, 新建文件夹, 取消, 确定); 确定 only chooses the destination and closes the picker,
# after which the label shows the full target.  保存到网盘 is the one and only copy submission and
# ends in a 保存成功 dialog.  Labels/offsets below are PROVISIONAL until measured on real frames: a
# picker is only accepted when one surface shows exactly one confirm and one cancel label plus a crumb
# rooted at one of PICKER_ROOTS; anything else is reported, never confirmed and never followed by a save.
PICKER_ROOTS = ("全部文件", "我的网盘")
PICKER_CONFIRM = "确定"
PICKER_CANCEL = "取消"
# Measured once (1920x1152 PUBLIC frame, sanitized): the in-page modal x655..1265 y355..795 shows the
# exact title 保存到 top-left, below it the root crumb with a 已加载前N个 marker at the right end of
# the same row, folder rows, then 新建文件夹 / 取消 / 确定 on one bottom row (取消 left of 确定).  The
# share page behind keeps its own 全部文件 crumb, destination label and 保存到网盘 visible, so every
# picker lookup is bounded to the region spanned by that title and button row.
PICKER_TITLE = "保存到"
PICKER_LOADED_RE = re.compile(r"^已加载(?:前)?\d+个(?:文件夹|文件)?$")  # crumb-row count marker, never a segment
PICKER_ELIDED_RE = re.compile(r"…|\.\.|。。")  # a shortened crumb never proves a path on its own
# Measured (1920x1152 PUBLIC frame, sanitized): deep in the tree the picker crumb reads
# "全部文件 / ... / m2610skilldependency / sources" (one line x663 y419 w348 h17, 已加载前3个 at x1170).
# Such a crumb is only accepted as the fresh result of one proven child click (see elided_crumb_proves).
PICKER_ELLIPSIS_RE = re.compile(r"^(?:…+|\.+|。{2,})$")  # a whole elided segment, never a partial one
# Measured (1920x1152 PUBLIC frame, final child click): OCR read the deep crumb's ellipsis as ONE
# period, "全部文件 / . / MA8 / sd-…".  No folder can be named "."; such a WHOLE segment is an elision
# mark (dotted names such as ".git" or "a.b" stay ordinary segments).
PICKER_DOT_ELISION = "."
PICKER_ELIDED_MIN_SUFFIX = 2  # visible segments after the ellipsis needed to prove a transition
# Stable material ID tag ending a folder name (CodeHub IDs, e.g. sd-2578c263a774).  Measured
# (sanitized): an own list row dropped the "_" before the tag; see material_tag_row.
MATERIAL_TAG_RE = re.compile(r"^(?P<prefix>.*\S)_(?P<tag>sd-[0-9a-f]{12})$")
# Measured (1920x1152 PUBLIC frame inside a folder): the picker shows a 返回上一级 control left of the
# root, and OCR merged both into one line "返回上一级>全部文件/data".  Only this exact control plus the
# observed ">" may precede the root, merged or as separate lines; it is never a path segment and never
# the root-reset click target.
PICKER_BACK = "返回上一级"
PICKER_BACK_SEP = ">"
PICKER_PAD_TITLES = 2  # region margin around title/buttons, in title heights
PICKER_SCROLL_DELTA = -360
DEST_PREFIX_RE = re.compile(r"^(?:保存到|保存至)\s*[:：]?\s*")
# PROVISIONAL, one sanitized 1920x1152 observation: icon centre ~102 px left of the Save button centre
# (button ~128 px wide); short destination text can end far to the left, so the label is no anchor.
DEST_ICON_FROM_SAVE_CENTER = 102
DEST_ICON_SAVE_CLEARANCE = 24  # icon centre must stay this far left of the Save text box
# Measured once on the 1920x1152 PUBLIC frame: OCR reads the folder icon itself as a lone "D"
# (bbox x1355 y1071 w23 h23; Save x1430 y1073 w74 h18, so the anchor 1365,1082 lies inside it).
# Only that glyph, in that frame size, of that size, on the Save row and around the anchor, may
# overlap the anchor; any other text there still refuses the click.
DEST_ICON_GLYPHS = {(1920, 1152): {"text": "D", "size": (20, 26), "square": 3, "row": 4, "centre": 6}}
SAVE_DONE = "保存成功"
SAVE_FAILED_RE = re.compile(r"保存失败|空间不足|容量不足")
SAVE_VIEW = "查看"
SHARE_NAME_SPAN = 120  # px right of the share header where root item names start
# After the picker round trip one frame may be partly OCR'd: re-observe (no input) at most this often and
# this long until the selection count, exact root-row set and full target label hold on one frame.
SHARE_SETTLE_POLLS = 6
SHARE_SETTLE_SECONDS = 10.0
# Native own path (8.1.5.103, measured read-only via UIA): the main window's root Document "百度网盘" has the
# Value file:///<install>/BrowserEngine/resources/core.asar/index.html#/?path=%2Fa%2Fb&knowledgeCollShareInfo=null&...
# The own file view is route "/" with one ``path``; the crumb's pixels may clip a long last segment, the value not.
NATIVE_DOCUMENT_NAME = "百度网盘"
NATIVE_INDEX_SUFFIX = "/BrowserEngine/resources/core.asar/index.html"
NATIVE_LOCAL_PATH_RE = re.compile(r"^/[A-Za-z]:/")
NATIVE_SHARE_PARAM = "knowledgeCollShareInfo"  # "null" in the own view
# Measured: every own window also holds one nested Document "百度网盘-第二大脑" for the official AI search panel,
# https://pan.baidu.com/aipan/search?<query>.  Only that exact origin and path, nested, is tolerated (query ignored).
NATIVE_AIPAN = ("https", "pan.baidu.com", "/aipan/search")
NATIVE_MAX_AGE = 3.0  # s between the frame's observation and the start of the UIA read
SHARE_ROW_SKIP_RE = re.compile(r"^(-|\d+(?:\.\d+)?[KMGT]?B?|\d{4}[-.]\d{2}[-.]\d{2}.*)$", re.I)


class StepFailed(RuntimeError):
    """The screen did not match the expectation before any submit input."""

    def __init__(self, code: str, **facts: object):
        super().__init__(code)
        self.code = code
        self.facts = facts


# ---- seams over the public services (replaced by fakes in tests) -------------

class ProjectionDriver:
    """Public window-projection + OCR calls; the only place touching the desktop."""

    def __init__(self, ocr_dir: Path | None = None):
        from backend.core.temp_paths import codeyun_temp_root
        self.ocr_dir = ocr_dir or codeyun_temp_root("baidu-netdisk", "ocr")

    def list_windows(self) -> list[dict]:
        from backend.core.devices.window_projection import list_projection_windows
        return list_projection_windows()

    def activate(self, window_id: str) -> dict:
        from backend.core.devices.window_projection import activate_projection_window
        return activate_projection_window(window_id)

    def frame(self, window_id: str) -> dict:
        # No previous token: the frame always carries one full-image patch.
        from backend.core.devices.window_projection import projection_frame
        return projection_frame(window_id=window_id)

    def close_window(self, window_id: str, expected_title: str) -> bool:
        """WM_CLOSE (what the title-bar × sends) to one client window, only if its title is exactly ``expected_title``.

        The window projection captures the client area only and has no close action; a share
        window's × lies in the native title bar.  Returns False (nothing sent) on a title mismatch.
        """
        import win32con
        import win32gui
        hwnd = int(str(window_id).split(":", 1)[0])
        if win32gui.GetWindowText(hwnd) != expected_title:
            return False
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return True

    def clipboard_text(self) -> str | None:
        """Read-only: the current Unicode clipboard text (the share window's ✓复制链接 fills it)."""
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return None
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()

    def open_scheme(self, url: str) -> None:
        """Hand an official ``baiduyunguanjia://`` URL to its registered handler (no GUI input)."""
        os.startfile(url)  # type: ignore[attr-defined]  # Windows ShellExecute

    def native_documents(self, window_id: str) -> dict:
        """Read-only UIA Document names/values of one window (see ``projection_window_documents``)."""
        from backend.core.devices.window_projection import projection_window_documents
        return projection_window_documents(window_id)

    def input(self, frame: dict, **action: object) -> dict:
        from backend.core.devices.window_projection import projection_input
        return projection_input(token=frame["input_token"], window_id=frame["window_id"], **action)

    def ocr(self, frame: dict, *, keep_image: Path | None = None) -> dict:
        """``{"lines": [...], "tokens": [...]}`` from extract_ocr_spatial_document."""
        from backend.core.ocr.preview import run_paddle_ocr_preview
        from backend.core.ocr.spatial_document import extract_ocr_spatial_document
        path = keep_image or self.ocr_dir / f"{frame['token']}.png"
        frame_image(frame).save(path)
        try:
            preview = run_paddle_ocr_preview(path, shape_type="rectangle")
        finally:
            if keep_image is None:
                path.unlink(missing_ok=True)
        payload = ((preview.get("document") or {}).get("flags") or {}).get("paddleocr_payload") or {}
        return extract_ocr_spatial_document(payload) if payload else {"lines": [], "tokens": []}


def _norm(text: object) -> str:
    """Known client UI labels only: OCR may insert spaces into them."""
    return re.sub(r"\s+", "", str(text or ""))


def _literal(text: object) -> str:
    """File and folder names: internal spaces are significant ("a b" != "ab")."""
    return str(text or "").strip()


def _name_key(text: object) -> str:
    """Name comparison key: OCR (8.1.5.103) often reads '_' as a space ("MA_x" -> "MA x"), so both fold together."""
    return _literal(text).replace("_", " ")


def _list_key(text: object) -> str:
    """Listing comparison key: OCR of list rows drops or spaces out '_' between frames ("FIN_x"/"FIN x"/"FINx")."""
    return re.sub(r"[\s_]+", "", _literal(text))


def same_path(got: list[str] | None, parts: list[str]) -> bool:
    """Crumb segments (OCR text) against requested segments, under :func:`_name_key`."""
    return got is not None and [_name_key(g) for g in got] == [_name_key(p) for p in parts]


def _cy(box: dict) -> float:
    return box["y"] + box["h"] / 2


def _brief(line: dict) -> dict:
    return {k: line.get(k) for k in ("text", "x", "y", "w", "h")}


@dataclass
class Screen:
    frame: dict
    lines: list[dict]
    tokens: list[dict] = field(default_factory=list)
    observed_at: float | None = None  # monotonic time the frame was requested (NetdiskClient.observe)

    @property
    def size(self) -> tuple[int, int]:
        return self.frame["width"], self.frame["height"]

    def find(self, match: str | re.Pattern, *, below: float | None = None, above: float | None = None,
             left_of: float | None = None, right_of: float | None = None, literal: bool = False) -> list[dict]:
        """Labels compare whitespace-free; ``literal=True`` (names) keeps internal spaces."""
        fold = _name_key if literal else _norm

        def ok(line: dict) -> bool:
            text = fold(line.get("text"))
            hit = bool(match.search(text)) if isinstance(match, re.Pattern) else text == fold(match)
            return hit and (below is None or _cy(line) > below) and (above is None or _cy(line) < above) \
                and (left_of is None or line["x"] + line["w"] / 2 < left_of) and (right_of is None or line["x"] >= right_of)
        return [line for line in self.lines if ok(line)]

    def unique(self, match: str | re.Pattern, code: str, *, literal: bool = False, **region: float) -> dict:
        found = self.find(match, literal=literal, **region)
        if len(found) != 1:
            raise StepFailed(code if not found else f"{code}_ambiguous",
                             matches=[_brief(line) for line in found][:6], texts=self.texts()[:60])
        return found[0]

    def same_row(self, anchor: dict) -> list[dict]:
        return sorted((line for line in self.lines if abs(_cy(line) - _cy(anchor)) < max(anchor["h"], 8) * .6),
                      key=lambda line: line["x"])

    def span(self, line: dict, text: str) -> dict | None:
        """Exact bbox of ``text`` inside ``line`` from its OCR tokens (no width guessing).

        Tokens are joined in order; the substring must start and end on token
        boundaries, otherwise None.
        """
        parts = sorted((t for t in self.tokens if t.get("parent_line_id") == line.get("line_id")),
                       key=lambda t: t.get("order", 0))
        target = _norm(text)
        for start in range(len(parts)):
            joined = ""
            for end in range(start, len(parts)):
                joined += _norm(parts[end].get("text"))
                if joined == target:
                    chosen = parts[start:end + 1]
                    x1 = min(t["x"] for t in chosen)
                    y1 = min(t["y"] for t in chosen)
                    x2 = max(t["x"] + t["w"] for t in chosen)
                    y2 = max(t["y"] + t["h"] for t in chosen)
                    return {"text": text, "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}
                if not target.startswith(joined):
                    break
        return None

    def texts(self) -> list[str]:
        return [str(line.get("text") or "") for line in self.lines]


def _center(screen: Screen, box: dict) -> tuple[float, float]:
    width, height = screen.size
    return (box["x"] + box["w"] / 2) / width, _cy(box) / height


def list_header(screen: Screen) -> dict | None:
    """The file-list header ("文件"/"文件名" with 大小/修改时间 on the same row).

    A merged "文件已全部加载，共N个" / "文件已选中N个文件/文件夹" line counts too; it is
    returned trimmed to the name part (proportional width) with the marker under ``merged``.
    """
    found = []
    for line in screen.lines:
        text = _norm(line.get("text"))
        merged = MERGED_HEADER_RE.match(text)
        if text in LIST_HEADER_NAMES or merged:
            peers = {_norm(other.get("text")) for other in screen.same_row(line) if other is not line}
            if peers & set(LIST_HEADER_PEERS):
                if merged:
                    line = {**line, "text": merged.group(1), "w": line["w"] * len(merged.group(1)) / len(text),
                            "merged": merged.group(2)}
                found.append(line)
    return found[0] if len(found) == 1 else None


def selection_count(screen: Screen) -> int | None:
    """N from the unique full "已选中N个文件/文件夹" on the list header row; None when absent or clipped."""
    header = list_header(screen)
    if header is None:
        return None
    found = [int(m.group(1)) for line in screen.same_row(header)
             if line is not header and (m := SELECTION_RE.match(_norm(line.get("text"))))]
    return found[0] if len(found) == 1 else None


def toolbar_download(screen: Screen) -> list[dict]:
    """Sized 下载(…) buttons on the toolbar row (the row of the unique 上传 above the list header).

    A context menu's plain 下载 can sit above the header too; it never has a
    size and is not on the 上传 row.  No provable toolbar row: StepFailed.
    """
    header = list_header(screen)
    if header is None:
        raise StepFailed("file_list_header_not_found", texts=screen.texts()[:60])
    upload = screen.unique("上传", "toolbar_not_found", above=header["y"])
    return [line for line in screen.same_row(upload) if DOWNLOAD_SIZE_RE.match(_norm(line.get("text")))]


def selection_evidence(screen: Screen) -> list[dict]:
    """Lines showing something is selected: toolbar 下载(…) or any (even clipped) 已选中 on the header row."""
    header = list_header(screen)
    if header is None:
        raise StepFailed("file_list_header_not_found", texts=screen.texts()[:60])
    return toolbar_download(screen) + [
        line for line in screen.same_row(header) if line is not header and _norm(line.get("text")).startswith("已选中")]


def context_menu_column(screen: Screen) -> list[dict]:
    """Lines of an open file context menu: >= 3 known menu labels in one column (|dx| <= 8), else []."""
    labels = [line for line in screen.lines if _norm(line.get("text")) in MENU_LABELS]
    best: list[dict] = []
    for line in labels:
        column = [o for o in labels if abs(o["x"] - line["x"]) <= 8]
        if len(column) > len(best):
            best = column
    return best if len(best) >= 3 else []


def blank_band_right_of_crumb(screen: Screen) -> dict | None:
    """A verified empty strip on the breadcrumb row, right of the crumb and left of any menu.

    Must cover no OCR line and be near-white in the frame's pixels; None otherwise (e.g. root: no crumb).
    """
    header = list_header(screen)
    crumb = breadcrumb(screen, header) if header else None
    if crumb is None:
        return None
    end = crumb[0]["x"] + crumb[0]["w"]
    for line in screen.same_row(crumb[0]):  # the contiguous crumb run, as in breadcrumb()
        if line["x"] >= crumb[0]["x"] and line["x"] - end <= 40:
            end = max(end, line["x"] + line["w"])
    left = end + 30
    right = left + BLANK_BAND_WIDTH
    menu = context_menu_column(screen)
    if menu and right > min(line["x"] for line in menu) - 20:
        return None
    band = {"x": left, "y": _cy(crumb[0]) - 8, "w": BLANK_BAND_WIDTH, "h": 16}
    if right >= screen.size[0] or any(line["x"] < right + 4 and line["x"] + line["w"] > left - 4
                                      and line["y"] < band["y"] + band["h"] + 4 and line["y"] + line["h"] > band["y"] - 4
                                      for line in screen.lines):
        return None
    pixels = frame_image(screen.frame).crop((int(left), int(band["y"]), int(right), int(band["y"] + band["h"])))
    if min(low for low, _high in pixels.getextrema()) < 235:
        return None
    return band


def loaded_count(screen: Screen) -> int | None:
    """N from the header row's "已全部加载，共N个": the unselected list's positive marker."""
    header = list_header(screen)
    if header is None:
        return None
    texts = [_norm(line.get("text")) for line in screen.same_row(header) if line is not header]
    texts += [header["merged"]] if header.get("merged") else []  # the merged source line never matches ^已
    found = [int(m.group(1)) for text in texts if (m := LOADED_RE.match(text))]
    return found[0] if len(found) == 1 else None


def _quota_bytes(number: str, unit: str) -> int:
    """Displayed (rounded, binary-unit) size -> bytes; precision is only that of the label."""
    power = QUOTA_UNITS.index(unit.upper()) + 1 if unit else 0
    return int(float(number) * 1024 ** power)


def read_quota(screen: Screen) -> dict | None:
    """Used/total from the top bar label right of 会员中心 on its row; None unless exactly one parses."""
    _, height = screen.size
    anchors = [line for line in screen.find(re.compile("^" + QUOTA_ANCHOR)) if _cy(line) < height * .15]
    if len(anchors) != 1:
        return None
    anchor = anchors[0]
    found = []
    for line in screen.same_row(anchor):
        if line is not anchor and line["x"] < anchor["x"]:
            continue
        text = _norm(line.get("text"))
        text = text[len(QUOTA_ANCHOR):] if text.startswith(QUOTA_ANCHOR) else text
        if m := QUOTA_RE.match(text):
            found.append((line, m))
    if len(found) != 1:
        return None
    line, m = found[0]
    return {"used": f"{m.group(1)}{m.group(2).upper()}", "total": f"{m.group(3)}{m.group(4).upper()}",
            "used_bytes": _quota_bytes(m.group(1), m.group(2)), "total_bytes": _quota_bytes(m.group(3), m.group(4)),
            "raw": str(line.get("text") or "")}


def frame_image(frame: dict):
    """The frame's single full-size patch as an RGB PIL image (the pixels OCR and input refer to)."""
    from PIL import Image
    patches = frame.get("patches") or []
    if not frame.get("full") or len(patches) != 1             or (patches[0]["width"], patches[0]["height"]) != (frame["width"], frame["height"]):
        raise StepFailed("partial_frame")
    return Image.open(BytesIO(base64.b64decode(patches[0]["image"].split(",", 1)[1]))).convert("RGB")


def _blue_components(pixels, x0: int, y0: int) -> list[dict]:
    """4-connected components of checkbox blue in an RGB array; boxes in frame coordinates."""
    height, width = len(pixels), len(pixels[0]) if pixels else 0
    mask = [[b >= 220 and r <= 130 and 110 <= g <= 190 for r, g, b in row] for row in pixels]
    seen, found = set(), []
    for y in range(height):
        for x in range(width):
            if not mask[y][x] or (x, y) in seen:
                continue
            stack, cells = [(x, y)], []
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                cells.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < width and 0 <= ny < height and mask[ny][nx] and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            xs, ys = [c[0] for c in cells], [c[1] for c in cells]
            found.append({"x": x0 + min(xs), "y": y0 + min(ys), "w": max(xs) - min(xs) + 1,
                          "h": max(ys) - min(ys) + 1, "pixels": len(cells)})
    return found


def _checkbox_boxes(screen: Screen, x0: int, y0: int, x1: int, y1: int) -> tuple[list[dict], int]:
    """Checkbox-like blue squares (13..19 px, mostly filled) and the count of all non-trivial blue blobs in a region."""
    image = frame_image(screen.frame)
    if image.size != screen.size:
        raise StepFailed("checkbox_frame_size_mismatch", image=image.size, frame=screen.size)
    x0, y0 = max(0, x0), max(0, y0)
    crop = image.crop((x0, y0, x1, y1))
    pixels = [[crop.getpixel((x, y)) for x in range(crop.width)] for y in range(crop.height)]
    found = _blue_components(pixels, x0, y0)
    lo, hi = CHECKBOX_SIDE
    boxes = [c for c in found if lo <= c["w"] <= hi and lo <= c["h"] <= hi and abs(c["w"] - c["h"]) <= 2
             and c["pixels"] >= .55 * c["w"] * c["h"]]
    return boxes, len([c for c in found if c["pixels"] >= 12])


def header_checkbox(screen: Screen) -> dict:
    """The header checkbox box measured in this frame's pixels; StepFailed unless exactly one fits.

    Search region: left of the "文件" header text, its row ±14 px.  The box
    must be one blue near-square of 13..19 px, mostly filled (partial bar
    or check mark cut out), centred on the header row.  Only on a measured
    window profile.
    """
    if screen.size not in CHECKBOX_PROFILES:
        raise StepFailed("checkbox_profile_unverified", size=screen.size)
    header = list_header(screen)
    if header is None:
        raise StepFailed("file_list_header_not_found", texts=screen.texts()[:60])
    boxes, blobs = _checkbox_boxes(screen, int(header["x"]) - CHECKBOX_ROI_LEFT, int(_cy(header)) - 14,
                                   int(header["x"]) - 2, int(_cy(header)) + 15)
    boxes = [c for c in boxes if abs(c["y"] + c["h"] / 2 - _cy(header)) <= 3]
    if len(boxes) != 1 or blobs != 1:
        raise StepFailed("header_checkbox_not_found" if not boxes else "header_checkbox_ambiguous",
                         boxes=boxes[:4], blobs=blobs)
    return {k: boxes[0][k] for k in ("x", "y", "w", "h")}


def _share_checkbox_region(screen: Screen, header: dict, x: float, cy: float) -> tuple[list[dict], int]:
    """Checkbox candidates within ±20 px of column ``x`` on the row centred at ``cy`` (never the header text)."""
    boxes, blobs = _checkbox_boxes(screen, int(x) - 20, int(cy) - 14, min(int(x) + 21, int(header["x"]) - 2),
                                   int(cy) + 15)
    return [c for c in boxes if abs(c["y"] + c["h"] / 2 - cy) <= 4], blobs


def share_checkbox_anchor(screen: Screen, header: dict, count: int) -> dict:
    """Click column of the share list's checkboxes, proven on this frame; StepFailed (no input) otherwise.

    Only on a measured profile (SHARE_CHECKBOX_PROFILES).  With a selection
    (``count`` > 0) the header region must hold exactly one blue square within
    SHARE_CHECKBOX_TOLERANCE of the predicted column, and its measured centre
    is the anchor.  With none, the region must hold no blue at all (empty
    outline) and the profile column is used; each later click is proven by
    the box turning checked on it.  Header clear/select-all and row selection
    all use this one anchor.
    """
    offset = SHARE_CHECKBOX_PROFILES.get(screen.size)
    if offset is None:
        raise StepFailed("share_checkbox_profile_unverified", size=screen.size)
    predicted = header["x"] - offset
    boxes, blobs = _share_checkbox_region(screen, header, predicted, _cy(header))
    anchor = {"x": predicted, "header_x": header["x"], "size": screen.size, "measured": False}
    if count == 0:
        if blobs:
            raise StepFailed("share_checkbox_state_mismatch", selected=0, blobs=blobs)
        return anchor
    if len(boxes) != 1 or blobs != 1:
        raise StepFailed("share_checkbox_not_found" if not boxes else "share_checkbox_ambiguous",
                         boxes=boxes[:4], blobs=blobs)
    centre = boxes[0]["x"] + boxes[0]["w"] / 2
    if abs(centre - predicted) > SHARE_CHECKBOX_TOLERANCE:
        raise StepFailed("share_checkbox_off_profile", measured=centre, predicted=predicted)
    return {**anchor, "x": centre, "measured": True}


def share_checkbox_state(screen: Screen, anchor: dict, header: dict, cy: float) -> str:
    """'checked' (one blue box on the anchor column), 'empty' (no blue) or 'ambiguous' for the row at ``cy``.

    The frame must still match the anchor: same size, header text within 2 px.
    """
    if screen.size != anchor["size"] or abs(header["x"] - anchor["header_x"]) > 2:
        raise StepFailed("share_checkbox_anchor_moved", size=screen.size, header_x=header["x"])
    boxes, blobs = _share_checkbox_region(screen, header, anchor["x"], cy)
    if not blobs:
        return "empty"
    if len(boxes) == 1 and blobs == 1 and abs(boxes[0]["x"] + boxes[0]["w"] / 2 - anchor["x"]) <= 3:
        return "checked"
    return "ambiguous"


def list_rows(screen: Screen) -> list[dict]:
    """Visible list rows: ``{"name", "kind": "dir"|"file", "size", "line"}``.

    A row is a name-column line (starting within LIST_NAME_SPAN of the
    header, so hover action icons are not names) below the header whose OCR
    row carries a 修改时间 date cell; that excludes the inline new-folder
    edit.  Rows reaching under the floating 知识搜索 box are dropped (it hides
    their cells); scrolling brings them up.  ``kind`` is ``dir`` when the
    类型 cell reads 文件夹; ``size`` is the raw 大小 cell (folders: ``-``).
    """
    header = list_header(screen)
    if header is None:
        return []
    columns = {_norm(line.get("text")): line for line in screen.same_row(header) if line is not header}
    size_col = columns.get(LIST_SIZE)
    if size_col is None:
        return []
    type_col = columns.get(LIST_TYPE)
    size_right = type_col["x"] - 10 if type_col else size_col["x"] + size_col["w"] + 80
    overlay = [line["y"] for line in screen.lines if LIST_OVERLAY_RE.match(_norm(line.get("text")))]
    bottom = min(overlay) - 20 if overlay else float("inf")
    rows = []
    for line in screen.lines:
        if _cy(line) <= header["y"] + header["h"] or not header["x"] - 60 <= line["x"] < header["x"] + LIST_NAME_SPAN \
                or line["x"] + line["w"] > size_col["x"] - 10 or line["y"] + line["h"] > bottom:
            continue
        row_lines = screen.same_row(line)
        names = [c for c in row_lines if header["x"] - 60 <= c["x"] and c["x"] + c["w"] <= size_col["x"] - 10]
        if any(c["x"] < line["x"] for c in names):
            continue  # one name per row: this is a hover badge (常看) or the tail of a split name
        name, right = _literal(line.get("text")), line["x"] + line["w"]
        for c in names:  # join only a tight continuation (a name OCR-split at a space)
            if c["x"] > line["x"] and 0 <= c["x"] - right <= LIST_NAME_JOIN_GAP:
                name, right = f"{name} {_literal(c.get('text'))}", c["x"] + c["w"]
        cells = [c for c in row_lines if c is not line and c["x"] >= size_col["x"] - 60]
        if not any(MTIME_RE.match(_norm(c.get("text"))) for c in cells):
            continue
        size = [c for c in cells if c["x"] < size_right and not MTIME_RE.match(_norm(c.get("text")))]
        kind = "dir" if any(_norm(c.get("text")) == FOLDER_TYPE for c in cells) else "file"
        rows.append({"name": name, "kind": kind,
                     "size": _norm(size[0].get("text")) if len(size) == 1 else None, "line": line})
    return rows


def row_mtime_cell(screen: Screen, row: dict) -> dict | None:
    """The target row's OCR box under the 修改时间 header (right-click anchor).

    A right-click there opens the menu at the right edge, leaving the toolbar
    下载(size) and the header's selection count readable.
    """
    header = list_header(screen)
    if header is None:
        return None
    mtime = [line for line in screen.same_row(header) if _norm(line.get("text")) == LIST_MTIME]
    if len(mtime) != 1:
        return None
    column = mtime[0]["x"] + mtime[0]["w"] / 2
    cells = [line for line in screen.same_row(row) if line is not row and line["x"] <= column <= line["x"] + line["w"]]
    return cells[0] if len(cells) == 1 else None


def context_menu_item(before: Screen, after: Screen, text: str) -> list[dict]:
    """Exact ``text`` lines that appeared with the click, in a column of other new menu entries."""
    fresh = [line for line in after.lines if not _was_shown(before, line)]
    found = []
    for line in fresh:
        if _norm(line.get("text")) != text:  # exact: the toolbar's 下载(…) never qualifies
            continue
        peers = {_norm(o.get("text")) for o in fresh if o is not line and abs(o["x"] - line["x"]) <= 8}
        if len(peers & set(CONTEXT_MENU_PEERS)) >= 3:
            found.append(line)
    return found


def _was_shown(screen: Screen, line: dict) -> bool:
    """Same text at (about) the same place on an earlier frame."""
    return any(_norm(o.get("text")) == _norm(line.get("text")) and abs(o["x"] - line["x"]) <= 6
               and abs(o["y"] - line["y"]) <= 6 for o in screen.lines)


def breadcrumb(screen: Screen, header: dict) -> tuple[dict, list[str]] | None:
    """Crumb line(s) above the header that start with 全部文件; returns (root line, segments).

    OCR merges "全部文件/新建文件夹" (or "> ") into one line or splits it; the
    contiguous lines on the root's row are joined, then split on / or >.
    Segments keep their internal spaces; only the 全部文件 label is normalised.
    None when the crumb is missing, duplicated or does not parse.
    """
    roots = [line for line in screen.lines if _cy(line) < header["y"] and _norm(line.get("text")).startswith(ROOT_CRUMB)]
    if len(roots) != 1:
        return None
    root = roots[0]
    row, right = [], root["x"]
    for line in screen.same_row(root):
        if line["x"] >= root["x"] and line["x"] - right <= 40:
            row.append(line)
            right = max(right, line["x"] + line["w"])
    chain = [c.strip() for c in CRUMB_SPLIT_RE.split("/".join(_literal(line.get("text")) for line in row))]
    chain = [c for c in chain if c]
    if not chain or _norm(chain[0]) != ROOT_CRUMB:
        return None
    return root, chain[1:]


def screen_path(screen: Screen) -> list[str] | None:
    """Segments of the open folder, ``[]`` for root, None when not provable (fail closed).

    Root needs positive evidence: a bare 全部文件 crumb, or no crumb at all
    together with the root home's 最近文件 strip above the list header, or no
    crumb band at all (toolbar 上传 within ROOT_TOOLBAR_GAP of the header, the
    全部文件 root list) — as observed on 8.1.5.103.  A crumb that is present but does not parse is
    never taken for root.
    """
    header = list_header(screen)
    if header is None:
        return None
    if any(_cy(line) < header["y"] and _norm(line.get("text")).startswith(ROOT_CRUMB) for line in screen.lines):
        crumb = breadcrumb(screen, header)
        return crumb[1] if crumb else None
    if screen.find(ROOT_HOME_MARK, above=header["y"]):
        return []
    # 全部文件 root list (after a root-crumb click): no crumb band, header right under the toolbar.
    uploads = screen.find("上传", above=header["y"])
    if len(uploads) == 1 and 0 < header["y"] - uploads[0]["y"] < ROOT_TOOLBAR_GAP:
        return []
    return None


def top_tab_home(screen: Screen) -> dict:
    """The one 首页 in the top tab band, at the left edge; raises (no input) otherwise.

    Every 首页 in the band counts, so a second one anywhere in it (a page title, a web crumb)
    fails closed as ambiguous; a lone 首页 that is not leftmost is no tab.  Facts carry boxes only.
    """
    width, height = screen.size
    found = screen.find(SIDE_NAV_HOME, above=height * TOP_TAB_BAND)
    if not found:
        raise StepFailed("side_nav_home_not_found")
    if len(found) > 1:
        raise StepFailed("top_tab_home_ambiguous", matches=[_brief(line) for line in found][:6])
    home = found[0]
    if home["x"] + home["w"] / 2 >= width * TOP_TAB_HOME_LEFT or home["y"] < 0:
        raise StepFailed("top_tab_home_not_proven", matches=[_brief(home)])
    return home


def crumb_space_variant(seen: list[Screen], row: dict, parent: list[str], path: list[str] | None) -> str:
    """Whether a crumb that differs from the clicked row only in ASCII spaces is that row; "proven" or why not.

    Own file view only, after one double-click on the unique exact ``row`` under the proven
    ``parent``: the crumb must be ``parent`` (exact, see :func:`same_path`) plus one segment
    equal to the row text with ASCII spaces removed on both sides (OCR: "Example01示例" row,
    "Example01 示例" crumb).  The frames read before the click must prove the parent listing
    complete (one 已全部加载，共N个 count, N distinct rows read, the row among them) and hold no
    other name that folds onto it under :func:`_list_key` ("a b" beside "ab").  Names, files
    and every other comparison stay exact.
    """
    if path is None or len(path) != len(parent) + 1 or not same_path(path[:-1], parent):
        return "parent_not_verified"
    clicked, shown = _literal(row.get("text")), _literal(path[-1])
    if clicked.replace(" ", "") != shown.replace(" ", ""):
        return "not_space_variant"
    return _parent_listing_proof(seen, clicked)


def _parent_listing_proof(seen: list[Screen], clicked: str) -> str:
    """The frames read before the click list the whole parent, ``clicked`` once and without a fold twin.

    One 已全部加载，共N个 count, N distinct rows read, ``clicked`` among them, and no other
    name equal to it under :func:`_list_key` ("a b" beside "ab"); "proven" or why not.
    """
    counts = {loaded_count(frame) for frame in seen}
    names = {_literal(r["name"]) for frame in seen for r in list_rows(frame)}
    if len(counts) != 1 or None in counts or counts != {len(names)} or clicked not in names:
        return "parent_listing_incomplete"
    if any(_list_key(name) == _list_key(clicked) for name in names - {clicked}):
        return "name_fold_collision"
    return "proven"


def own_crumb_elided(path: list[str] | None) -> bool:
    """The own-view crumb carries a whole elision segment ("全部文件/./a/b", "…", ".."); never a path."""
    return path is not None and any(PICKER_ELLIPSIS_RE.match(seg) for seg in path)


def elided_own_crumb_proves(seen: list[Screen], before: list[str], row: dict, part: str,
                            aliases: list[set[str]], path: list[str]) -> str:
    """Whether an elided own-view crumb right after one open is ``parent + [part]``; "proven" or why not.

    Measured (sanitized): deep in the own tree the client shortens the crumb and OCR reads it
    as "全部文件/. /Folder01示例/Sub" (the mark as one period, the parent's space dropped).  The
    elided crumb never proves a prefix by itself; it is accepted only as the stateful result of
    one double-click on the unique exact ``row`` (requested ``part``) from the FULL parent this
    call proved, under all of:

    * ``aliases``: per depth of that parent, the forms this call proved for it (the requested
      segment and, after a proven space variant, the crumb's own form); no other folding.
    * Stable parent: every frame read before the click (``seen``) shows the crumb ``before``
      accepted for the parent, and ``path`` differs from it (fresh, not a replay).
    * Exactly one elision mark, as a whole segment; every visible segment before it matches the
      parent's head, at least PICKER_ELIDED_MIN_SUFFIX visible segments after it match the
      tail, and it hides at least one segment.  The last segment is ``part`` (or, as in
      :func:`crumb_space_variant`, the clicked row text up to ASCII spaces).
    * The parent listing read before the click is complete and fold-free
      (:func:`_parent_listing_proof`).
    """
    if any(screen_path(frame) != before for frame in seen) or path == before:
        return "stale_parent_state"
    marks = [i for i, seg in enumerate(path) if PICKER_ELIDED_RE.search(seg) or seg == PICKER_DOT_ELISION]
    if len(marks) != 1 or not PICKER_ELLIPSIS_RE.match(path[marks[0]]):
        return "elision_not_unique"
    head, tail = path[:marks[0]], path[marks[0] + 1:]
    depth = len(aliases) + 1
    if len(tail) < PICKER_ELIDED_MIN_SUFFIX or len(head) + len(tail) >= depth:
        return "elided_span_unproven"
    known = lambda seg, forms: _name_key(seg) in {_name_key(f) for f in forms}
    if not all(known(seg, aliases[i]) for i, seg in enumerate(head)):
        return "elided_head_mismatch"
    start = depth - len(tail)
    if not all(known(seg, aliases[start + j]) for j, seg in enumerate(tail[:-1])):
        return "elided_tail_mismatch"
    clicked, shown = _literal(row.get("text")), _literal(tail[-1])
    if not same_path([shown], [part]) and clicked.replace(" ", "") != shown.replace(" ", ""):
        return "elided_child_mismatch"
    return _parent_listing_proof(seen, clicked)


def material_tag_row(seen: list[Screen], screen: Screen, part: str, before: list[str] | None) -> tuple[dict | None, str]:
    """The own-list folder row for ``part`` read with its material-tag ``_`` dropped; (row, "proven") or (None, why).

    Measured (sanitized): a parent listing read the folder "示例合集_sd-abcdef012345" as
    "示例合集sd-abcdef012345", so the exact row lookup failed.  Navigation only, and only when
    that lookup found nothing: ``part`` must end in "_" plus a stable material ID
    (MATERIAL_TAG_RE).  The one accepted row text is ``part`` without that single "_"; the
    prefix and the ID stay exact.  All of these must hold before the one open:

    * every frame read for the lookup (``seen``) shows the crumb ``before``, which the
      previous step accepted for the parent (unknown is refused);
    * the parent listing is complete and has no fold twin (:func:`_parent_listing_proof`);
      folders that differ only in "_" are refused as a collision;
    * on ``screen``, exactly one row line reads that text, and that row is a folder.

    After the open, the usual crumb proofs judge the opened row's text.
    """
    m = MATERIAL_TAG_RE.match(part)
    if m is None:
        return None, "not_material_tag"
    observed = m["prefix"] + m["tag"]
    if before is None:
        return None, "parent_not_verified"
    if any(screen_path(frame) != before for frame in seen):
        return None, "stale_parent_state"
    if observed not in {_literal(r["name"]) for frame in seen for r in list_rows(frame)}:
        return None, "tag_row_not_found"  # other ID, prefix or spelling: no row
    proof = _parent_listing_proof(seen, observed)
    if proof != "proven":
        return None, proof
    rows = [r for r in list_rows(screen) if _literal(r["line"].get("text")) == observed]
    if len(rows) != 1 or rows[0]["kind"] != "dir":
        return None, "tag_row_not_unique"
    return rows[0]["line"], "proven"


def canonical_entries(path: str, rows: list[dict], listing: object) -> tuple[list[dict] | None, str]:
    """Native rows of the complete folder ``path`` renamed to a provider listing's exact names.

    ``listing`` follows ``web_directories.list_directory``.  Accepted only when it is
    ``listed`` for exactly ``path``, every entry is a valid direct child (``path/name``)
    with a unique id and name, names stay unique under :func:`_list_key`, the counts
    agree, and each entry pairs with exactly one native row under :func:`_list_key` with
    the same kind. An explicitly ellipsized material row can pair by its exact visible
    prefix only when that prefix includes at least five material-ID hex digits and
    identifies one entry in the entire provider listing. No edit-distance matching.
    Native OCR renders full-width parentheses as ASCII, and the ideographic
    separator in a parenthesized ASCII label list (App、Web) as a dot. These
    measured aliases apply only inside this complete-list bijection, refusing
    collisions. A two-dot terminal clip needs eight exact material-ID digits
    (three dots/ellipsis need five). Literal provider names are never rewritten.
    Native kind and size label are kept. (entries, "ok") or (None, why).
    """
    if not isinstance(listing, dict) or listing.get("status") != "listed":
        return None, "provider_not_listed"
    if listing.get("path") != path:
        return None, "provider_path_mismatch"
    entries, count = listing.get("entries"), listing.get("count")
    if not isinstance(entries, list) or type(count) is not int or count != len(entries):
        return None, "provider_count_invalid"
    if count != len(rows):
        return None, "count_mismatch"
    def name_key(text):
        key = _list_key(text).translate(str.maketrans({"（": "(", "）": ")"}))
        return re.sub(r"\(([A-Za-z][A-Za-z0-9]*(?:[、.][A-Za-z][A-Za-z0-9]*)+)\)",
                      lambda m: "(" + m[1].replace("、", ".") + ")", key)
    ids, names, keys = set(), set(), {}
    for entry in entries:
        if not isinstance(entry, dict):
            return None, "provider_entry_invalid"
        row_id, name, is_dir = entry.get("id"), entry.get("name"), entry.get("is_dir")
        if not isinstance(row_id, str) or not row_id or not isinstance(name, str) or type(is_dir) is not bool:
            return None, "provider_entry_invalid"
        try:
            validate_file_name(name)
        except ValueError:
            return None, "provider_name_invalid"
        if entry.get("path") != f"{path.rstrip('/')}/{name}":
            return None, "provider_entry_path_mismatch"
        if row_id in ids or name in names or name_key(name) in keys:
            return None, "provider_name_collision"
        ids.add(row_id)
        names.add(name)
        keys[name_key(name)] = entry
    all_keys = dict(keys)  # uniqueness is judged over the whole list, never remaining rows
    out = []
    for row in rows:
        key = name_key(row["name"])
        entry = keys.pop(key, None)
        if entry is None:
            # The measured native list clips long material names, including the
            # stable ID suffix. Only explicit terminal ellipsis permits truncation.
            clipped = re.fullmatch(r"(.+sd-[0-9a-f]{5,11})(?:\.{3}|…)", key)
            if clipped is None:
                clipped = re.fullmatch(r"(.+sd-[0-9a-f]{8,11})\.{2}", key)
            if clipped:
                matches = [k for k in all_keys if k.startswith(clipped[1])]
                if len(matches) == 1:
                    entry = keys.pop(matches[0], None)
        if entry is None:
            return None, "unpaired_row"
        if entry["is_dir"] != (row["kind"] == "dir"):
            return None, "kind_mismatch"
        out.append({**row, "name": entry["name"]})
    return (out, "ok") if not keys else (None, "unpaired_row")


def own_file_view(screen: Screen) -> bool:
    """Own file list: header plus a proven folder or root (see :func:`screen_path`)."""
    return list_header(screen) is not None and screen_path(screen) is not None


def _native_index(value: str) -> tuple[str, str] | None:
    """(route, query) of the official client's own local index page URI, None for any other URI."""
    try:
        uri = urlsplit(value)
        local = unquote(uri.path, errors="strict")
    except (ValueError, UnicodeError):
        return None
    if uri.scheme != "file" or uri.netloc or uri.query or not NATIVE_LOCAL_PATH_RE.match(local) \
            or not local.endswith(NATIVE_INDEX_SUFFIX):
        return None
    route, _, query = uri.fragment.partition("?")
    return route, query


def _native_aipan_panel(document: dict) -> bool:
    """The official nested AI search panel Document (NATIVE_AIPAN); its query is neither judged nor kept."""
    try:
        uri = urlsplit(str(document.get("value") or ""))
        port = uri.port
    except ValueError:
        return False
    return document.get("nested") is True and (uri.scheme, uri.netloc, uri.path) == NATIVE_AIPAN         and port is None and not uri.fragment


def native_own_path(native: dict | None, screen: Screen) -> tuple[list[str] | None, str]:
    """The exact own-view path from the main window's native Document; (segments, "proven") or (None, why).

    Measured (sanitized): after opening "…/Example_sd-abcdef012345" the own crumb's pixels clipped
    the last segment ("Example_sd-abcdef.."), while the window's root UIA Document value held the
    full path.  ``native`` is one fresh :meth:`ProjectionDriver.native_documents` read; all must hold:

    * provenance: the read is of the window ``screen`` was captured from (same identity), by the
      official app, untruncated, and started after that capture, within NATIVE_MAX_AGE;
    * ``screen`` itself is the own file view (:func:`own_file_view`);
    * exactly one own Document: top-level, named NATIVE_DOCUMENT_NAME, its value a local file URI
      ending in NATIVE_INDEX_SUFFIX (a second one, even nested, refuses); besides it at most the one
      nested official AI search panel (:func:`_native_aipan_panel`); any other Document refuses;
    * its fragment is route "/" with exactly one ``path`` and NATIVE_SHARE_PARAM absent or "null"
      (share, login and other routes refuse);
    * the path is absolute and already normal (:func:`validate_remote_dir` leaves it unchanged).

    Nothing is cached, and reasons carry no text from the value (it holds local install paths).
    """
    if not isinstance(native, dict):
        return None, "native_unavailable"
    read_at, observed_at = native.get("read_at"), screen.observed_at
    if native.get("window_id") != screen.frame.get("window_id") or not isinstance(read_at, (int, float)) \
            or observed_at is None or not 0 <= read_at - observed_at <= NATIVE_MAX_AGE:
        return None, "stale_provenance"
    if not CLIENT_APP_RE.match(str(native.get("application") or "")):
        return None, "wrong_app"
    documents = native.get("documents")
    if native.get("truncated") is not False or not isinstance(documents, list):
        return None, "documents_truncated"
    if not own_file_view(screen):
        return None, "not_own_view"
    indexes = [d for d in documents if _native_index(str(d.get("value") or "")) is not None]
    if len(indexes) > 1:
        return None, "own_document_not_unique"
    if not indexes:
        return None, "own_document_missing"
    others = [d for d in documents if d is not indexes[0]]
    if len(others) > 1 or not all(_native_aipan_panel(d) for d in others):
        return None, "interfering_document"
    doc = indexes[0]
    if doc.get("nested") is not False or doc.get("name") != NATIVE_DOCUMENT_NAME:
        return None, "wrong_document"
    route, query = _native_index(str(doc["value"]))
    if route != "/":
        return None, "not_own_route"
    params: dict[str, list[str]] = {}
    for item in query.split("&") if query else []:
        key, sep, raw = item.partition("=")
        try:
            params.setdefault(unquote(key, errors="strict"), []).append(unquote(raw, errors="strict") if sep else "")
        except UnicodeError:
            return None, "path_query_invalid"
    if params.get(NATIVE_SHARE_PARAM, ["null"]) != ["null"]:
        return None, "share_view"
    paths = params.get("path", [])
    if len(paths) != 1:
        return None, "path_not_unique" if paths else "path_missing"
    path = paths[0]
    try:
        if path != "/" and validate_remote_dir(path) != path:
            return None, "path_not_normal"
    except ValueError:
        return None, "path_invalid"
    return [p for p in path.split("/") if p], "proven"


# ---- client --------------------------------------------------------------------

@dataclass
class TransferResult:
    status: str
    next: str
    key: str | None = None
    facts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        # Public status/next/key are fixed; facts can never override them.
        return {**self.facts, "status": self.status, "next": self.next, "key": self.key}


class NetdiskClient:
    """One instance per task; ``lease_check`` raises when the GUI lease is lost.

    ``canonical_listing`` (optional, default None): a caller-injected public listing
    function with the contract of ``web_directories.list_directory`` — called as
    ``canonical_listing(path)`` and returning ``{status: "listed", path, count,
    entries: [{id, name, path, is_dir}]}`` — e.g. ``functools.partial(list_directory,
    context=...)``.  When given, :meth:`list_remote_dir` replaces OCR row names with
    the exact provider names after a strict one-to-one check (see
    :func:`canonical_entries`); any doubt fails closed.  :meth:`open_remote_dir` also uses it
    to address a requested child folder whose row OCR keeps misreading
    (:meth:`_canonical_child_row`), proven only by the exact native path after the open.
    Without it nothing changes.
    """

    def __init__(self, *, ledger: TransferLedger, lease_check: Callable[[], object],
                 driver: ProjectionDriver | None = None, settle: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep, max_scrolls: int = 8, poll_attempts: int = 10,
                 canonical_listing: Callable[[str], dict] | None = None):
        self.canonical_listing = canonical_listing
        self.ledger = ledger
        self.lease_check = lease_check
        self.driver = driver or ProjectionDriver()
        self.settle = settle
        self.sleep = sleep
        self.max_scrolls = max_scrolls
        self.poll_attempts = poll_attempts

    # -- windows / observation ---------------------------------------------------

    def client_windows(self) -> list[dict]:
        return [w for w in self.driver.list_windows() if CLIENT_APP_RE.match(str(w.get("application") or ""))]

    def main_window(self) -> str:
        rows = [w for w in self.client_windows() if w.get("title") != FILE_DIALOG_TITLE
                and not SHARE_WINDOW_RE.match(str(w.get("title") or ""))  # an open 分享： window is not the main one
                and w.get("width", 0) >= 600 and w.get("height", 0) >= 400]
        if len(rows) != 1:
            raise StepFailed("client_main_window_not_unique", windows=[(w.get("title"), w.get("width"), w.get("height")) for w in rows])
        return rows[0]["id"]

    def dialog_window(self) -> str | None:
        rows = [w for w in self.client_windows() if w.get("title") == FILE_DIALOG_TITLE]
        if len(rows) > 1:
            raise StepFailed("file_dialog_not_unique")
        return rows[0]["id"] if rows else None

    def observe(self, window_id: str) -> Screen:
        self.lease_check()  # capture may restore/repaint the window
        observed_at = time.monotonic()
        frame = self.driver.frame(window_id)
        doc = self.driver.ocr(frame)
        return Screen(frame, list(doc.get("lines") or []), list(doc.get("tokens") or []), observed_at)

    def native_path(self, screen: Screen) -> tuple[list[str] | None, str]:
        """One fresh read-only native read for ``screen``'s window, judged by :func:`native_own_path`.

        A driver without the read, or a read that raises, is "native_unavailable": callers keep the
        strict OCR crumb proofs.  The error text is dropped (it may name local paths).
        """
        read = getattr(self.driver, "native_documents", None)
        if read is None:
            return None, "native_unavailable"
        self.lease_check()
        try:
            native = read(screen.frame["window_id"])
        except Exception:  # UIA missing, COM failure, window replaced: never a path
            return None, "native_unavailable"
        return native_own_path(native, screen)

    def fresh_native_path(self, screen: Screen) -> tuple[Screen, list[str] | None, str]:
        """Read the path; on expired provenance recapture once without any input.

        Return the screen owning that proof as well as its judged path. On a
        pixel-identical new capture, its OCR is already known exactly; reuse
        that OCR to avoid spending the proof window on recognition again.
        Changed pixels require new OCR. No timestamp is rebased, the age limit
        is unchanged, and a second stale read is refused rather than looped.
        """
        native, why = self.native_path(screen)
        if native is None and why == "stale_provenance":
            self.lease_check()
            observed_at = time.monotonic()
            frame = self.driver.frame(screen.frame["window_id"])
            if frame.get("window_id") != screen.frame.get("window_id"):
                return screen, None, "stale_provenance"
            try:
                previous, current = frame_image(screen.frame), frame_image(frame)
                identical = previous.size == current.size and previous.tobytes() == current.tobytes()
            except (ValueError, KeyError, OSError):
                identical = False
            if identical:
                screen = Screen(frame, screen.lines, screen.tokens, observed_at)
            else:
                doc = self.driver.ocr(frame)
                screen = Screen(frame, list(doc.get("lines") or []), list(doc.get("tokens") or []), observed_at)
            native, why = self.native_path(screen)
        return screen, native, why

    def act(self, screen: Screen, window_id: str, **action: object) -> Screen:
        """One input on the frame just observed, then a fresh observation."""
        self.lease_check()
        self.driver.input(screen.frame, **action)
        self.sleep(self.settle)
        return self.observe(window_id)

    def click_box(self, screen: Screen, window_id: str, box: dict, action: str = "click", button: str = "left") -> Screen:
        x, y = _center(screen, box)
        return self.act(screen, window_id, action=action, x=x, y=y, button=button)

    def observe_transfers(self, *, completed: bool = False) -> tuple[str, Screen]:
        """Open the official transfer page for diagnosis, without changing tasks.

        Only the unique left side-navigation 传输 may be clicked, once. The
        returned page needs a transfer-status heading; no download, resume,
        pause, delete or purchase control is used. Leaves the page visible.
        """
        main = self.main_window()
        screen = self.observe(main)

        def transfer_page(s):
            w, h = s.size
            return any(
                re.fullmatch(r"(?:下载中|上传中|正在下载|正在上传|已完成)(?:[（(].*[）)])?", _norm(line.get("text")))
                and _cy(line) < h * .22 and line["x"] >= w * .07 for line in s.lines)

        if not transfer_page(screen):
            w, h = screen.size
            nav = screen.unique("传输", "transfer_navigation_not_unique", left_of=w * .06, below=h * .12)
            screen = self.click_box(screen, main, nav)
            if not transfer_page(screen):
                screen = self._poll(main, transfer_page)
                if screen is None:
                    raise StepFailed("transfer_page_not_verified")
        if completed:
            w, h = screen.size
            tabs = [line for line in screen.lines if re.fullmatch(r"已完成(?:[（(]\d+[）)])?", _norm(line.get("text")))
                    and _cy(line) < h * .22 and line["x"] >= w * .07]
            if len(tabs) != 1:
                raise StepFailed("completed_transfer_tab_not_unique")
            screen = self.click_box(screen, main, tabs[0])
            if not transfer_page(screen):
                raise StepFailed("transfer_page_not_verified")
        return main, screen

    # -- remote navigation -------------------------------------------------------

    def _header(self, screen: Screen) -> dict:
        header = list_header(screen)
        if header is None:
            raise StepFailed("file_list_header_not_found", texts=screen.texts()[:60])
        return header

    def current_path(self, screen: Screen) -> list[str] | None:
        """See :func:`screen_path`: None unless the folder (or root) is proven."""
        return screen_path(screen)

    def _find_in_list(self, window_id: str, screen: Screen, name: str,
                      seen: list[Screen] | None = None) -> tuple[Screen, dict | None]:
        """Exact name below the list header (not the side nav); bounded scrolling.

        ``seen`` collects every frame read (no extra input) for :func:`crumb_space_variant`.
        """
        for attempt in range(self.max_scrolls + 1):
            header = self._header(screen)
            if seen is not None:
                seen.append(screen)
            found = screen.find(name, below=header["y"] + header["h"], right_of=header["x"] - 60, literal=True)
            if len(found) > 1:
                raise StepFailed("list_row_ambiguous", name=name, matches=[_brief(f) for f in found])
            if found:
                return screen, found[0]
            if attempt == self.max_scrolls:
                break
            before = screen.texts()
            screen = self.act(screen, window_id, action="scroll", x=.6, y=.7, delta=-360)
            if screen.texts() == before:
                break  # end of list
        return screen, None

    def _to_file_view(self, main: str, screen: Screen) -> Screen:
        """From e.g. the transfer page, click the side-nav 首页 (left strip, below the tab bar).

        A tab without side nav (资源分享/extraction) falls back to the unique top-tab 首页:
        one click, then observation only until the own list header and a proven path show.
        """
        if list_header(screen) is not None:
            return screen
        width, height = screen.size
        sides = screen.find(SIDE_NAV_HOME, left_of=width * .06, below=height * .12)
        if sides:
            home = screen.unique(SIDE_NAV_HOME, "side_nav_home_not_found", left_of=width * .06, below=height * .12)
            screen = self.click_box(screen, main, home)
            self._header(screen)
            return screen
        home = top_tab_home(screen)
        screen = self.click_box(screen, main, home)
        if own_file_view(screen):
            return screen
        polled = self._poll(main, own_file_view)
        if polled is None:
            raise StepFailed("own_file_view_not_verified")  # no texts: the left tab may show share content
        return polled

    def _to_root(self, main: str, screen: Screen) -> Screen:
        path = self.current_path(screen)
        if path == []:
            return screen
        if path is None:  # no provable crumb and no root-home evidence: fail closed
            raise StepFailed("current_path_unknown", texts=screen.texts()[:60])
        root_line, _ = breadcrumb(screen, self._header(screen))
        box = screen.span(root_line, ROOT_CRUMB)
        if box is None:  # a separate 全部文件 line is clickable as a whole
            if _norm(root_line.get("text")) != ROOT_CRUMB:
                raise StepFailed("root_crumb_span_unknown", line=_brief(root_line))
            box = root_line
        screen = self.click_box(screen, main, box)
        if self.current_path(screen) == []:
            return screen
        # The root home (recent-files strip) may render a moment after the click: observe only.
        polled = self._poll(main, lambda s: self.current_path(s) == [])
        if polled is None:
            raise StepFailed("root_not_verified", texts=screen.texts()[:60])
        return polled

    def open_remote_dir(self, remote_dir: str, *, create_missing: bool = False) -> tuple[str, Screen]:
        """File view → root → one double-click per segment, each verified by the crumb chain.

        ``create_missing`` creates each absent segment via the toolbar 新建文件夹 (inline name,
        typed then Enter) and only continues once the exact name is OCR-visible in the list.
        Segments created in this call are recorded in ``self.created_dirs``.
        """
        parts = [p for p in validate_remote_dir(remote_dir).split("/") if p]
        self.created_dirs: list[str] = []
        main = self.main_window()
        self.lease_check()
        self.driver.activate(main)
        screen = self.observe(main)
        native, _ = self.native_path(screen)
        # A valid native path decides alone; the OCR crumb (folded names) only stands in without one.
        if native == parts or (native is None and same_path(self.current_path(screen), parts)):
            return main, screen
        screen = self._to_root(main, self._to_file_view(main, screen))
        proven: list[str] = []  # the crumb's own segments, carried forward after a proven space variant
        aliases: list[set[str]] = []  # per proven depth: requested segment plus the crumb form proven for it
        for depth, part in enumerate(parts):
            seen: list[Screen] = []
            before = self.current_path(screen)
            screen, row = self._find_in_list(main, screen, part, seen)
            created = False
            forms = {part}  # this depth's aliases: the requested segment, plus a proven tag row's text
            if row is None and create_missing:
                screen, row = self._create_folder(main, screen, part)
                self.created_dirs.append("/" + "/".join(parts[:depth + 1]))
                created = True
            if row is None and MATERIAL_TAG_RE.match(part):
                row, tagged = material_tag_row(seen, screen, part, before)
                if row is None:
                    raise StepFailed("remote_dir_not_found", segment=part, path="/" + "/".join(parts[:depth + 1]),
                                     variant=tagged)
                forms.add(_literal(row.get("text")))
            canonical = row is None and self.canonical_listing is not None
            if canonical:
                screen, row = self._canonical_child_row(main, screen, parts, depth, seen)
            if row is None:
                raise StepFailed("remote_dir_not_found", segment=part, path="/" + "/".join(parts[:depth + 1]))
            screen = self.click_box(screen, main, row, action="double_click")
            if canonical:
                screen, native, why = self.fresh_native_path(screen)
            else:
                native, why = self.native_path(screen)
            path = self.current_path(screen)
            if native == parts[:depth]:
                # A fresh frame may still expose the parent while navigation
                # loads. Observe only; never double-click the row a second time.
                settled = self._poll(main, lambda fresh: self.native_path(fresh)[0] == parts[:depth + 1])
                if settled is not None:
                    screen = settled
                    path = self.current_path(screen)
                    native, why = self.native_path(screen)
            if native == parts[:depth + 1]:  # the exact full path; the crumb's pixels may clip it
                proven, aliases = parts[:depth + 1], [*aliases, forms]
                continue
            if canonical:  # a canonically addressed row is proven by the native path alone
                raise StepFailed("canonical_child_refused", refusal="native_child_path_mismatch" if native is not None
                                 else "native_child_unavailable", native=why, path="/" + "/".join(parts[:depth + 1]))
            if native is not None:
                raise StepFailed("folder_not_verified", segment=part, variant="native_path_mismatch",
                                 native_depth=len(native), expected_depth=depth + 1,
                                 path="/" + "/".join(parts[:depth + 1]), texts=screen.texts()[:60])
            if same_path(path, [*proven, part]):
                proven, aliases = [*proven, part], [*aliases, forms]
                continue
            elided = own_crumb_elided(path)
            if created:
                variant = "created_row"
            elif elided:
                variant = elided_own_crumb_proves(seen, before, row, part, aliases, path)
            else:
                variant = crumb_space_variant(seen, row, proven, path)
            if variant != "proven":
                raise StepFailed("folder_not_verified", segment=part, variant=variant, native=why,
                                 texts=screen.texts()[:60])
            shown = path[-1]
            proven, aliases = ([*proven, shown] if elided else list(path)), [*aliases, {*forms, shown}]
        return main, screen

    def _canonical_child_row(self, main: str, screen: Screen, parts: list[str], depth: int,
                             seen: list[Screen]) -> tuple[Screen, dict]:
        """Row address of the exact child ``parts[depth]`` whose list row OCR keeps misreading.

        Only with ``canonical_listing``, after the exact lookup found no row.  The fresh native
        path must be exactly the parent ``parts[:depth]``; the parent's complete native listing
        (header count) pairs one-to-one with the provider listing of that same parent
        (:func:`canonical_entries`); the requested name must be one provider folder entry
        verbatim; its paired OCR row text must then read verbatim on exactly one folder row of a
        fresh frame whose native path is still the parent.  No name folding is added: the
        provider's exact metadata gives the address, the native path after the open proves it.
        Any doubt raises ``canonical_child_refused`` before input on a row.
        """
        parent = parts[:depth]
        where = "/" + "/".join(parent)
        target = "/" + "/".join(parts[:depth + 1])

        def refuse(refusal: str, **facts: object) -> StepFailed:
            return StepFailed("canonical_child_refused", refusal=refusal, path=target, **facts)
        for _ in range(len(seen) - 1):  # the exact lookup scrolled down: back to the top for a full listing
            screen = self.act(screen, main, action="scroll", x=.6, y=.7, delta=360)
        screen, native, why = self.fresh_native_path(screen)
        if native != parent:
            raise refuse("native_parent_unproven", native=why)
        screen, rows, complete = self._list_dir_entries(main, screen)
        if not complete:
            raise refuse("native_listing_incomplete")
        try:
            listing = self.canonical_listing(where)
        except Exception as exc:
            raise refuse("provider_error", error=type(exc).__name__) from exc
        entries, reason = canonical_entries(where, rows, listing)
        if entries is None:
            raise refuse(reason)
        # canonical_entries keeps row order: entry i is native row i renamed.
        paired = [(row["name"], entry) for row, entry in zip(rows, entries) if entry["name"] == parts[depth]]
        if len(paired) != 1:
            raise refuse("child_not_listed")
        ocr_name, entry = paired[0]
        if entry["kind"] != "dir":
            raise refuse("child_not_folder")
        screen = self.observe(main)  # the provider took time: only a fresh frame addresses the row
        for attempt in range(self.max_scrolls + 1):
            screen, native, why = self.fresh_native_path(screen)
            if native != parent:
                raise refuse("native_parent_changed", native=why)
            found = [r for r in list_rows(screen) if r["name"] == ocr_name]
            if len(found) > 1:
                raise refuse("row_not_unique")
            if found:
                if found[0]["kind"] != "dir":
                    raise refuse("row_not_folder")
                return screen, found[0]["line"]
            if attempt == self.max_scrolls:
                break
            before = screen.texts()
            screen = self.act(screen, main, action="scroll", x=.6, y=.7, delta=-360)
            if screen.texts() == before:
                break
        raise refuse("row_not_found")

    def _new_folder_rows(self, screen: Screen) -> list[dict]:
        """Uncommitted inline edit rows: a list row (a cell under 大小/类型/修改时间) whose name
        column reads a default 新建文件夹(n) and that shows no 修改时间 date (an existing
        folder of that name always shows one; the open edit shows ``-``).

        OCR may split the highlighted edit name into tight fragments; they are joined.
        A 新建文件夹 label with no list cell on its row (e.g. an empty-folder call to
        action) is no row.  Each hit keeps its first fragment's box with ``joined`` text.
        """
        header = self._header(screen)
        columns = [c for c in screen.same_row(header) if c is not header
                   and _norm(c.get("text")) in (LIST_SIZE, LIST_TYPE, LIST_MTIME)]
        if not columns:
            return []
        cell_left = min(c["x"] for c in columns) - 60
        found = []
        for line in screen.lines:
            if _cy(line) <= header["y"] + header["h"] or not header["x"] - 60 <= line["x"] < header["x"] + LIST_NAME_SPAN \
                    or line["x"] + line["w"] > cell_left:
                continue
            row = screen.same_row(line)
            names = [c for c in row if header["x"] - 60 <= c["x"] and c["x"] + c["w"] <= cell_left]
            if any(c["x"] < line["x"] for c in names):
                continue  # the tail of a split name
            text, right = _norm(line.get("text")), line["x"] + line["w"]
            for c in names:
                if c["x"] > line["x"] and 0 <= c["x"] - right <= LIST_NAME_JOIN_GAP:
                    text, right = text + _norm(c.get("text")), c["x"] + c["w"]
            cells = [c for c in row if c["x"] >= cell_left]
            if NEW_FOLDER_ROW_RE.match(text) and cells \
                    and not any(MTIME_RE.match(_norm(c.get("text"))) for c in row if c is not line):
                found.append({**line, "joined": text})
        return found

    def _create_folder(self, main: str, screen: Screen, name: str) -> tuple[Screen, dict]:
        """Toolbar 新建文件夹 (above the list header) → exactly one proven new inline row → type name → Enter → name visible.

        An inline edit already open before the click is never typed into (``stale_inline_folder_row``);
        after the click exactly one inline row not shown before must appear, else nothing is typed.
        """
        header = self._header(screen)
        upload = screen.unique("上传", "toolbar_not_found", above=header["y"])
        # Toolbar row only: the root home's recent-files strip may repeat a folder named 新建文件夹.
        buttons = [line for line in screen.same_row(upload) if _norm(line.get("text")) == NEW_FOLDER_BUTTON]
        if len(buttons) != 1:
            raise StepFailed("new_folder_button_not_found", matches=[_brief(b) for b in buttons], texts=screen.texts()[:60])
        button = buttons[0]
        stale = self._new_folder_rows(screen)
        if stale:  # an earlier edit is still open: which row has focus is unproven
            raise StepFailed("stale_inline_folder_row", name=name, rows=[_brief(r) for r in stale])
        before = screen

        def opened(s: Screen) -> bool:
            return list_header(s) is not None and len([r for r in self._new_folder_rows(s) if not _was_shown(before, r)]) == 1
        screen = self.click_box(screen, main, button)
        if not opened(screen):  # the inline row renders a moment later: observe only
            screen = self._poll(main, opened) or screen
        if not opened(screen):  # no single provable inline edit row: type nothing
            raise StepFailed("new_folder_row_not_verified", name=name, texts=screen.texts()[:60])
        screen = self.act(screen, main, action="key", key="select_all")
        screen = self.act(screen, main, action="type", text=name)
        self.lease_check()
        self.driver.input(screen.frame, action="key", key="enter")

        def settled(s: Screen) -> bool:
            # The name is visible, or the inline edit closed: in a long list the renamed
            # folder re-sorts out of the visible rows (measured at the 26-item root).
            h = list_header(s)
            return h is not None and (len(s.find(name, below=h["y"] + h["h"], right_of=h["x"] - 60, literal=True)) == 1
                                      or not self._new_folder_rows(s))
        screen = self._poll(main, settled)
        if screen is None:
            raise StepFailed("new_folder_not_verified", name=name)
        screen, row = self._find_in_list(main, screen, name)
        if row is None:
            raise StepFailed("new_folder_not_verified", name=name, texts=screen.texts()[:60])
        return screen, row

    def _poll(self, window_id: str, predicate: Callable[[Screen], bool]) -> Screen | None:
        """Observation only (no input) until predicate holds or attempts run out."""
        for _ in range(self.poll_attempts):
            self.sleep(self.settle)
            screen = self.observe(window_id)
            if predicate(screen):
                return screen
        return None

    def _await_share_list(self, main: str, listed: Callable[[Screen], bool]) -> Screen | None:
        """Poll (no input) until the share list shows; a visible extraction rejection stops at once.

        Raises ``share_extraction_rejected`` (client hint visible) or, after the polls ran out on the
        extraction page, ``share_extraction_not_passed``.  Facts carry only the fixed hint label: the
        code box renders the code letter-spaced, which redaction cannot match, so no texts are kept.
        """
        screen = self._poll(main, lambda sc: listed(sc) or share_extraction_state(sc) == "rejected")
        if screen is not None and listed(screen):
            return screen
        last = screen or self.observe(main)
        state = share_extraction_state(last)
        if state == "rejected":
            raise StepFailed("share_extraction_rejected", hint=_extraction_hint(last), code_submitted_by_flow=False)
        if state == "pending":
            raise StepFailed("share_extraction_not_passed", code_submitted_by_flow=False)
        return None

    def _open_share_address_bar(self, main: str, url: str, code: str) -> None:
        """Type the full share link into the top address field once (no Enter, no retry).

        A fresh frame must show exactly one address field (placeholder or a prior pan.baidu.com link)
        in the top band; otherwise :class:`StepFailed` before any input.  Facts carry counts only: a
        prior link in the field belongs to another share and is never reported.
        """
        screen = self.observe(main)
        field = address_bar_field(screen)
        screen = self.click_box(screen, main, field)
        screen = self.act(screen, main, action="key", key="select_all")
        self.lease_check()
        self.driver.input(screen.frame, action="type", text=f"{url}?pwd={code}")  # exactly once per call

    def _not_submitted(self, key: str, exc: Exception, secrets: tuple[str, ...] = ()) -> TransferResult:
        code = getattr(exc, "code", type(exc).__name__)
        error = _redact_secrets(str(exc), secrets)
        facts = getattr(exc, "facts", {})
        diagnostic = {k: facts[k] for k in ("refusal", "variant", "native", "native_depth", "expected_depth", "provider_status", "path", "window_role", "count",
                                           "evidence_capture_id", "evidence_capture_sha256") if k in facts}
        self.ledger.update(key, "failed", reason=code, error=error, before_submit=True,
                           diagnostic=_redact_secrets(diagnostic, secrets))
        return TransferResult("not_submitted", "inspect_then_retry", key,
                              {"reason": code, "error": error, **_redact_secrets(getattr(exc, "facts", {}), secrets)})

    # -- quota (read only) ---------------------------------------------------------

    def quota(self, *, evidence_dir: Path | None = None) -> dict:
        """Observe the main window once (no input) and read the top bar 已用/总量 label.

        On failure the observed frame is kept under ``evidence_dir`` (default
        %TEMP%/codeyun/baidu-netdisk/quota) and its path reported.
        """
        main = self.main_window()
        self.lease_check()
        self.driver.activate(main)
        screen = self.observe(main)
        found = read_quota(screen)
        if found is not None:
            return {"status": "ok", **found}
        if evidence_dir is None:
            from backend.core.temp_paths import codeyun_temp_root
            evidence_dir = codeyun_temp_root("baidu-netdisk", "quota")
        evidence_dir.mkdir(parents=True, exist_ok=True)
        shot = evidence_dir / f"quota-{screen.frame.get('token', 'frame')}.png"
        frame_image(screen.frame).save(shot)
        top = [str(line.get("text") or "") for line in screen.lines if _cy(line) < screen.size[1] * .15]
        return {"status": "failed", "reason": "quota_label_not_found", "screenshot": str(shot), "top_texts": top}

    # -- upload ------------------------------------------------------------------

    def upload_file(self, local_path: str | os.PathLike, remote_dir: str, *, create_missing: bool = False) -> TransferResult:
        source = describe_upload_source(local_path, remote_dir)
        record, created = self.ledger.begin("upload", sha256=source["sha256"], remote_path=source["remote_path"])
        if not created:
            return artifact_status(record)
        key = record["key"]
        try:
            main, screen = self.open_remote_dir(source["remote_dir"], create_missing=create_missing)
            screen, existing = self._find_in_list(main, screen, source["name"])
            if existing is not None:
                raise StepFailed("remote_name_exists", remote_path=source["remote_path"])
            button = screen.unique("上传", "upload_button_not_found", above=self._header(screen)["y"])
            self.click_box(screen, main, button)
            dialog = self._wait_dialog()
            dscreen = self._select_in_dialog(dialog, Path(source["local_path"]))
            confirm = dscreen.unique(DIALOG_CONFIRM, "confirm_not_found")
        except StepFailed as exc:
            if exc.code == "dialog_closed_after_directory_enter":
                # The dialog may have accepted the directory as an upload: block retries.
                self.ledger.update(key, "submitted", unintended=exc.code, directory=str(Path(source["local_path"]).parent))
                return TransferResult("state_unknown", "check_remote_and_transfer_page_do_not_resubmit", key, {"reason": exc.code})
            return self._not_submitted(key, exc)
        except Exception as exc:  # nothing irreversible has been sent yet
            return self._not_submitted(key, exc)
        # Submit: the only irreversible input.  Never repeated.
        self.ledger.update(key, "submitted", via="official_client_gui", source=source)
        try:
            x, y = _center(dscreen, confirm)
            self.lease_check()
            self.driver.input(dscreen.frame, action="click", x=x, y=y, button="left")
        except Exception as exc:  # the click may or may not have landed
            return TransferResult("state_unknown", "observe_remote_do_not_resubmit", key, {"error": str(exc)})
        return self._verify_upload(key, main, source)

    def _wait_dialog(self) -> str:
        for _ in range(self.poll_attempts):
            self.sleep(self.settle)
            dialog = self.dialog_window()
            if dialog:
                return dialog
        raise StepFailed("file_dialog_not_open")

    def _name_field(self, screen: Screen) -> tuple[dict, list[dict]]:
        label = screen.unique(FILE_NAME_LABEL_RE, "file_name_label_not_found")
        return label, [line for line in screen.same_row(label) if line is not label and line["x"] >= label["x"] + label["w"] - 2]

    def _select_in_dialog(self, dialog: str, source: Path) -> Screen:
        """Type the source *directory* + Enter to navigate, click the basename row, confirm the name field."""
        self.lease_check()
        self.driver.activate(dialog)
        screen = self.observe(dialog)
        label, _ = self._name_field(screen)
        # The edit box starts at the label's right edge; click just inside it.
        width, height = screen.size
        x = min(.98, (label["x"] + label["w"] + max(24, label["h"] * 2)) / width)
        screen = self.act(screen, dialog, action="click", x=x, y=_cy(label) / height, button="left")
        screen = self.act(screen, dialog, action="key", key="select_all")
        screen = self.act(screen, dialog, action="type", text=str(source.parent).rstrip("\\/") + os.sep)
        self.lease_check()
        self.driver.input(screen.frame, action="key", key="enter")
        self.sleep(self.settle)
        if self.dialog_window() != dialog:
            # Enter on a directory must navigate; a closed dialog may mean the
            # folder itself was submitted.
            raise StepFailed("dialog_closed_after_directory_enter")
        screen = self.observe(dialog)
        label, _ = self._name_field(screen)
        row = screen.unique(source.name, "source_row_not_found", above=label["y"] - 4, literal=True)
        screen = self.click_box(screen, dialog, row)
        label, field_lines = self._name_field(screen)
        label_rest = _literal(FILE_NAME_LABEL_RE.sub("", _literal(label.get("text"))))
        values = [_literal(line.get("text")) for line in field_lines] + ([label_rest] if label_rest else [])
        if source.name not in values:
            raise StepFailed("file_name_value_mismatch", expected=source.name, values=values)
        return screen

    def _verify_upload(self, key: str, main: str, source: dict) -> TransferResult:
        facts = {"remote_path": source["remote_path"], "source_sha256": source["sha256"], "content_verified": False}

        def present(screen: Screen) -> bool:
            header = list_header(screen)
            return self.dialog_window() is None and header is not None \
                and len(screen.find(source["name"], below=header["y"] + header["h"], literal=True)) == 1
        try:
            screen = self._poll(main, present)
        except Exception as exc:
            return TransferResult("state_unknown", "observe_remote_do_not_resubmit", key, {**facts, "error": str(exc)})
        if screen is None:
            return TransferResult("submitted", "check_transfer_page_or_remote_later", key, facts)
        # The name was absent before submit and is present now; content is not compared here.
        self.ledger.update(key, "verified", verification="remote_present", source_sha256=source["sha256"])
        return TransferResult("remote_present", "download_roundtrip_to_verify_content", key, facts)

    # -- download ----------------------------------------------------------------

    def download_file(self, remote_path: str, local_dir: str | os.PathLike, *, client_download_dir: str | os.PathLike,
                      expected_size: int, expected_sha256: str | None = None) -> TransferResult:
        remote = validate_remote_dir(remote_path)
        name = PurePosixPath(remote).name
        local_dir = os.fspath(Path(local_dir).resolve())
        identity = {"remote_path": remote, "local_dir": local_dir}
        validate_file_name(name)
        prior = self._prior("download", identity, _content_identity(expected_size, expected_sha256))
        if isinstance(prior, TransferResult):
            return prior
        key = prior
        manifest = self.ledger.path.parent / f"download-{key}.before.json"
        try:
            before = snapshot_dir(client_download_dir)
            # Persisted so `collect` can resume in a later process without resubmitting.
            write_manifest(manifest, client_download_dir, before)
            main, screen = self.open_remote_dir(str(PurePosixPath(remote).parent))
            screen, row = self._find_in_list(main, screen, name)
            if row is None:
                raise StepFailed("remote_file_not_found", remote_path=remote)
            button, screen = self._select_for_download(main, screen, row, expected_size)
        except Exception as exc:
            return self._not_submitted(key, exc)
        self.ledger.update(key, "submitted", via="official_client_gui", manifest=os.fspath(manifest), name=name,
                           expected_size=expected_size, expected_sha256=_upper(expected_sha256), local_dir=local_dir)
        try:
            x, y = _center(screen, button)
            self.lease_check()
            self.driver.input(screen.frame, action="click", x=x, y=y, button="left")
        except Exception as exc:
            return TransferResult("state_unknown", "collect_do_not_resubmit", key, {"error": str(exc), "manifest": os.fspath(manifest)})
        return self.collect(key, client_download_dir, before, name, expected_size, local_dir, expected_sha256)

    def _close_context_menu(self, main: str, screen: Screen) -> Screen:
        """Dismiss a leftover context menu with one click on a verified blank breadcrumb-row strip.

        Only on the plain file page of a non-root folder (the strip needs a
        crumb), with no file dialog and no other client window.  Proof after
        the click: no menu column and the same folder path.  Escape is not
        used (measured ineffective).  No menu: no input.
        """
        if not context_menu_column(screen):
            return screen
        if self.dialog_window() is not None:
            raise StepFailed("close_menu_dialog_open")
        others = [w for w in self.client_windows() if w.get("id") != main]
        if others:
            raise StepFailed("close_menu_extra_window", windows=[(w.get("title"), w.get("width"), w.get("height")) for w in others])
        path = screen_path(screen)
        band = blank_band_right_of_crumb(screen) if path is not None else None
        if band is None:
            raise StepFailed("menu_close_anchor_not_found", path=path, texts=screen.texts()[:60])
        screen = self.click_box(screen, main, band)
        if context_menu_column(screen):
            raise StepFailed("context_menu_not_closed", texts=screen.texts()[:60])
        if screen_path(screen) != path:
            raise StepFailed("menu_close_changed_page", path=screen_path(screen), expected=path)
        return screen

    def _clear_selection(self, main: str, screen: Screen) -> Screen:
        """Bounded selection reset before a download via the list header's own checkbox.

        Measured on 8.1.5.103 (1213x864, 96 DPI): with a partial selection one
        header-checkbox click selects all, a second clears; with everything
        selected one click clears.  (Two Escapes left a selection: not a clear.)  Each
        click needs the plain file page (no file dialog, no other client
        window, header and folder path readable), a readable full
        "已选中N个文件/文件夹", and the checkbox found in this frame's pixels
        by :func:`header_checkbox`.  At most two clicks; a second only after
        the first visibly grew the selection (partial -> all), so it never
        re-selects all.  Done only on positive empty proof: no selection
        evidence and the header row's "已全部加载，共N个".  Otherwise
        StepFailed (nothing submitted); an input error propagates, no resend.
        """
        grew = True
        for attempt in range(MAX_CLEAR_CLICKS):
            if self.dialog_window() is not None:
                raise StepFailed("clear_selection_dialog_open")
            others = [w for w in self.client_windows() if w.get("id") != main]
            if others:
                raise StepFailed("clear_selection_extra_window",
                                 windows=[(w.get("title"), w.get("width"), w.get("height")) for w in others])
            if list_header(screen) is None or screen_path(screen) is None:
                raise StepFailed("clear_selection_page_unknown", texts=screen.texts()[:60])
            if not grew:
                raise StepFailed("selection_not_cleared", clicks=attempt, texts=screen.texts()[:60])
            if context_menu_column(screen):  # the checkbox click would only dismiss it
                raise StepFailed("clear_selection_menu_open", texts=screen.texts()[:60])
            count = selection_count(screen)
            if count is None:
                raise StepFailed("selection_count_unreadable", texts=screen.texts()[:60])
            box = header_checkbox(screen)  # raises when the frame/profile does not prove one checkbox
            screen = self.click_box(screen, main, box)
            if list_header(screen) is not None and not selection_evidence(screen):
                if loaded_count(screen) is None:
                    raise StepFailed("selection_clear_unproven", texts=screen.texts()[:60])
                return screen
            after = selection_count(screen)
            grew = after is not None and after > count
        raise StepFailed("selection_not_cleared", clicks=MAX_CLEAR_CLICKS, texts=screen.texts()[:60])

    def _select_for_download(self, main: str, screen: Screen, row: dict, expected_size: int | None) -> tuple[dict, Screen]:
        """One right-click on the row, proven on the very next frame; returns the menu's 下载 item.

        8.1.5.103: a left click on a txt row opens a preview and the checkbox
        only renders on hover, so the official right-click is the selection
        gesture.  No Escape afterwards: it may clear the selection just made.
        Proof before submit:
        * nothing was selected before (else the right-click may extend or
          keep another selection); a leftover selection is first cleared by
          :meth:`_clear_selection` and the row is found again on that frame;
        * the right-click lands on the row's own 修改时间 cell (same OCR row,
          under that header), so the menu opens at the right edge and covers
          neither the toolbar nor the header's selection count;
        * after it, the full "已选中1个文件/文件夹" reads 1 and the toolbar
          下载(size) above the list header matches ``expected_size`` (a folder,
          ``expected_size=None``, skips the size label check);
        * the context menu's 下载 appeared with this click in a column with
          other new menu entries (it may open leftwards or upwards, even above
          the header); the toolbar button is only ever a sized 下载(…) on the
          上传 row, so the two never mix.
        A context menu left open from an earlier step is closed first by
        :meth:`_close_context_menu`.
        """
        self._header(screen)
        start = screen
        # An open menu swallows the header-checkbox click and covers the 修改时间 column.
        screen = self._close_context_menu(main, screen)
        if selection_evidence(screen):
            screen = self._clear_selection(main, screen)
            screen = self._close_context_menu(main, screen)
        if screen is not start:
            header = self._header(screen)
            rows = screen.find(_literal(row.get("text")), below=header["y"] + header["h"], right_of=header["x"] - 60,
                               literal=True)
            if len(rows) != 1:
                raise StepFailed("row_lost_after_clear", row=_brief(row), matches=[_brief(r) for r in rows])
            row = rows[0]
        anchor = row_mtime_cell(screen, row)
        if anchor is None:
            raise StepFailed("row_mtime_cell_not_found", row=_brief(row), texts=screen.texts()[:60])
        before = screen
        screen = self.click_box(screen, main, anchor, button="right")
        if expected_size is not None:
            tops = toolbar_download(screen)
            if len(tops) != 1:
                raise StepFailed("download_button_not_found" if not tops else "download_button_not_found_ambiguous",
                                 matches=[_brief(t) for t in tops], texts=screen.texts()[:60])
            top = tops[0]
            if not _selection_size_matches(top.get("text"), expected_size):
                raise StepFailed("download_selection_size_mismatch", button=_brief(top), expected_size=expected_size)
        count = selection_count(screen)
        if count != 1:
            raise StepFailed("selection_count_unverified", count=count, texts=screen.texts()[:60])
        items = context_menu_item(before, screen, CONTEXT_MENU_DOWNLOAD)
        if len(items) != 1:
            raise StepFailed("context_menu_download_not_found" if not items else "context_menu_download_ambiguous",
                             matches=[_brief(i) for i in items], texts=screen.texts()[:60])
        return items[0], screen

    def _prior(self, op: str, identity: dict, content: dict) -> TransferResult | str:
        """Existing record for this transfer, or a new key.

        The exact record (identity + content expectation) answers first.  Then
        related records of the same identity: the pre-content-identity legacy
        key and, when a hash is now given, the hash-less key for the same size.
        A related record that is not verified always blocks (it may still
        land).  A verified one answers only when its recorded facts prove the
        same expectation; a proven difference is a new content version; an
        unprovable one needs review.
        """
        inputs = {**identity, **content}
        exact = self.ledger.get(transfer_key(op, **inputs))
        if exact and exact["state"] not in {"failed", "abandoned"}:
            return artifact_status(exact)
        related = [identity]
        if content["expected_sha256"]:
            related.append({**inputs, "expected_sha256": None})
        for candidate in related:
            record = self.ledger.get(transfer_key(op, **candidate))
            if not record or record["state"] in {"failed", "abandoned"}:
                continue
            if record["state"] != "verified":
                return artifact_status(record)
            same = _same_expectation(record.get("facts") or {}, content)
            if same is None:
                return TransferResult("needs_review", "manual_review_do_not_resubmit", record["key"],
                                      {"state": record["state"], "operation": op, "reason": "prior_expectation_unknown"})
            if same:
                return artifact_status(record, expected_sha256=content["expected_sha256"])
        record, created = self.ledger.begin(op, **inputs)
        return record["key"] if created else artifact_status(record)

    def resume_download(self, key: str) -> TransferResult:
        """Continue collecting a submitted download from its ledger record; filesystem only, never GUI input."""
        record = self.ledger.get(key)
        if record is None:
            return TransferResult("unknown_key", "check_key", key)
        state, facts = record["state"], record.get("facts") or {}
        if record.get("operation") not in {"download", "download_share"}:
            return TransferResult("not_a_download", "use_status", key, {"operation": record.get("operation")})
        if state == "verified":
            return artifact_status(record)
        if state in {"failed", "abandoned"}:
            return TransferResult("not_submitted" if facts.get("before_submit") else state, "inspect_then_retry", key,
                                  {"state": state, "reason": facts.get("reason")})
        if state == "planned" or not facts.get("manifest"):
            # Submit is recorded before the click, so planned means no submit input was sent.
            return TransferResult("planned", "no_submit_recorded_mark_failed_if_no_run_is_active", key, {"state": state})
        try:
            root, before = load_manifest(facts["manifest"])
        except (OSError, ValueError, KeyError) as exc:
            return TransferResult("needs_review", "manual_review_do_not_resubmit", key,
                                  {"state": state, "reason": "manifest_unreadable", "error": f"{type(exc).__name__}: {exc}"})
        expected_sha256 = (record.get("inputs") or {}).get("expected_sha256") or facts.get("expected_sha256")
        return self.collect(key, root, before, facts["name"], int(facts["expected_size"]), facts["local_dir"], expected_sha256)

    def collect(self, key: str, client_download_dir, before, name: str, expected_size: int,
                local_dir: str, expected_sha256: str | None) -> TransferResult:
        """Filesystem polling only; collected after two identical observations (+hash)."""
        token = None
        facts: dict = {"manifest": os.fspath(self.ledger.path.parent / f"download-{key}.before.json")}
        for _ in range(self.poll_attempts):
            self.sleep(self.settle)
            try:
                result = collect_download(download_root=client_download_dir, before=before, expected_name=name,
                                          expected_size=expected_size, target_dir=local_dir,
                                          expected_sha256=expected_sha256, stable_token=token)
            except Exception as exc:  # already submitted: report, resume later, never resubmit
                return TransferResult("state_unknown", "resume_download_do_not_resubmit", key,
                                      {**facts, "error": f"{type(exc).__name__}: {exc}"})
            raw = result.to_dict()
            facts.update({k: v for k, v in raw.items() if k not in {"status", "next"}},
                         collect_status=raw["status"], collect_next=raw["next"],
                         content_verified=bool(expected_sha256) and raw["status"] in {"collected", "already_collected"})
            if result.status in {"collected", "already_collected"}:
                self.ledger.update(key, "verified", verification="local_hash" if expected_sha256 else "local_size",
                                   target_path=result.target_path, sha256=result.sha256)
                return TransferResult("downloaded", "done", key, facts)
            if result.status in {"hash_mismatch", "ambiguous", "target_exists"}:
                self.ledger.update(key, "observed", collect=result.status)
                return TransferResult("needs_review", "manual_review", key, facts)
            token = result.stable_token
        return TransferResult("submitted", "resume_download_later_do_not_resubmit", key, facts)


    # -- directory transfers -------------------------------------------------------

    def _list_dir_entries(self, main: str, screen: Screen) -> tuple[Screen, list[dict], bool]:
        """Rows of the open folder over bounded scrolling, then scrolled back; (screen, rows, complete).

        ``complete`` needs the header's 已全部加载，共N个 to equal the rows read.
        """
        expected = loaded_count(screen)
        rows: dict[str, dict] = {}
        scrolled = 0
        for attempt in range(self.max_scrolls + 1):
            frame = sorted(list_rows(screen), key=lambda r: _cy(r["line"]))
            known = [i for i, row in enumerate(frame) if _list_key(row["name"]) in rows]
            # Scrolled frames: new rows only come in below the last known one; an OCR variant
            # of a known row higher up ("宫" for "营") is not a new entry.
            for row in frame[known[-1] + 1:] if attempt and known else frame:
                rows.setdefault(_list_key(row["name"]), {k: v for k, v in row.items() if k != "line"})
            if attempt == self.max_scrolls or (expected is not None and len(rows) >= expected):
                break  # a first frame that already shows every row is never re-read from a scrolled frame
            before = screen.texts()
            screen = self.act(screen, main, action="scroll", x=.6, y=.7, delta=-360)
            if screen.texts() == before:
                break
            scrolled += 1
        for _ in range(scrolled):  # later row lookups only scroll down
            screen = self.act(screen, main, action="scroll", x=.6, y=.7, delta=360)
        return screen, list(rows.values()), expected is not None and expected == len(rows)

    def list_remote_dir(self, remote_dir: str) -> dict:
        """Read-only: rows (name, kind, size label) of one remote folder.

        With ``canonical_listing`` the names are the provider's exact names (``canonical``
        True); a refused pairing raises :class:`StepFailed` ``canonical_listing_refused``.
        """
        path = validate_remote_dir(remote_dir)
        main, screen = self.open_remote_dir(path)
        _, rows, complete = self._list_dir_entries(main, screen)
        if self.canonical_listing is None:
            return {"path": path, "entries": rows, "complete": complete}
        if not complete:
            raise StepFailed("canonical_listing_refused", refusal="native_listing_incomplete", path=path)
        try:
            listing = self.canonical_listing(path)
        except Exception as exc:
            raise StepFailed("canonical_listing_refused", refusal="provider_error", path=path,
                             error=type(exc).__name__) from exc
        entries, reason = canonical_entries(path, rows, listing)
        if entries is None:
            raise StepFailed("canonical_listing_refused", refusal=reason, path=path,
                             provider_status=listing.get("status") if isinstance(listing, dict) else None)
        return {"path": path, "entries": entries, "complete": True, "canonical": True}

    def remote_tree(self, remote_dir: str) -> dict:
        """Read-only recursive listing: ``files`` rel -> size label, ``dirs`` rels, ``complete`` for every level."""
        root = validate_remote_dir(remote_dir)
        files: dict[str, str] = {}
        dirs: list[str] = []
        complete = True
        pending = [""]
        while pending:
            rel = pending.pop(0)
            listing = self.list_remote_dir(root if not rel else f"{root}/{rel}")
            complete = complete and listing["complete"]
            for row in listing["entries"]:
                child = f"{rel}/{row['name']}" if rel else row["name"]
                if row["kind"] == "dir":
                    dirs.append(child)
                    pending.append(child)
                else:
                    files[child] = row["size"]
        return {"path": root, "files": files, "dirs": dirs, "complete": complete}

    def _cancel_dialog(self, dialog: str) -> None:
        """Close the native upload dialog with its 取消 button (no observation after: it is gone)."""
        if self.dialog_window() != dialog:
            return
        screen = self.observe(dialog)
        cancel = screen.unique(DIALOG_CANCEL, "dialog_cancel_not_found")
        x, y = _center(screen, cancel)
        self.lease_check()
        self.driver.input(screen.frame, action="click", x=x, y=y, button="left")
        self.sleep(self.settle)
        if self.dialog_window() is not None:
            raise StepFailed("dialog_not_closed_after_cancel")

    def upload_dir(self, local_dir: str | os.PathLike, remote_parent: str, *, mode: str = "auto") -> TransferResult:
        """Upload a local folder as ``remote_parent/<name>``; existing remote names are never overwritten.

        ``native`` (tried first by ``auto`` when the remote folder is absent)
        selects the folder itself in the official 请选择文件/文件夹 dialog and
        submits once.  ``per_file`` (and ``auto`` when the folder exists or the
        dialog does not prove the folder name) creates each level with
        :meth:`open_remote_dir` and uploads each file via :meth:`upload_file`,
        skipping names already present.  Every run ends with a read-only,
        level-by-level comparison of remote names against the local tree.
        A native submission is never repeated: a rerun only verifies.
        """
        self.native_fallback_reason = None
        if mode not in {"auto", "native", "per_file"}:
            raise ValueError(f"mode 只能是 auto/native/per_file：{mode!r}")
        tree = describe_upload_tree(local_dir, remote_parent)
        record, created = self.ledger.begin("upload_dir", local_dir=tree["local_dir"], remote_path=tree["remote_path"],
                                            tree=tree["tree_digest"])
        key, facts = record["key"], record.get("facts") or {}
        verified = record["state"] == "verified"
        if not created and (facts.get("mode") == "native" and not verified or verified and mode != "per_file"):
            # A native submission is never repeated; an explicit per_file pass over a verified
            # tree only skips existing names (never overwrites), so it may run again.
            return self._verify_upload_dir(key, tree, facts.get("mode"), {})
        if verified:
            return self._upload_dir_per_file(key, tree)
        try:
            main, screen = self.open_remote_dir(tree["remote_parent"], create_missing=True)
            screen, existing = self._find_in_list(main, screen, tree["name"])
        except Exception as exc:
            return self._not_submitted(key, exc)
        if existing is None and mode in {"auto", "native"} and facts.get("mode") != "per_file":
            outcome = self._upload_dir_native(key, main, screen, tree)
            if outcome is not None or mode == "native":
                return outcome or self._not_submitted(key, StepFailed("native_folder_upload_unproven"))
        elif mode == "native":
            return self._not_submitted(key, StepFailed("remote_dir_exists", remote_path=tree["remote_path"]))
        return self._upload_dir_per_file(key, tree)

    def _upload_dir_native(self, key: str, main: str, screen: Screen, tree: dict) -> TransferResult | None:
        """None: nothing submitted and the dialog is closed again (caller may fall back)."""
        dialog = None
        try:
            button = screen.unique("上传", "upload_button_not_found", above=self._header(screen)["y"])
            self.click_box(screen, main, button)
            dialog = self._wait_dialog()
            dscreen = self._select_in_dialog(dialog, Path(tree["local_dir"]))
            confirm = dscreen.unique(DIALOG_CONFIRM, "confirm_not_found")
        except StepFailed as exc:
            if exc.code == "dialog_closed_after_directory_enter":
                self.ledger.update(key, "submitted", mode="native", unintended=exc.code)
                return TransferResult("state_unknown", "check_remote_and_transfer_page_do_not_resubmit", key,
                                      {"reason": exc.code})
            try:
                if dialog:
                    self._cancel_dialog(dialog)
            except Exception as cancel_exc:  # dialog may still be open: stop, never type into it again
                return self._not_submitted(key, StepFailed("dialog_cancel_failed", error=str(cancel_exc),
                                                           cause=exc.code))
            self.native_fallback_reason = exc.code
            return None
        self.ledger.update(key, "submitted", mode="native", via="official_client_gui", files_total=len(tree["files"]))
        try:
            x, y = _center(dscreen, confirm)
            self.lease_check()
            self.driver.input(dscreen.frame, action="click", x=x, y=y, button="left")
        except Exception as exc:
            return TransferResult("state_unknown", "verify_remote_do_not_resubmit", key, {"error": str(exc)})

        def present(s: Screen) -> bool:
            header = list_header(s)
            return self.dialog_window() is None and header is not None \
                and len(s.find(tree["name"], below=header["y"] + header["h"], literal=True)) == 1
        if self._poll(main, present) is None:
            return TransferResult("submitted", "rerun_upload_dir_to_verify_do_not_resubmit", key,
                                  {"mode": "native", "remote_path": tree["remote_path"]})
        return self._verify_upload_dir(key, tree, "native", {})

    def _upload_dir_per_file(self, key: str, tree: dict) -> TransferResult:
        if self.ledger.get(key)["state"] != "verified":
            self.ledger.update(key, "observed", mode="per_file")
        counts = {"uploaded": [], "skipped_existing": [], "already_recorded": [], "failed": []}
        base = Path(tree["local_dir"])
        for rel in [""] + tree["dirs"]:  # every level, including empty folders
            remote = tree["remote_path"] if not rel else f"{tree['remote_path']}/{rel}"
            try:
                self.open_remote_dir(remote, create_missing=True)
            except Exception as exc:
                counts["failed"].append({"rel": rel or ".", "reason": getattr(exc, "code", type(exc).__name__)})
        for item in tree["files"]:
            rel = item["rel"]
            parent = PurePosixPath(rel).parent.as_posix()
            remote = tree["remote_path"] if parent == "." else f"{tree['remote_path']}/{parent}"
            result = self.upload_file(base / rel, remote, create_missing=True)
            if result.status == "remote_present":
                counts["uploaded"].append(rel)
            elif result.facts.get("reason") == "remote_name_exists":
                counts["skipped_existing"].append(rel)
            elif result.status == "already_remote_present":
                counts["already_recorded"].append(rel)
            else:
                counts["failed"].append({"rel": rel, "status": result.status, "reason": result.facts.get("reason"),
                                         "key": result.key})
        return self._verify_upload_dir(key, tree, "per_file", counts)

    def _verify_upload_dir(self, key: str, tree: dict, mode: str | None, counts: dict) -> TransferResult:
        """Read-only: remote names, level by level, against the local tree (content is not compared)."""
        try:
            remote = self.remote_tree(tree["remote_path"])
        except Exception as exc:
            return TransferResult("state_unknown", "rerun_upload_dir_to_verify", key,
                                  {"mode": mode, "reason": getattr(exc, "code", type(exc).__name__), "error": str(exc)})
        have_files = {_list_key(rel) for rel in remote["files"]}
        have_dirs = {_list_key(rel) for rel in remote["dirs"]}
        missing = [f["rel"] for f in tree["files"] if _list_key(f["rel"]) not in have_files]
        missing_dirs = [rel for rel in tree["dirs"] if _list_key(rel) not in have_dirs]
        local_names = {_list_key(f["rel"]) for f in tree["files"]}
        facts = {
            "mode": mode, "remote_path": tree["remote_path"], "files_total": len(tree["files"]),
            "uploaded": len(counts.get("uploaded", [])) if mode == "per_file" else len(tree["files"]) - len(missing),
            "skipped_existing": counts.get("skipped_existing", []), "already_recorded": counts.get("already_recorded", []),
            "failed": counts.get("failed", []), "missing": missing, "missing_dirs": missing_dirs,
            "remote_extra": sorted(rel for rel in remote["files"] if _list_key(rel) not in local_names),
            "listing_complete": remote["complete"], "content_verified": False,
        }
        if getattr(self, "native_fallback_reason", None):
            facts["native_fallback_reason"] = self.native_fallback_reason
        if not missing and not missing_dirs and remote["complete"] and not facts["failed"]:
            if self.ledger.get(key)["state"] != "verified":
                self.ledger.update(key, "verified", verification="remote_names", mode=mode)
            elif mode == "per_file":
                self.ledger.update(key, "verified", last_per_file_pass=time.time())
            return TransferResult("remote_present", "download_roundtrip_to_verify_content", key, facts)
        return TransferResult("incomplete", "rerun_upload_dir_later" if mode == "native" else "inspect_failed_then_rerun",
                              key, facts)

    def download_dir(self, remote_dir: str, local_dir: str | os.PathLike, *,
                     client_download_dir: str | os.PathLike, expected_manifest: dict | None = None) -> TransferResult:
        """Download a remote folder (one official 下载 on the folder row) into ``local_dir/<name>``.

        Before submit: the caller's complete, previously verified manifest is
        validated, or the full remote tree is listed (every level complete),
        the client download dir is snapshotted, and the expectation is kept in
        the ledger.  After: :meth:`collect_dir` (filesystem only, also used by
        :meth:`resume_download_dir`) checks file count, names and sizes and
        moves the tree without overwriting.  A recorded transfer is never
        resubmitted; a rerun resumes collecting.
        """
        remote = validate_remote_dir(remote_dir)
        name = PurePosixPath(remote).name
        validate_file_name(name)
        local = os.fspath(Path(local_dir).resolve())
        supplied_tree = None
        if expected_manifest is not None:
            # The automation service proves the root's exact ID/path/type before
            # calling us. Reuse its full inventory rather than doing an OCR walk
            # of every nested folder during the five-minute native lease. The
            # landed file set and exact byte sizes still have to match.
            from .operations import normalize_request
            manifest_files = normalize_request({"kind": "download_dir", "remote_path": remote,
                                                "local_dir": local, "expected_manifest": expected_manifest})[
                                                    "expected_manifest"]["files"]
            if not manifest_files:
                raise ValueError("expected_manifest must contain the entire non-empty package")
            dirs = {str(p) for f in manifest_files for p in PurePosixPath(f["rel"]).parents if str(p) != "."}
            supplied_tree = {"complete": True, "files": {f["rel"]: str(f["size"]) for f in manifest_files},
                             "dirs": sorted(dirs)}
        record, created = self.ledger.begin("download_dir", remote_path=remote, local_dir=local)
        key = record["key"]
        if not created:
            return self.resume_download_dir(key)
        manifest = self.ledger.path.parent / f"download_dir-{key}.before.json"
        try:
            tree = supplied_tree if supplied_tree is not None else self.remote_tree(remote)
            if not tree["complete"]:
                raise StepFailed("remote_listing_incomplete", remote_path=remote)
            if not tree["files"]:
                raise StepFailed("remote_dir_has_no_files", remote_path=remote)
            before = snapshot_dir(client_download_dir)
            write_manifest(manifest, client_download_dir, before)
            main, screen = self.open_remote_dir(str(PurePosixPath(remote).parent))
            seen: list[Screen] = []
            screen, row = self._find_in_list(main, screen, name, seen)
            canonical = row is None and self.canonical_listing is not None
            if canonical:
                parts = remote.strip("/").split("/")
                screen, row = self._canonical_child_row(main, screen, parts, len(parts) - 1, seen)
            if row is None:
                raise StepFailed("remote_dir_not_found", remote_path=remote)
            kinds = [r["kind"] for r in list_rows(screen) if _list_key(r["name"]) == _list_key(name)]
            # The canonical fallback already proves a unique native folder row
            # in this exact parent against the complete provider listing. Its
            # OCR alias may differ from the requested spelling (spaces/_).
            if not canonical and kinds != ["dir"]:
                raise StepFailed("remote_row_not_a_folder", remote_path=remote, kinds=kinds)
            button, screen = self._select_for_download(main, screen, row, None)
        except Exception as exc:
            return self._not_submitted(key, exc)
        self.ledger.update(key, "submitted", via="official_client_gui", manifest=os.fspath(manifest), name=name,
                           expected_files=tree["files"], expected_dirs=tree["dirs"], local_dir=local)
        try:
            x, y = _center(screen, button)
            self.lease_check()
            self.driver.input(screen.frame, action="click", x=x, y=y, button="left")
        except Exception as exc:
            return TransferResult("state_unknown", "resume_download_dir_do_not_resubmit", key, {"error": str(exc)})
        return self.collect_dir(key, client_download_dir, before, name, tree["files"], local)

    def collect_dir(self, key: str, client_download_dir, before, name: str, expected_files: dict[str, str],
                    local_dir: str) -> TransferResult:
        """Filesystem polling only; see :func:`collect_download_dir`."""
        token = None
        facts: dict = {"files_total": len(expected_files)}
        for _ in range(self.poll_attempts):
            self.sleep(self.settle)
            try:
                result = collect_download_dir(download_root=client_download_dir, before=before, expected_name=name,
                                              expected_files=expected_files, target_dir=local_dir, stable_token=token)
            except Exception as exc:
                return TransferResult("state_unknown", "resume_download_dir_do_not_resubmit", key,
                                      {**facts, "error": f"{type(exc).__name__}: {exc}"})
            raw = result.to_dict()
            facts.update({k: v for k, v in raw.items() if k not in {"status", "next", "stable_token"}},
                         collect_status=raw["status"], collect_next=raw["next"])
            if result.status in {"collected", "already_collected"}:
                self.ledger.update(key, "verified", verification="local_names_sizes", target_path=result.target_dir,
                                   files=result.files, duplicates=result.duplicates)
                return TransferResult("downloaded", "done", key, facts)
            if result.status in {"mismatch", "ambiguous", "target_exists"}:
                self.ledger.update(key, "observed", collect=result.status)
                return TransferResult("needs_review", "manual_review", key, facts)
            token = result.stable_token
        return TransferResult("submitted", "resume_download_dir_later_do_not_resubmit", key, facts)

    def resume_download_dir(self, key: str) -> TransferResult:
        """Continue collecting a submitted folder download; filesystem only, never GUI input."""
        record = self.ledger.get(key)
        if record is None:
            return TransferResult("unknown_key", "check_key", key)
        if record.get("operation") != "download_dir":
            return TransferResult("not_a_download_dir", "use_status", key, {"operation": record.get("operation")})
        state, facts = record["state"], record.get("facts") or {}
        if state == "verified":
            return artifact_status(record)
        if state in {"failed", "abandoned"}:
            return TransferResult("not_submitted" if facts.get("before_submit") else state, "inspect_then_retry", key,
                                  {"state": state, "reason": facts.get("reason")})
        if state == "planned" or not facts.get("manifest"):
            return TransferResult("planned", "no_submit_recorded_mark_failed_if_no_run_is_active", key, {"state": state})
        try:
            root, before = load_manifest(facts["manifest"])
        except (OSError, ValueError, KeyError) as exc:
            return TransferResult("needs_review", "manual_review_do_not_resubmit", key,
                                  {"state": state, "reason": "manifest_unreadable", "error": f"{type(exc).__name__}: {exc}"})
        return self.collect_dir(key, root, before, facts["name"], facts["expected_files"], facts["local_dir"])

    # -- own share creation ------------------------------------------------------

    def share_window(self, name: str) -> str | None:
        rows = [w for w in self.client_windows() if (m := SHARE_WINDOW_RE.match(str(w.get("title") or "")))
                and _name_key(m.group(1)) == _name_key(name)]
        if len(rows) > 1:
            raise StepFailed("share_window_not_unique", name=name)
        return rows[0]["id"] if rows else None

    def _clipboard(self) -> str | None:
        try:
            return self.driver.clipboard_text()
        except Exception:  # clipboard busy: OCR still decides
            return None

    def _close_share_window(self, window_id: str, title: str) -> bool:
        self.lease_check()
        if not self.driver.close_window(window_id, title):
            return False
        for _ in range(self.poll_attempts):
            self.sleep(self.settle)
            if all(w["id"] != window_id for w in self.client_windows()):
                return True
        return False

    def share_create(self, remote_path: str, *, days: int = 7, pwd: str | None = None) -> TransferResult:
        """Create a link share of one remote file/folder in the official client; ``url``/``pwd`` read by OCR.

        右键 the row's 修改时间 cell → 分享 → window 「分享： <name>」 → 有效期 option → (自定义 code,
        experimental) → 创建链接 (the only irreversible input, recorded first, never repeated) → read the
        link → close the window by WM_CLOSE after an exact title check.  A recorded share is answered
        from the ledger; one whose outcome is unknown is never recreated (check 我的分享 instead).
        """
        if days not in SHARE_DAYS:
            raise ValueError(f"days 只能是 {sorted(SHARE_DAYS)}：{days!r}")
        if pwd is not None and not SHARE_PWD_RE.match(pwd):
            raise ValueError("自定义提取码必须是 4 位字母或数字")
        remote = validate_remote_dir(remote_path)
        name = PurePosixPath(remote).name
        validate_file_name(name)
        record, created = self.ledger.begin("share_create", remote_path=remote, days=days, custom_pwd=bool(pwd))
        key = record["key"]
        if not created:
            facts = record.get("facts") or {}
            if record["state"] == "verified":
                return TransferResult("already_shared", "done", key, {"path": remote, "url": facts.get("url"),
                                                                      "pwd": facts.get("pwd"), "days": days})
            return TransferResult("state_unknown", "check_my_shares_do_not_recreate", key, {"path": remote, "state": record["state"]})
        window = title = None
        try:
            if self.share_window(name):
                raise StepFailed("share_window_already_open", name=name)
            main, screen = self.open_remote_dir(str(PurePosixPath(remote).parent))
            screen, row = self._find_in_list(main, screen, name)
            if row is None:
                raise StepFailed("remote_path_not_found", remote_path=remote)
            screen = self._close_context_menu(main, screen)
            if selection_evidence(screen):
                screen = self._clear_selection(main, screen)
                screen, row = self._find_in_list(main, screen, name)
                if row is None:
                    raise StepFailed("row_lost_after_clear", remote_path=remote)
            anchor = row_mtime_cell(screen, row)
            if anchor is None:
                raise StepFailed("row_mtime_cell_not_found", row=_brief(row))
            before = screen
            screen = self.click_box(screen, main, anchor, button="right")
            if selection_count(screen) != 1:
                raise StepFailed("selection_count_unverified", count=selection_count(screen))
            items = context_menu_item(before, screen, "分享")
            if len(items) != 1:
                raise StepFailed("context_menu_share_not_found" if not items else "context_menu_share_ambiguous")
            x, y = _center(screen, items[0])
            self.lease_check()
            self.driver.input(screen.frame, action="click", x=x, y=y, button="left")
            for _ in range(self.poll_attempts):
                self.sleep(self.settle)
                window = self.share_window(name)
                if window:
                    break
            if not window:
                raise StepFailed("share_window_not_open", name=name)
            title = next(str(w.get("title")) for w in self.client_windows() if w["id"] == window)
            self.lease_check()
            self.driver.activate(window)
            share = self.observe(window)
            label = share.unique(SHARE_VALIDITY_LABEL, "share_validity_label_not_found")
            options = [line for line in share.same_row(label) if _norm(line.get("text")) == SHARE_DAYS[days]]
            if len(options) != 1:
                raise StepFailed("share_days_option_not_found", days=days, texts=share.texts()[:40])
            share = self.click_box(share, window, options[0])
            if pwd:
                plabel = share.unique(SHARE_PWD_LABEL, "share_pwd_label_not_found")
                custom = [line for line in share.same_row(plabel) if _norm(line.get("text")) == SHARE_PWD_CUSTOM]
                if len(custom) != 1:
                    raise StepFailed("share_pwd_custom_not_found")
                share = self.click_box(share, window, custom[0])
                width, height = share.size
                field_x = (custom[0]["x"] + custom[0]["w"] + 80) / width  # the code box opens right of 自定义
                share = self.act(share, window, action="click", x=min(.95, field_x), y=_cy(custom[0]) / height, button="left")
                share = self.act(share, window, action="key", key="select_all")
                share = self.act(share, window, action="type", text=pwd)
                if pwd not in [_norm(t) for t in share.texts()]:
                    raise StepFailed("share_pwd_not_visible")
            buttons = [line for line in share.lines if _norm(line.get("text")).endswith(SHARE_CREATE)]
            if len(buttons) != 1:
                raise StepFailed("share_create_button_not_found", texts=share.texts()[:40])
        except Exception as exc:
            closed = self._close_share_window(window, title) if window and title else None
            result = self._not_submitted(key, exc)
            result.facts["share_window_closed"] = closed
            return result
        self.ledger.update(key, "submitted", via="official_client_gui", path=remote, days=days)
        clip_before = self._clipboard()
        try:
            x, y = _center(share, buttons[0])
            self.lease_check()
            self.driver.input(share.frame, action="click", x=x, y=y, button="left")
        except Exception as exc:
            return TransferResult("state_unknown", "check_my_shares_do_not_recreate", key, {"error": str(exc), "path": remote})
        link = None
        for _ in range(self.poll_attempts):
            self.sleep(self.settle)
            done = self.observe(window)
            link = parse_share_link(done)
            if link:
                break
        # The client copies "通过网盘分享的文件：<name> 链接: … 提取码: …" itself; exact text beats OCR.
        clip = self._clipboard()
        clip_link = parse_share_text(clip) if clip and clip != clip_before and name in clip else None
        source = "clipboard" if clip_link else ("ocr" if link else None)
        if clip_link and link and clip_link != link:
            source = "clipboard_ocr_differ"
        link = clip_link or link
        facts = {"path": remote, "days": days, "url": link[0] if link else None, "pwd": link[1] if link else None,
                 "read_from": source}
        if link and pwd and link[1] != pwd:
            facts["pwd_requested_mismatch"] = True
        try:
            frame_image(done.frame).save(self.ledger.path.parent / f"share_create-{key}.png")
        except Exception:
            pass
        facts["share_window_closed"] = self._close_share_window(window, title)
        if not link:
            self.ledger.update(key, "observed", reason="share_link_not_read")
            return TransferResult("submitted", "check_my_shares_do_not_recreate", key, facts)
        self.ledger.update(key, "verified", url=facts["url"], pwd=facts["pwd"], days=days)
        return TransferResult("shared" if not facts.get("pwd_requested_mismatch") else "needs_review",
                              "done" if not facts.get("pwd_requested_mismatch") else "check_pwd", key, facts)

    # -- share link download -----------------------------------------------------

    def download_share(self, share_url: str, item_name: str, local_dir: str | os.PathLike, *,
                       client_download_dir: str | os.PathLike, expected_size: int, pwd: str | None = None,
                       expected_sha256: str | None = None) -> TransferResult:
        """EXPERIMENTAL, not verified end to end: open a share link, tick one top-level item, press 下载 once, collect.

        The ``baiduyunguanjia://evoked-sharelink`` payload, the checkbox offset
        and the 保存到网盘/下载 row were derived from the official client's
        static main.js/CSS only.  Every step is still observed and the submit
        happens at most once, but no real share link has been run through this
        method; treat its results as provisional.

        The extraction code is used only for the scheme payload; ledger and
        results carry a hash of the link, never the code.
        """
        url, code = parse_share_url(share_url, pwd)
        validate_file_name(item_name)  # share root only; no sub-directories
        local_dir = os.fspath(Path(local_dir).resolve())
        link_id = hashlib.sha256(url.encode()).hexdigest()[:16]
        identity = {"link": link_id, "item": item_name, "local_dir": local_dir}
        prior = self._prior("download_share", identity, _content_identity(expected_size, expected_sha256))
        if isinstance(prior, TransferResult):
            return prior
        key = prior
        manifest = self.ledger.path.parent / f"download-{key}.before.json"
        try:
            before = snapshot_dir(client_download_dir)
            write_manifest(manifest, client_download_dir, before)
            main = self.main_window()
            known = {w.get("id") for w in self.client_windows()}
            self.lease_check()
            self.driver.open_scheme(share_scheme_url(url, code))  # exactly once per call
            self.lease_check()
            self.driver.activate(main)
            screen = self._await_share_list(main, lambda sc: bool(_share_rows(sc, item_name)))
            extra = [w for w in self.client_windows() if w.get("id") not in known]
            if extra:  # the scheme spawned a window this flow does not model
                raise StepFailed("share_opened_extra_window",
                                 windows=[(w.get("title"), w.get("width"), w.get("height")) for w in extra])
            if screen is None:
                raise StepFailed("share_list_not_visible")
            header, count = _share_header(screen)
            if count != 0:
                raise StepFailed("share_selection_not_empty", selected=count)
            rows = _share_rows(screen, item_name)
            if len(rows) != 1:
                raise StepFailed("share_item_ambiguous", matches=[_brief(r) for r in rows])
            width, height = screen.size
            anchor = share_checkbox_anchor(screen, header, count)
            screen = self.act(screen, main, action="click", x=anchor["x"] / width, y=_cy(rows[0]) / height, button="left")
            after = _share_header(screen)
            row = _share_rows(screen, item_name)
            if after is None or after[1] != 1 or len(row) != 1 \
                    or share_checkbox_state(screen, anchor, after[0], _cy(row[0])) != "checked":
                raise StepFailed("share_selection_unverified", header=after and _brief(after[0]), texts=screen.texts()[:60])
            button = _share_download_button(screen, after[0])
        except Exception as exc:
            return self._not_submitted(key, exc)
        self.ledger.update(key, "submitted", via="official_client_gui_share", manifest=os.fspath(manifest), name=item_name,
                           expected_size=expected_size, expected_sha256=_upper(expected_sha256), local_dir=local_dir,
                           link=link_id, experimental=True)
        try:
            bx, by = _center(screen, button)
            self.lease_check()
            self.driver.input(screen.frame, action="click", x=bx, y=by, button="left")
        except Exception as exc:
            return TransferResult("state_unknown", "collect_do_not_resubmit", key, {"error": str(exc), "manifest": os.fspath(manifest)})
        return self.collect(key, client_download_dir, before, item_name, expected_size, local_dir, expected_sha256)

    # -- save share into the own netdisk (experimental: destination bar/picker labels provisional) ---

    def save_share(self, share_url: str, target_dir: str, *, pwd: str | None = None,
                   items: list[str] | None = None, opener: str = "address_bar") -> TransferResult:
        """EXPERIMENTAL: save share root items straight into ``target_dir`` of the logged-in account.

        ``items=None`` selects every root item of the share (header checkbox; the
        selected count must equal the visible root rows); a list selects exactly
        those root names.  Order: validate link/target/items -> ledger (link hash,
        never the code) -> open/create target and require a complete listing ->
        open the share -> refuse any name already in the target
        (``observed_existing``, nothing is overwritten) -> select -> read the
        bottom destination label; unless it already equals ``target_dir`` exactly,
        click its folder icon, prove the picker crumb equals ``target_dir``, press
        the picker's 确定 (destination choice only, no copy) and require the label
        to show the full target -> re-check selection -> press 保存到网盘 exactly
        once (the only submission) -> only observe ``target_dir``.

        Every failure before 保存到网盘 is ``not_submitted``: neither the folder
        icon nor the picker copies anything.  Once 保存到网盘 was pressed (or its
        click failed midway) the record stays blocking and is never resubmitted.
        Remote sizes are OCR labels only (``size_label``); content is never verified.

        ``opener`` picks how the share is opened, exactly once per call and never falling back to
        the other: ``address_bar`` (default) types the full link into the main window's OCR-proven
        address field (see SHARE_OPENERS); ``scheme`` hands the evoked-sharelink scheme to the client.
        Either way the real share list must appear; an extraction page is reported, never typed into.
        """
        if opener not in SHARE_OPENERS:
            raise ValueError(f"opener 必须是 {'/'.join(SHARE_OPENERS)}")
        url, code = parse_share_url(share_url, pwd)
        target = validate_remote_dir(target_dir)
        if target == "/":
            raise ValueError("目标网盘目录不能是根目录")
        wanted = None
        if items is not None:
            wanted = [validate_file_name(name) or name for name in items]
            if not wanted or len({_name_key(n) for n in wanted}) != len(wanted):
                raise ValueError("items 必须是非空且不重复的分享根项名称")
        link_id = hashlib.sha256(url.encode()).hexdigest()[:16]
        secrets = _share_secrets(url, code)  # OCR texts/errors may echo the link or code: never output them
        identity = {"link": link_id, "target_dir": target, "items": wanted or "*all*"}
        key = transfer_key("save_share", **identity)
        prior = self.ledger.get(key)
        if prior and prior["state"] not in {"failed", "abandoned"}:
            return TransferResult("already_recorded", "verify_save_share_do_not_resubmit", key,
                                  {"state": prior["state"], **_save_facts(prior.get("facts") or {})})
        record, created = self.ledger.begin("save_share", **identity)
        if not created:
            return TransferResult("already_recorded", "verify_save_share_do_not_resubmit", key, {"state": record["state"]})
        scope = {"target_dir": target, "selection": "explicit" if wanted else "all_root_items", "link": link_id, "opener": opener}
        parts = [p for p in target.split("/") if p]
        try:
            main, screen = self.open_remote_dir(target, create_missing=True)
            created_dirs = list(self.created_dirs)
            _, existing, complete = self._list_dir_entries(main, screen)
            if not complete:
                raise StepFailed("target_listing_incomplete", entries=len(existing))
            known = {w.get("id") for w in self.client_windows()}
            if opener == "address_bar":
                self._open_share_address_bar(main, url, code)
            else:
                self.lease_check()
                self.driver.open_scheme(share_scheme_url(url, code))  # exactly once per call
                self.lease_check()
                self.driver.activate(main)
            screen = self._await_share_list(main, lambda sc: _share_header(sc) is not None and bool(share_root_rows(sc)))
            extra = [w for w in self.client_windows() if w.get("id") not in known]
            if extra:
                raise StepFailed("share_opened_extra_window",
                                 windows=[(w.get("title"), w.get("width"), w.get("height")) for w in extra])
            if screen is None:
                raise StepFailed("share_list_not_visible")
            header, count = _share_header(screen)
            rows = share_root_rows(screen)
            names = [_norm_name(r) for r in rows]
            if len({_name_key(n) for n in names}) != len(names):
                raise StepFailed("share_root_names_ambiguous", names=names)
            baselines = [_name_crops(screen, rows)]  # visual proof from the very frame the rows were read on
            preselected = count
            chosen = wanted or names
            missing = [n for n in chosen if _name_key(n) not in {_name_key(x) for x in names}]
            if missing:
                raise StepFailed("share_item_not_found", missing=missing, visible=len(names))
            if not _keys_disjoint(existing, chosen):
                clash = [n for n in chosen if _name_key(n) in {_name_key(e["name"]) for e in existing}]
                self.ledger.update(key, "failed", reason="observed_existing", before_submit=True, names=clash)
                return TransferResult("observed_existing", "verify_existing_content_do_not_overwrite", key,
                                      {**scope, "existing": [e for e in existing if e["name"] in clash],
                                       "content_verified": False})
            anchor = share_checkbox_anchor(screen, header, count)  # one column for every checkbox click
            if preselected:  # native one-root shares open with 已选择1个文件: clear, never trust an unproven set
                screen, header = self._clear_share_selection(main, screen, header, anchor, count, len(rows))
            screen = self._select_share_items(main, screen, header, anchor, rows, chosen, all_items=wanted is None)
            selected_before = (_share_header(screen) or (None, None))[1]
            baselines.append(_pre_picker_crops(screen, rows))  # selected-state frame, only if it reads ``rows`` exactly
            screen, initial = self._choose_destination(main, screen, parts)
            # The picker round trip must not have changed the selection or the visible share.
            screen, after, row_proof = self._await_share_roundtrip(main, screen, rows, len(chosen), parts,
                                                                   selected_before, baselines)
            save = screen.unique(SHARE_SAVE, "share_save_button_not_found", below=after[0]["y"])
        except StepFailed as exc:
            if exc.code == "save_dialog_before_save_click":  # a stray click may have copied: block, never retry blind
                self.ledger.update(key, "submitting", reason=exc.code, **scope)
                return TransferResult("state_unknown", "inspect_client_do_not_resubmit", key,
                                      {**scope, "reason": exc.code, **_redact_secrets(exc.facts, secrets)})
            return self._not_submitted(key, exc, secrets)
        except Exception as exc:
            return self._not_submitted(key, exc, secrets)
        scope.update(root_items=len(chosen), visible_root_rows=len(rows), names=chosen, created_dirs=created_dirs,
                     preselected=preselected, row_identity_proof=row_proof,
                     destination_before=initial)
        # 保存到网盘 copies immediately: from this update on the record blocks any resubmission.
        self.ledger.update(key, "submitted", via="official_client_gui_share_save", experimental=True,
                           destination_verified=True, **scope)
        try:
            bx, by = _center(screen, save)
            self.lease_check()
            self.driver.input(screen.frame, action="click", x=bx, y=by, button="left")
        except Exception as exc:
            return TransferResult("state_unknown", "verify_save_share_do_not_resubmit", key,
                                  {**scope, "error": _redact_secrets(str(exc), secrets)})
        reported = self._observe_save_outcome(main)
        if reported:
            self.ledger.update(key, "submitted", client_reported=reported)
        return self.verify_save_share(key)

    def _choose_destination(self, main: str, screen: Screen, parts: list[str]) -> tuple[Screen, list[str] | None]:
        """Make the bottom destination label equal ``parts``; returns the screen and the label seen first.

        Nothing here copies: the folder icon opens the picker and the picker's 确定
        only chooses the destination.  Any failure raises StepFailed after trying
        to cancel an open picker (``cancel`` fact says whether that was proven).
        """
        initial = destination_path(screen)
        if initial is None:
            raise StepFailed("destination_label_not_found", texts=screen.texts()[:60])
        if same_path(initial, parts):
            return screen, initial
        icon = destination_icon(screen)
        before = {w.get("id") for w in self.client_windows()}
        screen = self.click_box(screen, main, icon)
        picker_id, picker, why = self._find_save_picker(main, before)
        if picker is None:
            if screen.find(SAVE_DONE) or screen.find(SAVE_FAILED_RE):
                raise StepFailed("save_dialog_before_save_click", texts=screen.texts()[:60])
            # No modal was proven, so nothing is cancelled or clicked (an open but unrecognised
            # modal stays for the operator; ``picker`` names the refusal).
            raise StepFailed("save_picker_not_recognized", picker=why, cancel="not_attempted", texts=screen.texts()[:60])
        region = save_picker(picker)["region"]  # closure is proven where this modal was
        try:
            picker = self._picker_open(picker_id, picker, parts)
            # 确定 of the one known layout; the path was proven by _picker_open (maybe on an elided crumb)
            self.click_box(picker, picker_id, _picker_layout(picker)["confirm"])  # chooses the destination only
            if not self._poll_picker_gone(picker_id, region):
                raise StepFailed("save_picker_not_closed_after_confirm")
        except StepFailed as exc:
            exc.facts["cancel"] = "verified" if self._picker_cancel(picker_id, region) else "unverified"
            raise
        shown = self._poll(main, lambda sc: same_path(destination_path(sc), parts))
        if shown is None:
            last = self.observe(main)
            raise StepFailed("destination_label_mismatch", label=destination_path(last), expected=parts)
        return shown, initial

    def _await_share_roundtrip(self, main: str, screen: Screen, rows: list[dict], expected: int, parts: list[str],
                               selected_before: int | None, baselines: list[list[str | None] | None] = ()
                               ) -> tuple[Screen, tuple[dict, int], str]:
        """Final proof right before the one submission, all on one frame: 已选择N个文件 equals ``expected``,
        the root rows are exactly ``rows`` (same count, same name keys; never fuzzy) and the label is
        the full target.  Returns the proving frame, its header and the row proof used (``exact_text``
        or ``ellipsis_pixels``).

        The one exception to exact text (``ellipsis_pixels``, see :func:`_ellipsis_pixel_proof`): the
        measured OCR drift of a trailing "..." read as "..", accepted only with the same ordered row
        count, the same exact box and byte-identical RGB pixels of that box against a ``baselines``
        frame (captured before the picker on frames whose OCR read ``rows`` exactly).

        Observation only: up to SHARE_SETTLE_POLLS fresh frames within SHARE_SETTLE_SECONDS, so one
        partly OCR'd frame does not refuse.  On exhaustion the unchanged reason codes are raised; the
        selection reason carries name hashes/lengths/boxes and counts only, never names."""
        wanted = sorted(_name_key(_norm_name(r)) for r in rows)

        proofs: dict[int, dict] = {}

        def same_share(sc: Screen) -> bool:
            after = _share_header(sc)
            if after is None or after[1] != expected:
                proofs.pop(id(sc), None)
                return False
            seen = share_root_rows(sc)
            if sorted(_name_key(_norm_name(r)) for r in seen) == wanted:
                proofs[id(sc)] = {"proof": "exact_text"}
                return True
            proofs[id(sc)] = _ellipsis_pixel_proof(sc, rows, seen, baselines)
            return proofs[id(sc)]["proof"] == "ellipsis_pixels"

        attempts, start = 1, time.monotonic()
        while not (same_share(screen) and same_path(destination_path(screen), parts)) \
                and attempts <= SHARE_SETTLE_POLLS and time.monotonic() - start < SHARE_SETTLE_SECONDS:
            self.sleep(self.settle)
            screen = self.observe(main)
            attempts += 1
        after = _share_header(screen)
        if not same_share(screen):
            seen = share_root_rows(screen)
            raise StepFailed("share_selection_changed_after_destination", selected=after and after[1],
                             expected=expected, selected_before=selected_before, expected_rows=len(rows),
                             observed_rows=len(seen), expected_identity=_row_identity(rows),
                             observed_identity=_row_identity(seen), attempts=attempts,
                             ellipsis_proof=proofs.get(id(screen)))
        if not same_path(destination_path(screen), parts):
            raise StepFailed("destination_not_verified", label=destination_path(screen), attempts=attempts)
        return screen, after, proofs[id(screen)]["proof"]

    def _observe_save_outcome(self, main: str) -> str | None:
        """Observation after 保存到网盘: 'success'/'failure' if the client says so; 查看 is pressed only as navigation."""
        try:
            for _ in range(self.poll_attempts):
                self.sleep(self.settle)
                for wid in [main, *[w["id"] for w in self.client_windows() if w.get("id") != main]]:
                    screen = self.observe(wid)
                    if screen.find(SAVE_DONE):
                        view = screen.find(SAVE_VIEW)
                        if len(view) == 1:
                            self.click_box(screen, wid, view[0])
                        return "success"
                    if screen.find(SAVE_FAILED_RE):
                        return "failure"
        except Exception:
            return None  # observation only; verify_save_share decides
        return None

    def verify_save_share(self, key: str) -> TransferResult:
        """Fresh read of ``target_dir`` (navigation, never the share tab); nothing resubmits.

        A ``verified`` record is history only: the folder is listed again and a name
        missing now is reported (``missing_after_verified`` / ``partial_present``),
        never answered from the cached state.
        """
        record = self.ledger.get(key)
        if not record or record.get("operation") != "save_share":
            return TransferResult("unknown_key", "inspect", key)
        facts = record.get("facts") or {}
        scope = _save_facts(facts)
        was_verified = record["state"] == "verified"
        if record["state"] not in {"submitted", "verified"} or not facts.get("names"):
            return TransferResult(record["state"] if record["state"] != "submitting" else "state_unknown",
                                  "inspect_client_do_not_resubmit", key, scope)
        try:
            main, screen = self.open_remote_dir(facts["target_dir"])
            _, entries, complete = self._list_dir_entries(main, screen)
        except Exception as exc:
            return TransferResult("state_unknown", "verify_save_share_later", key, {**scope, "error": str(exc)})
        by_key = {_name_key(e["name"]): e for e in entries}
        found = [{"name": by_key[_name_key(n)]["name"], "kind": by_key[_name_key(n)]["kind"],
                  "size_label": by_key[_name_key(n)]["size"]} for n in facts["names"] if _name_key(n) in by_key]
        missing = [n for n in facts["names"] if _name_key(n) not in by_key]
        result = {**scope, "present": found, "missing": missing, "listing_complete": complete,
                  "content_verified": False, "previously_verified": was_verified}
        if not missing:
            self.ledger.update(key, "verified", verification="remote_present", present=found, checked_at=time.time())
            return TransferResult("remote_present", "business_verify_content", key, result)
        if not complete:  # an unread part of the list may hold the missing names
            return TransferResult("state_unknown", "verify_save_share_later_do_not_resubmit", key, result)
        if was_verified:
            self.ledger.update(key, "verified", last_check="missing", missing=missing, checked_at=time.time())
            status = "partial_present" if found else "missing_after_verified"
            return TransferResult(status, "business_review_do_not_resubmit", key, result)
        status = "partial_present" if found else "submitted"
        return TransferResult(status, "verify_save_share_later_do_not_resubmit", key, result)

    def _clear_share_selection(self, main: str, screen: Screen, header: dict, anchor: dict, count: int,
                               visible: int) -> tuple[Screen, dict]:
        """Header-checkbox clicks on ``anchor`` (at most MAX_CLEAR_CLICKS: partial -> all -> none) until
        已选择0个文件 with an empty header box is observed.

        Every click is followed by a fresh read; the visible root rows must stay the same.  A second
        click only follows a first that visibly grew the selection with the box checked on the
        anchor; anything else fails without further input.
        """
        width, height = screen.size
        for _ in range(MAX_CLEAR_CLICKS):
            screen = self.act(screen, main, action="click", x=anchor["x"] / width, y=_cy(header) / height, button="left")
            after = _share_header(screen)
            if after is None or len(share_root_rows(screen)) != visible:
                raise StepFailed("share_selection_clear_unverified", header=after and _brief(after[0]))
            header, now = after
            state = share_checkbox_state(screen, anchor, header, _cy(header))
            if now == 0:
                if state != "empty":
                    raise StepFailed("share_selection_clear_unverified", selected=0, checkbox=state)
                return screen, header
            if now <= count or state != "checked":
                raise StepFailed("share_selection_not_cleared", selected=now, before=count, checkbox=state)
            count = now
        raise StepFailed("share_selection_not_cleared", selected=count)

    def _select_share_items(self, main: str, screen: Screen, header: dict, anchor: dict, rows: list[dict],
                            chosen: list[str], *, all_items: bool) -> Screen:
        """Select-all via the header box or one click per chosen row box, all on ``anchor``; each proven by
        the next frame's count and the clicked box turning checked."""
        width, height = screen.size
        if all_items:
            screen = self.act(screen, main, action="click", x=anchor["x"] / width, y=_cy(header) / height, button="left")
            after = _share_header(screen)
            if after is None or after[1] != len(rows) or len(share_root_rows(screen)) != len(rows):
                # More selected than visible means rows outside the frame: never save an unseen set.
                raise StepFailed("share_select_all_unverified", selected=after and after[1], visible=len(rows))
            if share_checkbox_state(screen, anchor, after[0], _cy(after[0])) != "checked":
                raise StepFailed("share_select_all_unverified", selected=after[1], checkbox="not_checked")
            return screen
        for done, name in enumerate(chosen, 1):
            row = [r for r in share_root_rows(screen) if _name_key(_norm_name(r)) == _name_key(name)]
            if len(row) != 1:
                raise StepFailed("share_item_ambiguous", name=name, matches=len(row))
            screen = self.act(screen, main, action="click", x=anchor["x"] / width, y=_cy(row[0]) / height, button="left")
            after = _share_header(screen)
            if after is None or after[1] != done:
                raise StepFailed("share_selection_unverified", name=name, selected=after and after[1], expected=done)
            row = [r for r in share_root_rows(screen) if _name_key(_norm_name(r)) == _name_key(name)]
            if len(row) != 1 or share_checkbox_state(screen, anchor, after[0], _cy(row[0])) != "checked":
                raise StepFailed("share_selection_unverified", name=name, selected=done, checkbox="not_checked")
        return screen

    def _find_save_picker(self, main: str, before: set) -> tuple[str | None, Screen | None, str | None]:
        """Observe only: a new client window, else the main window, showing exactly one picker modal.

        Returns ``(window, screen, None)`` or ``(None, None, last refusal code)``.
        """
        why = None
        for _ in range(self.poll_attempts):
            self.sleep(self.settle)
            new = [w["id"] for w in self.client_windows() if w.get("id") not in before]
            for wid in (new or [main]):
                screen = self.observe(wid)
                try:
                    save_picker(screen)
                except StepFailed as exc:
                    why = exc.code
                    continue
                return wid, screen, None
        return None, None, why

    def _picker_open(self, wid: str, screen: Screen, parts: list[str]) -> Screen:
        """Reset to the picker root when not on a prefix, then ONE single click per folder name.

        Observed: a single click on a folder name opens it and the crumb updates
        asynchronously; a double-click can cross two levels.  Every lookup stays inside
        the one recognised modal (:func:`save_picker`); a folder missing from its list
        viewport is searched by bounded scrolling there (:meth:`_picker_folder`).  After
        every click the crumb is re-observed until it equals the expected prefix; anything
        else (overshoot, wrong folder, no update) fails without another click.

        ``verified`` is the last FULL path proven on this modal.  A deep crumb the client
        shortens ("全部文件/.../m/sources") is accepted only as the fresh, changed crumb right
        after the one unique child click from ``verified`` and only via
        :func:`elided_crumb_proves`; the modal never opens navigation on an elided crumb.
        """
        picker = save_picker(screen)
        path = picker["path"]
        if not same_path(path, parts[:len(path)]):
            crumb = picker["crumb"]
            box = _picker_root_span(screen, crumb)
            if box is None:
                raise StepFailed("save_picker_root_span_unknown", line=_brief(crumb))
            screen = self.click_box(screen, wid, box)
            screen = self._poll(wid, lambda sc: save_picker_path(sc) == []) or screen
            path = save_picker_path(screen)
            if path != []:
                raise StepFailed("save_picker_root_not_verified", path=path)
            picker = save_picker(screen)
        verified = list(path)
        for depth in range(len(path), len(parts)):
            screen, row = self._picker_folder(wid, screen, picker, parts[depth])
            stale = picker["visible"]  # the crumb before the click: an unchanged elided crumb proves nothing
            screen = self.click_box(screen, wid, row)
            want = parts[:depth + 1]
            shows = lambda sc: _picker_shows(sc, verified, parts[depth], stale)
            screen = self._poll(wid, shows) or screen
            if not shows(screen):
                raise StepFailed("save_picker_folder_not_verified", segment=parts[depth],
                                 path=_picker_visible(screen))
            verified = want
            picker = save_picker(screen, elided_ok=True)
        if not same_path(verified, parts) or (not same_path(path, parts) and not _picker_shows(
                screen, verified[:-1], verified[-1], None)):
            raise StepFailed("save_picker_target_mismatch", path=_picker_visible(screen))
        return screen

    def _picker_folder(self, wid: str, screen: Screen, picker: dict, name: str) -> tuple[Screen, dict]:
        """The one row named exactly ``name`` wholly inside the modal's list viewport.

        When absent, scroll inside that viewport (at most ``max_scrolls`` times) and
        re-recognise the same modal on the same crumb after every scroll; unchanged
        visible rows mean the end of the list.  Two matches fail at once.
        """
        path = picker["visible"]
        seen = None
        for attempt in range(self.max_scrolls + 1):
            rows = picker_rows(screen, picker)
            found = [l for l in rows if _name_key(l.get("text")) == _name_key(name)]
            if len(found) > 1:
                raise StepFailed("save_picker_folder_ambiguous", segment=name, matches=len(found))
            if found:
                return screen, found[0]
            shown = [(_literal(l.get("text")), l["y"]) for l in rows]
            if shown == seen or attempt == self.max_scrolls:
                break
            seen = shown
            x, y = _center(screen, picker["viewport"])
            screen = self.act(screen, wid, action="scroll", x=x, y=y, delta=PICKER_SCROLL_DELTA)
            picker = save_picker(screen, elided_ok=True)
            if picker["visible"] != path:
                raise StepFailed("save_picker_scroll_changed_path", segment=name, path=picker["visible"])
        raise StepFailed("save_picker_folder_not_found", segment=name, matches=0, scrolls=attempt)

    def _picker_cancel(self, wid: str, region: dict) -> bool:
        """Click the freshly recognised modal's 取消 once, then observe: True only when the picker is proven gone.

        Without a recognised modal nothing is clicked; the observation still decides.
        """
        try:
            screen = self.observe(wid)
            self.click_box(screen, wid, _picker_layout(screen)["cancel"])  # layout only: works on an elided crumb
        except Exception:
            pass  # the observation below decides
        try:
            return self._poll_picker_gone(wid, region)
        except Exception:
            return False

    def _poll_picker_gone(self, wid: str, region: dict) -> bool:
        """Fresh observations: gone once no modal title is shown anywhere and no picker
        button is left inside ``region`` (where the modal was; share-page controls elsewhere don't count)."""
        for _ in range(self.poll_attempts):
            if wid not in {w.get("id") for w in self.client_windows()}:
                return True
            screen = self.observe(wid)
            buttons = [l for l in screen.find(PICKER_CONFIRM) + screen.find(PICKER_CANCEL) if _inside(l, region)]
            if not screen.find(PICKER_TITLE) and not buttons:
                return True
            self.sleep(self.settle)
        return False

    def observe_save_picker(self) -> dict:
        """READ-ONLY diagnosis (captures + OCR, no input): is a save-share folder picker visible now?

        Reports every client window with whether it passes the PROVISIONAL picker test,
        the parsed crumb path, the confirm/cancel boxes, the share bottom destination
        label (and where its folder icon would be clicked) and the OCR texts, so the real
        surfaces can be measured before ``save_share`` is trusted.  ``submittable`` is
        True only when exactly one picker surface is recognised.
        """
        surfaces = []
        for w in self.client_windows():
            screen = self.observe(w["id"])
            confirm, cancel = screen.find(PICKER_CONFIRM), screen.find(PICKER_CANCEL)
            try:
                picker, why = save_picker(screen), None
            except StepFailed as exc:
                picker, why = None, exc.code
            surfaces.append({"window": w["id"], "title": w.get("title"), "size": [w.get("width"), w.get("height")],
                             "picker_path": picker and picker["path"], "picker_reason": why,
                             "picker_region": picker and picker["region"], "confirm": [_brief(c) for c in confirm],
                             "cancel": [_brief(c) for c in cancel], "recognized": picker is not None,
                             "destination": destination_path(screen), "destination_icon": _icon_or_reason(screen),
                             "texts": screen.texts()[:80]})
        hits = [s for s in surfaces if s["recognized"]]
        return {"status": "picker_recognized" if len(hits) == 1 else ("picker_ambiguous" if hits else "picker_not_recognized"),
                "submittable": len(hits) == 1, "provisional_labels": {"roots": list(PICKER_ROOTS), "confirm": PICKER_CONFIRM,
                                                                       "cancel": PICKER_CANCEL,
                                                                       "title": PICKER_TITLE}, "surfaces": surfaces}


def _norm_name(line: dict) -> str:
    return str(line.get("text") or "").strip()


def _row_identity(rows: list[dict]) -> list[dict]:
    """Safe per-row diagnostics: full SHA-256 hex and length of the name comparison key, plus the OCR box."""
    out = []
    for r in rows:
        k = _name_key(_norm_name(r))
        out.append({"sha256": hashlib.sha256(k.encode("utf-8")).hexdigest(), "length": len(k),
                    **{a: r.get(a) for a in ("x", "y", "w", "h")}})
    return out


def _row_order(rows: list[dict]) -> list[int]:
    return sorted(range(len(rows)), key=lambda i: (rows[i].get("y"), rows[i].get("x")))


def _name_crops(screen: Screen, rows: list[dict]) -> list[str | None] | None:
    """Per row: SHA-256 over the box size and the exact RGB bytes of its OCR name box on this frame's
    own pixels (:func:`frame_image`).  None for a frame without its full pixel patch; a None entry
    for a box that is not numeric or not inside the frame."""
    try:
        image = frame_image(screen.frame)
    except Exception:  # no proof possible: every consumer fails closed on None
        return None
    out = []
    for r in rows:
        box = [r.get(a) for a in ("x", "y", "w", "h")]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box):
            out.append(None)
            continue
        x, y, w, h = (round(v) for v in box)
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > image.width or y + h > image.height:
            out.append(None)
            continue
        crop = image.crop((x, y, x + w, y + h))
        out.append(hashlib.sha256(f"{w}x{h}:".encode() + crop.tobytes()).hexdigest())
    return out


def _pre_picker_crops(screen: Screen, rows: list[dict]) -> list[str | None] | None:
    """Crops (in ``rows`` order) of the selected-state frame right before the picker, only when its OCR
    reads ``rows`` exactly: same count, same order, exact text and exact boxes.  Otherwise None."""
    seen = share_root_rows(screen)
    seen = [seen[i] for i in _row_order(seen)]
    order = _row_order(rows)
    if [_brief(r) for r in seen] != [_brief(rows[i]) for i in order]:
        return None
    crops = _name_crops(screen, seen)
    if crops is None:
        return None
    out: list[str | None] = [None] * len(rows)
    for i, crop in zip(order, crops):
        out[i] = crop
    return out


def _ellipsis_drift(expected: str, observed: str) -> bool:
    """The one measured OCR drift: ``expected`` ends in exactly three ASCII periods and ``observed`` is
    the identical text ending in exactly two (nothing else may differ; never the other direction)."""
    return expected == observed + "." and observed.endswith("..") and not observed.endswith("...") \
        and bool(observed[:-2].strip())


def _ellipsis_pixel_proof(sc: Screen, rows: list[dict], seen: list[dict],
                          baselines: list[list[str | None] | None]) -> dict:
    """Row identity when exact text failed: ``proof`` is ``ellipsis_pixels`` only if, in row order (y, x),
    the row count is the same, every row text is exact or :func:`_ellipsis_drift`, at least one row
    drifted, and every drifted row keeps the exact same box with an RGB crop hash byte-identical to
    that row's crop on one baseline frame.  Otherwise ``refused``.  Safe diagnostics only: relations,
    booleans, lengths and pixel hashes; never names or images."""
    seen = [seen[i] for i in _row_order(seen)]
    facts: dict = {"proof": "refused", "row_count_same": len(seen) == len(rows),
                   "baselines": [base is not None for base in baselines], "rows": []}
    if len(seen) != len(rows):
        return facts
    current = None
    drifted = proven = 0
    for k, (i, now) in enumerate(zip(_row_order(rows), seen)):
        a, b = _norm_name(rows[i]), _norm_name(now)
        relation = "exact" if a == b else "trailing_period_3to2" if _ellipsis_drift(a, b) else "other"
        row = {"relation": relation, "expected_length": len(a), "observed_length": len(b)}
        if relation == "trailing_period_3to2":
            drifted += 1
            if current is None:
                current = _name_crops(sc, seen) or [None] * len(seen)
            row["geometry_same"] = {**_brief(rows[i]), "text": None} == {**_brief(now), "text": None}
            row["current_sha256"] = current[k]
            row["baseline_sha256"] = [base[i] if base else None for base in baselines]
            row["pixels_same"] = row["geometry_same"] and row["current_sha256"] is not None \
                and row["current_sha256"] in row["baseline_sha256"]
            proven += row["pixels_same"]
        facts["rows"].append(row)
    if drifted and proven == drifted and all(r["relation"] != "other" for r in facts["rows"]):
        facts["proof"] = "ellipsis_pixels"
    return facts


def _keys_disjoint(existing: list[dict], chosen: list[str]) -> bool:
    return not ({_name_key(e["name"]) for e in existing} & {_name_key(n) for n in chosen})


def _share_secrets(url: str, code: str) -> tuple[str, ...]:
    """Current share link pieces that must never reach results/ledger: canonical URL, surl (both
    route forms: ``/s/1<surl>`` and ``share/init?surl=<surl>``) and the extraction code."""
    surl = url.rsplit("/", 1)[-1]
    found = {url, surl, code} | ({surl[1:]} if surl.startswith("1") and len(surl) > 1 else set())
    return tuple(sorted(found, key=len, reverse=True))


def _redact_secrets(value: object, secrets: tuple[str, ...]) -> object:
    """Diagnostics copy (dict/list/str) with every secret replaced, case-insensitively, by ``[redacted]``.

    Reason codes and the link hash are fixed identifiers, not diagnostics, and stay untouched so a
    4-char code can never corrupt them."""
    if not secrets:
        return value
    if isinstance(value, dict):
        return {k: v if k in {"reason", "link"} else _redact_secrets(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(v, secrets) for v in value]
    if isinstance(value, str):  # OCR may letter-space a secret ("x 7 k 9" in the code box)
        pattern = "|".join(r"\s*".join(re.escape(c) for c in s) for s in secrets)
        return re.sub(pattern, "[redacted]", value, flags=re.IGNORECASE)
    return value


def _save_facts(facts: dict) -> dict:
    keep = ("target_dir", "selection", "link", "opener", "root_items", "visible_root_rows", "names", "created_dirs", "reason",
            "destination_before", "client_reported", "preselected", "row_identity_proof")
    return {k: facts[k] for k in keep if k in facts}


def share_root_rows(screen: Screen) -> list[dict]:
    """Share tab root rows: name-column lines below the 已选择N个文件 header and above the bottom
    保存到网盘 bar (size/date cells and bar texts such as the destination label excluded)."""
    header = _share_header(screen)
    if header is None:
        return []
    line = header[0]
    bar = [s["y"] for s in screen.find(SHARE_SAVE) if s["y"] > line["y"]]
    floor = min(bar) if bar else float("inf")
    return [c for c in screen.lines
            if line["y"] + line["h"] < _cy(c) < floor and line["x"] - 10 <= c["x"] <= line["x"] + SHARE_NAME_SPAN
            and _norm(c.get("text")) and not SHARE_ROW_SKIP_RE.match(_norm(c.get("text")))
            and _norm(c.get("text")) not in {SHARE_SAVE, "下载"} and _destination_parts(c) is None]


def _picker_crumb_prefix(line: dict) -> str | None:
    """The exact text before the root on a picker crumb line: "" or 返回上一级>; None when not root-led."""
    text = _norm(line.get("text"))
    for prefix in ("", PICKER_BACK + PICKER_BACK_SEP):
        if text.startswith(prefix) and any(text[len(prefix):].startswith(r) for r in PICKER_ROOTS):
            return prefix
    return None


def _root_prefixed(line: dict) -> bool:
    return _picker_crumb_prefix(line) is not None


def _picker_root_span(screen: Screen, crumb: dict) -> dict | None:
    """Click box of the root label itself on the picker crumb line, never the 返回上一级 control.

    A bare root line is clickable whole; otherwise OCR tokens must give exactly the known
    prefix, then exactly the root, each ending on a token boundary.  No search elsewhere in
    the line (a folder may be named like the root); None when the tokens do not prove it.
    """
    prefix, text = _picker_crumb_prefix(crumb), _norm(crumb.get("text"))
    if prefix is None:
        return None
    root = next(r for r in PICKER_ROOTS if text[len(prefix):].startswith(r))
    if not prefix and text == root:
        return crumb
    tokens = sorted((t for t in screen.tokens if t.get("parent_line_id") == crumb.get("line_id")),
                    key=lambda t: t.get("order", 0))
    joined, start = "", (None if prefix else 0)
    for i, token in enumerate(tokens):
        joined += _norm(token.get("text"))
        if start is None and joined == prefix:
            start = i + 1
        elif start is not None and joined == prefix + root:
            chosen = tokens[start:i + 1]
            x1, y1 = min(t["x"] for t in chosen), min(t["y"] for t in chosen)
            x2, y2 = max(t["x"] + t["w"] for t in chosen), max(t["y"] + t["h"] for t in chosen)
            return {"text": root, "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}
        if not (prefix + root).startswith(joined):
            return None
    return None


def _inside(line: dict, box: dict) -> bool:
    return (box["x"] <= line["x"] and line["x"] + line["w"] <= box["x"] + box["w"]
            and box["y"] <= line["y"] and line["y"] + line["h"] <= box["y"] + box["h"])


def _picker_layout(screen: Screen) -> dict:
    """The one 保存到 modal layout (title, 取消, 确定, region) on ``screen``, whatever its crumb shows.

    A layout is an exact title above one 取消 + 确定 pair on a single row (取消 left of
    确定, both right of the title).  Its region spans title to buttons, padded by
    PICKER_PAD_TITLES title heights; exactly one layout may exist.  Cancelling a known
    modal relies on this alone, so an unparseable (e.g. elided) crumb never blocks 取消.
    """
    layouts = []
    for title in screen.find(PICKER_TITLE):
        for ok in screen.find(PICKER_CONFIRM):
            for no in screen.find(PICKER_CANCEL):
                buttons_top = min(ok["y"], no["y"])
                if (abs(_cy(no) - _cy(ok)) < max(ok["h"], no["h"], 8) * .6 and no["x"] + no["w"] <= ok["x"]
                        and title["x"] < no["x"] and title["y"] + title["h"] <= buttons_top):
                    layouts.append((title, no, ok))
    if len(layouts) != 1:
        raise StepFailed("save_picker_ambiguous" if layouts else "save_picker_layout_not_found",
                         layouts=[[_brief(b) for b in lay] for lay in layouts][:4])
    title, no, ok = layouts[0]
    pad = PICKER_PAD_TITLES * max(title["h"], 8)
    top, bottom = title["y"] - pad, max(ok["y"] + ok["h"], no["y"] + no["h"]) + pad
    region = {"x": title["x"] - pad, "y": top, "w": ok["x"] + ok["w"] + pad - (title["x"] - pad), "h": bottom - top}
    return {"title": title, "cancel": no, "confirm": ok, "region": region, "buttons_top": min(ok["y"], no["y"])}


def save_picker(screen: Screen, *, elided_ok: bool = False) -> dict:
    """The one 保存到 modal on ``screen`` (see :func:`_picker_layout`), else StepFailed.

    Exactly one root-prefixed crumb must lie inside the region between title and buttons.
    ``visible`` is the crumb plus the region's lines right of it on its row, minus the known
    已加载 marker, split into segments (root first); ``path`` is ``visible[1:]``.  An elided
    crumb fails closed unless ``elided_ok``, which returns ``path=None`` with the raw
    ``visible`` segments: only :func:`elided_crumb_proves` may then decide what it shows.
    ``viewport`` is the folder list between the crumb row and the button row: folder
    lookup and scrolling never leave it.
    """
    layout = _picker_layout(screen)
    title, region, buttons_top = layout["title"], layout["region"], layout["buttons_top"]
    crumbs = [l for l in screen.lines if _root_prefixed(l) and _inside(l, region)
              and l["y"] >= title["y"] + title["h"] and l["y"] + l["h"] <= buttons_top]
    if len(crumbs) != 1:
        raise StepFailed("save_picker_crumb_not_unique", matches=[_brief(l) for l in crumbs])
    crumb = crumbs[0]
    prefix = _picker_crumb_prefix(crumb)
    same_row = [l for l in screen.same_row(crumb) if _inside(l, region)]
    # Left of the root only the 返回上一级 control (split lines, in order) — or nothing when merged.
    before = "".join(_norm(l.get("text")) for l in same_row if l["x"] < crumb["x"])
    if before not in ("", PICKER_BACK, PICKER_BACK + PICKER_BACK_SEP) or (before and prefix):
        raise StepFailed("save_picker_crumb_unknown", crumb=[_brief(l) for l in same_row])
    row = [l for l in same_row if l["x"] >= crumb["x"]]
    marker = [l for l in row if PICKER_LOADED_RE.match(_norm(l.get("text")))]
    segments = [l for l in row if l not in marker]
    text = "/".join(_norm(l.get("text")) for l in segments)[len(prefix):]
    parts = [p for p in CRUMB_SPLIT_RE.split(text) if p]
    elided = bool(PICKER_ELIDED_RE.search(text)) or PICKER_DOT_ELISION in parts[1:]
    if elided and not elided_ok:
        raise StepFailed("save_picker_crumb_elided", crumb=[_brief(l) for l in segments])
    if not parts or parts[0] not in PICKER_ROOTS or PICKER_BACK in parts:
        raise StepFailed("save_picker_crumb_unknown", crumb=[_brief(l) for l in segments])
    row_bottom = max(l["y"] + l["h"] for l in row)
    viewport = {"x": region["x"], "y": row_bottom, "w": region["w"], "h": buttons_top - row_bottom}
    return {"title": title, "cancel": layout["cancel"], "confirm": layout["confirm"], "crumb": crumb, "marker": marker,
            "visible": parts, "path": None if elided else parts[1:], "region": region, "viewport": viewport}


def elided_crumb_proves(visible: list[str] | None, parent: list[str], child: str) -> bool:
    """True only when an elided picker crumb shows exactly ``parent + [child]``.

    PURE check; the caller owns the transition proof: ``parent`` is the last FULL path
    proven on this modal, ``child`` the one exact, unique row single-clicked from it, and
    ``visible`` comes from a fresh observation after that click (:func:`save_picker` with
    ``elided_ok``).  Required: a known root first; exactly one ellipsis, as a whole segment;
    every visible segment before it equal to the expected head and at least
    PICKER_ELIDED_MIN_SUFFIX visible segments after it equal to the expected tail (the
    child last); the ellipsis hides at least one segment.  Head and tail come from the
    known path, so the ellipsis can never stand for a contradictory prefix.
    """
    if not visible or visible[0] not in PICKER_ROOTS:
        return False
    marks = [i for i, seg in enumerate(visible) if PICKER_ELIDED_RE.search(seg) or seg == PICKER_DOT_ELISION]
    if len(marks) != 1 or not PICKER_ELLIPSIS_RE.match(visible[marks[0]]):
        return False
    head, tail = visible[1:marks[0]], visible[marks[0] + 1:]
    want = [*parent, child]
    if len(tail) < PICKER_ELIDED_MIN_SUFFIX or len(head) + len(tail) >= len(want):
        return False
    return same_path(head, want[:len(head)]) and same_path(tail, want[len(want) - len(tail):])


def save_picker_path(screen: Screen) -> list[str] | None:
    """Segments below the root of the one recognised picker modal (``[]`` = root), else None."""
    try:
        return save_picker(screen)["path"]
    except StepFailed:
        return None


def _picker_visible(screen: Screen) -> list[str] | None:
    """Raw crumb segments (root first, ellipsis kept) of the one recognised modal, else None."""
    try:
        return save_picker(screen, elided_ok=True)["visible"]
    except StepFailed:
        return None


def _picker_shows(screen: Screen, parent: list[str], child: str, stale: list[str] | None) -> bool:
    """The modal shows ``parent + [child]``: in full, or as an elided crumb that is fresh (not the
    ``stale`` pre-click crumb) and proven by :func:`elided_crumb_proves` from the FULL ``parent``."""
    try:
        picker = save_picker(screen, elided_ok=True)
    except StepFailed:
        return False
    if picker["path"] is not None:
        return same_path(picker["path"], [*parent, child])
    return picker["visible"] != stale and elided_crumb_proves(picker["visible"], parent, child)


def picker_rows(screen: Screen, picker: dict) -> list[dict]:
    """Lines lying wholly inside the picker's folder-list viewport (partly clipped rows excluded)."""
    return [l for l in screen.lines if _norm(l.get("text")) and _inside(l, picker["viewport"])]


def _destination_parts(line: dict) -> list[str] | None:
    """``[保存到[:：]]我的网盘/a/b`` -> ['a', 'b']; None unless rooted at one of PICKER_ROOTS."""
    text = DEST_PREFIX_RE.sub("", _literal(line.get("text")))
    parts = [p.strip() for p in CRUMB_SPLIT_RE.split(text)]
    parts = [p for p in parts if p]
    if not parts or _norm(parts[0]) not in PICKER_ROOTS:
        return None
    return parts[1:]


def _destination_line(screen: Screen) -> dict | None:
    """The share bottom-bar destination label: one root-prefixed line on the 保存到网盘 row, left of it."""
    save = screen.find(SHARE_SAVE)
    if len(save) != 1:
        return None
    found = [l for l in screen.same_row(save[0]) if l is not save[0] and l["x"] + l["w"] <= save[0]["x"]
             and _destination_parts(l) is not None]
    return found[0] if len(found) == 1 else None


def destination_path(screen: Screen) -> list[str] | None:
    """Segments of the share destination label (``[]`` = netdisk root); None when not uniquely shown."""
    line = _destination_line(screen)
    return None if line is None else _destination_parts(line)


def destination_icon(screen: Screen) -> dict:
    """Click box of the folder icon, anchored on the 保存到网盘 control (PROVISIONAL offset).

    Observed: the icon sits next to the Save control whatever the label length, so the
    label's right edge is no anchor.  Refused unless the point lies right of the label,
    clear of the Save text by DEST_ICON_SAVE_CLEARANCE and inside no OCR line.  The click
    is only trusted once a picker is recognised; nothing is ever saved on this basis.
    """
    line = _destination_line(screen)
    if line is None:
        raise StepFailed("destination_label_not_found", texts=screen.texts()[:60])
    save = screen.unique(SHARE_SAVE, "share_save_button_not_found")
    x, y = save["x"] + save["w"] / 2 - DEST_ICON_FROM_SAVE_CENTER, _cy(save)
    if x > save["x"] - DEST_ICON_SAVE_CLEARANCE or x <= line["x"] + line["w"]:
        raise StepFailed("destination_icon_out_of_bar", x=x, label=_brief(line), save=_brief(save))
    hit = [l for l in screen.lines if l["x"] <= x <= l["x"] + l["w"] and l["y"] <= y <= l["y"] + l["h"]]
    if hit and not _icon_glyph_only(screen, hit, x, line, save):
        raise StepFailed("destination_icon_on_text", hit=[_brief(h) for h in hit])
    return {"x": x - 1, "y": y - 1, "w": 2, "h": 2}


def _icon_glyph_only(screen: Screen, hit: list[dict], x: float, label: dict, save: dict) -> bool:
    """True only when the single hit is the measured OCR reading of the folder icon itself."""
    profile = DEST_ICON_GLYPHS.get(tuple(screen.size))
    if profile is None or len(hit) != 1:
        return False
    glyph = hit[0]
    lo, hi = profile["size"]
    if _literal(glyph.get("text")) != profile["text"] or not (lo <= glyph["w"] <= hi and lo <= glyph["h"] <= hi)             or abs(glyph["w"] - glyph["h"]) > profile["square"] or abs(_cy(glyph) - _cy(save)) > profile["row"]             or abs(glyph["x"] + glyph["w"] / 2 - x) > profile["centre"]             or glyph["x"] <= label["x"] + label["w"] or glyph["x"] + glyph["w"] > save["x"] - DEST_ICON_SAVE_CLEARANCE:
        return False
    between = [l for l in screen.same_row(save) if label["x"] + label["w"] < l["x"] and l["x"] + l["w"] <= save["x"]
               and _literal(l.get("text")) == profile["text"]]
    return between == [glyph]


def _icon_or_reason(screen: Screen) -> dict | str:
    try:
        return destination_icon(screen)
    except StepFailed as exc:
        return exc.code


def _upper(value: str | None) -> str | None:
    return value.upper() if value else None


def _content_identity(expected_size: int, expected_sha256: str | None) -> dict:
    """New content expectations for the same path are a different transfer."""
    return {"expected_size": int(expected_size), "expected_sha256": _upper(expected_sha256)}


def _same_expectation(facts: dict, content: dict) -> bool | None:
    """Do a verified record's facts prove ``content``?  True/False when provable, None otherwise.

    ``facts.sha256`` is the digest of the file actually collected and
    ``facts.expected_size`` the size that was asked for.
    """
    size, digest = facts.get("expected_size"), facts.get("sha256")
    if size is not None and int(size) != content["expected_size"]:
        return False
    if content["expected_sha256"]:
        return None if not digest else str(digest).upper() == content["expected_sha256"]
    return True if size is not None else None


def _selection_size_matches(text: object, expected_size: int) -> bool:
    """``下载(1.1K)`` covers ``expected_size`` within one display step; unparseable is a mismatch."""
    match = DOWNLOAD_SIZE_RE.match(_norm(text))
    if not match:
        return False
    shown, decimals, unit = float(match.group(1)), len(match.group(2) or ""), match.group(3).upper()
    scale = 1024 ** "_KMGT".index(unit) if unit else 1
    return abs(expected_size / scale - shown) <= 10 ** -decimals


def artifact_status(record: dict, *, expected_sha256: str | None = None) -> TransferResult:
    """Ledger record -> public status; a verified download re-checks its local artifact.

    ``content_verified`` is True only when the unchanged local artifact matches
    an expected SHA-256: the one it was collected against, or ``expected_sha256``.  Uploads only reach
    ``already_remote_present``: the remote name was seen, content was not compared.
    """
    key, state, facts = record["key"], record["state"], record.get("facts") or {}
    verification = facts.get("verification")
    public = {"state": state, "operation": record.get("operation"), "verification": verification, "content_verified": False}
    if state != "verified":
        return TransferResult("already_submitted", "resume_or_check_remote_do_not_resubmit", key, public)
    target = facts.get("target_path")
    if record.get("operation") == "download_dir" and target:
        files = facts.get("files") or []
        changed = [f["rel"] for f in files if not (Path(target) / f["rel"]).is_file()
                   or (Path(target) / f["rel"]).stat().st_size != f["size"]]
        if changed:
            return TransferResult("artifact_changed", "manual_review_do_not_resubmit", key,
                                  {**public, "target_path": target, "changed": changed})
        return TransferResult("already_downloaded", "done", key, {**public, "target_path": target, "files": len(files)})
    if record.get("operation") in {"upload", "upload_dir"} or not target:
        return TransferResult("already_remote_present", "download_roundtrip_to_verify_content", key, public)
    path = Path(target)
    if not path.is_file():
        return TransferResult("artifact_missing", "manual_review_do_not_resubmit", key, {**public, "target_path": target})
    digest = sha256_file(path)
    if digest != facts.get("sha256"):
        return TransferResult("artifact_changed", "manual_review_do_not_resubmit", key,
                              {**public, "target_path": target, "sha256": digest})
    hashed = verification == "local_hash" or (bool(expected_sha256) and digest == expected_sha256.upper())
    return TransferResult("already_verified" if hashed else "already_downloaded", "done", key,
                          {**public, "content_verified": hashed, "target_path": target, "sha256": digest})


def parse_share_text(joined: str) -> tuple[str, str | None] | None:
    """``(https://pan.baidu.com/s/<surl>, pwd)`` from share text; None unless exactly one surl and one code.

    Whitespace separates fragments (two links never merge); callers join wrapped OCR lines without it.
    """
    links = {(m.group(1), m.group(2)) for m in SHARE_URL_TEXT_RE.finditer(joined)}
    surls = {surl for surl, _ in links}
    if len(surls) != 1:
        return None
    surl = surls.pop()
    codes = {code for _, code in links if code} | {m.group(1) for m in SHARE_PWD_TEXT_RE.finditer(joined)}
    if len(codes) > 1:
        return None
    return f"https://pan.baidu.com/s/{surl}", (codes.pop() if codes else None)


def parse_share_link(screen: Screen) -> tuple[str, str | None] | None:
    """The link from a share window after 创建链接 (OCR); None unless one surl.

    8.1.5.103 shows it in a tooltip bubble above ✓复制链接, wrapped over several OCR lines
    ("接: https://pan.bai" / "du.com/s/1ySpxd" / ...): the left-aligned column of lines around the
    one holding ``http`` is joined without separators.  The code comes from ``?pwd=`` or the 提取码
    text and both must agree.
    """
    whole = parse_share_text(" ".join(_norm(line.get("text")) for line in screen.lines))
    if whole:
        return whole
    starts = [line for line in screen.lines if "http" in _norm(line.get("text"))]
    if len(starts) != 1:
        return None
    start = starts[0]
    column = sorted((line for line in screen.lines if abs(line["x"] - start["x"]) <= 15
                     and start["y"] - 80 <= line["y"] <= start["y"] + 200), key=lambda line: line["y"])
    return parse_share_text("".join(_norm(line.get("text")) for line in column))


def parse_share_url(share_url: str, pwd: str | None = None) -> tuple[str, str]:
    """Public share link -> (canonical ``https://pan.baidu.com/s/<surl>``, code).

    Accepted: ``https://pan.baidu.com/s/<surl>[?pwd=xxxx]`` and the official landing
    page ``https://pan.baidu.com/share/init?surl=<surl>[&pwd=xxxx]`` (same share as
    ``/s/1<surl>``).  Any other scheme, host, port, path, fragment or credential is
    rejected.  The canonical URL never carries the code, so ledger link hashes of
    both forms agree.
    """
    from urllib.parse import parse_qs, urlsplit
    raw = str(share_url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.netloc != "pan.baidu.com" or parts.fragment:
        raise ValueError("分享链接必须是 https://pan.baidu.com/s/<surl> 或 /share/init?surl=<surl>")
    query = parse_qs(parts.query, keep_blank_values=True)
    if any(len(v) != 1 for v in query.values()):
        raise ValueError("分享链接查询参数重复")
    if parts.path == "/share/init":
        surl = (query.get("surl") or [""])[0]
        if not SHARE_SURL_RE.match(surl) or set(query) - {"surl", "pwd"}:
            raise ValueError("share/init 链接需要合法 surl 参数")
        base = f"https://pan.baidu.com/s/1{surl}"
    else:
        base = f"https://pan.baidu.com{parts.path}"
        if set(query) - {"pwd"}:
            raise ValueError("分享链接只允许 pwd 查询参数")
    if not SHARE_URL_RE.match(base):
        raise ValueError("分享链接必须是 https://pan.baidu.com/s/<surl> 或 /share/init?surl=<surl>")
    code = pwd or (query.get("pwd") or [None])[0]
    if not code or not SHARE_PWD_RE.match(code):
        raise ValueError("需要 4 位提取码（参数 pwd 或链接 ?pwd=）")
    return base, code


def share_extraction_state(screen: Screen) -> str | None:
    """'rejected' (extraction hint such as 提取码格式错误 visible), 'pending' (提取文件 page), else None."""
    if screen.find(SHARE_EXTRACT_ERROR_RE):
        return "rejected"
    return "pending" if screen.find(SHARE_EXTRACT_BUTTON) else None


def _extraction_hint(screen: Screen) -> str:
    """The fixed hint wording only (regex match), never the surrounding OCR line."""
    for line in screen.find(SHARE_EXTRACT_ERROR_RE):
        found = SHARE_EXTRACT_ERROR_RE.search(_norm(line.get("text")))
        if found:
            return found.group(0)
    return "unknown"


def address_bar_field(screen: Screen) -> dict:
    """The one top-band OCR line that is the address field: its placeholder or a pan.baidu.com link.

    Missing -> ``share_address_field_not_found``; several -> ``share_address_field_ambiguous``.
    Facts carry counts only (no texts: the field may hold another share's link and code).
    """
    band = screen.size[1] * ADDRESS_BAR_BAND
    found = [line for line in screen.lines if _cy(line) < band and _is_address_field_text(line.get("text"))]
    if len(found) != 1:
        raise StepFailed("share_address_field_ambiguous" if found else "share_address_field_not_found",
                         candidates=len(found), code_submitted_by_flow=False)
    return found[0]


def _is_address_field_text(text: object) -> bool:
    raw = str(text or "").strip()
    if ADDRESS_BAR_PLACEHOLDER_RE.match(_norm(raw)) or ADDRESS_BAR_LINK_RE.match(_norm(raw)):
        return True
    return bool(ADDRESS_BAR_LINK_RE.match(_norm(ADDRESS_BAR_ICON_PREFIX_RE.sub("", raw, count=1))))


def share_scheme_url(url: str, code: str) -> str:
    """Payload shape read by main.js "evoked-sharelink": base64(JSON{share_info:{url,pwd,is_public}}).

    UNRESOLVED: the same handler forwards ``info=JSON.stringify({shareid: share_info.share_id,
    uk: share_info.share_uk, ...})`` to the 资源分享 tab; a bare link cannot supply those ids offline, so
    ``info`` arrives as ``{}``.  Natively the tab then rejected even a hand-typed correct code with
    提取码格式错误; whether the missing ids cause it is unproven (the linkPreviewer chunk was not read).
    """
    payload = {"share_info": {"url": url, "pwd": code, "is_public": 0}}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"baiduyunguanjia://evoked-sharelink/?param={encoded}"


def _share_header(screen: Screen) -> tuple[dict, int] | None:
    found = []
    for line in screen.lines:
        m = SHARE_HEADER_RE.match(_norm(line.get("text")))
        if m and any(_norm(o.get("text")) == "大小" for o in screen.same_row(line)):
            found.append((line, int(m.group(1))))
    return found[0] if len(found) == 1 else None


def _share_rows(screen: Screen, name: str) -> list[dict]:
    header = _share_header(screen)
    if header is None:
        return []
    line = header[0]
    return screen.find(name, below=line["y"] + line["h"], right_of=line["x"] - 10, literal=True)


def _share_download_button(screen: Screen, header: dict) -> dict:
    save = screen.unique(SHARE_SAVE, "share_save_button_not_found", below=header["y"])
    found = [line for line in screen.same_row(save) if _norm(line.get("text")) == "下载"]
    if len(found) != 1:
        raise StepFailed("share_download_button_not_found", matches=[_brief(f) for f in found])
    return found[0]


# ---- read-only diagnosis -----------------------------------------------------------

def inspect_client(driver: ProjectionDriver, lease_check: Callable[[], object], *, redact: bool = False,
                   with_tokens: bool = False, image_dir: Path | None = None) -> dict:
    """Enumerate client windows (main + modal), capture and OCR each; no input.

    ``redact`` hides line texts that are not known client UI labels.
    """
    known = {ROOT_CRUMB, DIALOG_CONFIRM, SIDE_NAV_HOME, "上传", "新建文件夹", "传输", *LIST_HEADER_NAMES, *LIST_HEADER_PEERS}

    def shown(text: str) -> str:
        norm = _norm(text)
        if not redact or norm in known or FILE_NAME_LABEL_RE.match(norm) or DOWNLOAD_BUTTON_RE.match(norm):
            return text
        return f"<{len(text)} chars>"

    windows = [w for w in driver.list_windows() if CLIENT_APP_RE.match(str(w.get("application") or ""))]
    report = {"windows": []}
    for window in windows:
        meta = {k: window.get(k) for k in ("id", "title", "application", "width", "height")}
        try:
            lease_check()
            observed_at = time.monotonic()
            frame = driver.frame(window["id"])
            keep = image_dir / f"{len(report['windows'])}.png" if image_dir else None
            doc = driver.ocr(frame, keep_image=keep) if keep else driver.ocr(frame)
            screen = Screen(frame, list(doc.get("lines") or []), list(doc.get("tokens") or []), observed_at)
            header = list_header(screen)
            path = screen_path(screen)
            read = getattr(driver, "native_documents", None)
            if read is not None:  # read-only UIA; only the judged path is reported, never the raw value
                lease_check()
                try:
                    native, why = native_own_path(read(frame["window_id"]), screen)
                except Exception:
                    native, why = None, "native_unavailable"
                meta.update(native_reason=why, native_path=None if native is None else
                            "/" + "/".join(shown(seg) for seg in native))
            meta.update(frame={k: frame.get(k) for k in ("width", "height", "capture_mode", "title")},
                        list_header=_brief(header) if header else None,
                        path=None if path is None else "/" + "/".join(path),
                        image=os.fspath(keep) if keep else None,
                        lines=[{**_brief(line), "text": shown(str(line.get("text") or ""))} for line in screen.lines])
            if with_tokens:
                meta["tokens"] = [{**_brief(t), "text": shown(str(t.get("text") or "")), "parent_line_id": t.get("parent_line_id")}
                                  for t in screen.tokens]
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}: {exc}"
        report["windows"].append(meta)
    return report
