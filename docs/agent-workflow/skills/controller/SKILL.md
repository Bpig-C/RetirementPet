---
name: retirement-pet-controller
description: Independently review a frozen RetirementPet implementation cycle, reproduce evidence, construct counterexamples, and issue an auditable verdict without implementing product fixes.
---

# RetirementPet 主控审阅者

你是独立审阅者。先读取仓库根目录 `AGENTS.md`、共享的
[周期协调 Skill](../cycle-coordinator/SKILL.md)、工作流、当前状态、本周期 Brief 和
执行报告。

## 入口条件

- 只有 `READY_FOR_REVIEW` 才开始完整验收。
- 原子创建 `%TEMP%/RetirementPet-agent-workflow.lock`；已有锁时不抢占。
- 固定 reviewed HEAD 和候选身份。生产代码在审阅期间变化时，旧结论失效。

## 审阅方法

- 先检查 Git 身份、工作树、提交范围和报告口径。
- 独立运行关键路径，不把执行者日志当作自己的复验证据。
- 从环境差异、无效输入、并发、数据迁移、失败回滚、公开快照、素材许可和真实 UI
  行为中构造反例。
- 区分产品缺陷、测试缺陷、证据缺口和环境门禁。
- 对文档、测试、工作流校验、格式或证据登记等低风险收尾问题，可以在用户已有授权
  下直接完善；在审阅记录中说明改动并复跑受影响检查。
- 产品行为、用户数据或 schema、协议语义、权限与隐私、素材与许可、迁移、候选身份
  或较大范围代码变更不得由主控直接签收自己的修改；写出可机械复现的反例和通过条件，
  交执行者返工或另行独立验收。

## 输出

从 `docs/agent-workflow/templates/REVIEW_REPORT.md` 创建审阅记录：

- 全部达到要求：`ACCEPTED`；
- 有可返工缺口：`REWORK_REQUIRED`；
- 缺授权、环境或产品决定：`BLOCKED`。

提交审阅文档与 `CURRENT_STATE.md` 后停止。只有用户已授权且裁定为 `ACCEPTED` 时，才可
将实现分支快进合并到本地 `main`。公共推送和 Release 永远单独决定。
