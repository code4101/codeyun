# TIM 消息解析逆向记录

本文记录 CodeYun 对 TIM/QQ 旧版本地消息库的静态分析结论，避免后续继续把 `.bak` 当成普通文本导出处理。

## 已确认的组件边界

- `MsgMgr.dll` 主要负责消息管理器 UI，包含 `MSGMGR_EXPORT1`、`MSGMGR_IMPORT1`、`.bak`、`MsgMgr\MsgMgr.xml|ExportWnd` 等界面字符串。
- `IM.dll` 包含消息导入/导出主逻辑，关键日志字符串包括 `MsgExport`、`ImportMsgExBak %s`、`ImportMsg20Bak %s`、`ImportFile Key OK`。
- `KernelUtil.dll` 暴露加密 SQLite 包装和导入辅助函数，包括：
  - `CMultiSQLite3DB::open`
  - `CreateDataStorage2`
  - `CreateSvrSeal2`
  - `CreateEncryptProxy`
  - `CreateUIGetPass`
  - `MsgImport::AutoBackupMsgDB`

## `.bak`/DB 路线

TIM 的“加密文件 (*.bak, 支持导入)”不是纯文本导出。`IM.dll` 的导入链会：

1. 读取 `info.dat`、`Matrix.dat`、`Matrix.db` 等内部资源名。
2. 调用 `CreateEncryptProxy`、`CreateUIGetPass`。
3. 调用 `CreateSvrSeal2` 创建 SvrSeal 加密上下文。
4. 通过 `CMultiSQLite3DB::open(path, guid, dataStorage, svrSeal, uiGetPass, callback, flags)` 打开加密库。

导出 DB 路线会先创建 `DataStorage2` 和 `SvrSeal2`，再用同一套 `CMultiSQLite3DB::open` 创建目标库。导出的消息表结构已经确认：

```sql
create table %s (
    Time integer,
    Rand integer,
    SenderUin integer,
    MsgContent blob,
    Info blob,
    primary key(Time, Rand)
);
```

随后从源库执行：

```sql
select Time, Rand, SenderUin, MsgContent, Info from %s;
```

并把结果插入导出库：

```sql
insert into %s values (?, ?, ?, ?, ?);
```

这说明完整自动化不应依赖消息管理器人工“保存为”，而应做一个独立的 TIM 32 位解库 helper：在独立进程中加载 TIM 的 32 位 DLL，复用 `KernelUtil.dll` 的 DataStorage/SvrSeal/CMultiSQLite3DB 路径，把 `Msg3.0.db` 或 `.bak` 读成结构化行，再交给 CodeYun 入库。

## 文本路线

TIM 另有文本/HTML/MHT 导出路线，会写入 `From: <Save by Tencent MsgMgr>`、`MSGMGR_MSGREC`、`MSGMGR_MSGFOLDER`、`MSGMGR_MSGOBJ` 等内容。CodeYun 当前只对这类低风险文本导出做直接解析。

## 2026-05-31 进一步探查

- 本机 `Msg3.0.db`、`Msg3.0index.db`、`Info.db`、`Misc.db`、`FaceStore.db` 等都不是标准 SQLite；直接搜索 UIN、中文消息片段、`MsgContent`、`SenderUin` 均无命中，说明完整文本不在文件中明文存放。
- `MsgMgr.dll` 字符串确认存在更正统的内部取数路径：`MsgRecordDataProvider`、`BatchGetRecordByTimeFromLocalDB`、`GetMsgRecordByRowids`、`GetRoamMsg`、`SearchMsg`。
- `MsgMgr.dll` 包含 `CMsgRecordDataProvider.CMsgRecordData` 和 CLSID `{125DAAF8-70F7-4954-8D39-C47083D61A4B}`。它没有注册到系统 COM；`regsvr32`/`DllRegisterServer` 返回 `0x80070715`。
- 独立 32 位探针直接调用 `DllGetClassObject({125DAAF8-70F7-4954-8D39-C47083D61A4B}, IID_IClassFactory)` 成功，`CreateInstance(IID_IUnknown)` 成功，但对象不支持 `IDispatch`，也不是普通 Python `win32com.client.Dispatch` 可调用接口。
- 因此“更多数据”的可行路线不是继续扩大内存扫描，而是：
  1. 短期：自动化 TIM 消息管理器/漫游加载，让 TIM 自己把历史记录加载出来，再由 CodeYun 增量入库。
  2. 中期：逆向 `CMsgRecordDataProvider` 的自定义 vtable 接口，直接调用 `BatchGetRecordByTimeFromLocalDB`/`GetRoamMsg` 类能力。
  3. 长期：跑通 `KernelUtil.dll` 的 `CMultiSQLite3DB::open` 加密库路径，直接读取 `Msg3.0.db`/`.bak`。

