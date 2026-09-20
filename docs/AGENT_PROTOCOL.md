# RetirementPet 本地 Agent 协议（retirement-pet.agent.v1）

> 面向本地自动化工具（CLI Agent、脚本、编辑器集成）的进程间控制协议。
> 版本：`retirement-pet.agent.v1`（1.3.0 引入）。协议版本独立于应用版本、
> Todo schema 与 PetPack schema，各自单独演进。
>
> 安全边界：这是**同机本地**控制端点，复用单实例的按用户命名管道
> （`QLocalServer`）。它不是远程 API，没有认证语义；不要把它暴露到网络。

## 传输与帧格式

- 端点：运行中的 RetirementPet 主实例的本地服务器（与单实例守卫同一端点）。
- 请求：一行 UTF-8 JSON 对象，以 `\n` 结尾。
- 响应：恰好一行 UTF-8 JSON 对象，以 `\n` 结尾；连接可复用发送多条。
- 单行请求上限 256 KiB；单行响应上限 2 MiB；超限返回结构化错误并关闭连接。
- 半包由服务端缓冲重组；一条不完整行静置超过 10 秒会被服务端断开。
- 旧版 ASCII 命令（`show`/`quiet`/`panel`/`panel-close`）行为不变；首字节为
  `{` 的行进入本协议。

## 请求

