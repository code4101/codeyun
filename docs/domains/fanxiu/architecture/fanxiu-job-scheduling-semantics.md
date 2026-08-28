# 凡修 Job 状态与时间契约

## 最小模型

```text
next_time 到期 → Scheduler 提交 Job Cell
→ Job 形成 job_status 并写 next_time
→ Cell 形成 run_status → Scheduler 机械收尾
```

| 概念 | 所有者 | 生命周期 |
|---|---|---|
| `job_status` | Job 业务 | 当前 Cell |
| `next_time` | Job；技术错误时 Scheduler 有受限写权 | 跨 Cell |
| `run_status` | Cell 框架 | 当前 attempt |
| `attempt_id` | Scheduler | 当前 Job Cell |

## 规则

- `next_time <= now` 为到期，未来为等待，`None` 为休眠；不存在平行触发队列或隐藏恢复状态。
- 正常业务结果由 Job 分类并写入下一次绝对时间；`run_status=success` 不等于游戏目标成功。
- `run_status=error` 只表示程序、基础设施或契约异常；Scheduler 仅应用预设技术重试。
- `interrupted` 恢复本 attempt 的 `original_next_time`，不保存业务步骤。
- 终态必须携带预期 `attempt_id`；迟到 attempt 失去写权。

## 禁止恢复中间状态

Cell 结束后，`job_status`、generator、局部步骤、scene cursor 和按钮意图全部失效。新 attempt：

1. 读取当前事实；
2. 消费当前页面可直接证明的本 Job 合法终态；
3. 否则通过正式导航回稳定入口；
4. 整单幂等运行。

禁止从旧确认页、进度页或结果页推断上次意图后续点。同一 attempt 内一个动作的多个合法直接落点不属于跨 attempt 恢复。

## 手动运行

- 提前运行：以计划时间作为业务时间。
- 立即运行：只有用户明确要求时使用真实墙钟。

两者提交同一种 Job Cell；业务时间不持久化，也不改变 Scheduler 的真实 attempt 时间。
