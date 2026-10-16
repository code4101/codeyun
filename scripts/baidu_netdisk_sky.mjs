// Observe / plan / execute-one helpers for the official Baidu Netdisk client
// over the Computer Use `sky` API.
//
// Import from a node_repl session that already initialised `sky` per the
// computer-use skill.  The caller owns the gui:desktop:local lease and must
// `check` it before every `executeOne`.  The flow follows the skill's two-cell
// loop:
//   1. `observe(sky, window)` -> inspect the returned observation yourself;
//   2. build ONE plan from that observation (`planClick`, `planTypeText`,
//      `planPressKey`, or a semantic helper such as `uploadNextStep`);
//   3. `executeOne(sky, plan)` sends exactly one input and immediately returns
//      a fresh observation plus whether the plan's expectation holds.
// Plans are bound to the observation they came from; a plan whose
// observation is no longer the latest for that window is refused.  Nothing
// here retries or chains inputs, and no helper claims a transfer succeeded
// without a row-level observation of it.

const CLIENT_APP_RE = /baidunetdisk(unite)?\.exe/i;

// ---- accessibility tree -----------------------------------------------------

const MULTIWORD_ROLES = ["menu item", "list item", "combo box", "tab item", "data item", "tree item", "split button", "check box", "radio button"];
const MARKERS = [
  ["value", " Value: "],
  ["automationId", " ID:"],
  ["secondaryActions", " Secondary Actions: "],
  ["description", " Description: "],
];

// Role aliases: the client reports Chinese ControlType names on this machine.
export const ROLES = {
  button: ["按钮", "button"],
  link: ["链接", "link"],
  text: ["文本", "text"],
  group: ["组", "group"],
  edit: ["编辑", "edit"],
  combo: ["组合框", "combo box", "combobox"],
  dialog: ["对话框", "dialog", "window"],
  menuItem: ["菜单项", "menu item", "menuitem"],
  listItem: ["列表项目", "列表项", "list item", "listitem", "数据项", "data item", "row"],
  tabItem: ["选项卡项目", "tab item", "tabitem"],
};

function parseLine(raw) {
  // "  9 链接 (settable, string) 首页 Value: javascript:;"  and legacy "[9] ..."
  const match = raw.match(/^(\s*)(?:[-*]\s*)?\[?(\d+)\]?[:.)]?\s+(.*\S)\s*$/);
  if (!match) return null;
  let rest = match[3];
  const lower = rest.toLowerCase();
  const multi = MULTIWORD_ROLES.find((r) => lower.startsWith(`${r} `) || lower === r);
  const role = multi ? rest.slice(0, multi.length) : rest.split(/\s+/)[0];
  rest = rest.slice(role.length).trimStart();
  let flags = [];
  const flagMatch = rest.match(/^\(([^)]*)\)\s*/);
  if (flagMatch) {
    flags = flagMatch[1].split(",").map((f) => f.trim()).filter(Boolean);
    rest = rest.slice(flagMatch[0].length);
  }
  const element = { index: Number(match[2]), depth: match[1].length, role, flags, text: match[3] };
  let padded = ` ${rest}`;
  // An empty trailing "Value: " loses its space to the line trim.
  if (MARKERS.some(([, marker]) => padded.endsWith(marker.trimEnd()))) padded += " ";
  const cuts = MARKERS.map(([key, marker]) => [key, marker, padded.indexOf(marker)]).filter(([, , at]) => at >= 0).sort((a, b) => a[2] - b[2]);
  element.name = (cuts.length ? padded.slice(0, cuts[0][2]) : padded).trim().replace(/^"(.*)"$/, "$1");
  cuts.forEach(([key, marker, at], i) => {
    const end = i + 1 < cuts.length ? cuts[i + 1][2] : padded.length;
    element[key] = padded.slice(at + marker.length, end).trim();
  });
  return element;
}

// Elements keep `parent` (index of the nearest shallower line) so selectors
// can be scoped to a dialog subtree or a table row.
export function parseTree(tree) {
  const elements = [];
  const stack = [];
  const seen = new Set();
  for (const raw of String(tree || "").split(/\r?\n/)) {
    const element = parseLine(raw);
    if (!element) continue;
    while (stack.length && stack[stack.length - 1].depth >= element.depth) stack.pop();
    element.parent = stack.length ? stack[stack.length - 1].index : null;
    stack.push(element);
    // Owned modals can be repeated in the combined tree; keep the first copy
    // of each index (and its parent) so a repeat never makes a target ambiguous.
    if (seen.has(element.index)) continue;
    seen.add(element.index);
    elements.push(element);
  }
  return elements;
}

