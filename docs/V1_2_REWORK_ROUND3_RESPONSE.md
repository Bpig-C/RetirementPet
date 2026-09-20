# V1.2 第三轮主控返工响应（CR-U01…U06 + CR-T04）

> 日期：2026-09-12；返工基线 `fc8cf47`，本响应随提交 `cfa854f`、`029f5b1`、
> `c132617` 交付。关联：[V12-05 主控审阅](V1_2_V12_05_CONTROLLER_REVIEW.md) ·
> [作者工具第三次复验](V1_2_AUTHOR_CONTROLLER_REVIEW.md) ·
> [执行方交付报告](V1_2_V12_05_REPORT.md) · [版本工单](V1_2_WORK_ORDER.md) ·
> [文档中心](README.md)。
>
> 提交号与分支名仅存内部仓库，公开导出前须脱敏。

## 总览

七项返工全部完成，按主控建议顺序交付（先防跨写与丢稿，再恢复与分类，
穿插作者工具标注收尾）。新增 **17 项测试**，全部走真实按钮/真实
PetApplication + TodoPage + SQLite 合成数据（隔离目录，不触碰日用库）：

- 直接受影响六个套件合计 **244 passed、1 skipped**
  （petpack_cli 33、todo_v2 30、rework3 8、control_panel、todo、
  todo_backup_rework）。
- 全量回归 **1058 passed、13 skipped**（返工前基线 1041/13，净增 17）。

其中两项产品缺陷是本轮新测试**第一次跑就抓出来的真 bug**，已一并修复
（见"顺带修复"）。这印证主控要求 UI 链路级测试是对的：仅服务层测试
发现不了它们。

提交切分（便于按项审阅）：

| 提交 | 内容 | 对应项 |
| --- | --- | --- |
| `cfa854f` | service/app：无条件写备注、任务缓存去备注、store_generation、降级可列备份 | CR-U06、CR-U04 底座 |
| `029f5b1` | todo_page：备注绑定/失败态/全量草稿/备份恢复对话框/未分类规则 + 10 项 UI 链路测试 | CR-U02/U03/U01/U04/U05 |
| `c132617` | petpack_cli：接触图续带全局帧号 + 3 项标注测试 | CR-T04 |

---

## CR-U02（P1）备注读取失败后，编辑器保留 A 内容继续保存覆盖 B

**复现**（主控审阅 §缺口 1）：打开任务 A 的备注并加载成功；让读取通道故障
（TaskError / 存储不可用 / 任意异常）；切换到任务 B —— 旧实现只清空
`_note_dirty`，编辑器里仍是 A 的文本；此时点保存，A 的内容写进 B。

**修复**（`src/retirement_pet/ui/panel/todo_page.py`）：

- 绑定三元组 `_note_task_id` / `_note_loaded` / `_note_dirty` 现在**只能
  在 `_open_note_for` 里一起切换**：先 flush 旧任务（仅当已加载），随即
  置新 id、`_note_loaded=False`、`_note_dirty=False`，并清空编辑器。
  不存在"id 已换、内容还挂着旧任务"的中间态。
- 读取失败进入**显式失败态**：编辑器置只读、内容替换为占位文本
  `备注读取失败，内容未加载；可重试或重新选择任务`，状态栏给出具体原因
  （TaskError / 存储不可用 / 其他异常三类分别提示）。
- `_flush_note` 与保存按钮都以 `_note_loaded` 为前提：**内容未加载成功
  之前没有任何路径能把编辑器文本写到存储**（程序化调用同样被拒）。
- 故障消失后 `refresh()` 会重试 `_load_note()`，自愈后恢复正常编辑。

**测试**：`tests/test_todo_rework3.py::test_note_read_failure_never_binds_previous_task_content`
—— 三类异常逐一注入 `note_for`；断言失败态（只读、占位文本、dirty=False、
`_note_loaded=False`）；断言程序化构造的保存路径对 B 不产生任何写入；
用捕获的 `real_note_for`（而非被 patch 的入口）核对 B 的备注仍是原值；
解除故障后 `page.refresh()` 自愈，B 的真实内容出现且可编辑。

