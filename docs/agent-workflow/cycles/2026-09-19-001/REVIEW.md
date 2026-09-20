# 2026-09-19-001 主控审阅报告

```yaml
cycle_id: 2026-09-19-001
role: controller
verdict: REWORK_REQUIRED
reviewed_head: 4cdc163fbf223da2cdad20377e003dd80d54550b
reviewed_candidate: null
started_at: 2026-09-20T10:45:51+08:00
finished_at: 2026-09-20T11:10:35+08:00
```

## 身份与范围

- 审阅分支：`automation/2026-09-19-001`。
- 实现提交：`7633e367bd10814ead84fca867973a5c85ca302c`。
- 实际冻结交接 HEAD：`4cdc163fbf223da2cdad20377e003dd80d54550b`。
- 基线：`29273eb22d0dec8bbb5386ea717cbc27f026069b`，为实际 `main` HEAD，且是
  审阅 HEAD 的 merge-base。
- 本周期未构建候选；1.3.0 的旧 Alpha 候选不继承本周期结论。

功能审阅覆盖 Todo 重要/紧急轴、期限、截止日期、同级排序的协议与 CLI 增量，
以及执行者同时修改的 allowlist 和文档图治理。未操作运行中的桌宠，未读写用户
`tasks.db`，未执行原生桌面门禁。

执行报告把 `head_commit` 写成实现提交，而实际 READY_FOR_REVIEW 交接 HEAD 是后续
提交；`handoff_commit` 又是自然语言占位。功能差异本身可以审阅，但冻结身份未达到
跨会话机器可读要求，见 CR-001。

## 独立复验

1. Git 身份：`git merge-base main HEAD` 为 `29273eb...`；工作树在审阅前干净；
   `git diff --check 29273eb..4cdc163` 通过。
2. 目标测试：
   `pytest tests/test_agent_protocol.py tests/test_agent_cli.py
   tests/test_documentation_links.py`，隔离 basetemp，结果 **62 passed**。
3. 全量回归：共收集 1329 项。第一次运行到 100%、exit 0，进度中为 16 个 SKIP；
   第二次为 **1312 passed / 16 skipped / 1 failed**。唯一失败是既有
   `test_nonreturning_import_times_out_and_releases_child` 等待子进程标记超时；随后用
   两个独立 basetemp 连续复跑该节点，均为 **1 passed**。因此没有证据把该偶发失败
   归因于本周期代码，但本机全量结果也不能写成稳定的一次性全绿。
4. 执行者原始日志复核：日志到达 100%，无 `F/E`，其中 13 个 `s`；结合当前收集
   总数 1329，可得到其报告中的 1316/13。该方法透明但仍缺 pytest 正式汇总行。
5. 公开快照：从冻结 HEAD 实测导出 **ACCEPTED**，包含 317 个文件、排除 408 个
   私有文件，公开快照提交 `281da706...`；没有新增隐私或罗小黑公开边界问题。
6. 协议反例：通过真实 `AgentProtocolServer` 发送 `due_date="20260920"` 和
   `due_date="2026-W38-7"`，两者都返回 `OK`，而 CLI metavar 与错误文本承诺
   `YYYY-MM-DD`。反例测试期望 `INVALID_ARGS` 时稳定失败 2 项。
7. 代码路径复核：五个新 handler 均经过统一 `_check_expected_generation` 和请求指纹
   幂等缓存；同 key 异参数仍冲突。`move_within_siblings` 的大幅越界由既有 service
   返回 no-op，排序与 generation 不被改变，和 UI 现有语义一致。

## 发现

| 编号 | 严重度 | 结论 | 复现与要求 |
| --- | --- | --- | --- |
| CR-001 | P1 | 冻结交接身份不是机器可读的唯一提交 | `CURRENT_STATE.implementation_head` 和 `EXECUTION.head_commit` 都是 `7633e36`，实际 READY_FOR_REVIEW HEAD 是 `4cdc163`，`handoff_commit` 只是说明文字。返工时在状态、执行报告及模板中区分 `implementation_commit` 与完整 40 位 `handoff_head`；交接前冻结后者，后续主控只审阅该值。增加治理测试，拒绝 READY_FOR_REVIEW 时缺失、非 40 位或不可解析/非当前实现分支祖先的 handoff HEAD。 |
| CR-002 | P1 | `cycles/` 被整体移出文档图，会让历史交接在下一周期失去入口 | 当前子索引没有链接 `2026-09-19-001`，而 CURRENT_STATE 只能指向一个活跃周期。创建受常设索引链接的周期索引，并逐周期链接 BRIEF/EXECUTION/REVIEW；撤销对整个 `cycles/` 的无条件排除，让新增孤儿周期文档重新触发治理失败。允许用生成式索引，但结果必须随 Git 提交、可离线阅读。治理裁定点 B 不接受。 |
| CR-003 | P2 | 截止日期输入契约比实现更窄 | `date.fromisoformat` 还接受 basic ISO 与 week-date。若产品契约为现有文案的 `YYYY-MM-DD`，应在解析前做精确格式校验，并新增上述两个协议反例及 CLI 反例；若决定接受全部 Python ISO 变体，则统一改协议文档、CLI metavar/帮助和错误文本。优先保持简单的 `YYYY-MM-DD` 契约。 |
| CR-004 | P2 | 全量证据没有正式汇总行，且主控观察到一次既有超时抖动 | 返工后的最终 HEAD 应让 pytest 自己完成并保存汇总行；不要再只按进度字符推算。若导入超时节点再次偶发失败，记录完整失败与隔离复跑结果并建立独立后续项，本周期无需改动无关产品代码。 |
| CR-005 | P3 | `_level_arg` 有重复不可达 `return value` | 删除 `src/retirement_pet/agent_protocol.py:163` 的重复行，并保持 `git diff --check`/目标测试通过。 |