```json
{"protocol":"retirement-pet.agent.v1","request_id":"...","operation":"status.get","args":{}}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `protocol` | 是 | 必须精确等于 `retirement-pet.agent.v1` |
| `request_id` | 是 | 非空字符串 ≤128 字符，响应原样回带 |
| `operation` | 是 | 见下方 allowlist |
| `args` | 否 | JSON 对象；默认 `{}` |
| `idempotency_key` | 否 | 字符串 ≤128；见幂等一节 |
| `expected_generation` | 否 | 非负整数；见乐观并发一节 |

未知顶层字段一律拒绝（`BAD_REQUEST`）。

## 响应

```json
{"protocol":"retirement-pet.agent.v1","request_id":"...","ok":true,"code":"OK","message":"ok","data":{},"generation":7}
```

- `ok`：布尔，true 表示操作成功。
- `code`：稳定结果码（见下）；成功恒为 `OK`。
- `message`：人读说明，内容不保证稳定，不要用它做分支。
- `data`：操作相关数据对象。
- `generation`：当前 Todo 数据代数（每次已提交变更 +1，进程内单调）。

## 结果码

| code | 含义 |
|---|---|
| `OK` | 成功 |
| `BAD_UTF8` / `BAD_JSON` / `BAD_REQUEST` | 帧或 schema 非法 |
| `UNKNOWN_PROTOCOL` | `protocol` 不是本版本 |
| `UNKNOWN_OPERATION` | operation 不在 allowlist |
| `INVALID_ARGS` | 参数缺失/类型/取值非法 |
| `NOT_FOUND` | 目标任务不存在 |
| `CONFLICT` | `expected_generation` 不匹配，或删除确认数量不符 |
| `UNAVAILABLE` | 应用未运行、Todo 存储降级或请求超时 |
| `REQUEST_TOO_LARGE` / `RESPONSE_TOO_LARGE` | 超出字节上限 |
| `INTERNAL` | 服务端内部错误（已捕获，不影响 UI） |

## 幂等（写操作安全重试）

携带 `idempotency_key` 的请求：服务端保留最近 64 个
`operation:key` 的响应；同键重发返回**字节相同**的首次响应。客户端超时后
用同一键重试不会重复创建/完成/删除。不携带键的请求按普通请求执行。

## 乐观并发（不覆盖 UI 中的用户编辑）

写请求可带 `expected_generation`：若与当前 `generation` 不等，返回
`CONFLICT` 且不执行。Agent 应读取最新 `generation` 后重试。

## 操作 allowlist（1.3.0 基础 + 1.3.x 追加写操作）

| operation | 参数 | 说明 |
|---|---|---|
| `status.get` | — | 应用与 Todo 概要（版本、可用性、generation、是否专注），不含任务正文 |
| `todo.add` | `title`, `horizon?`, `parent_id?`, `due_date?` | 新建任务，返回任务摘要 |
| `todo.get` | `task_id`, `include_note?` | 读取单个任务（可含备注） |
| `todo.list` | `parent_id?`, `status?`, `horizon?`, `archived?`, `limit?`（默认 200，≤1000）, `cursor?` | 分页列出任务摘要 |
| `todo.rename` | `task_id`, `title` | 重命名 |
| `todo.set_importance` | `task_id`, `importance`（必填 `high`\|`low`\|`null`，`null` 清除该轴） | 设置重要轴；与 `todo.list` 的 quadrant 筛选配套 |
| `todo.set_urgency` | `task_id`, `urgency`（必填 `high`\|`low`\|`null`，`null` 清除该轴） | 设置紧急轴 |
| `todo.set_horizon` | `task_id`, `horizon`（必填 `short`\|`medium`\|`long`） | 修改期限 |
| `todo.set_due_date` | `task_id`, `due_date`（必填 ISO 日期或 `null`，`null` 清除） | 修改截止日期 |
| `todo.move_within_siblings` | `task_id`, `delta`（必填整数，负数上移/正数下移） | 同级排序调整；越界为安全 no-op |
| `todo.note_set` | `task_id`, `note` | 保存备注原文；响应只确认，不回显全文 |
| `todo.complete` | `task_id`, `scope`（必填 `self`\|`subtree`） | 完成单项或整个子树 |
| `todo.restore` | `task_id`, `scope`（必填 `self`\|`subtree`） | 恢复单项或子树内全部已完成项 |
| `todo.archive` | `task_id` | 归档子树（可逆，日常主操作） |
| `todo.restore_archived` | `task_id` | 恢复已归档子树（含归档祖先/后代，一次事务） |
| `todo.delete` | `task_id`, `confirm_subtree_count`（必填，须与存储一致） | 物理删除子树 |
| `todo.focus_start` / `todo.focus_stop` | `task_id` / — | 开始/停止专注 |
| `action.list` | — | 全部动作及优先级与用户模式（auto/manual/disabled） |
| `action.status` | — | 当前动作、会议/勿扰事实 |
| `action.trigger` | `action` | 走正式触发链（能力→模式→会议→勿扰→冷却）；返回实际结果与拒绝原因 |
| `action.stop` | — | 结束用户表演（与 UI 待机同语义） |
| `character.list` | — | 已安装角色（含 builtin 标记与 revision 摘要） |
| `character.current` | — | 当前 ACTIVE 角色与安全模式状态 |
| `character.switch` | `character_id`, `package_id?` | 走 REQUEST→PREPARE→SWAP→COMMIT 正式切换；失败保留当前角色 |
| `media.status` | — | 系统媒体桥状态（低敏摘要：app 名/状态/控件能力，无歌名） |
| `media.play_pause` / `media.next` / `media.previous` | `session_id?` | 向所选系统媒体会话发控制命令；返回玩家真实接受结果。注意：命令最多阻塞调用 750ms，无应答播放器会得到 `player did not answer in time` |

任务摘要字段：`id`、`parent_id`、`title`、`horizon`、`status`、`importance`、
`urgency`、`archived`、`due_date`、`sort_key`、`created_at`、`updated_at`、
`completed_at`。备注只在 `todo.get` 带 `include_note:true` 时返回。

后续版本追加新域不破坏现有调用。任何版本的 allowlist 都不包含 SQL、文件、shell、动态导入或任意对象
访问能力。1.3.0 不要求 Agent 导入、卸载或确认第三方角色包——这些仍由 UI 承担。

## 隐私边界

- 应用普通日志只记录 operation、结果码和去标识 request id 摘要；任务标题、
  备注、媒体标题、路径不进入日志。
- `status.get` 不返回任务正文或个人路径。
- 所有写操作都进入应用进程，由正式 service 执行事务；Agent 永远不直接
  打开 `tasks.db` 或改写设置文件。应用未运行时写命令返回 `UNAVAILABLE`，
  不会启动第二套存储。

## CLI

命令行入口见 `python -m retirement_pet agent --help`（打包后
`RetirementPet.exe agent --help`）。CLI 是本协议的薄封装：`--json` 时 stdout
恰好输出一个响应 JSON 对象，诊断走 stderr；退出码
`0` 成功、`1` 操作失败（ok=false）、`2` 用法错误、`3` 无法连接运行中的
实例、`4` 响应不可解析。