**产物**：见提交 `029f5b1` 中 `_open_note_for` / `_load_note` /
`_flush_note` / `_show_note_unloaded`；复验命令见文末。

## CR-U03（P1）保存失败的草稿，切换任务后关闭面板即丢失

**复现**：任务 A 备注写入失败（存储故障），草稿留在编辑器；切到任务 B
（旧实现把 A 的草稿留在局部变量里）；关闭面板 —— `_flush_all_drafts`
只 flush 当前编辑器，A 的草稿在同一进程内永久丢失。

**修复**：

- 应用级有界草稿库 `PetApplication.todo_note_drafts`（`src/retirement_pet/app.py`）：
  `dict[str, str]`，上限 `_MAX_KEPT_NOTE_DRAFTS = 16`，超出按最旧插入
  逐出。挂在应用对象上而非面板页上，因为面板关闭会 `deleteLater` 销毁
  所有页 —— 挂页上必然随页销毁。
- 编辑过程中每次改动 `_keep_draft` 同步进草稿库；写入成功即移除；
  `_flush_all_drafts` 关闭面板时：停定时器 → flush 当前已加载编辑器 →
  遍历草稿库逐任务 `_flush_kept_draft` 重试落盘 → 仍失败的留在草稿库。
- 正常流程**零弹窗**：关面板不打断用户；失败草稿静默保留，重新打开
  面板选择该任务时编辑器直接呈现草稿内容供继续编辑。
- 面板关闭后不再有任何备注轮询：定时器停止，测试断言关闭后
  `app.todo_events == []`。

**测试**：
`tests/test_todo_rework3.py::test_failed_draft_survives_task_switch_panel_close_and_reopen`
—— A 写入失败→切 B→关面板；变体一（关面板前故障已自愈）：关闭 flush
成功落盘，断言 A 的备注已是新值且 `app.todo_events == []`（无后续轮询）；
变体二（`test_draft_survives_close_while_fault_persists_and_offers_retry`）：
故障持续，草稿留在应用级库，重开面板后编辑器呈现草稿。

**产物**：`029f5b1` 中 `_drafts` / `_keep_draft` / `_flush_kept_draft` /
`_flush_all_drafts`；`cfa854f` 中 `PetApplication.todo_note_drafts`。

## CR-U01（P1）已完成且已归档的任务无法恢复

**复现**：完成一个任务再归档，选中它 —— 旧实现 `restore.setEnabled(
is_done or is_archived)` 里归档分支的实现路径对 done 状态返回了禁用，
"已完成+已归档"组合按钮不可点。

**修复**：`_update_action_states` 统一为
`self.buttons["restore"].setEnabled(is_done or is_archived)`，四个组合
全部可恢复；恢复语义保持完成状态不丢（done 恢复后仍是 DONE，只是脱离
归档；再从"已完成"视图可继续重开）。

**测试**（全部通过**真实按钮**点击，非直接调服务）：

- `tests/test_todo.py::test_restore_button_covers_every_done_archived_combination`
  —— 未完成/已完成 × 未归档/归档 2×2：未完成+未归档按钮禁用；其余三个
  组合可用；done+archived 点击后保持 `Status.DONE`，切到"已完成"视图
  后可再次点击"重开"。
- `tests/test_todo.py::test_restore_buttons_cover_done_root_done_leaf_and_mixed_subtree`
  —— done 根、done 叶、混合子树三种归档形态逐一经按钮恢复，断言子树
  各节点状态与父任务展开焦点规则。

**产物**：`029f5b1` 中 `_update_action_states` 一行门控 + 既有归档服务
路径；主控已裁定"接受可恢复归档替代物理删除"，本项只修恢复入口。

## CR-U06（P2）服务重开后，清空备注被等值短路跳过

**复现**：任务备注非空；服务重开（新 TodoService 实例，任务缓存里
`note` 是未加载占位空串）；把备注清空保存 —— 旧 `set_note` 用
`task.note == note` 等值短路，拿"未加载占位空串"与"要写入的空串"比较
相等，直接 return，磁盘上仍是旧备注；重开后又显示旧内容。

