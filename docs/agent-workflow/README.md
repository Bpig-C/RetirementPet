# 异步 Agent 工作流索引

> 受 [文档中心](../../README.md)管理的子索引。
> 本目录的常设文档与[周期记录索引](cycles/README.md)都从本页可达。

异步开发流程把"执行者—主控—用户"的角色分工、状态机和交接规则固化在仓库内，
Git 提交与这些文档是跨会话的唯一通信通道。

## 常设文档

| 文档 | 职责 |
|---|---|
| [仓库 Agent 入口](../../AGENTS.md) | 所有 Agent 会话的第一入口：启动顺序、共同约束与角色规则 |
| [异步开发与主控验收流程](WORKFLOW.md) | 角色定义、状态机、周期目录、同机互斥与发布边界 |
| [当前异步开发状态](CURRENT_STATE.md) | 当前周期、基线提交、下一行动者与交接文档入口 |
| [调度任务提示模板](SCHEDULE_PROMPTS.md) | 首次执行、后续重复运行与主控审阅的无上下文提示词 |
| [共享周期协调 Skill](skills/cycle-coordinator/SKILL.md) | 执行者与主控共用的状态路由、异步交接、直接完善边界和定时跟进规则 |
| [执行者 Skill](skills/implementer/SKILL.md) | 执行者的开始、工作原则与交付要求 |
| [主控审阅者 Skill](skills/controller/SKILL.md) | 主控的验收流程、复现要求与裁定边界 |
| [Agent CLI 操作 Skill](skills/agent-cli/SKILL.md) | 操作运行中桌宠的 CLI 用法与安全边界 |
| [执行报告模板](templates/EXECUTION_REPORT.md) | 每周期 EXECUTION.md 的起始结构 |
| [审阅报告模板](templates/REVIEW_REPORT.md) | 每周期 REVIEW.md 的起始结构 |

## 周期记录

每个周期使用 `cycles/<YYYY-MM-DD-NNN>/` 目录，包含 BRIEF（需求与边界）、
EXECUTION（执行交付）和 REVIEW（主控裁定）。这些记录按周期追加、不覆盖，
逐周期登记在[周期记录索引](cycles/README.md)中；当前活跃周期另以
[CURRENT_STATE.md](CURRENT_STATE.md) 中的路径为准。
