// node --test scripts/baidu_netdisk_sky.test.mjs  (fake sky; no GUI input)
import assert from "node:assert/strict";
import test from "node:test";

import {
  executeOne,
  fileDialogState,
  findClientWindow,
  observe,
  parseTree,
  planCalibratedClick,
  planTypeText,
  readTransferCounts,
  readTransferTask,
  routePath,
  selectElements,
  uploadNextStep,
  verifyFolder,
} from "./baidu_netdisk_sky.mjs";

const SHOT = { id: "s1", width: 1213, height: 864 };

// Combined main-window tree with the owned native dialog repeated, as observed.
function dialogTree({ value = "", selectedFlag = "selectable" } = {}) {
  const dialog = [
    `  50 对话框 请选择文件/文件夹`,
    `    51 列表 项目视图`,
    `      52 列表项 (${selectedFlag}) codeyun-transfer-probe.txt`,
    `      53 列表项 (selectable) other.txt`,
    `    60 编辑 (settable, string) 文件名(N): Value: ${value}`,
    `    61 按钮 存入百度网盘`,
  ].join("\n");
  return `0 窗口 百度网盘\n  1 链接 首页\n  2 按钮 上传\n  3 按钮 上传中\n${dialog}\n${dialog}`;
}

function fakeSky(states, { failClick = false } = {}) {
  const inputs = [];
  let i = 0;
  return {
    inputs,
    list_apps: async () => [{ id: "C:\\x\\BaiduNetdiskUnite.exe", windows: [{ id: 7 }] }],
    get_window_state: async () => states[Math.min(i++, states.length - 1)],
    click: async (input) => { inputs.push(["click", input]); if (failClick) throw new Error("element unavailable"); },
    type_text: async (input) => inputs.push(["type", input]),
    press_key: async (input) => inputs.push(["key", input]),
  };
}

const state = (tree, focused = "Document") => ({ accessibility: { tree, focused_element: focused, selected_elements: [] }, screenshots: [SHOT] });

test("parseTree dedups repeated modal indices and keeps parents", () => {
  const els = parseTree(dialogTree());
  const idx = els.map((e) => e.index);
  assert.equal(new Set(idx).size, idx.length);
  const row = els.find((e) => e.index === 52);
  assert.equal(row.parent, 51);
  assert.equal(els.find((e) => e.index === 60).name, "文件名(N):");
});

test("exact Chinese role/name: 上传 does not match 上传中", () => {
  const els = parseTree(dialogTree());
  assert.deepEqual(selectElements(els, { role: "button", name: "上传" }).map((e) => e.index), [2]);
});

test("findClientWindow requires the official client", async () => {
  assert.equal((await findClientWindow(fakeSky([]))).status, "ok");
});

test("Document focus is a mismatch; visual proof bound to seq/screenshot passes", async () => {
  const sky = fakeSky([state(dialogTree())]);
  const o = await observe(sky, 7);
  const focus = { role: "edit", name: "文件名(N):" };
  assert.equal(planTypeText(o, "C:\\x", { focus }).status, "focus_mismatch");
  assert.equal(planTypeText(o, "C:\\x", { focus, visualFocus: { seq: o.seq - 1, screenshotId: "s1", target: "文件名(N):" } }).status, "focus_mismatch");
  assert.equal(planTypeText(o, "C:\\x", { focus, visualFocus: { seq: o.seq, screenshotId: "other", target: "文件名(N):" } }).status, "focus_mismatch");
  const ok = planTypeText(o, "C:\\x", { focus, visualFocus: { seq: o.seq, screenshotId: "s1", target: "文件名(N):" } });
  assert.equal(ok.plan.focusEvidence, "visual");
  assert.equal(fileDialogState(o, { visualFocus: { seq: o.seq, screenshotId: "s1", target: "文件名(N):" } }).editFocused, true);
});

