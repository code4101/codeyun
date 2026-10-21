"""Read complete Skyvendor rejection snapshots for CodeHub's online table.

The browser retains the site's login; cookies, sandbox keys and signed URLs
never leave it. A failed/inconsistent scrape raises instead of publishing an
empty or partial snapshot. CodeHub owns storage, comparison and idempotency.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import re
from typing import Callable
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

SOURCE = "https://www.skyvendorise.com"
SERIES = "frontier-bench-fw"
SCOPE = "current_account_visible"
CODEHUB_SNAPSHOT_API = "/api/data-platform/frontier-bench/rejections/snapshots"
CODEHUB_TABLE_API = "/api/data-platform/frontier-bench/rejections"

# Whitelist at the page boundary, before the API response enters Python. In
# particular extraInfo, templateConfig and file accessUrl can contain secrets.
PAGE_GET = r"""
return fetch(arguments[0], {credentials:'include'}).then(async response => {
if (!response.ok) throw new Error('Skyvendor HTTP ' + response.status);
const body = await response.json();
if (body.code !== 0) throw new Error('Skyvendor API code ' + body.code);
const keep = (obj, fields) => Object.fromEntries(fields.map(k => [k,obj[k] ?? null]));
const basic = obj => keep(obj,['id','taskId','appId','batchId','status','size',
    'taskName','name','rejectReason','gmtModified']);
const d = body.data;
if (Array.isArray(d.data)) return {
    recordCount:d.recordCount, data:d.data.map(basic)
};
return {...basic(d),
    dataList:(d.dataList || []).map(item => ({dataId:item.dataId,
        file_names:((item.result || {}).markResult || [])
            .flatMap(mark => mark.value || []).map(file => file.name || '')})),
    rejectRecords:(d.rejectRecords || []).map(item =>
        keep(item,['stage','rejectTime','rejectReason']))
};
});
"""


def existing_site_reader() -> Callable[[str], dict]:
    """Attach to the established CodeYun DP automation session; never launch it.

    No navigation, input or desktop focus is used. Only same-origin GETs are
    sent through an existing Skyvendor tab, as in the prior import workflow.
    """
    from DrissionPage import Chromium, ChromiumOptions
    from backend.core.dp_browser import resolve_dp_browser_debug_address

    browser = Chromium(ChromiumOptions(read_file=False)
                       .set_address(resolve_dp_browser_debug_address()).existing_only())
    tabs = [tab for tab in browser.get_tabs()
            if urlsplit(tab.url).scheme == "https"
            and urlsplit(tab.url).netloc == "www.skyvendorise.com"]
    if not tabs:
        raise RuntimeError("现有 DP 浏览器中没有 Skyvendor 标签页，请先打开并登录标注平台")
    tab = tabs[0]

    def get(path: str) -> dict:
        if not path.startswith("/api/v1/label/") or path.startswith("//"):
            raise ValueError("Only the established read-only label API is supported")
        return tab.run_js(PAGE_GET, SOURCE + path, timeout=30)

    return get


def list_all(get: Callable[[str], dict], path: str, **params) -> list[dict]:
    """Read all pages, refusing drifting counts, duplicate IDs or truncation."""
    result, total = [], None
    for page in range(1, 1001):
        data = get(path + "?" + urlencode({**params, "page": page, "pageSize": 100}))
        count, items = data.get("recordCount"), data.get("data")
        if not isinstance(count, int) or count < 0 or not isinstance(items, list):
            raise ValueError("平台分页响应不完整，未生成可同步快照")
        if total is not None and total != count:
            raise ValueError("抓取期间平台总数发生变化，请重新抓取")
        total = count
        result.extend(items)
        if len(result) >= total:
            if len(result) != total:
                raise ValueError("平台分页行数与总数不一致")
            ids = [item.get("id") for item in result]
            if path.endswith("/subTasks") and (None in ids or len(set(ids)) != total):
                raise ValueError("平台分页包含重复或缺失的子任务 ID")
            return result
        if not items:
            raise ValueError("平台分页提前结束，拒绝同步部分数据")
    raise ValueError("平台分页超出本次抓取上限")


def rejection_rows(item: dict, detail: dict) -> list[dict]:
    """Extract stable question identities and exact latest reviewer reasons."""
    files = detail.get("dataList") or []
    if item.get("size") != 1 or detail.get("size") != 1 or len(files) != 1:
        raise ValueError(f"子任务 {item['id']} 的数据粒度发生变化，拒绝截断导出")
    history = sorted(detail.get("rejectRecords") or [], key=lambda r: r.get("rejectTime") or "")
    latest = history[-1] if history else {}
    reason = latest.get("rejectReason") or item.get("rejectReason") or ""
    if not reason.strip():
        raise ValueError(f"子任务 {item['id']} 缺少驳回原因")
    if reason != item.get("rejectReason"):
        raise ValueError(f"子任务 {item['id']} 的列表原因与最新审核记录不一致")
    names = [name for name in files[0].get("file_names", []) if name]
    question_ids = set(re.findall(r"FB-\d{6}-\d{4}", ";".join(names)))
    if len(question_ids) > 1 or not names or not files[0].get("dataId"):
        raise ValueError(f"子任务 {item['id']} 的题目定位不完整或有歧义")
    return [{
        "project_name": item["taskName"], "task_id": item["taskId"],
        "project_id": item["appId"], "subtask_id": item["id"],
        "batch_id": item["batchId"], "data_id": files[0]["dataId"],
        "question_id": next(iter(question_ids), ""), "file_name": ";".join(names),
        "status_code": 3, "status_text": "被驳回", "latest_reject_reason_raw": reason,
        "latest_reject_stage": latest.get("stage") or "",
        "latest_reject_time": latest.get("rejectTime") or "",
        "reject_record_count": len(history),
        "revisit_url": SOURCE + "/corpora/labeling/sdk?" + urlencode({
            "subTaskId": item["id"], "missionType": "label", "taskId": item["taskId"],
            "projectId": item["appId"], "isFinished": "true", "disableEdit": "true"}),
    }]


def collect_snapshot(get: Callable[[str], dict] | None = None) -> dict:
    """Capture a full account-visible series; validate again before publishing.

    Empty rejection sets are valid only when the known series still exists in
    the task catalog. An inaccessible/renamed project is never treated as all
    its old rejections having disappeared.
    """
    get = get or existing_site_reader()
    projects = list_all(get, "/api/v1/label/center/tasks", subTaskType="label")
    matching = [p for p in projects if (p.get("name") or "").startswith(SERIES)]
    if not matching:
        raise ValueError("未找到目标系列项目，拒绝用空快照覆盖在线表")
    covered = {}
    for project in matching:
        pid = project.get("appId")
        if not isinstance(pid, int):
            raise ValueError("项目目录缺少来源项目 ID，未生成覆盖范围")
        if pid in covered:
            covered[pid].update(task_id=None, project_name=SERIES)
        else:
            covered[pid] = {"project_id": pid, "task_id": project.get("taskId"),
                            "project_name": project["name"]}
    params = {"type": "label", "filterByTaskUser": "true"}
    items = list_all(get, "/api/v1/label/center/subTasks", **params)
    selected = [item for item in items if (item.get("taskName") or "").startswith(SERIES)]
    if any(item.get("appId") not in covered for item in selected):
        raise ValueError("子任务列表与项目目录的覆盖范围不一致")
    rows = []
    for item in selected:
        if item["status"] != 3:
            continue
        detail = get(f"/api/v1/label/center/subTask/{item['id']}/data?" + urlencode({
            "page": 1, "pageSize": 1, "filterPassedVote": "false",
            "filter": json.dumps({"questions": [], "dataStatus": "ALL",
                                  "questionsQueryConditions": "AND"}, separators=(",", ":"))}))
        rows.extend(rejection_rows(item, detail))
    verify = list_all(get, "/api/v1/label/center/subTasks", **params)
    signature = lambda data: sorted((x["id"], x["status"], x.get("rejectReason"),
                                    x.get("gmtModified")) for x in data
                                   if (x.get("taskName") or "").startswith(SERIES))
    if signature(items) != signature(verify):
        raise ValueError("抓取期间任务状态发生变化，未同步；请重新抓取")
    identities = [(r["project_id"], r["question_id"] or r["data_id"]) for r in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("出现重复题目，拒绝同步歧义快照")
    counts = Counter(str(x["status"]) for x in selected)
    return {"source": "skyvendor", "series": SERIES, "scope": SCOPE,
            "captured_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            "rows": rows, "projects": list(covered.values()), "source_total": len(rows),
            "crawler": {"name": "CodeYun frontierbench_sync", "version": 1,
                        "source_counts": dict(counts)}, "complete": True}