function roleIn(element, roles) {
  if (!roles) return true;
  const list = (Array.isArray(roles) ? roles : [roles]).flatMap((r) => ROLES[r] || [r]);
  return list.some((r) => r.toLowerCase() === element.role.toLowerCase());
}

export function isDescendant(elements, element, ancestorIndex) {
  const byIndex = new Map(elements.map((e) => [e.index, e]));
  for (let p = element.parent; p != null; p = byIndex.get(p)?.parent) if (p === ancestorIndex) return true;
  return false;
}

// selector: { role, name (exact string or RegExp), within (ancestor index) }
export function selectElements(elements, { role, name, within } = {}) {
  return elements.filter((e) =>
    roleIn(e, role)
    && (name == null || (name instanceof RegExp ? name.test(e.name) : e.name === name))
    && (within == null || isDescendant(elements, e, within)));
}

function describe(e) {
  return `${e.index} ${e.role} ${e.name}${e.value ? ` Value: ${e.value}` : ""}`;
}

export function findUnique(observation, selector) {
  const found = selectElements(observation.elements, selector);
  if (found.length === 1) return { status: "ok", element: found[0] };
  return {
    status: found.length ? "ambiguous_element" : "element_not_found",
    next: "inspect_observation",
    selector: { ...selector, name: String(selector.name) },
    candidates: found.slice(0, 8).map(describe),
  };
}

export const SELECTORS = {
  homeNav: { role: "link", name: "首页" },
  transferNav: { role: "link", name: "传输" },
  uploadButton: { role: "button", name: "上传" },
  newFolderButton: { role: "button", name: "新建文件夹" },
  fileDialog: { role: "dialog", name: "请选择文件/文件夹" },
  fileNameEdit: { role: "edit", name: "文件名(N):" },
  fileDialogConfirm: { role: "button", name: "存入百度网盘" },
  downloadMenu: { role: "menuItem", name: "下载" },
};

// ---- observation ----------------------------------------------------------------

const latest = new Map(); // window id -> seq of the latest observation
let seqCounter = 0;

function windowKey(window) {
  return JSON.stringify(window?.id ?? window);
}

export async function findClientWindow(sky) {
  const apps = await sky.list_apps();
  const windows = apps.filter((app) => CLIENT_APP_RE.test(String(app.id))).flatMap((app) => app.windows || []);
  if (windows.length === 1) return { status: "ok", next: "observe", window: windows[0] };
  return {
    status: windows.length ? "multiple_windows" : "client_not_running",
    next: windows.length ? "choose_window_by_title" : "launch_official_client",
    windows: windows.map((w) => ({ id: w.id, app: w.app, title: w.title })),
  };
}

// One get_window_state.  Owned modals (the native file dialog) are returned in
// the main window's combined tree and screenshots; they are not separate
// windows in list_windows on this machine.
export async function observe(sky, window, { screenshot = true } = {}) {
  const state = await sky.get_window_state({ window, include_screenshot: screenshot, include_text: true });
  const seq = ++seqCounter;
  latest.set(windowKey(window), seq);
  const a = state?.accessibility || {};
  return {
    seq,
    window,
    elements: parseTree(a.tree),
    focused: a.focused_element ? parseLine(`0 ${String(a.focused_element).replace(/^\s*\[?\d+\]?[:.)]?\s+/, "")}`) : null,
    focusedText: a.focused_element || "",
    selected: a.selected_elements || [],
    text: `${a.tree || ""}\n${a.document_text || ""}`,
    screenshotIds: (state?.screenshots || []).map((s) => s.id),
    screenshots: (state?.screenshots || []).map((s) => ({ id: s.id, width: s.width ?? s.size?.width ?? null, height: s.height ?? s.size?.height ?? null })),
    state,
  };
}

// ---- plans ----------------------------------------------------------------------

function basePlan(observation, kind, fields) {
  return { kind, window: observation.window, seq: observation.seq, screenshotIds: observation.screenshotIds, ...fields };
}