## 裁定

**REWORK_REQUIRED**。

功能主体没有发现数据破坏、越权写入或幂等/代际绕过；治理裁定点 A（精确 allowlist
快照并把 `import` 限定为完整动词）接受，移动越界 no-op 语义接受。阻止签收的是两项
P1 交接治理缺口：冻结 HEAD 不可机器校验，以及周期历史被整体排除出可达性治理。

执行者下一轮先关闭 CR-001、CR-002；同一返工中应顺手关闭局部的 CR-003、CR-005，
然后按 CR-004 产生正式测试汇总。不得夹带 Markdown、动画、显示层级或媒体验收等新
范围，也不得构建候选或修改版本号。

## 下一状态

- `CURRENT_STATE.status`：`REWORK_REQUIRED`。
- `next_actor`：`implementer`。
- 本地合并：**不允许**，待同周期返工并重新进入 READY_FOR_REVIEW 后复验。
- 公共推送与 Release：不在本周期授权范围。

## R2 主控复验（2026-09-20）

```yaml
round: R2
role: controller
verdict: REWORK_REQUIRED
reviewed_implementation: 7f1077cf548985b8dead1cdbc3943cdd5fa3fe4d
reviewed_handoff: 39c4e6f944535d6b2601ea9f314d0d191126f0ae
reviewed_branch_head: 6866d8bc8245590a090541acfabcfe1d30568c40
reviewed_candidate: null
started_at: 2026-09-20T12:31:03+08:00
finished_at: 2026-09-20T12:35:03+08:00
```

### 已关闭项

- **CR-002 关闭**：周期索引已从常设文档入口可达。主控临时加入一个未登记周期
  Markdown 文件后，可达性测试准确失败并点名该文件；删除探针后工作树恢复干净。
- **CR-003 关闭**：协议的两个宽松 ISO 反例和 CLI 路径独立复验为 **3 passed**，
  `20260920`、`2026-W38-7` 均不再被接受。
- **CR-005 关闭**：重复不可达 `return value` 已删除，`git diff --check` 通过。
- **CR-004 的原始结果可信**：私有日志包含 pytest 正式汇总行
  `1319 passed, 14 skipped, 3 warnings in 420.40s`。该次全量发生在状态仍为
  `REWORK_REQUIRED` 时，真实状态治理项按设计 SKIP；因此它证明产品回归通过，但不能
  代替最终 `READY_FOR_REVIEW` 登记态的治理复验。

### 独立反例

1. 当前最终登记态目标集合收集 66 项；主控运行 protocol/CLI/doc-links/governance
   得到 **65 passed / 1 failed**。失败节点为
   `test_handoff_validator_fails_closed_on_bad_identities`：`git commit-tree` 依赖调用环境的
   Git 作者身份；主控会话没有 Builder 的临时身份，命令输出为空，测试失败。
2. 从分支 HEAD `6866d8b` 导出公开快照为 **ACCEPTED**，但在该快照运行治理与文档
   测试得到 **2 failed / 2 passed**。公开快照本身也是 Git 仓库，`.git.exists()` 为真；
   单根公开提交不含内部 `7f1077c`/`39c4e6f` 对象，真实状态校验因此报两个
   `unresolvable`，悬挂提交反例同样因硬编码内部 anchor 失败。
3. `handoff_head` 到实际分支 HEAD 之间当前确实只有一个登记提交，且只修改
   `CURRENT_STATE.md` 与 `EXECUTION.md`；但校验器只检查 ancestry，不检查提交数量或
   路径。若登记提交同时修改 `src/`，现有 `_handoff_problems(fields)` 仍会返回空，
   主控按 `handoff_head` 审阅就会漏掉生产变化。
