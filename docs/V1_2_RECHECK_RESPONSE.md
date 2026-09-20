# 1.2.0 进度核对响应与 V12-03 交付报告

> 日期：2026-09-12 · 角色：执行者（本地模型） · 交付性质：按
> [进度核对与返工反馈](V1_2_PROGRESS_RECHECK.md)完成两项补充返工，并交付
> V12-03 作者工具 · 状态：**自测完成待验收**
>
> 提交：`ebf1bbd`（CR-P03）、`d5b4c1f`（CR-A05）、`92f7764`（文档同步）、
> `8d6bea5`（V12-03），分支 `impl/v1.2-daily-use`。本报告含内部提交号/分支名，
> 仅存内部仓库，公开导出前须脱敏。

## 0. 范围对照

| 反馈编号 | 内容 | 状态 |
|---|---|---|
| CR-P03 / P1 | 连续切换永久丢失较早激活审计 | 已修复：持久待记事实 + 按事实补记，含三项指定场景回归 |
| CR-A05 / P2 | 恢复成功返回值不反映重开失败 | 已修复：部分成功结果 + 故障态自修复入口，含仅重开失败独立回归 |
| §文档口径 | IMPLEMENTATION_STATUS 两处旧口径 | 已修正（见 §3），未只改报告 |
| V12-03 | 作者工具从骨架补齐为完整工作流 | 已实现四项需求，自测通过（见 §4） |
| 旧版回滚演练 | V12-04/09 未闭环项 | 仍未执行，不随本报告声明完成 |

## 1. CR-P03：激活审计按持久事实补记

**方案**（`switcher.py`）：新增 `activation_audit_due` 待记事实表，键为
`(publisher_id, package_id, package_version, content_digest, generation,
commit_sequence)` 唯一。每次确认提交 ACTIVE 时，待记行与 ACTIVE 行在**同一个
SQLite 事务**内落库——不存在"已确认提交但没有可恢复审计事实"的窗口。补记来源
是这张事实表本身，而不是从当前 ACTIVE 槽位反推，因此被后续切换覆盖的
generation 也能找回。

- 清空时机：每次确认提交时按 rowid 序排空欠账（更早的事实先补，
  `recovered=True`），本轮选择的事实以 `skip=` 参数排除、走正常
  `record_activation_committed` 路径并在证实写入后删除对应待记行；启动对账
  `_reconcile_activation_journal` 同样先排空欠账，再保留旧库无表时的 ACTIVE
  行回填作为升级路径。整个对账包裹在 try/except 中，审计故障不阻塞启动。
- 不变量保持：ACTIVE 仍是当前选择唯一权威；审计失败只留下待记行，
  不反转、不阻塞任何已确认切换；重放幂等由既有
  `record_activation_committed(recovered=True)` 的
  (revision, generation, commit_sequence) 幂等保证。

**指定场景测试**（`tests/test_activation_audit.py`，共 9 项全过）：

| 要求场景 | 测试 | 结果 |
|---|---|---|
| 失败一次后继续切换 | `test_audit_failure_then_next_switch_restores_full_history` | gen1 审计注入失败 → undo → gen2 成功：journal 为 `[1, 2]`，事件 1 `recovered=True`、事件 2 `recovered=False`，pending 归零；全新 store 重启后仍 `[1, 2]` |
| 连续多次失败后恢复 | `test_consecutive_audit_failures_recover_on_restart` | 3 次失败切换 → pending=3 → 重启排空 `[1, 2, 3]` 全部 `recovered=True` → 第二个全新 store 幂等（无重复事件） |
| 重启幂等 | 同上两测试的重启断言 | 二次重启不产生重复记录 |

原有 7 项审计测试（正常写入、降级路径、活跃行回填等）不变且通过。

## 2. CR-A05：恢复结果如实反映重开失败

**方案**（`todo/backup.py`、`todo/service.py`）：