## TIM 前端“加载更多历史”策略

本机 `C:\Program Files (x86)\Tencent\TIM\Bin\MsgMgr.dll` 的首选镜像基址为 `0x57d10000`。以下地址记录优先使用 RVA，避免 ASLR 后绝对地址变化。

已定位到两类取历史入口：

- 本地库批量取历史：日志字符串 `BatchGetRecordByTimeFromLocalDB TaskID: %llu TaskType: %d TaskState: %d`，引用点在 RVA `0x2afc4`、`0x32ce4` 附近。
- 漫游历史取数：日志字符串 `GetRoamMsg TaskID: %llu TaskType: %d TaskState: %d`，引用点在 RVA `0x2c2ec`、`0x33f2b` 附近。

反汇编可以看出 UI 加载更多时不是一次性读完整库，而是任务状态机：

1. 先走 `BatchGetRecordByTimeFromLocalDB`，从本地加密库按时间/索引批量取一页。
2. 如果当前任务允许漫游，进入 `GetRoamMsg`。相关日志还包含：
   - `bCanGetRoamMsg`
   - `nRoamChatType`
   - `MsgIndex: %u %u %u`
   - `bReverse`
   - `dwMaxCount`
   - `dwGetRoamMsgCookie`
3. 漫游请求会经过 RVA `0x15445f` 附近的内部函数。该函数按 `nRoamChatType` 分支，只接受特定聊天类型，否则返回失败。
4. 群/讨论组有专门规避服务端缺陷的策略。原始日志为：`将本页最后一条正向拉取 改为 从最后一条+n条开始反向拉取n条, 以规避群消息server的正向拉取的缺陷--rodman`。
5. 该分支会把正向加载改造成反向加载：先把原请求数量计算成 `oldMaxCount * 2 + 1`，再以 `dwMaxCount + 10` 的形式向下层发起请求。普通路径也会额外加 `10` 条，说明 TIM 自己会多拉一点用于边界/去重。

这解释了为什么 CodeYun 当前直接读运行时明文只能看到一小段：右侧消息管理器真正的“加载更多”是惰性的，历史页没有进入进程明文对象前，内存扫描器也没有东西可抓。

后续自动化应按这个优先级推进：

1. 用 UI 自动化驱动消息管理器逐页触发“更多历史/向上翻页”，每次触发后调用 CodeYun 增量扫描并入库。这条路线不破解协议，最快能把用户可见历史结构化缓存下来。
2. 给 `MsgMgr.dll` 的漫游入口做轻量参数采集，记录 `nRoamChatType`、`MsgIndex` 三元组、`bReverse`、`dwMaxCount`、`cookie`，反推出每个会话的分页游标。
3. 继续逆向 `MsgRecordDataProvider` 自定义接口/vtable，目标是跳过 UI，直接调用本地批量取历史和漫游取历史。
4. 最后再攻 `KernelUtil.dll` 加密库路线，用独立 32 位 helper 直接打开 `Msg3.0.db` 或 `.bak`。

## 当前 CodeYun 策略

- `.txt/.htm/.html/.mht/.mhtml`：作为 MsgMgr 文本导出解析。
- `.bak`：只做格式识别，不再误用文本解析器。
- `Msg3.0.db`：如果标准 SQLite 不能打开，则标记为 TIM 加密 SQLite 变体。
- 后续目标：实现独立 32 位 `tim_kernelutil` helper，避免注入 TIM 进程，也避免用户每次手动导出。
