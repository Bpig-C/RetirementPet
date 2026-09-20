# 1.3.0 返工复验响应报告（RR13-01 至 RR13-05）

> 日期：2026-09-18 · 基线：[复验报告](V1_3_REWORK_RECHECK.md)（141af98）
> 最终候选身份（本报告全部引用此身份）：
>
> - commit `77be50e24dafbf557135d06c4740f6e0b3fab832`
> - build_id `b5aa931b8d77c3a7832fc5c19f82f326d9a3ef1763327ae07d08a29c368c0d9c`
> - run bundle（含材料包与自身 receipt）：
>   `<repo>/.release/77be50e24daf-20260918-004659-d7a22f34/`
>   - `release-materials/`（sbom.json + LICENSES/ + NOTICE +
>     LGPL-SOURCES.md + materials-receipt.json）
>   - `evidence/post-publish/…dist-manifest/release-receipt.json`
>   - `gonogo/`、`gonogo2/`（绑定门禁运行输出）
>   - `gonogo/pytest_regular.stdout.log`、`gonogo2/pytest_regular.stdout.log`（绑定门禁全量回归：**1311 passed / 13 skipped**）
>
> 注意：`4c7c3c3` 的 Alpha GO 依然真实有效（主控已确认）；因 media_bridge.py
> 属生产代码且本轮有改动，按复验报告"生产代码改变需重新冻结"的要求，
> 本轮从 `77be50e` 重新构建了唯一候选并重跑了绑定门禁。

## 逐项残余结果（原反例 → 新结果）

### RR13-01（P1）当前 HEAD 公开快照 REJECTED

- **原反例**：干净 69054db 导出 REJECTED（响应报告中 Qt 四段版本号
  `6.8.3.0` 触发 `ip_address` 规则）。
- **新结果**：报告已移除四段版本号（Qt 版本记为 6.8.3）。从**最终
  HEAD（77be50e）**重新导出：**ACCEPTED**；快照内从零测试
  **1281 passed / 15 skipped / exit 0**。审计与测试输出保存在
  `<evidence-out>/snapshot-final/`。本轮坚持了"报告写入后重跑导出"
  的顺序：先改文档、后导出、再写本报告。

### RR13-02（P1）SBOM 归因错误

- **原反例**：`_asyncio.pyd`、`_ssl.pyd`、`_sqlite3.pyd` 等被标为
  `retirement-pet / MIT`；`base_library.zip` 全归 PyInstaller；
  LGPL-SOURCES.md 缺 FFmpeg。
- **新结果**：
  1. `retirement-pet` 规则**删除了宽泛 `.pyd` 匹配**（应用自身不携带
     编译扩展）；CPython 规则改为覆盖所有非 PySide6/winrt 的 `.pyd`；
  2. 容器文件多重归因：SBOM 逐文件条目新增 `contains` 关系——
     `RetirementPet.exe` contains `pyinstaller-bootloader` +
     `retirement-pet`；`base_library.zip` contains
     `pyinstaller-bootloader` + `python`；
  3. 测试改为**语义断言**：`_ssl.pyd`/`_asyncio.pyd`/`_sqlite3.pyd`
     必须是 python/PSF-2.0；应用组件不得含任何 `.pyd`；两个容器文件
     的 contains 关系精确匹配；
  4. `LGPL-SOURCES.md` 增加 FFmpeg 对应源码（ffmpeg.org + Qt 构建
     配置入口）、版本对应关系与替换/重新链接步骤。
- 真实候选（77be50e dist）逐文件审计：298 文件、9 组件、0 null 版本，
  上述语义断言全部通过。

### RR13-03（P1）材料无产物身份与保存位置

- **原反例**：材料目录、sbom.json 均无可定位路径，也无自身 receipt。
- **新结果**：
  1. 材料固定保存到**最终 run bundle**：
     `<repo>/.release/77be50e24daf-20260918-004659-d7a22f34/release-materials/`；
  2. 生成器新增 `materials-receipt.json`：材料目录内 11 个文件的
     逐文件 SHA-256、combined SHA-256，以及候选绑定四元组
     （commit `77be50e24daf…`、recipe_id、artifact_id、exe_sha256、
     version 1.3.0）；
  3. 本报告给出上述仓库内可访问路径；若材料单独分发，
     materials-receipt.json 必须随包。

