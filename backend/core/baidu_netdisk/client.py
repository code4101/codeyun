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
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Callable

from .ledger import TransferLedger, transfer_key
from .local_files import (collect_download, describe_upload_source, load_manifest, sha256_file, snapshot_dir,
                          validate_file_name, validate_remote_dir, write_manifest)

CLIENT_APP_RE = re.compile(r"^baidunetdisk(unite)?\.exe$", re.I)
FILE_DIALOG_TITLE = "请选择文件/文件夹"
DIALOG_CONFIRM = "存入百度网盘"
FILE_NAME_LABEL_RE = re.compile(r"^文件名\s*[(（]\s*N\s*[)）]\s*[:：]?")
ROOT_CRUMB = "全部文件"
CRUMB_SPLIT_RE = re.compile(r"[/>›》]")
# The file list header row: a name column ("文件" on 8.1.5.103) sharing its row
# with a size/time column, so "最近文件" cards or file names never qualify.
LIST_HEADER_NAMES = ("文件", "文件名")
LIST_HEADER_PEERS = ("大小", "修改时间")
# While items are selected the header row keeps "文件" and adds, to its right,
# "已选中1个文件/文件夹".  Full text only: a menu-clipped "已选中1" proves nothing.
SELECTION_RE = re.compile(r"^已选中(\d+)个文件/文件夹$")
# 8.1.5.103 file context menu (opens left of the cursor near the right edge).
CONTEXT_MENU_DOWNLOAD = "下载"
CONTEXT_MENU_PEERS = ("打开", "打开方式", "分享", "复制", "剪切", "重命名", "移动到", "详细信息", "删除")
MENU_LABELS = {CONTEXT_MENU_DOWNLOAD, *CONTEXT_MENU_PEERS, "文档历史版本", "本地编辑", "用手机打开"}
BLANK_BAND_WIDTH = 200  # px of verified-blank breadcrumb row clicked to dismiss a context menu
LIST_MTIME = "修改时间"
# Unselected header row shows "已全部加载，共2个" where the selection count would be.
LOADED_RE = re.compile(r"^已全部加载[，,]共(\d+)个$")
MAX_CLEAR_CLICKS = 2  # partial -> all -> none
# Header checkbox (8.1.5.103, measured): a ~16 px blue square ~20 px left of the
# "文件" header, centred on its row; empty state is a grey outline (no blue).
CHECKBOX_PROFILES = {(1213, 864)}
CHECKBOX_SIDE = (13, 19)
CHECKBOX_ROI_LEFT = 40  # px left of the header text searched for the box
SIDE_NAV_HOME = "首页"
ROOT_HOME_MARK = "最近文件"  # the root home shows recent files above the list and no crumb
DOWNLOAD_BUTTON_RE = re.compile(r"^下载\s*([(（][^)）]*[)）])?$")
# "下载(1.1K)": total size of the current selection, one display step of precision.
DOWNLOAD_SIZE_RE = re.compile(r"^下载[(（](\d+(?:\.(\d+))?)([KMGT]?)B?[)）]$", re.I)
# EXPERIMENTAL (not verified end to end).  Share links: the official client's own web-evoke scheme (main.js parseWebEvokedUrl,
# handler "evoked-sharelink") opens the "资源分享" tab (linkPreviewer) and, when
# pwd is given, extracts the file list automatically.  The payload carries no
# uk/checkUserInfo, so the handler's account-mismatch logout branch is unreachable.
SHARE_URL_RE = re.compile(r"^https://pan\.baidu\.com/s/([A-Za-z0-9_-]{5,64})$")
SHARE_PWD_RE = re.compile(r"^[A-Za-z0-9]{4}$")
# linkPreviewer table: selection column (55 px) left of the name column whose
# header reads "已选择N个文件"; row-click opens the file, so only the checkbox is clicked.
SHARE_HEADER_RE = re.compile(r"^已选择(\d+)个文件$")
SHARE_CHECKBOX_OFFSET = 37  # px left of the name header text: cell padding 10 + half of 55
SHARE_SAVE = "保存到网盘"


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

    def open_scheme(self, url: str) -> None:
        """Hand an official ``baiduyunguanjia://`` URL to its registered handler (no GUI input)."""
        os.startfile(url)  # type: ignore[attr-defined]  # Windows ShellExecute

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


def _cy(box: dict) -> float:
    return box["y"] + box["h"] / 2