// Click one element chosen by selector from this observation.
export function planClick(observation, selector, { mouse_button, click_count, expect, why } = {}) {
  const pick = findUnique(observation, selector);
  if (pick.status !== "ok") return pick;
  return { status: "planned", plan: basePlan(observation, "click", { element_index: pick.element.index, target: describe(pick.element), mouse_button, click_count, expect, why }) };
}

// Coordinate click read by the caller from this observation's screenshot.
export function planClickPoint(observation, { x, y, screenshotId, mouse_button, click_count, expect, why }) {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return { status: "invalid_point", next: "inspect_screenshot" };
  if (screenshotId && !observation.screenshotIds.includes(screenshotId)) return { status: "stale_screenshot", next: "observe_again" };
  return { status: "planned", plan: basePlan(observation, "click", { x, y, screenshotId, mouse_button, click_count, expect, why }) };
}

// The main window's focused_element reports the Document, not the owned
// modal's focus.  A caller that confirmed the focus on this observation's
// screenshot may pass { seq, screenshotId, target } as a visual proof; it is
// accepted only for that exact observation/screenshot and target name.
export function visualFocusOk(observation, visualFocus, targetName) {
  return Boolean(visualFocus
    && visualFocus.seq === observation.seq
    && visualFocus.screenshotId != null && observation.screenshotIds.includes(visualFocus.screenshotId)
    && visualFocus.target === targetName);
}

function focusMatches(observation, focus, visualFocus) {
  const f = observation.focused;
  if (f && roleIn(f, focus.role) && (focus.name == null || (focus.name instanceof RegExp ? focus.name.test(f.name) : f.name === focus.name))) return "uia";
  return typeof focus.name === "string" && visualFocusOk(observation, visualFocus, focus.name) ? "visual" : null;
}

// type_text goes to the current focus, so the focused element must match
// (UIA focus, or a visual proof bound to this observation).
export function planTypeText(observation, text, { focus, visualFocus, expect, why } = {}) {
  let focusEvidence = null;
  if (focus) {
    focusEvidence = focusMatches(observation, focus, visualFocus);
    if (!focusEvidence) return { status: "focus_mismatch", next: "focus_target_first", focused: observation.focusedText };
  }
  return { status: "planned", plan: basePlan(observation, "type_text", { text, focusEvidence, expect, why }) };
}

// ---- calibrated coordinates (fallback when element_index is unavailable) -----
// Observed only on official client 8.1.5.103, main window 1213x864, native
// dialog 567x435.  Coordinates are main-window screenshot coordinates (the
// semantics verified with sky.click); modal-local values are not used.
export const CALIBRATION_PROFILES = {
  "client-8.1.5.103-main-1213x864": {
    clientVersion: "8.1.5.103",
    screenshot: { width: 1213, height: 864 },
    dialog: { title: "请选择文件/文件夹", width: 567, height: 435 },
    points: { fileNameEdit: { x: 640, y: 583 }, firstRow: { x: 545, y: 315 }, confirm: { x: 832, y: 583 } },
    rowStep: 21,
    maxRows: 12,
  },
};

// target: "fileNameEdit" | "confirm" | { row: n } (0-based visible row).
// Refuses unless the dialog title and the screenshot size match the profile.
export function planCalibratedClick(observation, target, { profile = "client-8.1.5.103-main-1213x864", expect, why } = {}) {
  const p = CALIBRATION_PROFILES[profile];
  if (!p) return { status: "calibration_required", next: "calibrate_profile", reason: "unknown_profile" };
  const dialog = findUnique(observation, { role: "dialog", name: p.dialog.title });
  if (dialog.status !== "ok") return { status: "calibration_required", next: "inspect_observation", reason: "dialog_title_mismatch", dialog };
  const shot = observation.screenshots?.[0];
  if (!shot || shot.width !== p.screenshot.width || shot.height !== p.screenshot.height) {
    return { status: "calibration_required", next: "calibrate_profile", reason: "screenshot_size_mismatch", screenshot: shot || null };
  }
  let point;
  if (typeof target === "object" && Number.isInteger(target?.row)) {
    if (target.row < 0 || target.row >= p.maxRows) return { status: "calibration_required", next: "inspect_screenshot", reason: "row_outside_profile" };
    point = { x: p.points.firstRow.x, y: p.points.firstRow.y + p.rowStep * target.row };
  } else {
    point = p.points[target];
    if (!point) return { status: "calibration_required", next: "calibrate_profile", reason: "unknown_target" };
  }
  return { status: "planned", plan: basePlan(observation, "click", { ...point, screenshotId: shot.id, profile, target: `calibrated:${JSON.stringify(target)}`, expect, why }) };
}

