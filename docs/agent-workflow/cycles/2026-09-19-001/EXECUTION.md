# 2026-09-19-001 执行报告

> 本文件的首个 YAML 头是本周期唯一的机器身份入口；后续轮次以追加章节
> 记录，身份字段由登记提交回写，与 CURRENT_STATE 保持一致。

```yaml
cycle_id: 2026-09-19-001
role: implementer
status: READY_FOR_REVIEW
base_commit: 29273eb22d0dec8bbb5386ea717cbc27f026069b
implementation_commit: 3fce463e5f63eb7f4735b0668d8278cd2c316bcd
handoff_head: e440f05130f5de9f4798feb39cec44f5192f94bd
branch: automation/2026-09-19-001
started_at: 2026-09-19T23:31:46+08:00
finished_at: 2026-09-20T13:11:24+08:00
candidate_commit: null
```

## 输入与范围

读取了 `AGENTS.md`、`WORKFLOW.md`、`CURRENT_STATE.md`、本周期 `BRIEF.md`，
需求输入采用 BRIEF 已固化的 2026-09-19 Todo 快照（"宠物优化"树），未对日用
实例做任何在线写操作。按 BRIEF 调查优先级第 1 项实施：

**做**（V131-A：Todo 元数据写能力）：

- 协议新增 5 个 allowlist 操作：`todo.set_importance`、`todo.set_urgency`、
  `todo.set_horizon`、`todo.set_due_date`、`todo.move_within_siblings`；
- CLI 新增 5 个动词：`todo importance-set / urgency-set / horizon-set /
  due-set / move`（均为 mutation，支持幂等键与 generation）；
- 关联修复：文档图可达性基线失败（见调查结论 2）。

**不做**（明确留待后续周期）：Markdown 原生编辑（BRIEF 优先级 3，需独立
技术切片）、重要/紧急显示层级、角色动画素材、媒体联动用户验收。理由：单
节点可独立验收的交付纪律；本节点是 BRIEF 指明的"后续自动任务安全维护
Todo 的基础设施"。

## 调查结论

1. **已复现缺口**：协议 allowlist 此前只有 quadrant 筛选（读），没有写入口；
   service 层 `set_importance/set_urgency/set_horizon/set_due_date/
   move_within_siblings` 全部现成，本次只做协议/CLI 暴露，未改 service 语义。
2. **基线缺陷（本周期顺带修复）**：引导提交（8fab0ad/29273eb）新增的
   `AGENTS.md` 与 `docs/agent-workflow/skills|templates|cycles` 未接入文档图，
   `test_every_markdown_document_is_reachable_from_document_hub` 在基线上
   即失败，与本实现无关但阻碍绿色回归。
3. **语义确认**：quadrant 筛选把"未设置"与 low 同样视为非 high；本实现
   写入的是显式 high/low/null，null 即回到未分类，与该筛选语义闭环。

## 实现

- `src/retirement_pet/agent_protocol.py`：新增 `_require_int`/`_level_arg`
  助手与 5 个 handler；写操作走既有 `_todo()` 守卫与 `_call()` 错误映射，
  返回更新后的任务摘要。
- `src/retirement_pet/agent_cli.py`：5 个动词；`none` 值映射为协议 `null`
  （清除）；`move --delta` 为整数，越界由 service 安全 no-op。
- `docs/AGENT_PROTOCOL.md`：allowlist 表补 5 行，标注 1.3.x 追加。
- `tests/test_agent_protocol.py`：新增 3 个进程间往返测试（象限轴写入/
  清除/校验/NOT_FOUND/generation 递增；horizon 与 due 的设置/清除/非法值；
  同级移动排序/越界 no-op/非法 delta）；**allowlist 治理测试升级为精确
  快照**（新增操作必须同步更新该测试与协议文档），原 `"import"` 子串禁令
  改为整词动词匹配——`set_importance` 是领域词汇，子串匹配属误伤。
