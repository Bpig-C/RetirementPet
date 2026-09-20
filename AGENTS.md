# RetirementPet Agent 入口

本仓库使用异步的“执行者—主控”工作流。不同会话不能互相发消息，
因此 Git 提交和 `docs/agent-workflow/` 下的记录是唯一交接通道。

## 启动顺序

1. 读取[当前状态](docs/agent-workflow/CURRENT_STATE.md)。
2. 读取[共享周期协调 Skill](docs/agent-workflow/skills/cycle-coordinator/SKILL.md)和
   [工作流](docs/agent-workflow/WORKFLOW.md)。
3. 按任务明确指定的角色读取对应 Skill：
   - 执行者：[执行者 Skill](docs/agent-workflow/skills/implementer/SKILL.md)
   - 主控审阅者：[主控 Skill](docs/agent-workflow/skills/controller/SKILL.md)
4. 需要操作运行中的桌宠时，再读取
   [Agent CLI Skill](docs/agent-workflow/skills/agent-cli/SKILL.md)。

任务提示必须明确写出“执行者”或“主控审阅者”。角色不明确时只能读取现状、
报告阻塞，不得修改生产代码、切换角色、操作媒体或改变 Todo。

## 共同约束

- 不把对话记忆当成项目事实；以当前 Git、状态文档和可复验证据为准。
- 不直接写 `%APPDATA%/RetirementPet/tasks.db`。在线修改必须走正式 UI、服务或 Agent CLI。
- 不覆盖已有 PetPack 版本；新公开素材使用新版本并保留来源与许可记录。
- 罗小黑素材与构建产物保持本地，不进入公开快照或公开发行。
- 不自动推送公共仓库或创建 GitHub Release。
- 未发现有证据支持的改进时，允许以“本周期无代码变更”结束。