def _brief(line: dict) -> dict:
    return {k: line.get(k) for k in ("text", "x", "y", "w", "h")}


@dataclass
class Screen:
    frame: dict
    lines: list[dict]
    tokens: list[dict] = field(default_factory=list)

    @property
    def size(self) -> tuple[int, int]:
        return self.frame["width"], self.frame["height"]

    def find(self, match: str | re.Pattern, *, below: float | None = None, above: float | None = None,
             left_of: float | None = None, right_of: float | None = None, literal: bool = False) -> list[dict]:
        """Labels compare whitespace-free; ``literal=True`` (names) keeps internal spaces."""
        fold = _literal if literal else _norm

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
    """The file-list header ("文件"/"文件名" with 大小/修改时间 on the same row)."""
    found = []
    for line in screen.lines:
        if _norm(line.get("text")) in LIST_HEADER_NAMES:
            peers = {_norm(other.get("text")) for other in screen.same_row(line) if other is not line}
            if peers & set(LIST_HEADER_PEERS):
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
    found = [int(m.group(1)) for line in screen.same_row(header)
             if line is not header and (m := LOADED_RE.match(_norm(line.get("text"))))]
    return found[0] if len(found) == 1 else None


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
    image = frame_image(screen.frame)
    if image.size != screen.size:
        raise StepFailed("checkbox_frame_size_mismatch", image=image.size, frame=screen.size)
    x0, x1 = max(0, int(header["x"]) - CHECKBOX_ROI_LEFT), int(header["x"]) - 2
    y0, y1 = max(0, int(_cy(header)) - 14), int(_cy(header)) + 15
    crop = image.crop((x0, y0, x1, y1))
    pixels = [[crop.getpixel((x, y)) for x in range(crop.width)] for y in range(crop.height)]
    lo, hi = CHECKBOX_SIDE
    boxes = [c for c in _blue_components(pixels, x0, y0)
             if lo <= c["w"] <= hi and lo <= c["h"] <= hi and abs(c["w"] - c["h"]) <= 2
             and c["pixels"] >= .55 * c["w"] * c["h"] and abs(c["y"] + c["h"] / 2 - _cy(header)) <= 3]
    blobs = [c for c in _blue_components(pixels, x0, y0) if c["pixels"] >= 12]
    if len(boxes) != 1 or len(blobs) != 1:
        raise StepFailed("header_checkbox_not_found" if not boxes else "header_checkbox_ambiguous",
                         boxes=boxes[:4], blobs=len(blobs))
    return {k: boxes[0][k] for k in ("x", "y", "w", "h")}


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
    together with the root home's 最近文件 strip above the list header (as
    observed on 8.1.5.103).  A crumb that is present but does not parse is
    never taken for root.
    """
    header = list_header(screen)
    if header is None:
        return None
    if any(_cy(line) < header["y"] and _norm(line.get("text")).startswith(ROOT_CRUMB) for line in screen.lines):
        crumb = breadcrumb(screen, header)
        return crumb[1] if crumb else None
    return [] if screen.find(ROOT_HOME_MARK, above=header["y"]) else None


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
    """One instance per task; ``lease_check`` raises when the GUI lease is lost."""

    def __init__(self, *, ledger: TransferLedger, lease_check: Callable[[], object],
                 driver: ProjectionDriver | None = None, settle: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep, max_scrolls: int = 8, poll_attempts: int = 10):
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
        frame = self.driver.frame(window_id)
        doc = self.driver.ocr(frame)
        return Screen(frame, list(doc.get("lines") or []), list(doc.get("tokens") or []))

    def act(self, screen: Screen, window_id: str, **action: object) -> Screen:
        """One input on the frame just observed, then a fresh observation."""
        self.lease_check()
        self.driver.input(screen.frame, **action)
        self.sleep(self.settle)
        return self.observe(window_id)

    def click_box(self, screen: Screen, window_id: str, box: dict, action: str = "click", button: str = "left") -> Screen:
        x, y = _center(screen, box)
        return self.act(screen, window_id, action=action, x=x, y=y, button=button)

    # -- remote navigation -------------------------------------------------------

    def _header(self, screen: Screen) -> dict:
        header = list_header(screen)
        if header is None:
            raise StepFailed("file_list_header_not_found", texts=screen.texts()[:60])
        return header

    def current_path(self, screen: Screen) -> list[str] | None:
        """See :func:`screen_path`: None unless the folder (or root) is proven."""
        return screen_path(screen)

    def _find_in_list(self, window_id: str, screen: Screen, name: str) -> tuple[Screen, dict | None]:
        """Exact name below the list header (not the side nav); bounded scrolling."""
        for attempt in range(self.max_scrolls + 1):
            header = self._header(screen)
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
        """From e.g. the transfer page, click the side-nav 首页 (left strip, below the tab bar)."""
        if list_header(screen) is not None:
            return screen
        width, height = screen.size
        home = screen.unique(SIDE_NAV_HOME, "side_nav_home_not_found", left_of=width * .06, below=height * .12)
        screen = self.click_box(screen, main, home)
        self._header(screen)
        return screen

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
        if self.current_path(screen) != []:
            raise StepFailed("root_not_verified", texts=screen.texts()[:60])
        return screen

    def open_remote_dir(self, remote_dir: str, *, create_missing: bool = False) -> tuple[str, Screen]:
        """File view → root → one double-click per segment, each verified by the crumb chain."""
        if create_missing:
            # Inline-name focus after 新建文件夹 is not verified yet; never fake it.
            raise StepFailed("create_missing_unsupported")
        parts = [p for p in validate_remote_dir(remote_dir).split("/") if p]
        main = self.main_window()
        self.lease_check()
        self.driver.activate(main)
        screen = self.observe(main)
        if self.current_path(screen) == parts:
            return main, screen
        screen = self._to_root(main, self._to_file_view(main, screen))
        for depth, part in enumerate(parts):
            screen, row = self._find_in_list(main, screen, part)
            if row is None:
                raise StepFailed("remote_dir_not_found", segment=part, path="/" + "/".join(parts[:depth + 1]))
            screen = self.click_box(screen, main, row, action="double_click")
            if self.current_path(screen) != parts[:depth + 1]:
                raise StepFailed("folder_not_verified", segment=part, texts=screen.texts()[:60])
        return main, screen

    def _poll(self, window_id: str, predicate: Callable[[Screen], bool]) -> Screen | None:
        """Observation only (no input) until predicate holds or attempts run out."""
        for _ in range(self.poll_attempts):
            self.sleep(self.settle)
            screen = self.observe(window_id)
            if predicate(screen):
                return screen
        return None

    def _not_submitted(self, key: str, exc: Exception) -> TransferResult:
        code = getattr(exc, "code", type(exc).__name__)
        self.ledger.update(key, "failed", reason=code, error=str(exc), before_submit=True)
        return TransferResult("not_submitted", "inspect_then_retry", key,
                              {"reason": code, "error": str(exc), **getattr(exc, "facts", {})})

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

    def _select_for_download(self, main: str, screen: Screen, row: dict, expected_size: int) -> tuple[dict, Screen]:
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
          下载(size) above the list header matches ``expected_size``;
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
            screen = self._poll(main, lambda sc: bool(_share_rows(sc, item_name)))
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
            x = (header["x"] - SHARE_CHECKBOX_OFFSET) / width
            screen = self.act(screen, main, action="click", x=x, y=_cy(rows[0]) / height, button="left")
            after = _share_header(screen)
            if after is None or after[1] != 1 or len(_share_rows(screen, item_name)) != 1:
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
    if record.get("operation") == "upload" or not target:
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


def parse_share_url(share_url: str, pwd: str | None = None) -> tuple[str, str]:
    """``https://pan.baidu.com/s/<surl>[?pwd=xxxx]`` -> (url without query, code)."""
    from urllib.parse import parse_qs
    base, _, query = str(share_url or "").strip().partition("?")
    code = pwd or (parse_qs(query).get("pwd") or [None])[0]
    if not SHARE_URL_RE.match(base):
        raise ValueError("分享链接必须是 https://pan.baidu.com/s/<surl>")
    if not code or not SHARE_PWD_RE.match(code):
        raise ValueError("需要 4 位提取码（参数 pwd 或链接 ?pwd=）")
    return base, code


def share_scheme_url(url: str, code: str) -> str:
    """Payload shape read by main.js "evoked-sharelink": base64(JSON{share_info:{url,pwd,is_public}})."""
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
            frame = driver.frame(window["id"])
            keep = image_dir / f"{len(report['windows'])}.png" if image_dir else None
            doc = driver.ocr(frame, keep_image=keep) if keep else driver.ocr(frame)
            screen = Screen(frame, list(doc.get("lines") or []), list(doc.get("tokens") or []))
            header = list_header(screen)
            path = screen_path(screen)
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
