# 1.2.0 节点 A 与 V12-02 返工交付报告

> 日期：2026-09-11 · 角色：执行者（本地模型） · 交付性质：按
> [主控审阅反馈](V1_2_CONTROLLER_REVIEW_A.md) 完成返工后重新提请复验
> · 状态：**自测完成；三轮独立 Agent 验收全部通过（§4），待主控复验**
>
> 主提交：`e4480b4`（返工）、`a325b1a`（验收第二轮缺口修复），分支
> `impl/v1.2-daily-use`。本报告含内部提交号/分支名，仅存内部仓库，公开
> 导出前须脱敏。

## 0. 返工范围对照

| 审阅编号 | 内容 | 状态 |
|---|---|---|
| CR-A01 / P1 | 恢复输入被保留清理删除 | 已修复，含全部指定回归 |
| CR-A02 / P1 | 清理未验证备份归属 | 已修复，含外来/损坏/重解析边界回归 |
| CR-A03 / P1 | 恢复发布后异常状态分裂 | 已修复，禁写故障态 + 缓存重建回归 |
| CR-A04 / P2 | 隐藏与会话暂停无联合判断 | 已修复，联合判定 + 接线回归 |
| CR-P01 / P1 | publisher_ref 必填被全局放宽 | 已修复，精确钉定历史豁免 |
| CR-P02 / P1 | 追加激活事实缺失 | 已实现（裁定 P-3） |
| §3.1 | layered idle 结构可解析 ≠ 运行准入 | 已分层测试 |
| §3.2 | "回滚演练"命名失实 | 已分别命名 + 记录真实演练路径 |
| §3.3 | 能力宣称无消费证据 / Variant 未标注 | 已修注册表与对照表 |
| §3.4 | "四个在售包" / 脱敏边界 | 已改名并加公开边界说明 |
| A-1 口径 | "整店不可用"命名 | 已改为"待办模块暂不可用" |

## 1. 修复方案

### CR-A01（恢复输入保护）

- `restore_backup()` 在打开候选之前计算 `protected = {候选目录绝对路径}`，
  本次 pre-restore 副本与候选输入一并传入 `create_backup(..., protected=)`
  与 `prune_backups(protected=)`；清理候选显式排除 protected 集合。
- 保护穿透到"恢复 v1 备份→重开→再迁移"路径：`TaskRepository` 新增
  `protected_backups` 参数，迁移前备份同样不清理恢复输入。
- 发布阶段区分：`os.replace` 之前失败 → `restore_publish_failed`；
  之后失败 → `restore_publish_unknown`。
- 同秒备份：目录名随机后缀本就唯一；回归以固定 `datetime` 注入验证同秒
  8 份互不覆盖、恢复其一后全部保留。

### CR-A02（清理归属证明）

- 新增 `_owned_manifest(directory, db_name)`：manifest 必须证明
  `app="retirement-pet"`、`database` 与当前库同名、kind 在白名单、
  `created_at` 可解析 ISO、`schema_version`/`sha256(64hex)`/`size`/
  `task_count` 类型合法；任一不满足即"归属未证明"。
- `list_backups()` 对未证明条目记错误日志并以 `valid=False`、`kind=None`
  列出；`prune_backups()` 只清理归属已证明且为本库直接子目录的条目。
- 路径重解析边界：`os.lstat` 重解析属性命中的目录不列出、不清理。

### CR-A03（发布后状态重建）

- `TodoService.restore_from_backup()` 无论成败都经
  `_reacquire_after_restore()` 丢弃全部内存状态（任务缓存/焦点投影/
  已加载标记），以受保护参数重开 `TaskRepository` 重建可信磁盘状态。
- 重开失败 → 进入永久禁写故障态（`_unavailable_reason`，读返回安全空集、
  写抛 `TaskStoreUnavailable`），不使用任何陈旧缓存。

### CR-A04（隐藏 × 会话暂停联合判定）

- `app.py` 新增 `_update_idle_timeline_running()`：
  `window.isVisible() and not _session_suspended` 才 resume，否则 pause；
  `_show_window`/`_hide_window`/`_on_session_suspended` 全部改经该汇合点，
  `_hide_window` 先 `hide()` 再判定（顺序敏感）。IdleTimeline 的
  pause/resume 以 `_frozen_ms` 守卫保持幂等。

### CR-P01（publisher_ref 必填 + 版本化豁免）

- `package.publisher_ref` 按规范 6.1 必填：缺失
  (`publisher_ref_required`)、非字符串 (`publisher_ref_invalid`)、悬空
  (`publisher_ref_dangling`) 均拒绝；豁免闭包在 content digest 计算之后、
  以精确 RevisionKey（含 digest）比对 `_HISTORICAL_PUBLISHER_REF_EXEMPT`
  （四个内部保留历史/当前包），命中 → `PPK-MAN-W002` 警告 +
  `publisher_ref_exempt:historical` 降级，每次加载可见、可进 receipt。