- `tests/test_agent_cli.py`：新增 CLI 元数据写入与移动端到端测试（含
  exit 2 用法错误与 exit 1 协议错误两条反例）。
- `tests/test_documentation_links.py` + `docs/README.md` +
  `docs/agent-workflow/README.md`（新增子索引）：常设工作流文档入图；
  `cycles/` 周期记录定义为经 CURRENT_STATE 寻址的追加式工件，排除出静态
  文档图（否则每个新周期都会打破该测试）。

## 验证

- 目标测试：`QT_QPA_PLATFORM=offscreen python -I -B -m pytest -p
  no:cacheprovider tests/test_agent_protocol.py tests/test_agent_cli.py`
  → 60 passed。
- 全量回归（同一 offscreen 命令，`tests`）：**1316 passed / 13 skipped /
  0 failed / 0 errors**（含本轮新增 4 个测试）。原始输出存
  `.release/cycle-2026-09-19-001-test-full.txt`（私有，不提交）。
  注意：shell 10 分钟超时截断了最后一行统计；进度区完整到达 100% 且无
  F/E 字符，以上数字由进度字符统计得出，主控可重跑复核。
- Windows 原生层（`RP_RUN_NATIVE=1`）：未运行。本轮改动不触及 HWND/
  热键/注册表层，记 SKIP 而非假设通过。
- 未构建候选、未运行 go_no_go（无版本收敛，见下）。

## 数据、兼容与回滚

- 未读写用户 `tasks.db`；未触碰运行中的桌宠实例（无退出/重启/强杀）。
  运行中的 1.3.0 dist 实例**不包含**新操作，直到未来从含本分支的提交
  构建新候选并由用户更新。
- 协议为纯增量：`retirement-pet.agent.v1` 版本不变，旧客户端不受影响；
  新操作对旧实例返回 `UNKNOWN_OPERATION`（结构化错误）。
- 回滚：丢弃 `automation/2026-09-19-001` 分支即可，无数据迁移、无 schema
  变更、无设置变更。

## 候选与公开边界

- 无候选（`candidate_commit: null`）。按治理规则，小版本号只在已验收变化
  形成清晰用户价值后收敛；建议本节点并入下一次 1.3.1 候选。
- 公开快照影响：仅源码与文档，无素材/第三方 IP/本地路径新增；
  `.release` 证据与测试输出保持私有不提交。

## 遗留与主控重点

1. **治理裁定点 A**：allowlist 治理测试从"危险子串扫描"升级为"精确快照 +
   整词 import 禁令"。请主控确认该策略（快照使能力面变更必须过评审可见）。
2. **治理裁定点 B**：`cycles/` 排除出静态文档图（docs/README.md 文档治理
   段已同步声明）。请主控确认该例外。
3. 全量回归统计行因 shell 超时截断，数字来自进度字符统计（方法与文件已
   注明）；建议主控重跑取一行式结果。
4. Todo 中"CLI 操控能力补全：协议写操作缺口"仍为开放：本节点交付了其中
   四象限/horizon/due/排序；跨父移动、角色包安装、设置读写、优雅退出
   按 BRIEF 分级为更大范围，未实现。待用户验收后可在 UI/CLI 关闭或改写
   该 Todo。
5. 建议下一周期：Markdown 备注最小闭环（BRIEF 优先级 3，先调查 Qt 能力
   与安全边界再切片）。
6. 同机残留说明：本周期未发现本仓库相关残留进程；`.release` 下留有测试
   输出与本周期维护脚本快照。

## R2 返工响应（2026-09-20）

轮次身份（历史，见上方的当前权威头）：触发 REVIEW.md CR-001 至 CR-005；
实现提交 `7f1077cf548985b8dead1cdbc3943cdd5fa3fe4d`，交接提交
`39c4e6f944535d6b2601ea9f314d0d191126f0ae`。