**修复**（`src/retirement_pet/todo/service.py`）：

- `set_note` **删除等值短路**，无条件构造候选、校验、写库、广播
  changed。写入正确性不再依赖缓存里是否加载过备注。
- `note_for` 不再把读到的备注写回任务缓存：`Task.note` 在长期存活的
  `_tasks` 缓存里**永远保持未加载占位空串**。这一并解决主控同条提出的
  "备注正文在 `_tasks` 里长期累积"的内存问题——正文只经
  `note_for`/`note_of` 按需读取，不进缓存。
- 领域层 `Task.set_note` 仍负责类型与长度校验并 `touch()` 时间戳。

**测试**（`tests/test_todo_v2.py` 新增 3 项）：

- `test_set_note_clear_to_empty_survives_reopen_without_prior_read`：
  非空→清空，**全程未调用过 note_for**（最苛刻路径），新实例重读磁盘
  确认清空生效。
- `test_set_note_covers_empty_to_full_same_content_and_reload`：
  空→非空且内容与占位相同、同内容重写、其他字段编辑不波及备注。
- `test_note_reads_and_writes_keep_note_bodies_out_of_the_task_cache`：
  `note_for` 与 `set_note` 之后断言 `service._tasks[id].note == ""`，
  备注正文不进缓存。

**产物**：`cfa854f` 中 `set_note` / `note_for`。

## CR-U04（P1）备份/恢复用户入口未交付 + 恢复后备注编辑器仍显示恢复前内容

**复现**：服务层 `create_manual_backup` / `list_backups` /
`restore_from_backup` 早已存在并有测试，但面板没有任何入口调用它们；
主控实测：恢复成功后备注编辑器仍显示恢复前任务的内容，此时随手保存
就会把旧内容写回恢复后的库。

**修复**（`029f5b1` 面板新增"备份"行 + 对话框；`cfa854f` 服务侧配套）：

- **入口**：任务页布局新增"备份…"按钮（objectName `todo_backup_open`），
  打开模态对话框（`todo_backup_dialog`）：备份列表
  （`todo_backup_list`，行格式 `时间 · 类型 · N 个任务 · 校验通过/无法校验`）、
  "立即备份"（`todo_backup_create`）、"恢复所选"（`todo_backup_restore`）、
  "刷新"、"关闭"、状态栏 `todo_backup_status`。对话框顶部固定提示恢复
  的**覆盖范围**（整库替换）与"恢复前自动做一次备份"。
- **恢复前草稿处理**：确认框中列明当前未落盘草稿数量；确认后
  `_prepare_for_restore()` 清空应用级草稿库并解绑编辑器——恢复前的
  旧内容**没有任何通道**写回恢复后的库。
- **结果三态**（`_on_backup_restore`，状态文案先算好，列表刷新后
  再设置，避免刷新自身文案覆盖结果）：
  1. 完全成功：`恢复完成（N 个任务）`，随后全量刷新树/计数/选择/备注；
  2. 磁盘已写回但存储重开失败：`备份内容已写回磁盘，但任务存储暂不可
     用；重启应用后可再次在此恢复以修复`（与 CR-A03/A05 自修复契约
     一致，降级态下恢复入口保持可用）；
  3. 普通失败：TaskStoreUnavailable / BackupError / TodoError / 其他
     异常分别给出不泄内部细节的文案。
- **动态门控**：写入类入口不再只看构造期状态，`_writable_now()` =
  `_available and not degraded and _active and not _disposed`，在
  `_update_action_states` 与每次 refresh 里重算——服务降级/恢复的
  瞬间，新建/编辑/完成/归档/备注全部即时禁用；备份/恢复入口按
  available+active 单独门控保持可达。服务侧 `list_backups` 改为直接
  读 `store_path`（不再 `_ensure_loaded`），降级态也能列出备份供自救。