4. `EXECUTION.md` 的第一个机器头仍是 R1 的 `head_commit: 7633e36` 和自然语言
   `handoff_commit`；新的双哈希只出现在第二个 YAML 块。读取第一块的通用工具仍会得到
   旧身份，CR-001 要求的“执行报告机器可读唯一身份”尚未完成。
5. `CURRENT_STATE.updated_at` 写为 `2026-09-20T19:55:00+08:00`，但登记提交时间是
   `11:39:42+08:00`，主控复验时系统时间为 `12:34:13+08:00`。该时间戳在未来，不能
   作为可靠交接证据。

### R2 发现

| 编号 | 严重度 | 结论 | 复现与通过条件 |
| --- | --- | --- | --- |
| RR2-01 | P1 | 治理门在主控环境与公开快照中失败 | 移除对调用者全局 Git 身份和硬编码内部提交的依赖。坏身份单测应使用纯函数/注入式 Git 查询，或在隔离临时仓库显式设置本地身份。用 `PUBLIC_SNAPSHOT_MANIFEST.json` 等确定标记区分单根公开快照：内部 ancestry 集成检查应明确 SKIP，纯校验器反例仍应运行。要求内部最终登记态目标测试全过，且新导出的公开快照中 governance + doc-links **0 failed**。 |
| RR2-02 | P1 | 登记提交之后可夹带未审阅生产变化 | 固化“交接内容 + 登记信封”契约：READY_FOR_REVIEW 时应验证当前 HEAD 的直接父提交就是 `handoff_head`、尾部恰有一个登记提交，并对 `handoff_head..HEAD` 使用精确路径 allowlist（至少禁止 `src/`、`tests/`、素材、构建与配置变化）。新增隔离仓库反例：纯登记文档通过；登记提交夹带 `src/`、多出一个尾部提交均失败。主控报告同时记录 handoff 与登记 HEAD。 |
| RR2-03 | P2 | 执行报告仍有两个相互冲突的机器头 | 将当前周期的首个 YAML 头迁移为模板新字段，或新建 `EXECUTION-R2.md` 并让 CURRENT_STATE 指向它。只能有一个权威身份头；增加一致性测试，要求报告的 implementation/handoff 与 CURRENT_STATE 相同。同步修正 WORKFLOW 中“implementation_head 包含报告”的表述：当前实际 `7f1077c` 不包含 R2 报告提交。 |
| RR2-04 | P2 | 时间与目标测试口径不可靠 | `updated_at` 必须来自实际系统时间且不晚于登记提交/审阅时刻；目标测试报告声称 71+1，但当前所列四个文件只收集 66 项，应写出完整命令并以 pytest 汇总为准。返工后的正式验证必须在最终 READY_FOR_REVIEW 登记态执行。 |

### R2 裁定

**REWORK_REQUIRED**。CR-002、CR-003、CR-004、CR-005 保持关闭；Todo 元数据写能力
不需要重做。CR-001 的方案方向正确，但目前同时存在主控环境失败、公开快照失败和登记
尾部未受约束三项缺口，尚不能作为后续每日自动化的可信交接门。

执行者仅处理 RR2-01 至 RR2-04，不夹带新产品范围、不构建候选、不修改版本号。
本地 `main` 暂不合并。

## R3 主控复验（2026-09-20）

```yaml
round: R3
role: controller
verdict: REWORK_REQUIRED
reviewed_implementation: efb7a24e99979746b3be772c6d8207deb4e1752f
reviewed_handoff: a8ddb0e674e9ff91ebc95b805c721d393f4f8aa8
reviewed_branch_head: 8be69dc2415867cadf6210ade901134b80e07d97
reviewed_candidate: null
started_at: 2026-09-20T14:05:08+08:00
finished_at: 2026-09-20T14:08:44+08:00
```

### 已通过部分

- 双哈希可解析，`implementation_head` 是 `handoff_head` 祖先；实际登记 HEAD 的
  直接父提交等于 handoff，实际尾部仅修改 CURRENT_STATE 与 EXECUTION。
- 执行报告现在只有一个 YAML 机器头，implementation/handoff 与 CURRENT_STATE
  一致。主控内部目标集合独立复跑为 **71 passed**。
- R3 全量原始日志存在正式汇总行：**1325 passed / 13 skipped / 3 warnings**；
  总数 1338 与收集结果一致。登记提交 amend 后生产树未变化，最终治理门已重跑，
  因此本周期不要求禁止 amend。
- 从最终登记 HEAD 独立导出公开快照 **ACCEPTED**（公开提交 `0192e7c...`）；
  快照 governance + doc-links 为 **7 passed / 2 skipped**，没有失败。
