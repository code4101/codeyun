# 随机世界 Demo

独立 BepInEx 验证插件：在创建角色时选择原版路线或“天道试炼·随机世界”。

当前版本生成 28×28 的炼气级地图蓝图并保存到插件自己的持久化目录，不改写游戏原始资源与官方存档结构。

## Demo 边界

- 创建角色页提供“原版仙途 / 天道试炼·随机世界”选择。
- 试炼路线为新角色生成独立种子、28×28 地形、仙缘城及九处炼气级地点，并保证地点道路连通。
- 第一章初始化后、原版 `MapController.LoadMap()` 前调用游戏自己的随机地图生成器，并把仙缘城地块与美术对象嵌入地图中心。
- 状态保存在 Unity `persistentDataPath/Code4101.Tiandao/RandomStoryDemo`，不向官方存档增加字段。

## 构建

```powershell
dotnet build tools\zaohua_mod\RandomStoryDemo\RandomStoryDemo.csproj -c Release
```

输出 DLL：`bin/Release/Code4101.Zaohua.RandomStoryDemo.dll`。
