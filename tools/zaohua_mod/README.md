# 造化仙缘 Mod

基于 BepInEx 6 的《造化仙缘》非官方智能炼丹插件。源码保存在 CodeYun，游戏原始 DLL 和存档均不做修改。

## 当前功能

- `丹谱`：按游戏品阶折叠展示全部丹方，点击后复用游戏左下角的丹方详情。
- `智能`：读取当前丹方、已装备丹炉和背包有限药材，在插件内离线求解可摆放方案。
- 求解结果沿用官方方案卡片，每批展示 5 条；点击方案只回填炼丹格，不会自动炼丹。
- 不依赖 CodeYun 服务、浏览器、`localhost` 或联网 API，可单机分发运行。

## 构建与部署

游戏默认目录为 `D:\SteamLibrary\steamapps\common\GodWorld`。

```powershell
.\tools\zaohua_mod\build.ps1
.\tools\zaohua_mod\deploy.ps1
```

完全退出并重启游戏后验证：

1. 主界面点击“再寻仙踪”。
2. 进入顶部 `K` 炼丹入口，再进入“炼制丹药”。
3. 右侧依次出现 `丹方 / 炼制 / 配方 / 丹谱 / 智能`。
4. `BepInEx\LogOutput.log` 包含 `Smart Alchemy patches registered.`。

Steam 文件校验不会修改 CodeYun 中的源码；插件部署产物位于 `BepInEx\plugins`。游戏或 BepInEx 更新后应重新构建并按上述真实入口验证兼容性。
