# 节点 B 集成验收材料：功能集成（V12-02/03/05/06/07）

> 关联：[版本工单](V1_2_WORK_ORDER.md) · [节点 A 报告](V1_2_NODE_A_REPORT.md) ·
> [文档中心](README.md)。内部仓库文档，含内部提交号。
> 范围：工单 §13 节点 B（02、03、05、06、07 的端到端演示与 diff）。单项
> 工单签收不替代节点验收；旧版回滚演练、真机与发布门禁继续单独跟踪。

## 1. 模块清单与状态

| 工单 | 状态 | 主交付提交 | 交付/审阅文档 |
| --- | --- | --- | --- |
| V12-02 PetPack 一致性 | 验收通过（节点 A 复验范围） | `956bacb`、返工 `e4480b4` | V1_2_CONTROLLER_REVIEW_A |
| V12-03 作者工具 | 验收通过 | `8d6bea5`→`c132617` | AUTHOR_REVIEW（四次复验签收） |
| V12-05 Todo 使用流程 | 工程范围签收 | `3de1db7`、`88bdaa2`、返工 `cfa854f`、`029f5b1` | V12_05_REPORT / ROUND3_RESPONSE |
| V12-06 配置与面板 | 工程范围签收 | `f33bf14`→`68a0b06`、返工 `9974d5a`→`7c65105` | V12_06_REPORT / CONFIG_REVIEW |
| V12-07 角色库管理 | 工程范围签收 | `134ceba`→`4318182`、返工 `ce23767`→`3fe2306` | V12_07_REPORT / CONTROLLER_REVIEW |

V12-04（Todo 数据升级）属节点 A 语义；其运行时行为在下面的 Todo 全流程
演示中一并覆盖。V12-08 正在实施（多屏验收重写已完成首片，见 §5 已知
状态）；V12-09 未开始；V12-C1（Blender 内容）与 V12-E1（真机门禁）按
独立工单跟踪。

## 2. 端到端演示（主控可独立复跑）

以下每条演示均使用隔离数据目录；`RP_DATA` 指 `--data-dir` 指向的临时
目录。测试级演示给出 pytest 节点（可机械复跑）；人工演示给出面板操作
步骤（离屏或真机均可）。

### 2.1 Todo 全流程（V12-04/05）

- 四象限归类与筛选：`pytest tests/test_todo_rework3.py -q`（12 项）；
  人工：面板 Todo 页建任务 → 通过分类控件（紧急/重要两轴）把任务
  归入四象限（单轴任务留在未分类）→ 计数与列表联动。
- 长备注与草稿：失败草稿不静默淘汰（容量 16，第 17 条如实拒绝）、
  按任务绑定、关闭后草稿恢复：`test_todo_rework3.py` 中
  `test_full_draft_store_keeps_existing_and_refuses_new_with_feedback`、
  `test_draft_limit_heals_close_flush_recovers_live_and_recycles`；
  未读长备注侧页懒加载：`test_todo_v2.py` 相关节点。
- 归档（可恢复子树）与恢复：归档→列表消失→恢复→原位返回：
  `test_todo_rework3.py` 的归档/恢复组。
- 备份与恢复错误阶段：`test_todo_backup_rework.py`（22 项）——备份
  列表/恢复/占用失败/替换后失败如实分阶段（restore_publish_unknown）。
- 数据升级失败保全：`test_todo_v2.py` 迁移组（v1→v2 失败回滚）。

### 2.2 配置重启有效（V12-06）

- 服务层事务与恢复：`pytest tests/test_config_system.py -q`。
- 真实 UI+App 链路（隔离数据目录）：`pytest tests/test_config_pages.py
  -q`（35 项）。完整重启循环的代表性节点：显示页往返
  （`test_display_apply_changes_window_and_survives_restart` 等，
  真正重建 PetApplication 验证落盘与恢复）、
  文案模板应用与角色覆盖、动作模式/循环、720×480 真实命中测试。
