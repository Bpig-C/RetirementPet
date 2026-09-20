# 1.2.0 节点 A 交付报告（基础可用）

日期：2026-09-11　分支：`impl/v1.2-daily-use`　基线：main @ 1a9ea96

> **主控审阅后状态**：节点 A 暂不签收；本报告 §8 所列事项已由主控按
> [审阅反馈](V1_2_CONTROLLER_REVIEW_A.md) §1 裁定，报告内涉及的开发口径
> 修正（A-1 命名、回滚命名、V12-02 设计被取代）已就地修订；返工内容见
> [V1_2_NODE_A_REWORK_REPORT.md](V1_2_NODE_A_REWORK_REPORT.md)。
>
> **公开边界**：本报告含内部提交号/分支名等私有开发身份，仅存于内部
> 仓库；任何公开导出前须按治理要求脱敏（提交号、分支、内部路径与个人
> 数据），不得以链接测试替代隐私审计。文中"冻结包"一律指**内部保留的
> 历史/当前包**，冻结 0.1.0 不是当前公开产品。

| 工单项 | 状态 |
|---|---|
| V12-01 idle 时间轴、角色比例与脚底锚 | 自测完成待验收（0b377bd） |
| V12-04 Todo schema v2、备份与恢复 | 自测完成待验收（06d8802） |
| V12-02 PetPack 一致性（Node A 相关设计与测试） | 设计已交付，实现未开始 |
| V12-C1 / V12-E1 | 未开始（按工单独立验收，不阻塞本节点） |

## 1. 解决的用户问题

- **V12-01**：PetPack 角色 idle 不动（快照无动作时 elapsed 恒为 0）；包几何
  被拉伸变形、脚底位置随窗口漂移；点击命中区域与画面不符。
- **V12-04**：旧 v1 库无法承载四象限/归档/长备注；升级存在数据损坏风险；
  此前没有任何备份与恢复手段。

## 2. 关键设计

### V12-01（已实现）

- `IdleTimeline`（`timeline.py`）：单调时钟锚点；进入 idle、切换角色/回退渲染器
  时 reset（`IdleResetReason`）；窗口隐藏与系统挂起时 pause 冻结、恢复后
  continue，不重放不跳变。`_compose_snapshot` 无显式动作时改用时间轴，
  显式动作时长语义不变。
- `character_layout.py`：`CharacterGeometry.from_character` 严格解析
  logical_canvas/content_bounds/motion_bounds/base_anchor/bubble_anchor/
  reference_height/hit_regions（有限数值、边界含于画布、≤16 区域），畸形返回
  None 走旧适配回退。`compute_body_layout` 单一均匀缩放
  `min(avail_h/reference_height, avail_w/motion.w, avail_h/motion.h)`——先按
  reference_height 定比例、再被 motion bounds 收敛，杜绝逐帧呼吸；base_anchor
  钉在足线（视口底 2 DIP），越界时整体平移不重缩放。
- `pet_window`：setMask = motion rect ∪ 面板 ∪ 气泡（有文案时）∪ 效果区
  （有效果时），Qt6 DIP 掩码；`hit_regions` 只裁点击语义不裁绘制；注入式旧
  3 参 renderer 用 `inspect.signature` 探测，保持兼容不传 layout。
- 播放循环：`_asset_for_elapsed = elapsed_ms % total`，动画起止由
  ActionController 事实驱动；resolver 立即结束当前角色不可渲染的请求动作，
  回退 core.idle 并记 missing_semantics。

### V12-04（已实现）

- **schema v2**（`migrations.py`）：tasks 表末尾追加
  `importance TEXT NULL(high/low)`、`urgency TEXT NULL`（NULL=未分类）、
  `archived INTEGER 0/1`、`archived_at TEXT`、`note TEXT NOT NULL DEFAULT ''`；
  新增 `idx_tasks_archived`；meta 恰好两行 `schema_version=2` +
  `established_at`。全部 v1 列名/类型/约束/顺序不变，迁移是纯加列。
- **迁移**（单事务，SQLite 标准 recipe）：`foreign_keys=OFF` → `BEGIN
  IMMEDIATE` → 复核 v1 → 建影子表存 10 列 → DROP/重建 tasks 为 v2 →
  `INSERT ... SELECT NULL,NULL,0,NULL,''` → 重建索引 → **逐行比对影子与
  新表 + 新字段全默认校验** → `foreign_key_check` 空 → COMMIT → FK ON →
  全量 quick_check 复验。中断/提交失败回滚即 v1 原样；不猜测重要性、
  不批量改状态、焦点行不动。
- **备份**（新 `backup.py`）：SQLite backup API（活连接或只读连接），落盘
  `<db>/backups/todo-backup-<UTC>-<kind>-<rand>/`（db + manifest.json：
  格式/类型/UTC 时间/schema 版本/sha256/size/task_count）。发布前在临时
  目录完成完整形状+quick_check+FK 校验，失败清理不留残目录。保留上限 8，
  只删有合法 manifest 的目录，最新可用 pre-migration 永远在限内保留。