test("upload: row click then value evidence leads to confirm, not reselect", async () => {
  const sky = fakeSky([state(dialogTree()), state(dialogTree({ value: "codeyun-transfer-probe.txt" }))]);
  const fileName = "codeyun-transfer-probe.txt";
  const first = uploadNextStep(await observe(sky, 7), { fileName });
  assert.equal(first.phase, "file_listed");
  assert.equal(first.plan.element_index, 52);
  const second = await observe(sky, 7);
  assert.equal(uploadNextStep(second, { fileName }).phase, "file_listed");
  const confirm = uploadNextStep(second, { fileName, lastWhy: "select_file_row" });
  assert.equal(confirm.phase, "file_selected");
  assert.equal(confirm.selectionEvidence, "value_after_row_click");
  assert.equal(confirm.plan.element_index, 61);
});

test("calibrated profile: main-frame coordinates only after title+size check", async () => {
  const sky = fakeSky([state(dialogTree({ value: "codeyun-transfer-probe.txt" })), { ...state(dialogTree()), screenshots: [{ id: "s2", width: 1000, height: 700 }] }]);
  const o = await observe(sky, 7);
  const r = uploadNextStep(o, { fileName: "codeyun-transfer-probe.txt", lastWhy: "select_file_row", coordinates: true });
  assert.deepEqual([r.plan.x, r.plan.y, r.plan.screenshotId], [832, 583, "s1"]);
  assert.deepEqual([planCalibratedClick(o, { row: 1 }).plan.y, planCalibratedClick(o, "fileNameEdit").plan.x], [336, 640]);
  assert.equal(planCalibratedClick(o, { row: 40 }).status, "calibration_required");
  const small = await observe(sky, 7);
  assert.equal(planCalibratedClick(small, "confirm").reason, "screenshot_size_mismatch");
  const noDialog = { ...small, elements: parseTree("0 窗口 百度网盘"), screenshots: [SHOT] };
  assert.equal(planCalibratedClick(noDialog, "confirm").reason, "dialog_title_mismatch");
});

test("executeOne: stale plan refused; input error returns state_unknown without resend", async () => {
  const sky = fakeSky([state(dialogTree())], { failClick: true });
  const o1 = await observe(sky, 7);
  const stale = uploadNextStep(o1, { fileName: "codeyun-transfer-probe.txt" });
  await observe(sky, 7);
  assert.equal((await executeOne(sky, stale.plan, { settleMs: 0 })).status, "stale_plan");
  assert.equal(sky.inputs.length, 0);
  const o3 = await observe(sky, 7);
  const r = await executeOne(sky, uploadNextStep(o3, { fileName: "codeyun-transfer-probe.txt" }).plan, { settleMs: 0 });
  assert.equal(r.status, "state_unknown");
  assert.ok(r.observation);
  assert.equal(sky.inputs.length, 1);
});

test("executeOne submit reports submitted only, needs remote verification", async () => {
  const sky = fakeSky([state(dialogTree({ value: "codeyun-transfer-probe.txt" })), state("0 窗口 百度网盘")]);
  const o = await observe(sky, 7);
  const r = await executeOne(sky, uploadNextStep(o, { fileName: "codeyun-transfer-probe.txt", lastWhy: "select_file_row" }).plan, { settleMs: 0 });
  assert.equal(r.submitted, true);
  assert.equal(r.next, "verify_remote_row_or_task");
});

test("verifyFolder matches exact route path and tolerates bad encoding", () => {
  const mk = (value) => ({ elements: parseTree(`1 链接 x Value: ${value}`) });
  assert.equal(routePath("pan://x?path=%2Fa%E0%A4%A&y=1"), null);
  assert.equal(verifyFolder(mk("https://pan/x?path=%2Fab"), "/a").status, "not_verified");
  assert.equal(verifyFolder(mk("https://pan/x?path=%2F%E6%96%B0%E5%BB%BA%E6%96%87%E4%BB%B6%E5%A4%B9&t=1"), "/新建文件夹").status, "in_folder");
  assert.equal(verifyFolder(mk("https://pan/x?path=%E0%A4%A"), "/a").status, "not_verified");
});