- 人工演示：改布局 → 窗口立即变化 → 关闭应用 → 重新启动 → 布局保持
  → 恢复默认（显示页提供该入口）→ 设置键从磁盘消失。动作页禁用某动作
  → 进入对应上下文
  不再表演（rest+RESTING、meeting+MEETING 均有测试）。

### 2.3 导入、版本管理与失败路径（V12-02/03/07）

- 本地导入预检与失败：`pytest tests/test_control_panel.py -q`（45 项）
  的导入组（后台预检、确认对话框、篡改包拒绝、进程取消）。
- 安装/版本/回滚/卸载：`pytest tests/test_lifecycle.py -q`（57 项）：
  两版本同存、digest 冲突拒绝、内置/活跃/恢复依据拒删、占用文件
  →pending-delete→真实重启续删、媒体消失一致性、取消待删幂等。
- 应用级角色切换与重启恢复：`pytest tests/test_app_smoke.py -q`
  （48 项）：切换事务、失败切换安全模式、重启恢复 active/last_known_good、
  待删版本重选撤销后跨重启保持。
- 人工演示：角色页 → 系列筛选 → 查看详情（版本/digest/来源/预算/缩略图）
  → 切换指定版本 → 回到上一健康版本 → 删除未使用版本；导入非法包看
  如实拒绝。

### 2.4 端到端演出事实（V12-06 第五轮收口内容）

- 真实安装包（非均匀帧时长 500/700ms）单次播放 1200ms、帧边界按素材
  时序、1200ms 处真实结束、自动/上下文路径不受循环开关影响：
  `test_config_pages.py::test_loop_switch_and_single_pass_follow_installed_pack`
  及相邻节点。
- 被禁用语义对一切请求路径生效（Context 推导/随机/音频/面板）且不
  留下旧表演：`test_disabled_core_action_blocks_context_performance`、
  `test_vetoed_meeting_request_ends_stale_work_performance` 等。

## 3. 测试命令与实际结果

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -I -B -m pytest -p no:cacheprovider tests
git diff --check
```

执行方最近一次全量：**1116 passed、13 skipped**（V12-07 签收基线
`3fe2306`）。治理与文档链接 68 项通过。主控各轮独立复验结果记录在
对应审阅文档中，未与执行方全量混写。

## 4. 兼容与回滚方法

- Todo：v1 数据保留原文件语义，迁移失败回滚（V12-04）；备份/恢复
  含 pre-restore 保护与分阶段错误反馈。
- 配置：显示页有"恢复默认"按钮（事务回滚）；文案与动作设置可在界面
  改回默认值（程序化恢复走服务层），保存失败按精确回滚处理；损坏配置
  在载入时消毒降级，不阻塞启动（CR-C01）。
- 角色库：卸载先进库内回收区，凭证（receipts）与权利事实永久保留；
  last_known_good 提供回退；pending-delete 跨重启续删且保护性重检；
  内置安全包不可卸载。
- 应用整体：角色切换失败进入安全模式保留原状态；启动失败进入
  Bootstrap 模式。

## 5. 已知缺陷与开放项（不混入节点结论）

1. **V12-08 进行中**：可信多屏验收重写与运行降载；单屏基线与多屏
   认证在 go_no_go 中的分别表达属于该工单。
2. **旧版（1.1.x）程序配套数据回滚演练**从未执行，独立开放。
3. **V12-E1 真机六项门禁**、24 小时稳定性、性能样本：无环境部分如实
   SKIP，属节点 C/发布门禁。
4. **Markdown 预览 R5-MD-01**：按主控安排持续后置。
5. **V12-C1**（Blender 内容）是独立工单；包推荐 UI 入口属工程增强
   开放项，不是 V12-C1 已承担的内容工作，两者都不在节点 B。
6. 视觉验证均为离屏结果，不代表真机鼠标穿透、多屏、DPI 结论。

## 6. 需要主控决定的具体事项

- 节点 B 演示中如需执行方配合的现场环境（真机面板操作演示），请指定
  日期与方式；离屏 + 测试节点是否足够由主控裁定。
- `docs/README.md` 中主控未提交的审阅行与执行方新增行将在主控下次
  提交一并入库（当前工作区状态）。
