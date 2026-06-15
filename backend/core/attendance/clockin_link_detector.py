from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from backend.core.ai.chat import chat_with_provider


class ClockinLinkDetectionError(RuntimeError):
    pass


@dataclass
class _CandidateChoice:
    index: int
    used_ai: bool = False
    reason: str = ""


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any, limit: int = 1200) -> str:
    text = " ".join(_normalize_text(value).split())
    return text[:limit]


def _hash_query_params(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    fragment = parsed.fragment or ""
    query = ""
    if "?" in fragment:
        query = fragment.split("?", 1)[1]
    elif parsed.query:
        query = parsed.query
    result: dict[str, str] = {}
    for key, values in parse_qs(query, keep_blank_values=True).items():
        if values:
            result[key] = values[-1]
    return result


def _url_matches_params(url: str, required: dict[str, str]) -> bool:
    if "component_name=clock_task_data" not in url:
        return False
    params = _hash_query_params(url)
    return all(params.get(key) == value for key, value in required.items() if value)


def _wait_until(tab: Any, predicate: Any, timeout: float = 30.0, interval: float = 0.35) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _body_text(tab: Any) -> str:
    return _normalize_text(tab.run_js('return document.body && document.body.innerText || ""'))


def _click_text_group(tab: Any, text: str, group_index: int) -> dict[str, Any]:
    script = """
    const text = __TEXT__;
    const groupIndex = __GROUP_INDEX__;
    const raw = Array.from(document.querySelectorAll('button,[role=button],span,a,div'))
      .filter(el => (el.innerText || el.textContent || '').trim() === text)
      .map((el, index) => {
        const rect = el.getBoundingClientRect();
        return {
          el,
          index,
          tag: el.tagName,
          cls: String(el.className || ''),
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height),
        };
      })
      .filter(item => item.w > 0 && item.h > 0)
      .sort((a, b) => a.y - b.y || a.x - b.x || a.tag.localeCompare(b.tag));
    const groups = [];
    for (const item of raw) {
      let group = groups.find(existing => Math.abs(existing.y - item.y) <= 3 && Math.abs(existing.x - item.x) <= 90);
      if (!group) {
        group = { x: item.x, y: item.y, items: [] };
        groups.push(group);
      }
      group.items.push(item);
    }
    const serializable = groups.map((group, index) => ({
      index,
      x: group.x,
      y: group.y,
      items: group.items.map(item => ({
        index: item.index,
        tag: item.tag,
        cls: item.cls,
        x: item.x,
        y: item.y,
        w: item.w,
        h: item.h,
      })),
    }));
    const group = groups[groupIndex];
    if (!group) {
      return { ok: false, text, group_index: groupIndex, groups: serializable };
    }
    const item = group.items.find(entry => entry.tag === 'BUTTON')
      || group.items.find(entry => entry.tag === 'A')
      || group.items[0];
    item.el.click();
    return {
      ok: true,
      text,
      group_index: groupIndex,
      clicked: { tag: item.tag, cls: item.cls, x: item.x, y: item.y, w: item.w, h: item.h },
      groups: serializable,
    };
    """.replace("__TEXT__", json.dumps(text, ensure_ascii=False)).replace("__GROUP_INDEX__", str(group_index))
    result = tab.run_js(script)
    return dict(result) if isinstance(result, dict) else {"ok": False, "raw": result}


def _collect_category_candidates(tab: Any) -> list[dict[str, Any]]:
    script = """
    const rows = Array.from(document.querySelectorAll('tr'))
      .map((tr, index) => {
        const rect = tr.getBoundingClientRect();
        return {
          index,
          text: (tr.innerText || '').trim(),
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height),
        };
      })
      .filter(row => row.text);
    return rows
      .filter(row => !row.text.includes('名称\t包含打卡数'))
      .filter(row => !row.text.includes('操作'))
      .filter(row => !/^管理\\s*删除$/.test(row.text))
      .filter(row => row.text.includes('202') || row.text.includes('展示') || row.text.includes('隐藏'))
      .map((row, index) => ({ ...row, candidate_index: index }));
    """
    raw = tab.run_js(script)
    return [dict(item) for item in raw] if isinstance(raw, list) else []


def _collect_task_candidates(tab: Any) -> list[dict[str, Any]]:
    script = """
    const rows = Array.from(document.querySelectorAll('tr'))
      .map((tr, index) => {
        const rect = tr.getBoundingClientRect();
        return {
          index,
          text: (tr.innerText || '').trim(),
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height),
        };
      })
      .filter(row => row.text);
    return rows
      .filter(row => row.text.includes('起：') && row.text.includes('止：'))
      .filter(row => row.text.includes('管理'))
      .map((row, index) => ({ ...row, candidate_index: index }));
    """
    raw = tab.run_js(script)
    return [dict(item) for item in raw] if isinstance(raw, list) else []


def _collect_text_groups(tab: Any, text: str) -> list[dict[str, Any]]:
    script = """
    const text = __TEXT__;
    const raw = Array.from(document.querySelectorAll('button,[role=button],span,a,div'))
      .filter(el => (el.innerText || el.textContent || '').trim() === text)
      .map((el, index) => {
        const rect = el.getBoundingClientRect();
        return {
          index,
          tag: el.tagName,
          cls: String(el.className || ''),
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height),
        };
      })
      .filter(item => item.w > 0 && item.h > 0)
      .sort((a, b) => a.y - b.y || a.x - b.x || a.tag.localeCompare(b.tag));
    const groups = [];
    for (const item of raw) {
      let group = groups.find(existing => Math.abs(existing.y - item.y) <= 3 && Math.abs(existing.x - item.x) <= 90);
      if (!group) {
        group = { x: item.x, y: item.y, items: [] };
        groups.push(group);
      }
      group.items.push(item);
    }
    return groups.map((group, index) => ({ index, x: group.x, y: group.y, items: group.items }));
    """.replace("__TEXT__", json.dumps(text, ensure_ascii=False))
    raw = tab.run_js(script)
    return [dict(item) for item in raw] if isinstance(raw, list) else []


def _parse_task_dates(text: str) -> dict[str, Any]:
    normalized = _normalize_text(text)
    start_match = re.search(r"起[:：]\s*(\d{4}-\d{2}-\d{2})", normalized)
    end_match = re.search(r"止[:：]\s*(\d{4}-\d{2}-\d{2})", normalized)
    day_match = re.search(r"(\d+)\s*/\s*(\d+)", normalized)
    result: dict[str, Any] = {}
    if start_match:
        result["start_date"] = start_match.group(1)
    if end_match:
        result["end_date"] = end_match.group(1)
    if day_match:
        result["days"] = int(day_match.group(2))
    return result


def _choose_candidate_with_ai(
    *,
    target: str,
    candidates: list[dict[str, Any]],
    purpose: str,
    provider_id: str,
    model: str,
) -> _CandidateChoice | None:
    if not candidates:
        return None
    prompt = (
        "你要在小鹅通后台页面候选行里选择正确的打卡入口。"
        "只返回 JSON，不要解释。JSON 格式："
        '{"index": 候选数组下标, "reason": "一句话理由"}。\n\n'
        f"目标：{target}\n"
        f"选择目的：{purpose}\n"
        f"候选：{json.dumps([{ 'index': index, 'text': _compact_text(item.get('text'), 500) } for index, item in enumerate(candidates)], ensure_ascii=False)}"
    )
    try:
        response = chat_with_provider(
            provider_id=provider_id,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format="json",
        )
    except Exception:
        return None
    content = response.get("content") if isinstance(response, dict) else response
    try:
        data = json.loads(str(content or "").strip())
    except Exception:
        match = re.search(r"\{.*\}", str(content or ""), re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None
    try:
        index = int(data.get("index"))
    except Exception:
        return None
    if index < 0 or index >= len(candidates):
        return None
    return _CandidateChoice(index=index, used_ai=True, reason=_normalize_text(data.get("reason")))


def _choose_category_candidate(
    *,
    target: str,
    candidates: list[dict[str, Any]],
    provider_id: str,
    model: str,
) -> _CandidateChoice:
    ai_choice = _choose_candidate_with_ai(
        target=target,
        candidates=candidates,
        purpose="选择第一层打卡大类，例如共学或共修",
        provider_id=provider_id,
        model=model,
    )
    if ai_choice is not None:
        return ai_choice

    target_text = _normalize_text(target)
    for index, item in enumerate(candidates):
        if target_text and target_text in _normalize_text(item.get("text")):
            return _CandidateChoice(index=index, reason="名称包含目标")
    if candidates:
        return _CandidateChoice(index=0, reason="默认选择第一条")
    raise ClockinLinkDetectionError(f"没有找到 {target} 的打卡大类候选")


def _choose_task_candidate(
    *,
    target: str,
    candidates: list[dict[str, Any]],
    provider_id: str,
    model: str,
) -> _CandidateChoice:
    ai_choice = _choose_candidate_with_ai(
        target=target,
        candidates=candidates,
        purpose="选择当前新课程的打卡任务，避开旧的 1-6期 历史任务",
        provider_id=provider_id,
        model=model,
    )
    if ai_choice is not None:
        return ai_choice

    target_text = _normalize_text(target)
    for index, item in enumerate(candidates):
        text = _normalize_text(item.get("text"))
        if target_text in text and "1-6" not in text:
            return _CandidateChoice(index=index, reason="名称包含目标且不是 1-6 期历史任务")
    for index, item in enumerate(candidates):
        text = _normalize_text(item.get("text"))
        if "1-6" not in text:
            return _CandidateChoice(index=index, reason="选择非历史任务")
    if candidates:
        return _CandidateChoice(index=0, reason="默认选择第一条")
    raise ClockinLinkDetectionError(f"没有找到 {target} 的打卡任务候选")


def _find_matching_task_data_tab(browser: Any, required_params: dict[str, str]) -> Any | None:
    try:
        tabs = browser.get_tabs()
    except Exception:
        tabs = []
    for tab in tabs:
        try:
            url = _normalize_text(tab.url)
        except Exception:
            continue
        if _url_matches_params(url, required_params):
            return tab
    return None


def _tab_urls(browser: Any) -> set[str]:
    try:
        tabs = browser.get_tabs()
    except Exception:
        return set()
    urls: set[str] = set()
    for tab in tabs:
        try:
            urls.add(_normalize_text(tab.url))
        except Exception:
            pass
    return urls


def _close_new_detection_tabs(browser: Any, before_urls: set[str], created_tabs: list[Any]) -> None:
    try:
        tabs = browser.get_tabs()
    except Exception:
        tabs = []
    for tab in tabs:
        try:
            url = _normalize_text(tab.url)
        except Exception:
            url = ""
        should_close = tab in created_tabs
        if not should_close and url not in before_urls:
            should_close = (
                "component_name=clock_task_data" in url
                or "component_name=clock_task" in url
                or "component_name=clock_list" in url
                or "community_manage/data_analyze/clock_data" in url
            )
        if should_close:
            try:
                tab.close()
            except Exception:
                pass


def detect_clockin_links_browser(
    *,
    root_url: str,
    targets: list[str],
    provider_id: str = "codex-cli",
    model: str = "gpt-5.3-codex-spark",
    close_tabs: bool = True,
) -> dict[str, Any]:
    from DrissionPage import Chromium

    normalized_targets = [_normalize_text(item) for item in targets if _normalize_text(item)]
    if not _normalize_text(root_url):
        raise ClockinLinkDetectionError("缺少打卡根目录")
    if not normalized_targets:
        raise ClockinLinkDetectionError("缺少需要检测的打卡名称")

    browser = Chromium(9222)
    before_urls = _tab_urls(browser)
    created_tabs: list[Any] = []
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    ai_used = False

    try:
        tab = browser.new_tab(root_url)
        created_tabs.append(tab)
        _wait_until(tab, lambda: "管理" in _body_text(tab), timeout=30)

        for target in normalized_targets:
            target_result: dict[str, Any] = {
                "target": target,
                "url": "",
                "steps": [],
                "params": {},
            }
            tab.get(root_url)
            if not _wait_until(tab, lambda: target in _body_text(tab) and "管理" in _body_text(tab), timeout=30):
                raise ClockinLinkDetectionError(f"根目录没有加载出 {target} 打卡大类")

            category_candidates = _collect_category_candidates(tab)
            category_choice = _choose_category_candidate(
                target=target,
                candidates=category_candidates,
                provider_id=provider_id,
                model=model,
            )
            ai_used = ai_used or category_choice.used_ai
            click_result = _click_text_group(tab, "管理", category_choice.index)
            if not click_result.get("ok"):
                raise ClockinLinkDetectionError(f"无法点击 {target} 打卡大类管理按钮")
            target_result["steps"].append({
                "phase": "category",
                "choice": category_choice.index,
                "ai_used": category_choice.used_ai,
                "reason": category_choice.reason,
                "text": _compact_text(category_candidates[category_choice.index].get("text"), 600),
            })

            if not _wait_until(tab, lambda: "打卡标题" in _body_text(tab) and target in _body_text(tab), timeout=30):
                raise ClockinLinkDetectionError(f"{target} 任务清单没有加载完成")

            task_candidates = _collect_task_candidates(tab)
            task_choice = _choose_task_candidate(
                target=target,
                candidates=task_candidates,
                provider_id=provider_id,
                model=model,
            )
            ai_used = ai_used or task_choice.used_ai
            task_candidate = task_candidates[task_choice.index]
            target_result.update(_parse_task_dates(_normalize_text(task_candidate.get("text"))))
            click_result = _click_text_group(tab, "管理", task_choice.index)
            if not click_result.get("ok"):
                raise ClockinLinkDetectionError(f"无法点击 {target} 打卡任务管理按钮")
            target_result["steps"].append({
                "phase": "task_list",
                "choice": task_choice.index,
                "ai_used": task_choice.used_ai,
                "reason": task_choice.reason,
                "text": _compact_text(task_candidate.get("text"), 600),
            })

            if not _wait_until(tab, lambda: "component_name=clock_task" in _normalize_text(tab.url) and target in _body_text(tab), timeout=30):
                raise ClockinLinkDetectionError(f"{target} 单任务管理页没有加载完成")

            task_params = _hash_query_params(_normalize_text(tab.url))
            required_params = {
                "apply_id": task_params.get("apply_id", ""),
                "community_id": task_params.get("community_id", ""),
                "clock_id": task_params.get("clock_id", ""),
            }
            data_groups = _collect_text_groups(tab, "数据")
            top_data_index = next(
                (
                    int(group.get("index") or 0)
                    for group in data_groups
                    if int(group.get("x") or 0) > 1000 and int(group.get("y") or 0) > 100
                ),
                max(0, len(data_groups) - 1),
            )
            click_result = _click_text_group(tab, "数据", top_data_index)
            if not click_result.get("ok"):
                raise ClockinLinkDetectionError(f"无法点击 {target} 顶部数据入口")
            if not _wait_until(tab, lambda: "任务顺序" in _body_text(tab) and "编辑" in _body_text(tab), timeout=25):
                warnings.append(f"{target} 顶部数据入口切换后没有检测到任务顺序表，继续尝试行内数据按钮")

            row_data_groups = _collect_text_groups(tab, "数据")
            row_group_candidates = [
                group
                for group in row_data_groups
                if int(group.get("x") or 0) > 1000 and int(group.get("y") or 0) > int(data_groups[top_data_index].get("y") or 0) + 50
            ]
            if not row_group_candidates:
                raise ClockinLinkDetectionError(f"没有找到 {target} 行内数据按钮")
            row_data_index = int(row_group_candidates[0].get("index") or 0)
            click_result = _click_text_group(tab, "数据", row_data_index)
            if not click_result.get("ok"):
                raise ClockinLinkDetectionError(f"无法点击 {target} 行内数据按钮")

            opened_tab = None
            if _wait_until(
                tab,
                lambda: _find_matching_task_data_tab(browser, required_params) is not None,
                timeout=30,
            ):
                opened_tab = _find_matching_task_data_tab(browser, required_params)
            if opened_tab is None:
                raise ClockinLinkDetectionError(f"没有打开 {target} 的正式打卡数据页")

            final_url = _normalize_text(opened_tab.url)
            final_params = _hash_query_params(final_url)
            target_result["url"] = final_url
            target_result["params"] = final_params
            target_result["confidence"] = 1.0
            target_result["steps"].append({
                "phase": "task_data",
                "choice": row_data_index,
                "ai_used": False,
                "reason": "正式 URL 包含 component_name=clock_task_data，并匹配 apply_id/community_id/clock_id",
            })
            if "totalDay" in final_params:
                try:
                    target_result["days"] = int(final_params["totalDay"])
                except ValueError:
                    pass
            results.append(target_result)

        return {
            "root_url": root_url,
            "results": results,
            "warnings": warnings,
            "provider_id": provider_id,
            "model": model,
            "ai_used": ai_used,
        }
    finally:
        if close_tabs:
            _close_new_detection_tabs(browser, before_urls, created_tabs)
