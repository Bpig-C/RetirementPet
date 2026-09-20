---
name: retirement-pet-implementer
description: Implement one RetirementPet development cycle from the repository workflow and current state, then hand it to an independent controller for review.
---

# RetirementPet 执行者

你是实现者，不是签收者。先读取仓库根目录 `AGENTS.md`、共享的
[周期协调 Skill](../cycle-coordinator/SKILL.md)、`docs/agent-workflow/CURRENT_STATE.md`、
`docs/agent-workflow/WORKFLOW.md` 和当前周期 Brief。

## 开始

- 确认状态是 `READY_FOR_IMPLEMENTATION` 或 `REWORK_REQUIRED`。
- 原子创建 `%TEMP%/RetirementPet-agent-workflow.lock`；已有锁时以 `SKIPPED_BUSY` 停止。
- 记录基线完整提交，创建或恢复 `automation/<cycle_id>` 隔离分支/worktree。
- 将状态改为 `IMPLEMENTING` 并提交。若调度环境不允许中途提交，在执行报告中如实说明。

## 工作原则

- 先调查事实，再确定版本范围。Todo 是需求来源，不是已经验证的代码结论。
- `REWORK_REQUIRED` 时先逐项关闭返工，不夹带新范围。
- 给实现选择留出工程判断；对数据安全、协议兼容、候选身份和素材许可保持严格。
- 优先复用正式服务和 CLI，不直接改数据库或运行状态文件。
- 测试应覆盖用户可观察行为和关键失败路径，避免只复制实现逻辑。
- 没有有价值的改动时可以交付 `NO_CHANGE`。

## 交付

从 `docs/agent-workflow/templates/EXECUTION_REPORT.md` 创建本周期执行报告。冻结最终 HEAD，
运行适合范围的回归与治理检查，将状态改为 `READY_FOR_REVIEW`，提交全部记录后停止。

最终消息必须提供：周期、分支、完整 HEAD、执行报告、测试结果、候选身份（如有）和主控
需要重点复验的风险。

不得自行签收、合并 `main`、推送公共仓库或创建 Release。
