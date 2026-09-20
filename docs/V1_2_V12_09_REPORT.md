# V12-09 阶段报告：候选构建、Alpha 门禁全程与回滚材料

> 关联：[版本工单](V1_2_WORK_ORDER.md) · [V12-08 报告](V1_2_V12_08_REPORT.md) ·
> [候选回滚说明](V1_2_ROLLBACK.md) · [文档中心](README.md)。
> 状态：候选与 Alpha 门禁全程已完成；发布材料剩余项如实列于 §4。
> 内部仓库文档，含内部提交号。

## 1. 候选与 Alpha 门禁全程（实际执行）

- **候选构建**：`scripts/build.ps1` 在干净工作树（HEAD 70c593443ddd）
  上从 `git archive` 冻结源码，onedir、UPX 关；产物 `dist\RetirementPet`。
- **候选身份**：build_id `ce1b48aaf25cc3631917a406038302c7fcbb1d4b596284c80bf12843a2ca3ce9`，commit `70c593443ddd1644018732687548a867b848d060`，
  EXE sha256 `4775be3f04c2…`，receipt `result=PASS`（post-publish
  dist-manifest 校验）。
- **Alpha 门禁全程**（`go_no_go.py --profile alpha`，真实启动 EXE，
  证据 `.release/agent1-gate/v120/…`）：**23 个自动化门全部
  PASS，`displays_multi_screen_exe` 为带精确原因的 SKIP，裁决
  `ALPHA-GO`；Production 判 `PRODUCTION-NO-GO:PENDING_ENVIRONMENT`**
  （6 项环境门待真机）。包括：receipt/工具链/静态绑定、
  pytest_regular、pytest_native（原生窗口）、window_harness
  source+EXE（含候选绑定与严格检查契约）、smoke EXE（冻结 import
  canary）、displays EXE harness（V12-08 全链路）、
  performance/stability 的 ALPHA 观测、最终 dist 清点、receipt
  字节复核。包含全部 OVR-01～04 修复。
- 多屏认证 SKIP 原因：本机无真实拓扑变化事件；该 SKIP 是唯一可选项
  且不阻断 Alpha（D08-06 语义），Production 仍要求其完整通过。

## 2. 构建与门禁过程中修复的真实缺陷（均有回归测试）

1. **GNU tar 误判盘符**（Git for Windows `/usr/bin/tar` 把
   `E:\…` 解析为远程主机）：build.ps1 优先使用 Windows 系统 bsdtar。
2. **工具链备案过期**：按流程重新生成
   `.release/toolchain-attestation.json`（attest-toolchain，公开锁
   哈希不变）。
3. **verify_displays ctypes 崩溃**：`GetWindowRect` 的 argtypes 为
   `POINTER(RECT)` 而调用传本地 `RECTW` byref——真实运行必然
   ArgumentError。改用 `wt.RECT`。
4. **harness 进程无 DPI 感知**：缩放屏上原生坐标被虚拟化、与
   per-monitor DPI 事实混单位。启动即声明 per-monitor DPI aware；
   脚底/包含关系换算继续以诊断记录的 window_dpi 为权威。
5. **go_no_go 独立启动崩溃**：`-I` 把脚本目录移出 sys.path，
   `verify_displays` 导入失败；导入前显式恢复 scripts 路径。
6. **性能证据聚合 fail-closed**：`summarize()` 拒绝负值/非有限
   CPU、非法 private_bytes、外来 pid 样本；health PASS 与
   failures 列表交叉核对；dist manifest 要求当前 schema 与精确
   `source_comparison`（旧格式缺失即 INVALID）；window 报告消费
   要求 harness 绑定、非空 checks、EXE 候选绑定。
7. **fixture 二进制被行尾归一化破坏**：恢复
   `minimal-static/legal/license.txt` 原始字节（185 B）；全仓行尾
   归一化提交使归档导出与工作树字节一致。

## 3. 早期候选的独立验收历史（三个 agent 并行实操）

以下保留早期候选记录，不作为本轮 e270e93 的三次新验收；本轮主控复核见 §5。

- **agent 1（候选门禁）**：receipt 绑定 PASS、EXE 实际 sha256 与
  receipt 一致；门禁全程真实执行并复现 `INVALID_RUN →（修复后）
  ALPHA-GO` 的全过程；确认 displays 门对伪造/缺字段报告 fail-closed。
- **agent 2（EXE 实操）**：直接 Popen 启动候选 EXE，验证窗口真实
  可见且落在工作区、IPC 面板真实开合（EnumWindows 证据）、quit 退
  出；独立运行 displays harness 与门禁结论交叉印证；并实证：
  数据目录经环境变量 `RETIREMENT_PET_DATA_DIR` 配置、quit 需
  `--test-ipc-quit` 放行（设计使然）。
