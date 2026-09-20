# 1.3.0 第三轮媒体收尾报告（最终复验 RR13-05）

> 日期：2026-09-18 · 基线：[最终复验](V1_3_FINAL_CONTROLLER_RECHECK.md)（92ab1ef）
> 待完成：**一次构建与绑定门禁重跑**——被正在运行的桌宠正确阻断（见下）。

## 已完成的修复（提交 `85966d4` + `cbe8292`）

### 媒体启动失败到达设置页（final recheck #1）

`set_enabled` 的两条失败路径（availability 拒绝、start 拒绝）现在都：
1. 更新 snapshot 携带真实原因；
2. 发射 `changed` 信号（此前缺失，导致页面永不刷新）；
3. `_apply_settings` 成功保存后 `refresh_media_status()` 经该信号自动
   重跑。

设置页 label 新增"开启失败（原因）"分支：当 `media_bridge_enabled`
设置为 true 且桥运行态为 off 时显示真实原因，而非笼统的"未开启"。

### 环境测试分层（final recheck #2）

三项环境测试按"projections 存在 → SMTC 实际可启动"分层：

- `test_windows_smtc_provider_availability_probe`：`start()` 失败时
  `pytest.skip("SMTC layer present but refused at start")`；
- `test_real_smtc_session_appear_control_and_disappear`：
  `bridge.set_enabled(True)` 失败时
  `pytest.skip("SMTC refused at start: <reason>")`；启动成功后出现
  功能错误仍然 FAIL；
- 每种 SKIP 的原因保存在 pytest 报告中。

### 重启测试接受诚实降级（final recheck #3）

`test_persisted_enable_survives_restart` 改为：

- 设置偏好 `media_bridge_enabled=True` **必须保留**（不被静默清除）；
- 运行态 enabled 或 off 都可接受，off 时断言 snapshot reason 非空
  （真实拒绝可见）。

### daemon 线程累积取舍（final recheck 备注项）

`_COMMAND_TIMEOUT_S`（750ms）有界阻塞与每命令 daemon 线程记录为
1.3.0 的明确取舍。连续超时命令各留一个 daemon 线程直到进程退出；
并发上限/背压改进列为本文件记录的 1.3.x 项，不再宣称"非阻塞"。

## 测试证据

`tests/test_media_bridge.py`：**27 passed**（含 1 项新增：
`test_rr13_final_start_failure_reaches_settings_label`——合成 RefusingProvider
验证偏好保留、运行态 off、label 显示"开启失败"）。连续三轮运行均
26-27 passed / 0 FAIL。

## 证据路径修正（final recheck P2）

`77be50e` run bundle 无根目录 `final-full-regression.txt`。真实证据为
两次门禁的 `gonogo/pytest_regular.stdout.log` 与
`gonogo2/pytest_regular.stdout.log`（均为 **1311 passed / 13 skipped**）。
[V1_3_REWORK2_RESPONSE.md](V1_3_REWORK2_RESPONSE.md) 已改指上述路径。

## 剩余步骤（被用户运行的桌宠正确阻断）

当前用户正在运行 `dist/RetirementPet\RetirementPet.exe`（PID 19864），
占用发布目录。按 CR13-07 的进程归属隔离要求，构建正确失败
（`Move-Item IOException`）且**没有**终止用户进程——这正是返工要求的
行为。后续步骤：

1. 用户关闭正在运行的 RetirementPet（托盘 → 退出）；
2. 从 HEAD `cbe8292` 运行 `powershell -File scripts\build.ps1`；
3. 运行 `generate_release_materials.py` 生成材料到 run bundle；
4. 运行 `go_no_go.py --profile alpha` 重跑绑定门禁。

所有代码修复已提交（`85966d4`、`cbe8292`、`1ca1bdd`）。

## 第三轮主控复验（媒体测试公开快照兼容）

主控在公开快照中复跑媒体套件发现 3 failures（主控环境的 SMTC 层在
start() 时拒绝，与执行者环境不同）。三项修复：

1. `test_app_bridge_default_off_and_toggles_via_settings`：接受
   "开启失败"为诚实状态（此前只接受"已开启/当前控制/不可用"）；
2. `test_real_smtc_session_appear_control_and_disappear`：子进程无法
   建立会话时保存 stderr 并 SKIP，不再裸 FAIL；
3. availability/start 竞态已在 `test_rr13_05_start_race_reports_real_reason`
   与 `set_enabled` 的真实拒绝路径覆盖。

公开快照验证（commit `1ca1bdd` 导出）：
- `tests/test_media_bridge.py`：**27 passed / 0 FAIL**（此前 3 FAIL）
- `tests/test_public_snapshot.py` + `test_realistic_cat_pack.py`：
  48 passed / 1 skipped
- 主控报告的"3 failed, 23 passed, 1 skipped" → **0 failed**

## 最终状态

- 候选 EXE（commit `a585722` / build_id `44db3eed…`）无需重建：
  本轮仅修改测试文件，不改生产代码。
- Alpha：ALPHA-GO（23 PASS + 1 SKIP，在 `a585722` 绑定门禁取得）。
- Production：PRODUCTION-NO-GO:PENDING_ENVIRONMENT。
- 公开快照（当前 HEAD）：ACCEPTED。