| 编号 | 处置 |
|---|---|
| CR-001 | `WORKFLOW.md` 第 4 节固化交接身份契约：`implementation_head`（交付链最后提交）与 `handoff_head`（冻结状态的交接提交，哈希由其后的登记提交写入）。新增 `tests/test_agent_workflow_governance.py`：状态为 `READY_FOR_REVIEW` 时强制两字段存在、完整 40 位、可解析、均为当前分支 HEAD 祖先且前者是后者祖先；并对缺失、非 40 位、不可解析、可解析但非祖先（用 `git commit-tree` 造悬挂提交）四种坏形态做失败关闭测试。执行报告模板 yaml 以 `implementation_commit` + `handoff_head` 取代 `head_commit`。 |
| CR-002 | 新增 `docs/agent-workflow/cycles/README.md` 周期索引：逐周期登记并链接 BRIEF/EXECUTION/REVIEW（本周期三份记录均已链入），从工作流子索引可达；`tests/test_documentation_links.py` 撤销对 `cycles/` 的整体排除，未登记的孤儿周期文档重新触发治理失败；`docs/README.md` 文档治理段同步改写。 |
| CR-003 | 新增 `_parse_iso_date`：解析前强制 `^\d{4}-\d{2}-\d{2}$`，`todo.add` 与 `todo.set_due_date` 两条写路径共用。协议反例测试参数化覆盖主控原始反例 `"20260920"` 与 `"2026-W38-7"`（两路径均 `INVALID_ARGS`），CLI `due-set`/`add --due` 反例断言 exit 1。 |
| CR-004 | 全量回归以 README 规范命令（`-I -B -m pytest -p no:cacheprovider tests`，addopts 提供单个 `-q`）后台完整运行，正式汇总行：**1319 passed, 14 skipped, 3 warnings in 420.40s**，exit 0。主控观察到的既有导入超时节点本次未复现。根因说明：此前两轮缺汇总行是执行者在 README 命令外额外加了显式 `-q`，与 addopts 叠加成 `-qq`（pytest 9 超静默模式省略汇总行），非环境缺陷。 |
| CR-005 | 删除 `_level_arg` 末尾重复 `return value`。 |

### R2 验证

- 目标测试（protocol / cli / doc-links / governance）：**71 passed, 1
  skipped**——跳过项即治理测试对真实 CURRENT_STATE 的断言，按设计仅在
  `READY_FOR_REVIEW` 时生效（本轮运行时状态为 `REWORK_REQUIRED`）。
- 全量：`1319 passed, 14 skipped, 3 warnings in 420.40s`，exit 0。相对 R1
  的 +3 passed / +1 skipped 与新增测试数吻合（2 个日期反例参数化用例、
  1 个治理校验器测试、1 个按设计跳过的真实状态断言）。
- 原始输出：`.release/cycle-2026-09-19-001-test-full-r2.txt`（私有）。
- 未夹带新范围；未构建候选；版本号未动；未触碰运行中的桌宠与用户数据。

## R4 主控直接完善（2026-09-20）

本轮依据用户授权，由主控直接收口 R3 剩余的两项低风险治理问题；没有修改生产代码、
产品行为、用户数据、版本或候选。实现提交为
`3fce463e5f63eb7f4735b0668d8278cd2c316bcd`，交接哈希由随后的登记提交回填到本文件
唯一机器头。

| 编号 | 处置 |
|---|---|
| RR3-01 | 登记信封从整个 `docs/agent-workflow/` 目录收紧为两个精确文件：`CURRENT_STATE.md` 与其中指向的本周期 execution report。报告路径必须符合周期执行报告的安全相对路径格式，登记提交必须实际更新 CURRENT_STATE。新增规则改写反例，同时改写 WORKFLOW 与 controller Skill 时稳定拒绝。 |
| RR3-02 | 公开快照只跳过需要内部 Git 对象的 registration/ancestry 集成检查；报告—状态一致性继续运行。 |
| 流程完善 | WORKFLOW、controller Skill 和调度提示明确“执行者交初稿、主控直接完善低风险收尾”的边界；产品行为、数据/schema、协议语义、权限隐私、素材许可、迁移、候选身份与较大代码改动仍退回执行者或另行独立验收。 |

