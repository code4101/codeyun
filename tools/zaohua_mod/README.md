# 造化仙缘 Mod

基于 BepInEx 5 的《造化仙缘》非官方插件实验。源码保存在 CodeYun，游戏原始 DLL 不做修改。

## Hello World 验证

游戏默认目录为 `D:\SteamLibrary\steamapps\common\GodWorld`。

```powershell
.\tools\zaohua_mod\build.ps1
.\tools\zaohua_mod\deploy.ps1
```

启动游戏后验证：

1. 左上角出现 `Hello World` 面板。
2. `BepInEx\LogOutput.log` 包含 `Hello World from CodeYun Zaohua Mod!`。

Steam 文件校验不会修改这里的源码。游戏更新后应重新构建和验证插件兼容性。