- **恢复后全量再同步**：`TodoService._store_generation` 在
  `_reacquire_after_restore` 后自增（`cfa854f`）；页面 refresh 末尾
  `_sync_note_with_store` 检测到代数变化即停定时器、清空草稿、解绑
  编辑器；未加载状态会持续重试 `_load_note`，从**新库**读出恢复后的
  内容。树、计数、选择、焦点由既有 `changed` 广播全量重建。

**测试**（`tests/test_todo_rework3.py` 新增 3 项）：

- `test_backup_dialog_creates_lists_and_restores_with_full_resync`：
  真实对话框创建备份→列表出现 1 条→改备注制造"更新"→反选重选使编辑器
  读到新值→留一条脏草稿→确认框文本断言含"未保存的备注草稿"与覆盖范围
  →恢复后断言备注为快照旧值、草稿库已清空、状态栏"恢复完成"、编辑器
  与新库再同步。
- `test_restore_reports_partial_success_and_self_heals_on_second_restore`：
  注入一次性重开失败——断言"已写回磁盘"文案、`app.todo.degraded` 为真、
  全部写入入口禁用、**备份按钮仍可用**；第二次恢复自愈回"恢复完成"、
  写入恢复。这条测试第一次跑就抓出 `list_backups` 在降级态抛异常的
  真 bug（见"顺带修复"）。
- `test_restore_when_current_task_vanishes_keeps_page_consistent`：
  恢复后当前所选任务在新库中不存在——页面保持一致状态，不崩溃、不写
  任何旧内容。

**产物**：`029f5b1` 备份对话框与 `_sync_note_with_store`；`cfa854f`
`store_generation` / `list_backups` 降级可读 / facade 拒绝变更。

## CR-U05（P2）只设重要或紧急一个维度后，任务从五个分类入口全部消失

**复现**：旧 `_task_view_keys` 对 importance/urgency 任一为 None 的任务
返回一个不属于任何视图的 key——该任务从未分类、四个象限、全部任务
五个入口同时消失。

**修复**：`_task_view_keys` 改为：归档任务维持归档 key；否则**任一维度
为 None 即 `(state, "未分类")`**；两维齐全才进入唯一确定象限。清除某
一维度（高/低循环回无）后状态栏反馈 `重要已清除（未分类）` /
`紧急已清除（未分类）`，不再显示清除前的旧档位。

**测试**：

- `tests/test_todo_rework3.py::test_single_axis_tasks_stay_in_unclassified_and_quadrant_needs_both`
  —— 3×3（重要 高/低/无 × 紧急 高/低/无）九种组合，全部经**真实按钮**
  点击设置、每次点击间 `processEvents` 驱动刷新：两维齐全的组合只出现在
  唯一正确象限（并断言其余三个象限无此任务）；任一维为无的组合出现在
  未分类且五个入口都能看到它。
- `test_cycling_an_axis_back_to_none_reports_unclassified`：高→低→清除
  循环，断言清除后状态栏为"未分类"反馈而非旧档位。

**产物**：`029f5b1` 中 `_task_view_keys` 与 `_toggle_axis` 反馈分支。

## CR-T04（P2）接触图续带重新从 f0 标号；末帧/接缝/遗漏标注错误

**复现**（主控审阅 + 作者工具第三次复验）：16 帧序列的接触图第二续带
从 `f0` 重新编号（应为 `f8`–`f15`）；带间缝隙标签同样错位（显示
`f0 33ms` 应为对应全局帧）；字形预检只查当前带局部标号；40 帧截断行
的遗漏标注 `(+23 omitted)` 被省略号裁掉，用户看不到任何遗漏提示。

**修复**（`scripts/petpack_cli.py`，`c132617`）：

- `_contact_bands(rows_cells)` 返回 `(row, chunk, cells_chunk)`，chunk
  保留**块起始下标**；所有绘制标号经 `_band_caption(row, chunk, column)`
  取**全局**帧点 `row.points[chunk + column].label`——续带第二带正确
  显示 `f8`–`f15`，接缝带保留 `seam <总时长>`。
- 字形预检收集**所有带**的标号（全部点标号 + 副标行 + 需要时的
  `(cont.)`），不再漏检续带字形。