- `RestoreResult` 新增 `store_available: bool = True` 与
  `unavailable_reason: str | None`。数据已恢复但服务重开失败时返回
  `store_available=False, unavailable_reason="restore_reopen_failed:TaskStoreUnavailable"`，
  不再误称普通成功；"恢复失败原库未变"路径（发布前失败）维持既有异常语义不变。
- 已故障服务保留 `_store_path`，可直接再次调用恢复作为**自修复入口**：
  重开成功后返回 `store_available=True` 且 `degraded` 转 False。
- `focus_task()` 在故障态返回安全空值 `None`，与列表/快照读契约一致，
  不再抛 AttributeError。

**指定场景测试**（`tests/test_todo_backup_rework.py`，todo 相关 4 套件 41 项全过）：

| 要求场景 | 测试 | 结果 |
|---|---|---|
| 仅重开失败的独立测试 | `test_reopen_failure_alone_returns_partial_success_result` | 只注入 TaskRepository 重开失败（不动 fsync/replace）：返回部分成功结果（`store_available=False`、reason 如上、`restored_version=2`、`task_count=1`、`preserved_backup` 存在）；service 降级、读安全空、写拒绝；直接检查快照确认磁盘确实有 1 项 |
| 已故障服务再次恢复 | `test_degraded_service_restores_again_as_self_repair` | 故障态下恢复 → 部分成功；解除故障后再次恢复第二份备份 → `store_available=True`、`degraded=False`、任务齐全 |

原有"fsync + 重开同时失败"的禁写回归不变且仍走异常路径通过。
V12-05 界面须显示真实恢复结果与后续操作的要求已写入
IMPLEMENTATION_STATUS 备份入口条目，UI 部分随 05 交付。

## 3. 文档口径同步（`92f7764`）

- 不可绘制 idle：改为"绑定 `core.idle` 的不可绘制动作在校验层即被拒绝
  （`PPK-ACT-E004`，准入按运行时同规则取 loop 优先的单一 lifecycle 段判定），
  离屏首帧门禁仅作第二道防线"。
- 旧版回退：补明"本版恢复入口还原 v1 备份后会立即按正常迁移流程再次升级回
  v2；真正的降级回滚需在应用外离线还原 v1 库文件后再启动旧版程序（演练尚未
  执行）"。
- 备份入口：记录 `store_available` 两种成功结果契约；故障态条目补
  `focus_task` 安全空读；M3.5 行与
  [PETPACK 一致性闭环对照表](PETPACK_V12_CONSISTENCY.md) §5 同步 CR-P03
  待记事实方案。

## 4. V12-03：作者工具完整工作流（`8d6bea5`）

按工单 §5 四项需求逐条交付（入口 `scripts/petpack_cli.py`）：

1. **init 生成可直接构建的最小原创静态包**：完整 manifest（占位身份
   `community.example/my-pack/0.1.0`，含 `publisher_ref`、`compatibility`
   required 能力、rights/sources/legal_files、geometry、`core.idle` 绑定）、
   64×64 透明 idle、32×32 缩略图、双语原创许可。`init` 拒绝覆盖已有
   manifest 或任一模板文件。模板不需要外部下载，且直接通过
   validate/preflight。
2. **lint / 接触图 / 预算 / 画布锚点检查 / 预览**：`lint` 输出每角色每语义
   一行的动作接触图与全包解码工作集预算，`E:`（必须修）/`W:`（建议）两档，
   覆盖 schema、publisher_ref 存在且可解析、声明↔实物互检（缺声明文件、
   多未声明文件）、PNG 可解码/声明像素/透明度（缩略图豁免）、锚点与边界在
   逻辑画布内、`core.idle` 绑定、帧数 ≤300、单帧 ≥33 ms、预算 ≤48 MiB。
   `preview` 用与桌宠窗口相同的 `PackCharacterRuntime` 离屏逐帧渲染
   `<角色>-<语义>-<序号>.png`，静态与 sequence 都可预览；私有缓存，不触碰
   活动角色、Context、用户配置、用户库，不发声。
