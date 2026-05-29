---
name: pyxllib-versioning
description: pyxllib commit 前必须递增 __version__ 并用版本号标记 commit message
metadata:
  type: feedback
---

每次提交 pyxllib（`D:\home\chenkunze\slns\pyxllib`）时，必须做两件事：
1. 递增 `src/pyxllib/__init__.py` 中的 `__version__`（当前 `4.53`，下一次 `4.54`，以此类推）
2. commit message 以 `v4.xx` 开头，格式参考：`v4.53 wxautox微信自动化、问卷星重构与考勤模块`

**Why:** 这是用户要求的规范，确保每次 pyxllib 变更都有版本标记可追溯。

**How to apply:** 在做 pyxllib 的 commit 之前，先编辑 `__init__.py` 把 `__version__` 加 1，然后在 commit message 前加上 `v4.xx` 前缀。版本号用小版本号递增（4.53 → 4.54 → 4.55...）。
