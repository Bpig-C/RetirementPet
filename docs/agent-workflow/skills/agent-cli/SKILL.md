---
name: retirement-pet-agent-cli
description: Inspect or operate a running RetirementPet instance through the versioned local Agent CLI while preserving protocol, data, and unattended-automation safety boundaries.
---

# RetirementPet Agent CLI

使用 CLI 前读取 `docs/AGENT_PROTOCOL.md`。CLI 只连接正在运行且支持
`retirement-pet.agent.v1` 的实例；`UNAVAILABLE` 不代表数据库损坏。

## 入口

源码工作树采用 `src/` 布局：

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m retirement_pet agent status get --json
```

打包候选：

```powershell
.\dist\RetirementPet\RetirementPet.exe agent status get --json
```

使用 `agent --help` 和各域 `--help` 获取精确参数。自动任务优先使用 `--json`，检查
进程退出码以及响应的 `ok`、`code`、`message`、`generation`，不能只解析展示文字。

## 自动化权限

默认允许的只读操作：

- `status get`
- `todo list`、`todo get`
- `action list`、`action status`
- `character list`、`character current`
- `media status`

执行者可以在当前工单明确需要时使用可逆 Todo 写操作。无人值守时禁止：

- `todo delete`；
- 切换角色；
- 触发动作或控制媒体；
- 退出、重启或强杀桌宠；
- 绕过协议直接写 `tasks.db`。

协议写操作使用幂等键和 `expected_generation` 时，冲突必须重新读取事实后再决定，不能
盲目重放。命令超时或玩家拒绝应作为真实结果记录。

## CLI 不可用

先确认调用的是带 Agent 协议的运行版本。若旧版本正在运行，不得为了巡检强制替换它。
在用户授权的调查中，可以 SQLite `mode=ro` 读取数据库并运行 `quick_check`；任何修改都
必须等待正式 CLI/UI 能力或用户决定。

