# 凡修抓包退役与 Runtime 补齐清单

## 当前架构结论

- 当前业务事实只允许来自只读 Runtime 或已保存的高级业务语义表。
- Runtime 缺字段、模型未加载或读取失败时，业务必须安全失败、延后或报告不完整。
- 禁止以 pcap、tcp flow、协议 raw JSON、代理事件、socket 捕获或历史 decoded record 作为兜底。
- 历史抓包实现只保存在 `backend/core/fanxiu/history_museum/packet_capture/`，必须人工显式确认才能运行。

## 已完成替换

- Runtime 服务管理、启动流程、MuMu 恢复、游戏窗口流不再启动抓包服务。
- 道法争锋：排名、剩余次数、候选、自身战力读取 `ImmortalRaceMgr` Runtime。
- 仙缘斗法：次数、刷新、我方战力和候选读取 `PartnerarenaMgr` Runtime。
- 论道：房间、座位、自身档案和名单读取 `LundaoMgr` Runtime。
- 灵脉：房间、座位和名单读取灵脉 Runtime；不再读取历史座位包。
- 道祖挑战：当日次数读取道祖之路 Runtime。
- 世界线活动周期：读取 `ActivityMgr` Runtime。
- 邮件：当前清单、状态、顺序和动作策略读取 `MailMgr` Runtime，并保留既有高级邮件业务记录。
- 红包巡检：读取只读红包 Runtime 快照，不再重放协议事件。
- 数字门与热更新研究：生产 API 已移除 decoded fixture、socket sample、pcap 校准探针；旧实现归档到历史博物馆。
- 业务快照仓库：已从 `fanxiu.packet` 迁到 `fanxiu.business_data`；生产写入函数只接收 Runtime/业务观测。
- Wiki：抓包页签、深链、旧 API、协议样本状态与样式已物理删除；面板、邮件、储物袋只走 Runtime/业务接口。

## Runtime 待补能力

| 优先级 | 业务 | 缺失能力 | 当前安全行为 | 完成标准 |
|---|---|---|---|---|
| P0 | 灵宠竞武 | 本期 `QuestEntryVO` 完整任务集合及进度 | 任务明细报告 Runtime 不完整，不读取 raw JSON | 从已加载任务 Manager 只读得到声明数、完整 taskId 集合、进度和采集证据；正反样本验收 |
| P0 | 炼体法相 | 本期 `QuestEntryVO` 完整任务集合及进度 | 任务明细报告 Runtime 不完整，不读取 raw JSON | 同上，并能唯一连接本期 `ActiveTask` 配置 |
| P0 | 历练事件 | 选择后的奖励结算与胜负 | 不再 arm socket/pcap；无 Runtime 结果时不沉淀奖励结论 | 从事件/奖励 Runtime 模型读取本轮结果，能以事件 ID、选择 ID 和结算序号关联本次点击 |
| P1 | 灵脉 | 未入座时的自身 role id、战力、法则档案 | 缺档案时拒绝选座 | 从账号/角色常驻 Runtime 读取独立于座位的自身档案，并与灵脉模型交叉校验 |
| P1 | 世界线活动 | `openServerTime` 字段的真实布局验证 | 缺字段时 server day 返回未知，不猜测 | 在真实已加载 `ActivityMgr` 上验证字段名、数值范围和重启一致性 |
| P1 | 活动观测 | 活动进度与钱包绝对值的同一时点水位 | 不再用协议 raw 时间比较；资料不足时拒绝覆盖已有新事实 | Runtime 快照提供同进程、同模型世代或可比较版本号，防止旧钱包覆盖新进度 |

## 退役代码归档状态

- `backend/api/fanxiu.py` 与 `backend/api/fanxiu_resources.py` 的旧抓包路由、请求模型、处理函数和路由过滤门面均已删除。
- `backend/core/fanxiu/packet/` 生产包已清空；抓包服务、解析器、decoded store、insight worker、数字门/斗破/热更新抓包研究均在历史博物馆。
- 邮件运行链路已统一为 `runtime_*`；旧业务 payload 中嵌套的高层邮件内容和奖励在 Runtime 同步时扁平迁移，raw 来源元数据不再继续保存到新投影。
- 数据库历史表名及少量旧列名为保留既有高级业务记录暂不破坏；它们不是现行数据源，也不授权任何自动采集。

## 当前机器清理验收（2026-08-16）

- 已删除 tcp flow、pcap、packet insights、activity packet sync、raw decoded JSON 与临时退役备份；原始文件约释放 2.17 GiB，连同数据库收缩累计约释放 6.6 GB。
- `fanxiupacketdecodedrecord` 为 0 行；保留的高级语义记录为业务快照 1,494,675 行、邮件 1,807 行、玩家档案 112,976 行，数据库 `quick_check=ok`。
- 发现并停止一个仍加载旧代码的 8001 测试后端及其 `adb exec-out tcpdump`；重启正式 8000 后端与 Scheduler 后，主机/设备抓包进程、raw 目录和现行抓包 API 均为 0。
- 当前 Scheduler 清单已通过统一原子存储入口移除遗留的 `capture_retry_seconds` 字段。

## 验收门槛

- 全仓生产代码不得导入历史博物馆或抓包采集、raw 解码模块。
- `/fanxiu/packet-capture/**`、`/fanxiu/capture-runtime/**`、`/fanxiu/activity-packet-sync` 不得注册为现有 API。
- Runtime 页、Wiki 和其它现有 UI 不得出现启动、同步、追平、维护或查看 raw 抓包的入口。
- Scheduler、Job、巡检、窗口流、MuMu 恢复和应用启动不得产生抓包进程、tcpdump、代理配置或 raw 文件目录。
- Runtime 不完整测试必须断言安全失败，并断言没有任何抓包兜底调用。