- `_row_sub_lines` 把遗漏标注独立成行 `+N frames omitted`（124px 标号
  列内完整显示，不经省略号），与序列统计行分开；文档
  `docs/CHARACTER_LIBRARY_GUIDE.md` §6 同步此口径。

**测试**（`tests/test_petpack_cli.py` 新增 3 项，断言**喂给画笔的实际
标号字符串**，不是样本计数或暗像素）：

- `test_contact_bands_and_captions_keep_global_frame_labels`：16 帧 →
  块切分 `[0, 8, 16]`，逐带标号恰为 `f0`…`f15` 加 `seam 528ms`。
- `test_truncated_rows_keep_last_frame_seam_and_omission_labels`：40 帧
  截断采样 → 末帧标号 `f39 33ms`、接缝 `seam 1320ms`、副标行恰为
  `["seq 40f 1320ms", "+23 frames omitted"]`。
- `test_omission_note_line_fits_the_label_column_unelided`：
  QFontMetrics 实测两行副标在 `CONTACT_LABEL_WIDTH - 8` 内不触发省略。

**产物**：16 帧 MVP 包接触图第二带 `f8`–`f15`、末带接缝；40 帧合成包
遗漏行独立可读。复验命令见文末。

---

## 顺带修复的两个真实缺陷（本轮新测试首次运行即抓出）

1. **恢复结果文案被列表刷新覆盖**：`_on_backup_restore` 原先先设结果
   文案再刷新备份列表，降级态下列表刷新会写入"存储不可用"文案覆盖
   恢复结果。已改为：try/except 先算好 `message` → 刷新列表 → 最后
   `setText(message)`。
2. **降级态无法列出备份**：`TodoService.list_backups` 原先先
   `_ensure_loaded()`，写禁用态直接抛异常——用户恰恰在降级时才需要看
   备份自救。已改为直接读 `store_path` 列目录，不触碰加载状态
   （facade 不可用态仍返回空列表）。

## 主控独立复验命令

```bash
# 直接受影响六个套件（隔离目录，不触碰日用库）
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest \
  tests/test_todo_rework3.py tests/test_todo_v2.py tests/test_todo.py \
  tests/test_control_panel.py tests/test_todo_backup_rework.py \
  tests/test_petpack_cli.py -p no:cacheprovider -o addopts=''
# 预期：244 passed, 1 skipped

# 全量回归
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest \
  -p no:cacheprovider -o addopts=''
# 预期：1058 passed, 13 skipped

# 接触图：16 帧与 40 帧两包各跑一次 preview，检查 contact-*.png
# 第二带标号 f8 起、接缝 seam <总时长>、遗漏行 +N frames omitted 独立可读
.venv/Scripts/python.exe scripts/petpack_cli.py build <模板目录> <包>.petpack
.venv/Scripts/python.exe scripts/petpack_cli.py preview <包>.petpack <输出目录>
```

UI 链路各场景（备注失败切换、关面板丢稿、done+archived 恢复、备份恢复
三态、单轴未分类）均已由 `tests/test_todo_rework3.py` 与 `tests/test_todo.py`
的真实按钮测试覆盖；主控如需在真实面板上手工复现，可从上述测试函数体
直接对照操作步骤。

## 口径与边界

- 失败草稿仅会话内存保留（应用级 `todo_note_drafts`，上限 16 条）：
  进程退出前仍未落盘的草稿不跨重启；这是本轮明确取舍，跨进程草稿恢复
  未列入本版需求。
- 恢复入口在降级态保持可达，用于 CR-A03/A05 自修复契约；"完全成功 /
  磁盘已写回但重开失败 / 普通失败"三态文案见 CR-U04。
- `[V1_2_V12_05_REPORT.md](V1_2_V12_05_REPORT.md)` 新增 §5，修正此前
  两处被主控实测推翻的表述（"面板页关闭会落盘""恢复一致性由广播与
  刷新保证"），以本响应 CR-U03/CR-U04 为准。
- 提交号与分支名仅存内部仓库，公开导出前须脱敏。