export function planPressKey(observation, key, { expect, why } = {}) {
  return { status: "planned", plan: basePlan(observation, "press_key", { key, expect, why }) };
}

// Send exactly one input for a plan from the latest observation, then refresh.
// `plan.expect(observation) -> boolean` is evaluated once on the refresh; a
// false result is reported, never retried.
export async function executeOne(sky, plan, { settleMs = 800, screenshot = true } = {}) {
  if (!plan || !plan.kind) return { status: "no_plan" };
  if (latest.get(windowKey(plan.window)) !== plan.seq) return { status: "stale_plan", next: "observe_again" };
  latest.delete(windowKey(plan.window)); // a plan executes at most once
  const { window } = plan;
  if (!["click", "type_text", "press_key"].includes(plan.kind)) return { status: "unknown_plan_kind" };
  const executed = { kind: plan.kind, target: plan.target, why: plan.why };
  try {
    if (plan.kind === "click") {
      const input = { window, mouse_button: plan.mouse_button, click_count: plan.click_count };
      if (plan.element_index != null) input.element_index = plan.element_index;
      else Object.assign(input, { x: plan.x, y: plan.y, screenshotId: plan.screenshotId });
      await sky.click(input);
    } else if (plan.kind === "type_text") {
      await sky.type_text({ window, text: plan.text });
    } else {
      await sky.press_key({ window, key: plan.key });
    }
  } catch (error) {
    // The input may or may not have landed: never resend, just re-observe.
    let observation = null;
    let observeError = null;
    try { observation = await observe(sky, window, { screenshot }); } catch (e) { observeError = String(e?.message || e); }
    return { status: "state_unknown", next: "inspect_observation_do_not_resend", error: String(error?.message || error), observeError, executed, observation };
  }
  if (settleMs) await new Promise((resolve) => setTimeout(resolve, settleMs));
  const after = await observe(sky, window, { screenshot });
  const verdict = plan.expect ? plan.expect(after) : undefined; // undefined: not observable
  const met = verdict === undefined ? null : Boolean(verdict);
  // "submitted" only means the input was sent; success needs a remote row,
  // transfer task or round-trip check.
  const submitted = plan.why === "submit_upload_once" ? { submitted: true, next: "verify_remote_row_or_task" } : {};
  return { status: met === false ? "expect_not_met" : met ? "expect_met" : "executed", next: met === false ? "inspect_observation_before_any_retry" : "plan_next_step", executed, ...submitted, observation: after };
}

// ---- semantic readers -------------------------------------------------------------

export function fileDialogState(observation, { visualFocus } = {}) {
  const dialog = findUnique(observation, SELECTORS.fileDialog);
  if (dialog.status !== "ok") return { open: false, dialog };
  const within = dialog.element.index;
  const edit = findUnique(observation, { ...SELECTORS.fileNameEdit, within });
  const confirm = findUnique(observation, { ...SELECTORS.fileDialogConfirm, within });
  return {
    open: true,
    dialogIndex: within,
    edit: edit.element || null,
    confirm: confirm.element || null,
    editFocused: Boolean(focusMatches(observation, SELECTORS.fileNameEdit, visualFocus)),
    value: edit.element?.value ?? null,
    issues: [edit, confirm].filter((r) => r.status !== "ok"),
  };
}