- 不覆盖旧包、不放宽任意新包；重建变体因 digest 不同不在豁免内。
- 连带对齐：`BUNDLED_PREVIEW_WARNING_CODES = ("PPK-MAN-W002",)`（冻结
  预览包身份钉准）；GUI 导入确认协议下预检快照测试补传
  `warnings_acknowledged=preview.warning_codes`（与 pages.py 一致，非放宽）；
  `minimal-static` 夹具与参照包补 `publisher_ref` 并以 CLI 重建。

### CR-P02（ACTIVATE_COMMITTED，裁定 P-3）

- ACTIVE 行仍是选择唯一权威；`journal/events.jsonl` 追加
  `ACTIVATE_COMMITTED`（复用 `_append_event` 既有设施，隐私安全字段：
  pack/character 标识 + generation/commit_sequence + recovered）。
- 记录时机 = 已确认提交后：`swap_and_commit` CAS 胜出、INDETERMINATE
  读回证实采纳、`repair_active_from_current` 胜出、legacy
  `commit_candidate` 胜出（`_adopt_candidate`/`_record_activation` 收口）。
- 失败不误报：CAS 失败与未证实结果不记录；追加失败仅日志返回 False，
  审计钩子再包一层异常防护，绝不改变切换结果。
- 幂等：(revision, generation, commit_sequence) 查重；重启补记：
  `ActiveSelectionStore.__init__` 对缺少事件的 ACTIVE 行补记
  `recovered=true`，覆盖"提交后、追加前崩溃"窗口。

### P-2 / §3.1（layered idle 准入分层）

- `_check_drawable_bindings`：不可绘制 core.idle（含经 layered 模板）
  → `ACT_E004`（`petpack.actions.idle_undrawable`）拒绝运行准入；
  `PPK-ACT-W002` 仅用于非 idle 绑定（准入下限已保证 idle 回退可用）。
- 测试分层：结构可解析（actions 相之前无任何诊断）与运行准入
  （ACT_E004、无 W002、无 renderer_unsupported）分别断言；rig fixture
  改以共享 layered 模板绑定 `core.work` 保留 rig 规则覆盖。
- **验收第二轮补充修复**（`a325b1a`）：`_action_drawable` 改为镜像
  runtime 的 stage 选择规则（`lifecycle.loop or lifecycle.enter`，loop
  优先、单段决定）——修复"enter 为 static、loop 为 layered 的 idle 可
  通过准入但 runtime 不可绘制"的窄缺口；新增回归
  `test_idle_undrawable_loop_not_saved_by_drawable_enter`。

### P-1 / §3.3（能力证据规则）

- `SUPPORTED_CAPABILITIES` 仅保留有包内运行时消费证据的能力；移除
  `asset.wav.v1`、`asset.ogg.v1`、`text.plaintext.v1`（仅格式校验、无
  消费点）。required 声明它们 → `MAN_E006` 拒绝；正反两向测试固化。
- 对照表：Variant geometry 标注**未接入**；text_profiles/audio 标注
  格式校验与运行时消费的区分；`ENGINE_VERSION` 按 P-1 附证据说明。

### §3.2 / §3.4 / A-1（口径）

- 测试更名 `test_restoring_v1_backup_reupgrades_in_this_build`（原
  "rollback drill"表述废弃）；节点报告 §6 分别命名"新版本恢复旧备份"
  （已测）与"降级回滚演练"（离线恢复 v1 + 冻结 1.1.x 程序启动，本版
  未执行、仅记录流程，归后续真机认证/专项演练）。
- "四个在售包"→"内部保留的四个历史/当前包"；冻结 0.1.0 不描述为当前
  公开产品；节点报告与对照表加公开导出脱敏边界说明。
- "整店不可用/商店整体不可用"→"待办模块暂不可用"。

## 2. 提交

| 提交 | 内容 |
|---|---|
| `e4480b4` | 上述全部代码与测试返工（src 9 文件 + tests 9 文件 + 2 个夹具文件，+1328/−116） |
| `0fcc605` | 本报告初版、主控审阅文档入库、对照表/节点报告口径修订 |
| `a325b1a` | 验收第二轮发现的准入窄缺口修复（`_action_drawable` 镜像 runtime loop-or-enter 规则）+ 回归 |
| （本轮文档提交） | 验收结果记录与报告数字校正 |

## 3. 复现测试结果

环境：Windows 11（win32 10.0.26200），`.venv/Scripts/python.exe`，
`QT_QPA_PLATFORM=offscreen`，默认 pytest 临时目录（审阅 §4 同款注意：
仓库内 basetemp 会被 go_no_go 隔离门正确拒绝，不作为产品问题）。
以下为 `a325b1a` 后按文件实测的收集/通过数：