- **agent 3（全套件与口径）**：独立全量 1149 passed、13 skipped，
  `git diff --check` 干净；工单 V12-01..09 状态核对；V12-08 证据
  数字独立重算一致；并列出距最终发布的缺口清单（见 §4）。

## 4. 剩余缺口（距最终发布）

1. 真机六项门禁（V12-E1）：混合 DPI/拔插/登录自启/睡眠唤醒/干净
   VM/24 小时稳定性/三设备性能校准——需真实环境，工具与流程已就绪。
2. 旧版（1.1.x）程序 + v1 数据真实配套回滚演练：需主控提供旧版
   EXE 与时期数据；`scripts/prepare_v1_drill.py` 已可生成 v1 演练
   数据目录，正向迁移路径已在真实服务上验证（5 任务完整迁移）。
3. SBOM、许可目录、NOTICE 与 LGPL 对应源码入口等发布包组装项。
4. V12-C1（Blender 拟真猫）与 Markdown 预览（已裁定后置）独立跟踪。

## 5. 1.2.0 候选主控签收（2026-09-15）

**签收 1.2.0 Alpha 候选、V12-08 工程片和 V12-09 Alpha 候选范围。**
候选由 70c5934 的干净源码快照构建；03c1d9a 仅更新交付报告，不改变产物。

- receipt：`.release/70c593443ddd-20260915-003442-faa229a1/evidence/post-publish/20260915-003952-ce1b48aaf25c-dist-manifest/release-receipt.json`。
- acceptance：`.release/agent1-gate/v120/20260915-004035-alpha-5c5b17b7/acceptance.json`。
- 版本：1.2.0；build_id：`ce1b48aaf25cc3631917a406038302c7fcbb1d4b596284c80bf12843a2ca3ce9`。
- 主控对当前 dist 全目录重新校验：静态绑定 PASS；Windows 文件版本和产品
  版本均为 1.2.0.0；EXE SHA256 为
  `4775be3f04c2ba56d1f9dcc7fd49f2485d634d62401e164995adff16bfc269a1`。
- 原始窗口报告按预期 EXE 身份重新消费 PASS；显示报告重新校验无错误，消费
  为单屏 PASS、多屏 SKIP。acceptance 独立计数为 23 PASS、0 FAIL、1 SKIP、
  0 INVALID，`run_valid=true`、`ALPHA-GO`。
- 主控未重新启动整个门禁；本次做的是原始证据独立重算与当前产物逐字节核对。
  结果位于 `.release/controller-review-03c1d9a-results.json`。

### 工作审视报告

#### 原定目标

收敛应用版本至 1.2.0，重建包含 OVR 修复的候选，取得新 receipt 与 Alpha
门禁证据，并更新交付记录。

#### 完成情况

- [x] 版本、Python 包元数据和 Windows 版本资源统一为 1.2.0。
- [x] 70c5934 候选与 receipt、当前 dist 字节及门禁证据相互绑定。
- [x] Alpha 自动门 23 PASS、1 个环境型多屏认证 SKIP；Production 未认证。
- [ ] 发布材料、真机门禁、旧版配套回滚及公开快照仍按 §4 开放。

#### 发现的问题

| 严重程度 | 具体问题 | 根本原因 | 改进建议 |
|---|---|---|---|
| 应当改正 | 本报告顶部一度沿用旧候选 EXE hash，§5 仍签收 e270e93，§4 仍称版本未收敛 | 更新当前候选时只改了局部字段，没有按身份轴全篇检索 | 本节已更正；以后以 commit/build_id/hash/receipt/acceptance 五项清单机械核对 |
| 应当改正 | README、IMPLEMENTATION_STATUS、PROJECT_UPDATES 与 FUTURE_WORK 仍把 1.1.1 或已完成的 V12 工作写成当前事实 | 版本收敛只更新构建输入和阶段报告，未执行文档链收敛 | 公开快照前做一次文档事实迁移；保留 1.1.1 为历史，不再称当前版本 |

#### 做得好的地方

候选身份和门禁证据可独立重算；Alpha 与 Production 口径分开；多屏缺少真实
拓扑事件时保持 SKIP，没有冒充通过。

#### 下次重点关注

先统一文档链，再组装 SBOM/许可/LGPL 材料与脱敏公开快照；并行执行合成 v1
数据与已知旧构建的配套演练。真机证据继续按同一候选取证，任何重建都产生
新的身份与验收链。
