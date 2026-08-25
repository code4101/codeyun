from __future__ import annotations

import argparse
import json
import logging
import msvcrt
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from DrissionPage import Chromium
from DrissionPage.common import Keys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.xiaoe_video_archive import download_hls_video

LIST_URL = "https://admin.xiaoe-tech.com/t/course/video/list"
DETAIL_URL_PART = "/t/course/video/detail/"
ITEMS_PER_PAGE = 10
SHOP_NAME = "5034山中薪"


class XiaoeDaemonError(RuntimeError):
    """小鹅通常驻下载流程错误。"""


class XiaoeLoginRequired(XiaoeDaemonError):
    """DrissionPage 默认浏览器中的小鹅通需要人工登录。"""


class XiaoeUnsupportedPreview(XiaoeDaemonError):
    """当前上传文件暂不支持网页预览。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _wait_until(predicate: Any, *, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _next_cursor(page: int, item_index: int) -> tuple[int, int]:
    if item_index + 1 < ITEMS_PER_PAGE:
        return page, item_index + 1
    return page + 1, 0


def _cursor_reached_catalog_end(page: int, item_index: int, total_items: int | None) -> bool:
    if total_items is None:
        return False
    return (page - 1) * ITEMS_PER_PAGE + item_index >= total_items


def _catalog_total(tab: Any) -> int | None:
    return _catalog_total_from_text(_body_text(tab))


def _catalog_total_from_text(text: str) -> int | None:
    match = re.search(r"共\s*(\d+)\s*条", text)
    return int(match.group(1)) if match else None


def _body_text(tab: Any) -> str:
    return str(tab.run_js("return document.body ? document.body.innerText : '';") or "")


def _active_page(tab: Any) -> int | None:
    value = tab.run_js(
        "return document.querySelector('li.ss-pagination-item__active')"
        "?.textContent?.trim() || '';"
    )
    return int(value) if str(value).isdigit() else None


def _ensure_list_ready(tab: Any) -> None:
    if LIST_URL not in str(tab.url):
        tab.get(LIST_URL)
    if "muti_index#/chooseShop" in str(tab.url):
        _select_shop(tab)
        tab.get(LIST_URL)
    if _wait_until(
        lambda: _catalog_total(tab) is not None and "管理" in _body_text(tab),
        timeout=30,
    ):
        return
    text = _body_text(tab)
    if "登录" in text or "验证码" in text or "扫码" in text:
        raise XiaoeLoginRequired("DrissionPage 默认浏览器中的小鹅通需要登录")
    raise XiaoeDaemonError(f"视频列表没有加载完成，当前页面：{tab.url}")


def _load_xlproject_env() -> None:
    """复用 xlproject.loadenv 加载考勤环境变量。"""
    xlproject_src = ROOT.parent / "xlproject" / "src"
    if str(xlproject_src) not in sys.path:
        sys.path.insert(0, str(xlproject_src))
    import xlproject.loadenv  # noqa: F401


def _login_with_env(tab: Any) -> None:
    """使用考勤环境变量登录 DP 默认浏览器，不持久化账号密码。"""
    username = os.getenv("XIAOETONG_USERNAME")
    password = os.getenv("XIAOETONG_PASSWORD")
    if not username or not password:
        raise XiaoeLoginRequired("缺少 XIAOETONG_USERNAME 或 XIAOETONG_PASSWORD")

    tab.get("https://admin.xiaoe-tech.com/t/login#/acount")
    phone_input = tab.ele("t:input@@placeholder=请输入手机号", timeout=10)
    password_input = tab.ele("t:input@@placeholder=请输入密码", timeout=3)
    if not phone_input or not password_input:
        raise XiaoeLoginRequired("登录页没有加载出账号密码输入框")
    phone_input.input(username, clear=True)
    password_input.input(password, clear=True)
    agreement = tab.ele("t:label@@for=agree", timeout=2)
    if agreement:
        agreement.click()
    login_button = tab.ele("t:span@@text()=登录", timeout=3)
    if not login_button:
        raise XiaoeLoginRequired("登录页没有找到登录按钮")
    login_button.click()

    def login_finished() -> bool:
        url = str(tab.url)
        return "/t/login" not in url

    if not _wait_until(login_finished, timeout=8):
        if tab.ele("#tcaptcha_iframe_dy", timeout=1):
            _solve_slider_captcha(tab)
        if not _wait_until(login_finished, timeout=15):
            if tab.ele("#tcaptcha_iframe_dy", timeout=1):
                raise XiaoeLoginRequired("账号密码已填写，滑块验证码需要人工确认")
            raise XiaoeLoginRequired("账号密码登录未完成")

    if not login_finished():
        raise XiaoeLoginRequired("账号密码登录未完成")

    if "muti_index#/chooseShop" in str(tab.url):
        _select_shop(tab)
    tab.get(LIST_URL)


def _select_shop(tab: Any) -> None:
    """在小鹅通选店页进入考勤所用店铺。"""
    if not _wait_until(
        lambda: bool(tab.eles("css:.shop-list > .shop-list-item")),
        timeout=20,
    ):
        raise XiaoeLoginRequired("选店页没有加载出店铺列表")
    clicked = tab.run_js(
        """
        const target = arguments[0];
        const rows = Array.from(document.querySelectorAll('.shop-list > .shop-list-item'))
          .filter((row) => row.getBoundingClientRect().width > 0
            && row.getBoundingClientRect().height > 0);
        const row = rows.find((item) => (item.innerText || '').includes(target));
        if (!row) return false;
        row.click();
        return true;
        """,
        SHOP_NAME,
    )
    if not clicked:
        raise XiaoeLoginRequired(f"选店页没有找到店铺：{SHOP_NAME}")
    if not _wait_until(
        lambda: "muti_index#/chooseShop" not in str(tab.url),
        timeout=20,
    ):
        raise XiaoeLoginRequired(f"点击店铺后没有进入后台：{SHOP_NAME}")


def _solve_slider_captcha(tab: Any) -> None:
    """复用考勤模块的滑块缺口定位算法完成一次验证码。"""
    from pyxllib.cv.slidercaptcha import SliderCaptchaLocator

    iframe = tab.get_frame("#tcaptcha_iframe_dy")
    if not iframe:
        raise XiaoeLoginRequired("没有找到滑块验证码窗口")
    evidence_dir = Path(tempfile.gettempdir()) / "codeyun" / "xiaoe-video-daemon"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    image_path = evidence_dir / "slider-background.png"
    try:
        background = iframe.ele("#slideBg", timeout=5)
        slider = iframe.ele(".tc-fg-item tc-slider-normal", timeout=5)
        if not background or not slider:
            raise XiaoeLoginRequired("滑块验证码控件没有加载完整")
        background.get_screenshot(image_path)
        locator = SliderCaptchaLocator(image_path)
        locator.radius = 27
        position = locator.find_captcha_position()
        slider.drag(position)
    finally:
        image_path.unlink(missing_ok=True)


def _goto_page(tab: Any, page: int) -> None:
    _ensure_list_ready(tab)
    if _active_page(tab) == page:
        return

    previous_rows = _read_page_rows(tab)
    previous_signature = [
        (row.get("title"), row.get("published_at")) for row in previous_rows
    ]
    clicked = tab.run_js(
        """
        const wanted = String(arguments[0]);
        const item = Array.from(document.querySelectorAll('li.ss-pagination-item'))
          .find((node) => node.textContent.trim() === wanted);
        if (!item) return false;
        item.click();
        return true;
        """,
        page,
    )
    if not clicked:
        jump = tab.ele("css:.ss-pagination-options-jump-input input", timeout=3)
        if not jump:
            raise XiaoeDaemonError(f"找不到跳转到第 {page} 页的输入框")
        jump.input(str(page), clear=True)
        jump.input(Keys.ENTER)

    if not _wait_until(lambda: _active_page(tab) == page, timeout=15):
        raise XiaoeDaemonError(f"无法进入第 {page} 页，当前页：{_active_page(tab)}")
    if not _wait_until(
        lambda: (
            len(_read_page_rows(tab)) == ITEMS_PER_PAGE
            and [
                (row.get("title"), row.get("published_at"))
                for row in _read_page_rows(tab)
            ]
            != previous_signature
        ),
        timeout=15,
    ):
        raise XiaoeDaemonError(f"第 {page} 页视频列表没有稳定")


def _read_page_rows(tab: Any) -> list[dict[str, str]]:
    rows = tab.run_js(
        """
        const tables = Array.from(document.querySelectorAll('table'));
        const titles = Array.from(tables[3]?.querySelectorAll('tbody tr') || []);
        const metas = Array.from(tables[1]?.querySelectorAll('tbody tr') || []);
        return titles.map((row, index) => {
          const title = (row.innerText || '').trim().replace(/^更换封面\\s*/, '');
          const meta = metas[index]?.innerText || '';
          const match = meta.match(/20\\d{2}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}/);
          return {title, published_at: match ? match[0] : ''};
        });
        """
    )
    return list(rows or [])


def _complete_page_metadata(
    tab: Any,
    rows: list[dict[str, str]],
    page: int,
) -> list[dict[str, str]]:
    """为列表中已下架且显示 ``--`` 的条目补回原始上架时间。"""
    if all(row.get("title") and row.get("published_at") for row in rows):
        return rows

    api_rows = tab.run_js(
        """
        const appId = Object.keys(localStorage)
          .find((key) => key.endsWith('_shopInfo'))
          ?.replace(/_shopInfo$/, '');
        if (!appId) return [];
        return fetch('/xe.course.b_admin_r.course.base.list/1.0.0', {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: new URLSearchParams({
            app_id: appId,
            search_content: '',
            resource_type: '3',
            sale_status: '-1',
            created_source: '1',
            auth_type: '-1',
            page_index: String(arguments[0]),
            page_size: '10',
          }).toString(),
        })
          .then((response) => response.json())
          .then((payload) => (payload.data?.list || []).map((item) => ({
            title: item.title || '',
            published_at: item.sale_at || '',
          })));
        """,
        page,
        timeout=20,
    )
    api_rows = list(api_rows or [])
    if len(api_rows) != len(rows):
        raise XiaoeDaemonError(f"第 {page} 页元数据接口返回数量不一致")

    completed: list[dict[str, str]] = []
    for index, (row, api_row) in enumerate(zip(rows, api_rows, strict=True)):
        row_title = str(row.get("title") or "")
        api_title = str(api_row.get("title") or "")
        if _normalize_page_title(row_title) != _normalize_page_title(api_title):
            raise XiaoeDaemonError(
                f"第 {page} 页第 {index + 1} 条页面与接口标题不一致"
            )
        completed.append(
            {
                **row,
                "title": api_title,
                "published_at": row.get("published_at")
                or str(api_row.get("published_at") or ""),
            }
        )
    return completed


def _normalize_page_title(title: str) -> str:
    """移除列表附加的免费、指定用户等状态徽标文本。"""
    content_lines: list[str] = []
    for raw_line in title.replace("\xa0", " ").splitlines():
        line = raw_line.strip()
        if not line or line == "免费":
            continue
        if re.fullmatch(r"指定用户(?:\s*/\s*\d+\s*天)?", line):
            continue
        content_lines.append(line)
    return " ".join(content_lines)


def _open_detail(browser: Any, list_tab: Any, item_index: int) -> Any:
    before_ids = set(browser.tab_ids)
    clicked = list_tab.run_js(
        """
        const buttons = Array.from(document.querySelectorAll('button'))
          .filter((button) => button.textContent.trim() === '管理');
        const button = buttons[arguments[0]];
        if (!button) return false;
        button.click();
        return true;
        """,
        item_index,
    )
    if not clicked:
        raise XiaoeDaemonError(f"找不到第 {item_index + 1} 条的管理按钮")

    new_id: str | None = None

    def find_new_tab() -> bool:
        nonlocal new_id
        candidates = [tab_id for tab_id in browser.tab_ids if tab_id not in before_ids]
        if not candidates:
            return False
        for tab_id in candidates:
            tab = browser.get_tab(tab_id)
            if DETAIL_URL_PART in str(tab.url):
                new_id = tab_id
                return True
        return False

    if not _wait_until(find_new_tab, timeout=15) or new_id is None:
        raise XiaoeDaemonError("点击管理后没有打开视频详情页")
    detail_tab = browser.get_tab(new_id)
    if not _wait_until(lambda: "上传视频" in _body_text(detail_tab), timeout=20):
        raise XiaoeDaemonError(f"视频详情页没有加载完成：{detail_tab.url}")
    return detail_tab


def _capture_playlist(detail_tab: Any) -> str:
    text = _body_text(detail_tab)
    preview = detail_tab.ele("css:.video-preview--active", timeout=5)
    inactive_preview = (
        None
        if preview
        else detail_tab.ele("css:.video-preview", timeout=1)
    )
    unavailable_reason = _preview_unavailable_reason(
        text,
        has_preview=bool(preview),
        has_inactive_preview=bool(inactive_preview),
    )
    if unavailable_reason:
        raise XiaoeUnsupportedPreview(unavailable_reason)
    if not preview:
        raise XiaoeDaemonError("找不到视频预览区域")

    detail_tab.listen.start(
        targets=[
            r"\.m3u8(?:\?|$)",
            r"xe\.material-center\.play/getPlayUrl",
        ],
        is_regex=True,
    )
    try:
        preview.click()
        packet = detail_tab.listen.wait(timeout=30, raise_err=False)
    finally:
        detail_tab.listen.stop()
    if not packet:
        raise XiaoeDaemonError("点击预览后没有捕获到播放地址")
    playlist_url = _playlist_url_from_packet(packet)
    if not playlist_url:
        raise XiaoeDaemonError("播放地址响应中没有可用的 m3u8")
    return playlist_url


def _playlist_url_from_packet(packet: Any) -> str | None:
    """同时兼容直接 m3u8 请求和 getPlayUrl 接口响应。"""
    request_url = str(getattr(packet, "url", "") or "")
    if ".m3u8" in request_url.lower():
        return request_url
    response = getattr(packet, "response", None)
    return _find_playlist_url(getattr(response, "body", None))


def _find_playlist_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value if ".m3u8" in value.lower() else None
    if isinstance(value, dict):
        for child in value.values():
            found = _find_playlist_url(child)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _find_playlist_url(child)
            if found:
                return found
    return None


def _preview_unavailable_reason(
    text: str,
    *,
    has_preview: bool,
    has_inactive_preview: bool = False,
) -> str | None:
    """识别后台明确不可下载或没有绑定媒体的详情状态。"""
    if "暂不支持预览" in text:
        return "后台显示暂不支持预览"
    if not has_preview and "上传视频" in text and "选择文件" in text:
        return "详情页未绑定可预览视频（上传区域显示“选择文件”）"
    if not has_preview and has_inactive_preview:
        return "详情页视频预览处于不可播放状态"
    return None


def _get_or_open_list_tab(browser: Any) -> Any:
    for tab in browser.get_tabs():
        if LIST_URL in str(tab.url):
            return tab
    return browser.new_tab(LIST_URL)


def _is_xiaoe_or_blank_tab(tab: Any) -> bool:
    """判断 tab 是否属于小鹅通业务，空白页不视为其他业务占用。"""
    url = str(getattr(tab, "url", "") or "").lower()
    return (
        not url
        or url in {"about:blank", "chrome://newtab/"}
        or "admin.xiaoe-tech.com" in url
        or ".xiaoeknow.com" in url
    )


def _cleanup_xiaoe_incremental_tabs(browser: Any) -> bool:
    """8 点增量作业结束后关闭小鹅通 tab，同时保留其他业务 tab。"""
    try:
        tabs = list(browser.get_tabs())
    except Exception:
        return False
    xiaoe_only = bool(tabs) and all(_is_xiaoe_or_blank_tab(tab) for tab in tabs)
    for tab in tabs:
        if xiaoe_only or (
            _is_xiaoe_or_blank_tab(tab)
            and str(getattr(tab, "url", "") or "").lower()
            not in {"", "about:blank", "chrome://newtab/"}
        ):
            try:
                tab.close()
            except Exception:
                pass
    return xiaoe_only


def _open_browser() -> Any:
    """复用本机 DrissionPage 默认浏览器及其统一登录态。"""
    return Chromium()


def _load_state(path: Path, *, start_page: int, start_item: int) -> dict[str, Any]:
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    else:
        state = {}
    cursor = state.get("cursor")
    if not isinstance(cursor, dict):
        cursor = {"page": start_page, "item_index": start_item}
    return {
        "status": "starting",
        "pid": os.getpid(),
        "started_at": state.get("started_at") or _now(),
        "updated_at": _now(),
        "cursor": {
            "page": int(cursor.get("page") or start_page),
            "item_index": int(cursor.get("item_index") if cursor.get("item_index") is not None else start_item),
        },
        "completed_count": int(state.get("completed_count") or 0),
        "last_completed": state.get("last_completed"),
        "special_items": list(state.get("special_items") or []),
        "last_error": None,
    }


def run_forever(args: argparse.Namespace) -> None:
    _load_xlproject_env()
    helper_dir = args.output_dir / "_下载辅助"
    state_path = helper_dir / "current-state.json"
    lock_path = helper_dir / "daemon.lock"
    helper_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        if lock_file.seek(0, 2) == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        lock_file.close()
        raise XiaoeDaemonError("已有小鹅通下载守护进程在运行") from exc

    state = _load_state(
        state_path,
        start_page=args.start_page,
        start_item=args.start_item,
    )
    _write_json(state_path, state)
    browser = _open_browser()
    list_tab = _get_or_open_list_tab(browser)
    page_rows_cache: dict[int, list[dict[str, str]]] = {}

    while True:
        page = int(state["cursor"]["page"])
        item_index = int(state["cursor"]["item_index"])
        detail_tab = None
        try:
            _goto_page(list_tab, page)
            rows = page_rows_cache.get(page)
            if rows is None:
                rows = _complete_page_metadata(
                    list_tab,
                    _read_page_rows(list_tab),
                    page,
                )
                page_rows_cache[page] = rows
            if item_index >= len(rows):
                total_items = _catalog_total(list_tab)
                if _cursor_reached_catalog_end(page, item_index, total_items):
                    state.pop("current", None)
                    state["status"] = "completed"
                    state["updated_at"] = _now()
                    state["finished_at"] = _now()
                    state["total_items"] = total_items
                    state["processed_count"] = state["completed_count"] + len(
                        state["special_items"]
                    )
                    state["last_error"] = None
                    _write_json(state_path, state)
                    break
                raise XiaoeDaemonError(
                    f"第 {page} 页只有 {len(rows)} 条，无法读取第 {item_index + 1} 条"
                )
            item = rows[item_index]
            if not item["title"] or not item["published_at"]:
                raise XiaoeDaemonError(f"第 {page} 页第 {item_index + 1} 条元数据不完整")

            state.update(
                {
                    "status": "capturing",
                    "updated_at": _now(),
                    "current": {
                        "page": page,
                        "item_index": item_index,
                        **item,
                    },
                    "last_error": None,
                }
            )
            _write_json(state_path, state)
            detail_tab = _open_detail(browser, list_tab, item_index)
            playlist_url = _capture_playlist(detail_tab)
            detail_url = str(detail_tab.url)
            detail_tab.close()
            detail_tab = None

            state["status"] = "downloading"
            state["updated_at"] = _now()
            state["current"]["detail_url"] = detail_url
            state["current"]["download_started_at"] = _now()
            _write_json(state_path, state)
            result = download_hls_video(
                playlist_url=playlist_url,
                title=item["title"],
                published_at=item["published_at"],
                output_dir=args.output_dir,
            )
            next_page, next_item = _next_cursor(page, item_index)
            state["cursor"] = {"page": next_page, "item_index": next_item}
            state["completed_count"] += 1
            state["last_completed"] = {
                "page": page,
                "item_index": item_index,
                **item,
                **result,
                "finished_at": _now(),
            }
            state.pop("current", None)
            state["status"] = "running"
            state["updated_at"] = _now()
            _write_json(state_path, state)
            time.sleep(args.between_items)
        except XiaoeUnsupportedPreview as exc:
            if detail_tab is not None:
                detail_url = str(detail_tab.url)
                detail_tab.close()
                detail_tab = None
            else:
                detail_url = ""
            current = dict(state.get("current") or {})
            state["special_items"].append(
                {
                    **current,
                    "detail_url": detail_url,
                    "error": str(exc),
                    "recorded_at": _now(),
                }
            )
            next_page, next_item = _next_cursor(page, item_index)
            state["cursor"] = {"page": next_page, "item_index": next_item}
            state.pop("current", None)
            state["status"] = "running"
            state["updated_at"] = _now()
            _write_json(state_path, state)
        except XiaoeLoginRequired as exc:
            state["status"] = "needs_login"
            state["updated_at"] = _now()
            state["last_error"] = str(exc)
            _write_json(state_path, state)
            try:
                _login_with_env(list_tab)
            except XiaoeLoginRequired as login_exc:
                state["last_error"] = str(login_exc)
                state["updated_at"] = _now()
                _write_json(state_path, state)
                time.sleep(args.login_retry_seconds)
            else:
                state["status"] = "running"
                state["last_error"] = None
                state["updated_at"] = _now()
                _write_json(state_path, state)
        except Exception as exc:
            logging.exception("小鹅通下载循环失败")
            if detail_tab is not None:
                try:
                    detail_tab.close()
                except Exception:
                    pass
            state["status"] = "retry_wait"
            state["updated_at"] = _now()
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            _write_json(state_path, state)
            time.sleep(args.error_retry_seconds)
            try:
                browser = _open_browser()
                list_tab = _get_or_open_list_tab(browser)
            except Exception:
                logging.exception("重连 DrissionPage 默认浏览器失败")


def main() -> None:
    parser = argparse.ArgumentParser(description="小鹅通视频严格串行常驻下载器")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--start-item", type=int, default=0, help="当前页零基条目索引")
    parser.add_argument("--between-items", type=float, default=2)
    parser.add_argument("--login-retry-seconds", type=float, default=60)
    parser.add_argument("--error-retry-seconds", type=float, default=300)
    args = parser.parse_args()
    run_forever(args)


if __name__ == "__main__":
    main()
