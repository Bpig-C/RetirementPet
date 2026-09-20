# 1.2.0 候选回滚说明（V12-09 交付件）

> 关联：[版本工单](V1_2_WORK_ORDER.md) · [V12-08 报告](V1_2_V12_08_REPORT.md) ·
> [文档中心](README.md)。内部仓库文档，含内部提交号。

## 回滚场景与操作

### 场景 1：候选版（1.2.0 候选 EXE）出现日常使用问题，回退上一稳定版

1. 退出候选版：托盘菜单 → 退出（或结束 RetirementPet.exe 进程）。
2. 保留候选版数据目录（`%APPDATA%`/`--data-dir` 指向的目录）——其中
   Todo 库为 schema v2、配置为 settings.json、角色库为 library/。
3. 安装回上一稳定版程序目录（覆盖安装或目录替换，二选一）。
4. 启动旧版：旧版程序读取其可识别的数据（见下方兼容边界）。

### 场景 2：1.2.0 候选写坏的 Todo 数据需要回到旧程序可用状态

Todo 库迁移（V12-04）遵循"失败保全"：v1 → v2 迁移失败时原 v1 文件
保持可用；迁移成功后旧 v1 文件由备份/迁移日志管理。回滚操作：

1. 关闭 1.2.0 候选版。
2. 在数据目录的备份区（`logs`/备份目录，详见 Todo 备份 UI）选择
   迁移前/最近的 pre-migration 备份，恢复为 `tasks.db`。
3. 启动旧版程序验证任务列表完整。
4. 校验依据：备份条目含清单（schema、sha256、task_count），恢复
   前后均校验（V12-05/CR-U08 的分阶段错误反馈保证不会谎报成功）。

### 场景 3：1.2.0 角色库数据不被旧版识别

角色库（library/、state.db、receipts）是 1.2.0 新增：旧版程序不读取
这些目录，互不干扰；回退旧版后失去的只是"非内置角色"，内置官方猫
由旧版自带素材提供。回滚不需要清理 library/；再次升级 1.2.0 时原
角色库完整保留（结构未被动过）。

### 场景 4：配置项差异

1.2.0 新增配置键（ui_layout_id、naming_visible、ui_text_templates、
ui_action_modes、ui_action_loops）对旧版是未知键：旧版忽略未知键，
不阻塞启动。回滚无需删除这些键；再次升级后设置仍然生效。

## 配套真实演练状态（如实声明）

- **1.1.x 旧版程序 + v1 数据的真实配套回滚演练**：本地已发现
  20260901 时期的旧构建 EXE（`previous-RetirementPet/` 为 v1.0.1
  onedir、`pyinstaller-work/RetirementPet/` 为 v1.1.0，后者与其
  release receipt 的 exe_sha256 一致）；但二者均为现行开发线产物，
  与已发布旧稳定版的等价性未经验证，且 v1 时期真实数据不在本机。
  演练数据生成器（`scripts/prepare_v1_drill.py`）与正向迁移验证
  （5 任务完整迁移）已就绪；**演练整体如实记 SKIP**，待主控确认旧
  EXE 等效性并提供时期数据后执行。
- 数据兼容的单向保证已由测试覆盖：v1→v2 迁移失败保全
  （test_todo_v2 迁移组）、v2 备份/恢复、损坏配置载入降级。

## 候选身份

- 候选构建：`scripts/build.ps1`（git archive HEAD，onedir，UPX 关）。
- 产物经 dist_manifest 校验 + release receipt（schema 1，PASS）+
  verify_windows（EXE 段）+ 冻结 import canary。
- EXE sha256 / build_id / commit / git_tree 记录于
  `.release/<run-id>/bound-release-receipt.json`（候选身份的唯一
  权威记录；任何证据引用 candidate 时必须与该 receipt 匹配）。
- 回滚动作本身不需要任何 receipt——旧版程序不校验 1.2.0 的产物。