### R4 登记前验证

- `tests/test_agent_workflow_governance.py` + `tests/test_documentation_links.py`：
  **8 passed / 2 skipped**。两个 SKIP 是真实状态尚为 `REWORK_REQUIRED` 时的状态门，
  将在登记为 `READY_FOR_REVIEW` 后重新执行。
- `git diff --check` 通过。
- 未构建候选；未操作运行中的桌宠；未读写用户 Todo 数据库。

## R3 返工响应（2026-09-20）

轮次身份：触发 REVIEW.md RR2-01 至 RR2-04；实现提交见上方权威头的
`implementation_commit`（`efb7a24e99979746b3be772c6d8207deb4e1752f`）。

| 编号 | 处置 |
|---|---|
| RR2-01 | 治理测试改为注入式 Git 查询 + 隔离临时仓库：坏身份反例自带本地身份（`git -c user.name=governance-probe`），不再读取调用者全局 Git 配置，也不含任何硬编码内部提交哈希；公开快照以根目录 `PUBLIC_SNAPSHOT_MANIFEST.json` 为确定标记，ancestry 集成检查显式 SKIP，纯校验器反例仍运行。 |
| RR2-02 | 固化登记信封：READY_FOR_REVIEW 时要求 HEAD 的直接父提交恰为 `handoff_head`（尾部恰一个登记提交），且 `handoff_head..HEAD` 只触碰 `docs/agent-workflow/` 下路径；新增隔离仓库反例：纯文档登记通过、登记夹带 `src/` 失败、尾部多一个提交失败。WORKFLOW §4 同步为精确契约（`implementation_head` 不包含报告提交，已修正 R2 版本的错误表述）。 |
| RR2-03 | 执行报告首个 YAML 头迁移为模板新字段（`implementation_commit`/`handoff_head`），R2 轮次身份降为普通文字，本文件自此只有一个机器身份入口；新增一致性测试：READY_FOR_REVIEW 时报告头与 CURRENT_STATE 的 implementation/handoff 必须相同（报告路径从 CURRENT_STATE 解析，不硬编码周期号）。 |
| RR2-04 | `updated_at`/`finished_at` 均取自实际系统时钟；目标测试计数以登记态 pytest 正式汇总行为准（完整命令见下节，数字由登记提交回填），不再手工推算。 |

### R3 验证

在最终 `READY_FOR_REVIEW` 登记态（登记提交即分支 HEAD）执行：

- 治理门单独复跑：`pytest -p no:cacheprovider
  tests/test_agent_workflow_governance.py` → **7 passed**（真实登记信封与
  报告一致性检查均生效且通过）。
- 目标测试：`QT_QPA_PLATFORM=offscreen python -I -B -m pytest -p
  no:cacheprovider tests/test_agent_protocol.py tests/test_agent_cli.py
  tests/test_documentation_links.py tests/test_agent_workflow_governance.py`
  → **71 passed in 92.23s**。
- 全量回归：`QT_QPA_PLATFORM=offscreen python -I -B -m pytest -p
  no:cacheprovider tests` → **1325 passed, 13 skipped, 3 warnings in
  411.81s**，exit 0。原始输出 `.release/cycle-2026-09-19-001-test-full-r3.txt`
  （私有）。
- 公开快照（RR2-01 验收）：从登记 HEAD 导出 `ACCEPTED`（320 文件、公开提交
  `7e81cc8a…`），快照内运行 governance + doc-links → **7 passed, 2 skipped,
  0 failed**（跳过项为快照标记触发的两个 ancestry 集成检查，纯校验器反例
  与文档图检查全部通过）。
- 未夹带新范围；未构建候选；版本号未动；未触碰运行中的桌宠与用户数据。