3. **ZIP 压缩修复 + 确定性 + 冻结保护 + 双哈希口径**：裸 `ZipInfo` 默认
   STORED 导致归档声明 DEFLATE 而成员实际未压缩——现已逐成员固定
   `ZIP_DEFLATED`。构建保持确定性（成员排序、固定时间戳/权限、canonical
   JSON），相同源树在任意输出位置逐字节相同；源树存在未声明文件时
   `build` 直接拒绝。防覆盖从官方包扩大到 canonical 示例冻结输出
   （`assets/petpack/examples/realistic-retirement-cat-0.1.0/0.1.1`），
   旧 pin 一律不改。`archive_sha256`（容器）与 `content_digest`
   （canonical manifest+资产）的分工写入 CLI 头注、指南与测试：
   **只有 content digest 变化才需要新 Revision**。
4. **命令链与排错文档**：[角色库扩展指南](CHARACTER_LIBRARY_GUIDE.md)
   重写为"模板 → 编辑 → lint → build → validate → preflight → preview →
   GUI 导入"完整命令链 + 18 行排错表 + 双哈希对照。`install-local`
   未实现（工单明确非必交）。

**验收证据**（`tests/test_petpack_cli.py` 17 项全过）：

| 工单验收点 | 测试/事实 |
|---|---|
| 空目录从 init 走完全流程 | `test_init_to_preview_workflow_from_empty_directory`：lint clean（含接触图+预算行）→ build → validate ACCEPT → preflight ACCEPT 无警告 → preview 出帧 |
| 两个独立输出位置逐字节相同 | `test_templates_build_byte_identical_in_independent_locations`（4 位置）+ 真实猫源重建 |
| 实际压缩类型 | `test_zip_members_are_actually_deflate_compressed`：逐成员 `compress_type==8`，license 实际变小 |
| 透明度 | 模板 idle 透明背景 + 不透明形体，preview 像素断言 |
| 预算报告 | lint 接触图/预算行；超限为 `E:` |
| 可读诊断 | 排错表逐条对应 lint/build/preflight 输出 |
| 不支持文件不悄悄混入 | `test_build_refuses_undeclared_source_files`：lint 报文件名、build 退出码 2 且无输出文件 |
| 双哈希分离 | `test_recompression_changes_archive_hash_not_content_digest`：STORED 重建 digest 相等、archive hash 不等 |
| 冻结输出保护 | `test_frozen_examples_are_protected_like_official_media`：canary 完好 |
| 预览无副作用 | `test_preview_uses_real_runtime_without_touching_any_library`：真实 Runtime 出帧，目录无库痕迹 |
| GUI 能导入 | 模板包通过 `preflight_local_pack`（GUI 导入同一门禁）；character_onboarding 套件全过 |

**冻结包零改动证据**：`tests/fixtures/petpack/minimal-static.petpack` 随构建器
修复重建（该 fixture 是测试夹具，不是发行包）：archive hash
`d82d5903… → 5ec4b5cc…`，content digest `c2f6a270…` 不变，4 成员全部
DEFLATED，validate ACCEPT 零诊断；真实猫 0.1.1 冻结包重建对照测试断言
重建 digest == 冻结 digest（`6c4b368a…`）且归档字节不同——身份不因容器层
修复改变。`assets/petpack/` 下冻结文件一字节未动。

## 5. 自测结果

- 全量：`QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/
  -p no:cacheprovider -o addopts=''` → **1017 passed、13 skipped**（上轮
  1007+13，新增 10 项 V12-03/CR 回归）。
- 定向：petpack_cli 17、激活审计 9、todo 备份相关 4 套件 41、
  realistic_cat_pack + petpack_cli 合跑 25。
- 以上为执行者证据；主控独立复跑与签收另计。

## 6. 未闭环与边界

- 节点 A 完整签收仍待主控；本报告不构成验收。
- 旧版回滚演练（V12-04/09 未闭环项）仍未执行，可在隔离目录进行，不阻塞
  V12-05。
- V12-05 待办 UI 及恢复结果的真实呈现、V12-06/07/08/09 未开始。
- 本报告与提交号仅存内部仓库，公开导出前须脱敏。
