---
name: retirement-pet-cycle-coordinator
description: Coordinate or follow one asynchronous RetirementPet development cycle as an implementer, controller, or scheduled follow-up task using repository state and Git handoffs.
---

# RetirementPet 周期协调器

本 Skill 是执行者与主控共享的入口。它解决两个会话不能直接通信的问题：以 Git、
[当前状态](../../CURRENT_STATE.md)和周期记录作为唯一邮箱，让执行者先形成可靠初稿，
再由主控独立复验并完善低风险收尾。

## 启动与路由

任务提示必须明确角色是“执行者”或“主控审阅者”。角色不明确时只读取状态并报告，
不得修改生产代码、用户数据或运行中的桌宠。

1. 读取 `AGENTS.md`、[CURRENT_STATE.md](../../CURRENT_STATE.md)和
   [WORKFLOW.md](../../WORKFLOW.md)。
2. 执行者再读[执行者 Skill](../implementer/SKILL.md)、当前 Brief 和最新 REVIEW；
   主控再读[主控 Skill](../controller/SKILL.md)、当前 Brief 和 EXECUTION。
3. 实质工作前取得共享锁。已有锁时不抢占、不删除，以 `SKIPPED_BUSY` 结束。
4. 以状态机选择动作，不以聊天记忆推断另一会话做到了哪里。

| 当前状态 | 执行者动作 | 主控动作 |
|---|---|---|
| `READY_FOR_IMPLEMENTATION` | 调查并实施当前周期 | 只读观察，不提前验收 |
| `IMPLEMENTING` | 仅确认是自己留下的周期后继续 | 不介入；报告仍在实施 |
| `READY_FOR_REVIEW` | 停止修改，等待审阅 | 固定身份并完整验收 |
| `REWORK_REQUIRED` | 先关闭返工，不夹带新范围 | 可直接收口低风险问题；实质问题留给执行者 |
| `ACCEPTED` | 不再修改本周期 | 报告可合并状态；只有明确授权才合并 |
| `BLOCKED` | 不绕过阻塞 | 只向用户报告必须决定或补充的事项 |
| `NO_CHANGE` / `SKIPPED_BUSY` | 停止 | 核对事实后停止 |

## 异步交接

- Git 提交与 `docs/agent-workflow/cycles/<cycle_id>/` 是跨会话唯一消息通道。
- 执行者提交实现、证据和 EXECUTION 初稿，进入 `READY_FOR_REVIEW` 后停止。
- 主控独立复现，不用执行者的总结替代证据；审阅结论写入 REVIEW 和 CURRENT_STATE。
- 交接提交后的登记提交只能修改 CURRENT_STATE 与其中指向的 execution report；
  具体身份与精确路径约束以 WORKFLOW 第 4 节和治理测试为准。
- 生产代码或候选身份变化后，旧测试结论和旧候选不能自动继承。

## 主控直接完善的边界

主控可以直接修改并签收以下低风险收尾：

- 文档、报告、链接、格式和机器可读登记；
- 测试本身与工作流治理校验；
- 不改变产品行为的证据整理和口径修正。

主控直接完善时仍要留下实现提交、交接提交、登记提交与审阅记录，并复跑受影响检查。
这不是无痕修改，也不能跳过最终复验。

以下变化交回执行者，或安排另一轮独立验收：

- 产品/UI 行为、协议语义、用户数据、schema、迁移和恢复；
- 权限、隐私、安全边界、素材来源与许可；
- 候选构建身份、发布材料、较大范围代码或架构变化。

## 定时跟进任务

主控定时任务启动后先读状态和共享锁：

- 执行者仍在工作或锁被占用：不干扰，只记录当前状态；
- 已 `READY_FOR_REVIEW`：立即按主控 Skill 验收；
- 已 `REWORK_REQUIRED`：判断是否属于可直接完善的小问题，否则保留给执行者；
- 已 `ACCEPTED`、`NO_CHANGE` 或没有新提交：不重复审阅；
- 身份分裂、工作树脏、报告缺失或状态无法解释：停止修改并报告治理异常。

定时任务不创建其他自动任务，不自动发布、推送、重启桌宠或写用户 Todo。只有出现
以下情况才需要重点提醒用户：需要产品决定或授权、发现数据/许可/隐私风险、缺真实
环境门禁、周期已签收可合并，或发生无法安全自行收口的失败。普通等待和无变化不应
反复打扰用户。

## 结束条件

结束前核对分支、完整提交身份、状态文档、报告、测试结果和工作树；如实区分 PASS、
SKIP 与 PENDING。释放自己创建的共享锁。合并本地 `main`、公开推送和 Release 分别
需要明确授权。