- **打开路径**：无文件族 → 直接建 v2；声明 v1 → 先活连接校验恢复 journal，
  再生成 pre-migration 备份（**备份失败 = 待办模块暂不可用（禁写），桌宠
  其余功能继续；不开始迁移**——审阅 A-1 命名修正，仅针对需要迁移的 v1
  打开路径，不扩大到手动备份失败），
  再迁移；声明 v2 → 直接打开；v3+/损坏/未知/锁冲突 → 原样保留 +
  `SchemaTooNew`/`TaskStoreUnavailable`（reason 稳定可分支，无个人数据）。
- **恢复**：候选先 hash 对 manifest + 独立全量校验 → 当前库先备份
  （pre-restore）→ 确认无 sidecar（连接已关）→ 同卷原子 replace → 重开。
  恢复 v1 备份会按正常路径再次升级（**"新版本恢复旧备份"，不是降级回滚
  演练**，见 §6）。恢复失败不覆盖唯一可用数据；恢复全程保护输入备份
  （含迁移前输入，审阅 CR-A01 返工）；发布后异常会重建可信磁盘状态或
  进入禁写故障态（审阅 CR-A03 返工）。
- **domain/service**：`Level` 枚举、Task 五个 v2 字段、`set_classification`
  （None=未分类，独立两轴）、`set_note`（≤10000 字符，不截断不清洗）、
  `archive/unarchive`（不改完成状态）；归档焦点任务清焦点，恢复不抢焦点。
  备份入口：`create_manual_backup/list_backups/restore_from_backup`。

### V12-02 相关设计（已被主控审阅取代/修订）

> 主控审阅（[V1_2_CONTROLLER_REVIEW_A.md](V1_2_CONTROLLER_REVIEW_A.md) A-4、
> P-1/P-2/P-3、CR-P01）对本节早期设计做出裁定：未知 required 能力**在
> 验证/安装入口拒绝**（早期"可装不可激活"设计不再成立）；W 码扩展接受但
> 不可绘制 idle 不得靠警告放行；ENGINE_VERSION=2.0.0 作为 Engine API 兼容
> 版本、支持能力集合须有实际消费证据；publisher_ref 必填 + 精确钉定的
> 历史包豁免；须补 ACTIVATE_COMMITTED 追加事件。实现结果见
> [PETPACK_V12_CONSISTENCY.md](PETPACK_V12_CONSISTENCY.md)（返工后口径）。

- **对照表方法**：对每字段给"规范要求 → validator 诊断码 → GUI preflight →
  Runtime 消费"四列对照；geometry 列已由 V12-01 的 `CharacterGeometry`
  实现消费端，V12-02 反向核对 validator/preflight 是否同标准（NaN/无穷/
  越界/锚点越界/区域点数）。
- **compatibility 语义**（按裁定修订）：未知 **required** capability →
  验证/安装入口拒绝（`MAN_E006` 对照**有运行时消费证据**的注册表）；
  **optional** 缺失 → 降级激活并在 warning 集与角色详情可见；动作缺失语义
  沿用 resolver reconciliation（立即结束 + core.idle 回退）。
- **官方包审计**：先对两个冻结官方 Revision 与 0.1.1 跑严格化验证输出逐包
  结果；若现有包不达标，产出"新官方 Revision"差异清单交主控决定，绝不全局
  跳过校验或覆盖旧包补字段。
- **ACTIVATE_COMMITTED**：审计激活提交路径，确保成功才记录、失败/CAS 竞争
  不误提交活动选择（复用既有 PUBLISH_INTENT 事实链）。

## 3. 改动与提交

| 提交 | 内容 |
|---|---|
| 0b377bd | V12-01：`timeline.py`、`character_layout.py` 新模块；`petpack/runtime.py`、`ui/overlay_renderer.py`、`ui/renderer.py`、`ui/pet_window.py`、`app.py` 接线；3 个新测试文件 |
| 988918e | 工单文档（main） |
| 06d8802 | V12-04：`todo/migrations.py`、`repository.py`、`domain.py`、`service.py`、`__init__.py` 重构 + 新 `todo/backup.py`；新 `tests/test_todo_v2.py`；`docs/IMPLEMENTATION_STATUS.md`、`docs/ALPHA_TRIAL.md` 更新 |

## 4. 测试命令与实际结果

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest \
  tests/test_timeline.py tests/test_character_layout.py \
  tests/test_app_idle_playback.py -q          # V12-01：33 项全过
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest \
  tests/test_todo.py tests/test_todo_v2.py -q # V12-04：全过（含既有 113 项回归）
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q
                                              # 全量：exit 0，923 collected