| 套件 | 结果 |
|---|---|
| 新增 `tests/test_todo_backup_rework.py`（CR-A01/A02/A03 指定回归：满额最旧输入、失败重试、迁移前输入、同秒多备份、8 类归属篡改、损坏 manifest、owned-but-invalid 可清理、重解析边界、发布后重建、发布前保全、重开失败禁写态） | 19 passed |
| 新增 `tests/test_activation_audit.py`（CR-P02：成功恰一条、CAS 失败零条、INDETERMINATE 读回证实恰一条+幂等、重启补记恰一条+幂等、审计失败不翻转结果、LKG/degraded 不记、legacy 记录） | 7 passed |
| `test_todo.py + test_todo_v2.py`（含更名后的再升级测试） | 132 passed, 1 skipped（既有符号链接权限 skip） |
| `test_timeline.py + test_character_layout.py + test_app_idle_playback.py`（含 CR-A04 两条新接线测试） | 35 passed |
| petpack 四套件（冻结四包逐包审计、豁免与警告钉准、准入分层含 loop/enter 修复回归、能力证据正反向） | 107 passed |
| lifecycle + kill-matrix + onboarding + local-import-ipc + control-panel | 125 passed, 12 skipped |
| **全量** `pytest -q`（默认临时目录） | **1003 passed, 13 skipped, exit 0** |

主控审阅的五个探针场景全部转为固化回归（CR-A01/A02/A03/A04/P01 对应
上文新增测试名），并按 §5 要求保持既有正常路径断言不放松：

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -p no:cacheprovider \
  -o addopts='' -q
# 1003 passed, 13 skipped（exit 0）
```

## 4. 三轮独立 Agent 验收结果（用户指令）

三轮均为独立 Agent（未参与实现），在 `0fcc605`/`a325b1a` 源码状态上
自行读码 + 复跑测试 + 编写各自伪造性探针（全部使用隔离临时目录与合成
数据，未触碰日用数据库）：

| 轮次 | 范围 | 结论 |
|---|---|---|
| 第一轮 | CR-A01/A02/A03（备份/恢复/服务状态） | **三项 PASS**。探针：满额最旧恢复后输入与字节完好、9 目录零丢失；10 类外来/畸形 manifest 目录 + canary 全部幸存且正控可清理；post-replace 故障重建可信磁盘状态、重开失败永久禁写、pre-replace 失败原库保全；v1 迁移前输入穿透保护实测 |
| 第二轮 | CR-A04 + P-2/§3.1 + CR-P01 + P-1 证据规则 + 冻结包未改动 | **五项 PASS**。探针：layered idle 9/9 拒绝、重建变体不豁免（digest 改变即拒绝）、wav/ogg/text required 全部 E006；四个真实 RevisionKey 与豁免集合精确相等；四个冻结包字节不变（git blob 级比对）。**发现 1 个窄缺口**（enter static + loop layered 的 idle 可准入）→ 已由 `a325b1a` 修复并补回归 |
| 第三轮 | CR-P02 + §3 口径 + 全量回归 | **全项 PASS**。全量 1002 passed/13 skipped/exit 0（修复后复跑 1003）；探针：删事件行后新 Store 补记恰一条 recovered=true 且 ACTIVE 行未动、二次构造零追加；CAS 失败者零 journal；文档口径逐条核对通过（grep 无残留违禁表述） |

验收中记录的**非阻塞建议**（已评估，留待后续版本，不影响本次复验）：

- 故障态读契约的边角：`focus_task()` 在禁写故障态抛 `TaskStoreUnavailable`
  （fail-closed，但与其余读路径的安全空集不一致）；对已故障服务调用
  `restore_from_backup()` 抛 `AttributeError` 而非 `TaskStoreUnavailable`。
- 同秒创建的备份之间保留清理顺序未定义（有界且恢复保护不受影响）；
  `prune_backups(backups_dir=非默认)` 目前为保守 no-op（仅测试面）。
- publisher_ref 为显式 null/空串时的诊断归类（仍一律拒绝，无放行）。
- `list_backups` 每次全量哈希校验的成本在保留 8 份下可接受。

## 5. 剩余依赖

- V12-02 闭环与节点 A 复验：本报告 + 三轮独立验收结论已就绪，**等待
  主控复验签收**；随后进入 V12-03 作者工具。
- V12-05 备份/恢复 UI 以修好的服务层为前提集成（A-3 裁定），仍待
  V12-03 之后开工。
- 降级回滚真实演练（离线 v1 + 冻结 1.1.x 程序）未执行，归 V12-E1
  真机认证或专项演练；本版不宣称覆盖。
- §4 所列非阻塞建议（故障态读契约边角、同秒清理顺序等）留待后续
  版本处理，不阻塞本次复验。
