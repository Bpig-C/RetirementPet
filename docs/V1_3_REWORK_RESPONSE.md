# 1.3.0 主控返工响应报告（CR13-01 至 CR13-07 与 P2 项）

> 日期：2026-09-17 · 基线：[返工单](V1_3_CONTROLLER_REVIEW.md)（6e3ef9a）
> 最终候选身份（本报告全部引用此身份，不再引用更早候选）：
>
> - commit `4c7c3c317f88c09ea8af8e4fc1e8f01cc2fc0939`
> - build_id `c973ee6969892aeda47c070089b13446fc289f7f53caf0a866f2495ec7261871`
> - run `<repo>/.release/4c7c3c317f88-20260917-105924-445b4bee/`
> - EXE SHA-256 与 receipt：该运行目录 `evidence/post-publish/…dist-manifest/release-receipt.json`
>
> 本报告为文档提交，晚于候选构建；候选字节、receipt 与 ALPHA 门禁证据
> 绑定上述 4c7c3c3，未因本报告变化（报告不进入 EXE）。

## 逐项返工结果（原反例 → 新结果）

### CR13-01（P0）Markdown 本地文件读取

- **原反例**：`![secret][local]` + `[local]: <file:///…secret.png>` 引用式
  图片在预览渲染出 576 个目标色像素。
- **新结果**：同一反例 **0 个目标像素**。修复分两层：
  1. 根层：预览改用 `SecurePreviewDocument(QTextDocument)`，文档级
     `loadResource` 对一切资源请求返回空（引用式/折叠/快捷/相对/
     file:/data:/qrc 全部在此被拒）；
  2. 语法层：`sanitize_preview_markdown` 在解析前剥除内联、引用式、
     折叠/简写引用及引用定义行，仅保留 alt 文本；转义字面量不误伤。
- 新增像素级反例测试 3 项
  （`tests/test_todo_v13_04_ui.py::test_cr13_01_*`），套件 19 passed。
- 原复现脚本（`.acceptance-probe/cr1301_repro.py`）复跑：VULNERABLE → not reproduced。

### CR13-02（P1）Agent 端点用户隔离

- **原反例**：`socketOptions()` 为 NoOptions，任何本地账户可连接。
- **新结果**：`listen()` 前设置 `QLocalServer.UserAccessOption`，并在
  listen 后校验选项仍包含该位——平台拒绝用户域 DACL 时本地服务保持
  关闭（fail-closed）。测试 `test_cr13_02_*` 2 项：断言实际
  `socketOptions()` 含 UserAccessOption，且同用户正常 show/quiet 流程
  不受影响。`docs/AGENT_PROTOCOL.md` 安全边界改为"命名管道 DACL 限制
  为同一 Windows 用户（UserAccessOption），跨用户连接被系统拒绝"。

### CR13-03（P1）幂等键错配

- **原反例**：同 key 不同参数返回首次结果与旧 request_id。
- **新结果**：
  1. 缓存槽改为纯 `idempotency_key`（跨操作同键即冲突），值改为
     `{请求指纹, 业务结果, generation}`；指纹 = sha256(operation +
     规范化 args + expected_generation)；
  2. 同 key 不同指纹 → `CONFLICT` 且**不执行第二次写**；同 key 同指纹
     → 复用业务结果但响应绑定**本次** request_id；
  3. 三个要求的线级 socket 测试：同 key 同参数不同 request_id（复用
     结果、新 id）、同 key 不同参数（CONFLICT、库中无第二个任务）、
     并发前置改变（CONFLICT）；另加同 key 跨 operation（CONFLICT、
     未完成）。相关测试 5 项 + CLI 幂等回归，`test_agent_protocol.py`
     41 passed。

### CR13-04（P1）公开快照失效与罗小黑边界

- **原反例**：当前 HEAD 导出 REJECTED（节点 C 报告含盘符路径）；
  罗小黑构建脚本未入策略。
- **新结果**：报告已改为机器无关占位写法（`<repo>/`、`<evidence-out>/`）；
  `config/public_snapshot.json` 新增排除 `scripts/build_xiaohei_local.py`
  （prefix 规则），新增测试
  `test_cr13_04_policy_excludes_luo_xiaohei_local_channel`：含 xiaohei
  包/原图/截图 marker 的夹具树导出后三者在导出树中不存在。
  **从返工后同一 HEAD（4c7c3c3）重新导出：ACCEPTED**；快照内从零
  跑测试 **1281 passed / 15 skipped / exit 0**。审计与测试输出保存于
  `<evidence-out>/snapshot-rework/`。

### CR13-05（P1）SBOM/许可材料

- **原反例**：版本 null、摘要冒充全文、OpenSSL 归入 Python、FFmpeg 归
  入 PySide6、bootloader 归因无证明、无专项测试。
