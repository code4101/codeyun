# DSP 静态资源同步

戴森球计算器的静态资源统一通过仓库脚本构建和同步：

```powershell
uv run python scripts/build_dsp_static.py
```

脚本按源码和依赖清单计算指纹：内容未变化时快速跳过；变化时自动安装必要依赖、重新构建并替换 `frontend/public/dsp-calc`。本地同步状态写入已忽略的 `frontend/.codeyun-state/`。

需要忽略缓存强制重建时使用：

```powershell
uv run python scripts/build_dsp_static.py --force
```

参数、源目录覆盖和错误语义以脚本 `--help` 及代码为准，不在根 `AGENTS.md` 重复维护。