// Decide the single next upload step from one observation.  The native
// picker returns its list-view selection; a typed full file path was observed
// to close the dialog without creating any task, so the file is selected as a
// row in the dialog after navigating to its directory.
// `lastWhy` is `executed.why` of the previous executeOne; the edit's value is
// not always exposed, so it disambiguates "typed but not yet navigated".
// `visualFocus` is a screenshot focus proof (see visualFocusOk);
// `coordinates: true` returns calibrated main-window coordinate plans for the
// dialog controls when element_index input is unavailable.
export function uploadNextStep(observation, { dirPath, fileName, lastWhy, visualFocus, coordinates = false } = {}) {
  const d = fileDialogState(observation, { visualFocus });
  if (!d.open) {
    if (d.dialog.status === "ambiguous_element") return { phase: "dialog_ambiguous", evidence: d.dialog };
    const plan = planClick(observation, SELECTORS.uploadButton, { expect: (o) => fileDialogState(o).open, why: "open_upload_dialog" });
    return { phase: "folder_view", ...plan };
  }
  if (!d.edit || !d.confirm) return { phase: "dialog_unrecognised", evidence: d.issues };
  const fileRows = selectElements(observation.elements, { role: "listItem", name: fileName, within: d.dialogIndex });
  const uiaSelected = observation.selected.some((line) => parseLine(`0 ${String(line).replace(/^\s*\[?\d+\]?[:.)]?\s+/, "")}`)?.name === fileName) || fileRows.some((r) => r.flags.includes("selected"));
  // The list often shows only "(selectable)": after our own row click, the
  // exact basename in the edit plus a single matching row is the evidence.
  const clickedSelected = lastWhy === "select_file_row" && d.value === fileName && fileRows.length === 1;
  if ((uiaSelected && (d.value == null || d.value === fileName)) || clickedSelected) {
    const opts = { expect: (o) => !fileDialogState(o).open, why: "submit_upload_once" };
    const plan = coordinates ? planCalibratedClick(observation, "confirm", opts) : planClick(observation, { ...SELECTORS.fileDialogConfirm, within: d.dialogIndex }, opts);
    return { phase: "file_selected", selectionEvidence: uiaSelected ? "uia" : "value_after_row_click", ...plan };
  }
  if (fileRows.length === 1) {
    const opts = { expect: (o) => valueIs(o, fileName), why: "select_file_row" };
    const listRows = selectElements(observation.elements, { role: "listItem", within: d.dialogIndex });
    const plan = coordinates ? planCalibratedClick(observation, { row: listRows.indexOf(fileRows[0]) }, opts) : planClick(observation, { role: "listItem", name: fileName, within: d.dialogIndex }, opts);
    return { phase: "file_listed", ...plan };
  }
  if (fileRows.length > 1) return { phase: "file_row_ambiguous", evidence: fileRows.map(describe) };
  if (!d.editFocused) {
    // Codex observed element-index input on this edit as unavailable; prefer
    // planClickPoint with coordinates read from this observation's screenshot.
    // UIA rarely reports the modal's focus; confirm it on the refreshed
    // screenshot and pass visualFocus on the next call.
    const opts = { why: "focus_file_name" };
    const plan = coordinates ? planCalibratedClick(observation, "fileNameEdit", opts) : planClick(observation, { ...SELECTORS.fileNameEdit, within: d.dialogIndex }, opts);
    return { phase: "dialog_open", ...plan, nextAfter: "confirm_focus_on_screenshot_then_pass_visualFocus" };
  }
  const typed = d.value == null ? lastWhy === "type_directory_path" : d.value === dirPath;
  if (!typed) {
    if (d.value) return { phase: "file_name_not_empty", next: "select_all_then_type", ...planPressKey(observation, "Control_L+a", { why: "select_existing_file_name" }), value: d.value };
    return { phase: "file_name_focused", ...planTypeText(observation, dirPath, { focus: SELECTORS.fileNameEdit, visualFocus, expect: (o) => valueIs(o, dirPath), why: "type_directory_path" }) };
  }
  return { phase: "directory_typed", ...planPressKey(observation, "Return", { expect: (o) => fileDialogState(o).open, why: "navigate_to_directory" }) };
}

// Transfer page: a task is the exact-basename element plus status texts inside
// the same row container.  Counts are auxiliary only.
const DONE_RE = /^(已完成|上传完成|下载完成|完成)$/;
const FAIL_RE = /失败|错误|出错/;
const RUNNING_RE = /^(上传中|下载中|排队中|等待中|正在.*|\d+(\.\d+)?%)$/;

function valueIs(observation, expected) {
  const d = fileDialogState(observation);
  if (!d.open) return false;
  return d.value == null ? undefined : d.value === expected;
}

