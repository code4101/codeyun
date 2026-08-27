# Windows 通用界面增强图层

这套最小运行时把界面增强拆为两条独立链路：

1. 输入生产者负责窗口捕获、OCR、翻译、识别或 Agent 推理，并输出声明式 JSON。
2. `Windows Overlay Runtime` 只负责绑定目标窗口、坐标换算、点击穿透和图元渲染。

运行时不理解“翻译”“凡修场景”或其它业务语义。第一版协议支持 `text`、`rect` 两种图元；生产者通过 `target + viewport + elements` 描述完整画面。

运行时启动后会显示独立的“界面增强”控制窗口，提供两个互相独立的布尔开关：

- `显示增强图层` 控制输出是否可见，切换时不中断截图、识别和业务计算。
- `鼠标点击穿透` 默认开启；开启时鼠标直接操作目标应用，关闭时增强层接管交互，可拖选图层文字并用 `Ctrl+C` 复制。

两项选择都会写入运行时偏好文件并在下次启动时恢复；关闭控制窗口会同时关闭并隐藏增强层。

## 启动

```powershell
uv run python scripts/windows_overlay.py
```

停止：

```powershell
uv run python scripts/windows_overlay.py --stop
```

默认场景文件和心跳文件位于系统临时目录的 `codeyun/windows-overlay-runtime/`，不会污染仓库。

## Prefab 论文翻译演示

Chrome 打开 Prefab 英文论文后执行：

```powershell
uv run python scripts/windows_overlay_translation_demo.py
```

演示生产者通过 `PrintWindow` 获取不含遮挡窗口和 Overlay 的目标窗口原始帧，首次使用英文 OCR 提取论文标题并建立像素模板。中文标题使用透明背景显示在英文标题上方；后续滚动只运行轻量模板匹配，根据当前英文标题框重新计算中文标题相对坐标，不再反复做全页 OCR。标题滚出可见区、置信度不足、切换标签页、最小化或关闭 Chrome 后自动隐藏。

当前采用完整场景快照协议。后续需要高频更新时，可以在不改变业务语义的情况下把文件传输替换为本机 WebSocket 或 Named Pipe，并增加增量操作。

## 坐标与锚点

- `coordinate_mode=exact` 表示元素坐标只对产生它的当前窗口帧有效；窗口尺寸改变时，运行时立即隐藏，而不是同比缩放旧坐标。
- 首次 OCR 文本框定义英文标题锚点；运行期保存标题像素模板，并在每一帧的窗口相对坐标内跟踪。中文标题位置始终由当前锚点框加相对偏移计算。
- 原始坐标与最后一次成功坐标作为下一轮搜索先验；只有当前帧与原帧近似一致、锚点只是短暂漏检时才能复用。
- 如果检测到滚动、缩放、窗口布局或标签页变化且锚点无法重建，元素必须隐藏，不能把旧坐标画到新内容上。

标题锚点跟踪按目标帧周期调度：画面变化时目标为 `12.5 FPS`，静止时仍保持约 `11 FPS` 的轻量采样。每帧先计算低分辨率感知哈希，画面基本未变时跳过模板匹配；每 2 秒强制复核一次，避免长期哈希碰撞或漏检。运行状态每秒写入系统临时目录 `codeyun/windows-overlay-translation-demo/tracking-status.json`，包含实际 FPS、截图、哈希、匹配耗时和跳过率。

持续观察演示：

```powershell
uv run python scripts/windows_overlay_translation_demo.py --watch
```