- 隔离临时仓库显式设置 Git 身份后，治理测试不再依赖主控全局配置；上一轮环境型
  `commit-tree` 失败已关闭。

### 新反例与发现

| 编号 | 严重度 | 结论 | 复现与通过条件 |
| --- | --- | --- | --- |
| RR3-01 | P1 | 登记路径白名单仍是宽泛目录前缀，允许在交接后改写审阅规则 | 主控在隔离仓库建立合法 implementation→handoff 后，登记提交同时修改 `CURRENT_STATE.md`、`docs/agent-workflow/skills/controller/SKILL.md` 和 `WORKFLOW.md`；直接父仍等于 handoff，现有 `_registration_problems` 返回空，反例稳定失败。把白名单收紧为**精确文件集合**：只允许 `docs/agent-workflow/CURRENT_STATE.md` 与 CURRENT_STATE 指向的本周期 execution report；其他工作流文件（BRIEF、WORKFLOW、Skills、模板、历史 REVIEW 等）一律拒绝。新增上述规则改写反例，并保留纯登记通过、`src/` 夹带失败、多尾提交失败三例。 |
| RR3-02 | P2 | 公开快照多跳过了不依赖 Git 对象的报告一致性检查 | `test_execution_report_identity_matches_current_state` 只读取并比较两个文档，不调用 ancestry，却因 SNAPSHOT_MARKER 直接 SKIP。公开快照应只跳过真正需要内部提交对象的 registration/ancestry 集成检查；报告—状态一致性与全部纯反例继续执行。按当前测试数量，修复后快照目标应为 **8 passed / 1 skipped / 0 failed**。 |

### R3 裁定

**REWORK_REQUIRED**。RR2-01 至 RR2-04 的主体方向已经成立，产品功能、周期索引、
严格日期与唯一报告头均不重做。只处理 RR3-01、RR3-02 两个治理边界；不新增产品范围，
不构建候选，不修改版本号。

关于执行者询问的 amend：**不禁止**。验收关心最终登记提交的完整哈希、直接父、精确
路径集合和最终治理复验；在这些条件都成立时，为回填证据而 amend 登记提交是可接受的。

## R4 主控直接完善与最终签收（2026-09-20）

```yaml
round: R4
role: controller
verdict: ACCEPTED
reviewed_implementation: 3fce463e5f63eb7f4735b0668d8278cd2c316bcd
reviewed_handoff: e440f05130f5de9f4798feb39cec44f5192f94bd
reviewed_branch_head: d10407fa06afce3268cff5a6af87a51267c6997a
reviewed_candidate: null
started_at: 2026-09-20T14:14:00+08:00
finished_at: 2026-09-20T14:23:25+08:00
```

### 直接完善范围

用户已授权主控直接处理不涉及实质产品行为的收尾问题。本轮只修改工作流文档、主控
Skill、调度提示和治理测试：登记提交精确限制为 CURRENT_STATE 与当前 execution
report；公开快照继续执行报告—状态一致性检查；同时把“执行者交初稿、主控完善低风险
收尾”的边界写入常设规则。没有修改 `src/`、用户数据、schema、协议语义、版本、素材、
许可或候选。

### 独立复验

- 最终登记态父提交：`d10407f^ = e440f05`，与 `handoff_head` 完全一致；
  `handoff..HEAD` 只包含 CURRENT_STATE 与本周期 EXECUTION 两个精确文件。
- 治理与文档图：**10 passed**。
- protocol / CLI / 文档图 / 治理四文件：项目锁定 `.venv` 下 **72 passed**。
- 一次误用系统 Anaconda Python 时，PySide6 QtCore 因该环境已知 DLL 损坏产生 62 个
  fixture setup error；改用 README 指定的项目 `.venv` 后同一集合 72/72 通过，不记为
  产品回归失败。
- 从登记 HEAD 导出公开快照：**ACCEPTED**，320 个公开文件、408 个排除文件，公开提交
  `8b66f2f66d5641f5854f94aa4bfb561eeb7cf7d1`；快照 governance + doc-links 为
  **9 passed / 1 skipped / 0 failed**。唯一 SKIP 是确实依赖内部 Git ancestry 的登记
  集成检查，报告一致性已在快照中实际通过。
- R3 全量产品回归 **1325 passed / 13 skipped** 仍适用于同一产品树；R4 没有生产代码
  变化，因此未重复运行七分钟全量套件。

### 裁定

**ACCEPTED**。RR3-01、RR3-02 关闭；周期 2026-09-19-001 的 Todo 元数据写能力和
异步交接治理可以进入后续本地合并或 1.3.1 汇总。没有生成新候选；1.3.0 Alpha 与
Production 的既有结论不变。公共推送和 Release 仍需用户另行决定。