export function readTransferCounts(observation) {
  const pick = (label) => {
    const m = observation.text.match(new RegExp(`${label}\\s*[（(]?\\s*(\\d+)`));
    return m ? Number(m[1]) : null;
  };
  return { downloading: pick("下载中"), uploading: pick("上传中"), completed: pick("已完成") };
}

// Transfer list name cells carry a file-type icon prefix ("txt name.txt").
const ICON_PREFIX_RE = /^(txt|zip|rar|7z|pdf|doc|docx|xls|xlsx|ppt|pptx|jpg|jpeg|png|gif|mp4|mp3|exe|apk|folder|文件夹|其他)\s+/i;
const ROW_ROLES = ["custom", "自定义"];

function taskName(name) {
  return String(name || "").replace(ICON_PREFIX_RE, "");
}

// Rows are the nearest custom (or list item) container above an exact-basename
// cell; a hit without such a container is ignored rather than widened to the page.
export function findTaskRows(observation, fileName) {
  const els = observation.elements;
  const byIndex = new Map(els.map((e) => [e.index, e]));
  const rows = new Map();
  for (const hit of els.filter((e) => taskName(e.name) === fileName)) {
    let row = null;
    for (let p = hit.parent; p != null; p = byIndex.get(p)?.parent) {
      const candidate = byIndex.get(p);
      if (!candidate) break;
      if (roleIn(candidate, ROW_ROLES) || roleIn(candidate, "listItem")) { row = candidate; break; }
    }
    if (!row || rows.has(row.index)) continue;
    const members = els.filter((e) => e.index === row.index || isDescendant(els, e, row.index));
    const texts = members.map((e) => e.name).filter(Boolean);
    rows.set(row.index, {
      row: describe(row),
      texts,
      state: texts.some((t) => FAIL_RE.test(t)) ? "failed" : texts.some((t) => DONE_RE.test(t)) ? "completed" : texts.some((t) => RUNNING_RE.test(t)) ? "running" : "unknown",
    });
  }
  return [...rows.values()];
}

// The completed view shows a clear-records button and no pause/start-all
// buttons; that page evidence plus one exact row counts as completed.
export function isCompletedView(observation) {
  const has = (name) => selectElements(observation.elements, { role: "button", name }).length > 0;
  return (has("清除记录") || has("清空全部记录")) && !has("全部暂停") && !has("全部开始");
}

// `category` is the transfer tab the caller verified ("上传" / "下载").
export function readTransferTask(observation, fileName, { category } = {}) {
  const rows = findTaskRows(observation, fileName);
  const counts = readTransferCounts(observation);
  if (!rows.length) return { status: "task_not_visible", next: "observe_again_do_not_resubmit", counts };
  if (rows.length > 1) return { status: "ambiguous_task", next: "inspect_rows", rows, counts };
  const [row] = rows;
  let state = row.state;
  let evidence = "row_text";
  if (state === "unknown" && isCompletedView(observation)) { state = "completed"; evidence = "completed_view"; }
  const status = { completed: "task_completed", failed: "task_failed", running: "task_running" }[state] || "task_state_unknown";
  return { status, evidence: status === "task_state_unknown" ? null : evidence, category: category || null, next: status === "task_completed" ? "verify_remote_or_collect_local" : "observe_again_do_not_resubmit", row, counts };
}

// Folder verification needs positive evidence of the target path (route value
// or breadcrumb chain), never just the absence of the old row.
function safeDecode(text) {
  try { return decodeURIComponent(text.replace(/\+/g, " ")); } catch { return null; }
}

// Exact `path` query parameter of a route value, or null.
export function routePath(value) {
  const m = String(value || "").match(/[?&#]path=([^&#]*)/);
  return m ? safeDecode(m[1]) : null;
}

export function verifyFolder(observation, remotePath) {
  const routeHit = observation.elements.find((e) => e.value && routePath(e.value) === remotePath);
  if (routeHit) return { status: "in_folder", evidence: describe(routeHit) };
  const parts = remotePath.split("/").filter(Boolean);
  const last = parts[parts.length - 1];
  const crumbs = observation.elements.filter((e) => e.name === last && (roleIn(e, "link") || roleIn(e, "text")));
  return { status: "not_verified", next: "inspect_breadcrumb", candidates: crumbs.map(describe) };
}