```

实际结果：三组均通过（本环境 pytest 汇总行不打印，以退出码为准）。全量
923 项 = 基线 870 + V12-01 新增 33 + V12-04 新增 20。V12-04 覆盖工单验收
清单：中/Emoji/多层树/完成混合/日期/焦点合成 v1 fixture 逐字段比对、首次
新建 v2、重复打开不重复迁移、备份失败阻断、迁移中途崩溃回滚、提交边界失败、
v3 拒写、损坏库不重建、WAL sidecar 迁移、保留上限与保护、手动备份/恢复
往返、恢复失败保全、v1 备份恢复再升级演练、quick_check + foreign_key_check。

## 5. 复现步骤

1. `git checkout impl/v1.2-daily-use`，执行上节三条命令。
2. 手工观察动画：正常启动应用，导入任意含 sequence idle 的包，确认 idle
   循环推进、缩放窗口时脚底不漂移、气泡/面板外区域点击穿透。
3. 手工观察迁移：把任一 1.1.x 数据目录 `tasks.db`（v1）放入隔离测试数据
   目录启动，确认自动出现 `backups/todo-backup-*-pre-migration-*` 且待办
   数据完整；再次重启不产生新备份。

## 6. 兼容与回滚

- 代码回滚：分支按提交 revert 即可，未改冻结包、旧 receipt、历史 evidence。
- **新版本恢复旧备份**（restore-old-into-new，本版已测
  `test_restoring_v1_backup_reupgrades_in_this_build`）：恢复入口接受 v1
  备份，本版本在自身 pre-migration/pre-restore 保护下重新升级到 v2。它
  **不**证明"旧程序可打开"。
- **降级回滚演练**（真实"回到旧版"路径，本版**未执行**，仅记录流程；
  不以备份后新增任务为前提，属有损回滚）：
  1. 完全退出应用（含托盘）。
  2. 离线操作数据目录：删除 `tasks.db-wal`/`tasks.db-shm` sidecar，把
     选定 v1 备份目录中的 `tasks.db` 复制替换数据目录同名文件。
  3. 用冻结的 1.1.x 旧版程序启动该数据目录，核对任务列表/焦点/四象限
     字段缺省表现，并确认旧程序不再写坏数据。
  4. 演练脚本与样本数据须使用隔离目录与合成任务，不动日用数据库；执行
     时机归入后续真机认证（V12-E1）或专项演练，不在本版宣称已覆盖。
- 直接用旧版程序打开 v2 会被 `SchemaTooNew` 拒绝且不写任何文件（这是
  设计行为）。"恢复迁移失败"（原样可重试）≠"回到旧版"（有意回滚），
  两者分别命名，不混用"回滚演练"一词。
- 运行降级：备份失败/迁移失败/库不可用时 **Todo 模块**进入既有不可用
  禁写态，桌宠本体不受影响（`_UnavailableTodoService` 路径未变）。

## 7. 已知缺陷与限制

1. 迁移每次重试都会生成新的 pre-migration 备份（受保留上限约束，不会无限
   增长）；备份目录不可写时（如只读盘）**待办模块**暂不可用直至空间恢复——
   这是"备份失败不迁移"的必然结果（审阅 A-1 命名修正）。
2. 备份/恢复目前是服务层 API，面板可视入口随 V12-05 交付（见待决事项 3）。
3. 子树级归档/恢复编排（整树事务、归档祖先的恢复路径规则）按工单拆分
   归 V12-05；V12-04 交付的是逐任务存储语义与单任务服务操作。
4. V12-01：注入式 3 参旧 renderer 不走几何布局路径（签名探测兼容，行为
   与 1.1.x 完全一致）；效果区参与掩码计算，理论上效果矩形扩大会轻微
   扩大可点击区域（仅包围盒级别）。
5. `established_at` 在"新建 v2"与"迁移升 v2"两种来源下语义相同
   （v2 建立时刻），manifest 才区分 kind。

## 8. 需要主控决定的事项（已全部由主控裁定）

以下五项已由主控审阅裁定（[V1_2_CONTROLLER_REVIEW_A.md](V1_2_CONTROLLER_REVIEW_A.md)
§1 A-1..A-5、P-1..P-3），此处保留原文仅作历史对照：

1. ~~**备份失败 = 整店不可用**（当前实现，最严格口径）是否接受~~ →
   裁定 A-1/A-5：接受 Todo 暂不可用禁写、桌宠继续；仅限需迁移的 v1 打开
   路径；保留 8 份默认值，先修清理与保护逻辑（已返工）。
2. ~~**全新安装直接建 v2**~~ → 裁定 A-2：接受。
3. ~~**备份/恢复 UI 入口随 V12-05 面板交付**的拆分~~ → 裁定 A-3：接受。
4. ~~**V12-02 兼容语义设计**（未知 required → 可装不可激活）~~ →
   裁定 A-4：不接受旧设计，required 在验证/安装入口拒绝（已按此实现）。
5. ~~保留上限默认 8 份~~ → 裁定 A-5：接受为本版默认值。