test("transfer counts parse but are not task identity", () => {
  assert.deepEqual(readTransferCounts({ text: "下载中 0\n已完成（17）" }), { downloading: 0, uploading: null, completed: 17 });
});

// Real dialog tree captured by the main agent (role 列表项目 + Description).
const REAL_DIALOG = [
  "0 窗口 百度网盘",
  "  817 对话框 请选择文件/文件夹 Secondary Actions: Raise",
  "    848 列表项目 (selectable) codeyun-transfer-probe.txt Description: 类型: 文本文档 大小: 1.06 KB 修改日期: 2026/10/16 15:21",
  "      849 编辑 codeyun-transfer-probe.txt Description: 类型: 文本文档 大小: 1.06 KB",
  "    855 编辑 (settable, string) 文件名(N): Value: codeyun-transfer-probe.txt ID: 1148",
  "    861 按钮 存入百度网盘",
].join("\n");

test("real dialog: 列表项目 role and Description marker", async () => {
  const els = parseTree(REAL_DIALOG);
  const row = els.find((e) => e.index === 848);
  assert.equal(row.name, "codeyun-transfer-probe.txt");
  assert.match(row.description, /文本文档/);
  assert.deepEqual(selectElements(els, { role: "listItem", name: "codeyun-transfer-probe.txt", within: 817 }).map((e) => e.index), [848]);
  const sky = fakeSky([state(REAL_DIALOG)]);
  const o = await observe(sky, 7);
  assert.equal(fileDialogState(o).value, "codeyun-transfer-probe.txt");
  assert.equal(uploadNextStep(o, { fileName: "codeyun-transfer-probe.txt" }).plan.element_index, 848);
  const confirm = uploadNextStep(o, { fileName: "codeyun-transfer-probe.txt", lastWhy: "select_file_row" });
  assert.equal(confirm.phase, "file_selected");
  assert.equal(confirm.plan.element_index, 861);
});

const completedPage = (extra = "", rowName = "txt codeyun-transfer-probe.txt") => ({
  elements: parseTree([
    "700 组 pagegroup",
    "  760 文本 上传中(0)",
    "  761 文本 已完成(1)",
    "  762 按钮 清除记录",
    extra,
    "  770 表格",
    "    771 custom",
    `      772 项 ${rowName}`,
    "        773 图像 txt",
    "      774 项 1.07 KB",
    "        775 文本 1.07 KB",
    "      776 项 15:35:20",
    "        777 文本 15:35:20",
    "  780 文本 codeyun-transfer-probe.txt.bak",
  ].filter(Boolean).join("\n")),
  text: "上传中(0) 已完成(1)",
});

test("readTransferTask: icon prefix, custom row, completed view", () => {
  const r = readTransferTask(completedPage(), "codeyun-transfer-probe.txt", { category: "上传" });
  assert.equal(r.status, "task_completed");
  assert.equal(r.evidence, "completed_view");
  assert.deepEqual(r.row.texts, ["txt codeyun-transfer-probe.txt", "txt", "1.07 KB", "1.07 KB", "15:35:20", "15:35:20"]);
  assert.ok(!r.row.texts.includes("清除记录"));
});

test("readTransferTask: no completed-view evidence or substring name stays unknown", () => {
  assert.equal(readTransferTask(completedPage("  763 按钮 全部暂停"), "codeyun-transfer-probe.txt").status, "task_state_unknown");
  assert.equal(readTransferTask(completedPage("", "txt codeyun-transfer-probe(1).txt"), "codeyun-transfer-probe.txt").status, "task_not_visible");
  const noRow = { elements: parseTree("700 组 pagegroup\n  762 按钮 清除记录\n  772 文本 txt codeyun-transfer-probe.txt"), text: "" };
  assert.equal(readTransferTask(noRow, "codeyun-transfer-probe.txt").status, "task_not_visible");
});