### RR13-04（P2）Alpha 与罗小黑口径

- **Alpha 口径**：已修正为 **23 PASS + 1 SKIP（多屏真实拓扑变化的
  可选项，Alpha 规则允许）→ ALPHA-GO**。
- **罗小黑口径**：选择**收窄声明**——真实跟踪树中唯一的罗小黑产物是
  专用构建脚本，真实 policy 的 `additional_exclude_paths` 明确排除
  它。测试 `test_cr13_04_policy_excludes_luo_xiaohei_local_channel`
  已改用**未修改的真实 policy**（夹具直接嵌入仓库原版
  `config/public_snapshot.json`），并兼容公开快照环境（脚本本就不在
  快照中时直接通过）。不再声称对"未来新增的包/原图/截图"有排除证明；
  若这些内容进入跟踪树，需先扩充正式 policy。

### RR13-05（P2）媒体阻塞与环境不可复跑

- **承诺降级**：撤回"非阻塞"表述。当前实现是**有界阻塞**：每条媒体
  命令在 Qt 线程最多等待 750ms（`_COMMAND_TIMEOUT_S`），该上限写入
  `media_bridge.py` 文档字符串（"BY DESIGN and BY DOCUMENTED
  COMMITMENT… NOT an async implementation"）与 `AGENT_PROTOCOL.md`
  （`media.play_pause/next/previous` 行）。真异步化列为后续 1.3.x 候选。
- **worker 占死修复**：弃用共享单线程池，改为**每命令一个 daemon
  线程**——永不应答的播放器不再占住唯一 worker 使后续命令排队超时。
  已知取舍：连续超时的命令会各留下一个 daemon 线程直到进程退出；
  并发上限/背压列为 1.3.x 改进。
- **竞态与 UI 如实呈现**：availability() 通过但 start() 失败时，快照
  `reason` 保留真实拒绝原因；设置页在"设置已持久化为启用但桥未启动"
  时显示"开启失败（原因）"而非"未开启"。
- **可复跑性**：本轮媒体套件 **26 passed**（含新增 RR13-05 竞态与
  承诺文档两项测试）。复验会话出现的 5 failures（availability/start
  竞态连锁）由本次修复覆盖；真机 SMTC 端到端测试依赖本机媒体会话
  状态，环境不可用时该项 SKIP，不以合成证据替代。

## 门禁最终状态

- 最终候选 `77be50e` 绑定门禁（本会话）：
  receipt_schema、source_toolchain_preflight、static_artifact_binding、
  receipt_bytes_before_runtime、dist_manifest、pytest_regular
  **PASS**；`pytest_native` FAIL —— 唯一失败仍为
  `test_real_ctrl_alt_t_delivery_conflict_and_release`（无人值守会话
  热键注入不可达，环境型；主控复验与本轮源码对照均复现同一限制），
  其余门禁按序 SKIP；`final_receipt_bytes` PASS。
- **ALPHA 结论：NO-GO:ENVIRONMENT（本会话）**。ALPHA-GO 需在有人
  值守的交互桌面重跑 `scripts/go_no_go.py --profile alpha`
  （绑定 77be50e receipt）。此前 4c7c3c3 会话曾取得 23 PASS + 1 SKIP
  的 ALPHA-GO，证明该 gate 在交互会话可通过；本轮生产代码改动
  （media_bridge 命令线程模型）不涉及热键路径。
- Production 维持 `PRODUCTION-NO-GO:PENDING_ENVIRONMENT`。

## 快照最终状态

- 当前 HEAD `77be50e` 公开导出：**ACCEPTED**；快照内从零测试
  **1281 passed / 15 skipped / exit 0**；罗小黑构建脚本不在导出树。