- **新结果**（生成器重写 + `scripts/release_licenses/` 入库全文）：
  1. 完整全文随包：PSF-2.0（CPython LICENSE 逐字）、LGPL-3.0、
     LGPL-2.1、Apache-2.0、PyInstaller COPYING（bootloader 例外全文）、
     仓库 MIT——每份经"特征句"校验（截断/错文件直接 FAIL）；
  2. 独立组件与真实版本：**openssl 3.2.4**（libcrypto/libssl）、
     **ffmpeg 61.19.100**（av*/ffmpegmediaplugin，LGPL-2.1-or-later）、
     pyside6-qt6 **6.8.3**（DLL 版本资源直读）、winrt 3.2.1、
     pyinstaller 6.22.2（**RetirementPet.exe 本体即 bootloader 归因**，
     base_library.zip 同组）、python 3.12.7、retirement-pet 9.9.9
     （合成环境）/1.3.0（真实候选）、bundled-assets——全部非 null
     （真实候选复核 298/298 文件、0 null）；
  3. 合成目录测试 `tests/test_release_materials.py`：未知 DLL
     fail-closed（exit 2 且点名文件）、版本来源、独立组件、全文标记
     ——2 passed；
  4. 绑定关系：材料**独立分发**（不写入 dist，验收后不再修改候选），
     `sbom.json` 携带 receipt commit/recipe_id/artifact_id/exe_sha256
     与 receipt 文件自身 sha256。对 4c7c3c3 候选已生成
     `<evidence-out>/release-materials-4c7c3c3/`。

### CR13-06（P1）候选/报告/门禁身份闭环

- **原反例**：报告引用 2cc0269，产物对应 ac6f1eb，无最终候选的
  acceptance.json。
- **新结果**：CR13-01~05 完成后冻结 HEAD `4c7c3c3`，**一次性**完成
  构建 → 材料 → 门禁：`go_no_go.py --profile alpha` 对同一字节运行，
  **23 项门禁全部 PASS，ALPHA-GO**（含 pytest_native：热键真机投递在
  交互会话中通过，印证此前为无人值守会话环境限制），证据目录
  `.release/4c7c3c317f88-20260917-105924-445b4bee/gonogo/`（含
  acceptance.json）。最终全量回归（同 HEAD）：
  **1309 passed / 13 skipped / exit 0**，输出保存于同一运行目录。
  Production 仍为 `PRODUCTION-NO-GO:PENDING_ENVIRONMENT`（六项真机
  门禁未执行）。

### CR13-07（P1）开发流程终止用户桌宠

- **原反例**：为解锁 dist 手工 `Stop-Process` 终止了用户正在运行的
  1.2.0 桌宠。
- **新结果**：
  1. grep 取证：build.ps1、smoke_test.ps1、go_no_go.py 无任何终止
     调用；verify_windows.py 的 `proc.kill()` 仅作用于本次运行自己
     Popen 的句柄；
  2. 归属隔离演练 `.acceptance-probe/cr13_07_ownership_drill.py`
     **4/4 PASS**：外部启动的桌宠在发布动作（移动其安装目录）期间
     与之后均存活；构建遇占用时的行为是 Write-Error 中止（候选发布
     阶段），外部进程不受影响。证据：
     `evidence/v13/20260917-cr13-07-ownership-drill.md`。

## 应当修正项（P2，非阻塞）

- 媒体命令阻塞：`bridge._command` 改为有界等待（0.75s）线程池执行，
  永不应答的播放器返回 `player did not answer in time`——反例测试
  注入 5s 延迟 provider，断言调用方 <3s 返回。
- 隐藏/挂起暂停：`set_app_active(False)` 现在真正暂停 provider 刷新
  （paused 期间事件不触发 rescan/refresh），恢复时重新同步——反例
  测试断言隐藏期零刷新、恢复后重同步。
- 异常 IPC 断连：`waitForBytesWritten(1000)` 移除，恶意/停滞客户端
  不再占用 UI 线程。
- 媒体套件 24 passed。

## 复验命令与数字汇总

- `pytest tests/test_todo_v13_04_ui.py tests/test_agent_protocol.py
  tests/test_single_instance.py tests/test_media_bridge.py
  tests/test_release_materials.py tests/test_public_snapshot.py`：
  全绿（单项数字见上文各节）。
- 最终候选全量回归：1309 passed / 13 skipped / exit 0。
- 公开快照：ACCEPTED（4c7c3c3）；快照内 1281 passed / 15 skipped。
- Alpha 门禁：ALPHA-GO（23/23 PASS）。

## 剩余未竟（如实）

- V13-E1 六类真机门禁仍 PENDING；Production 维持
  `PRODUCTION-NO-GO:PENDING_ENVIRONMENT`。
- 公开快照推送与 GitHub Release 等外部发布动作仍由用户决定。
