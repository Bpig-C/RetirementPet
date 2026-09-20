# 1.3 节点 C 交付报告（V13-07 / V13-08 / V13-09）与 V13-E1 状态

> 日期：2026-09-17 · 分支：`impl/v1.3-agent-workflow`
> 范围：内容与发布节点（工单第 15 节顺序 3）+ 环境节点（顺序 4）

## 实际提交（节点 C 主线）

| 提交 | 内容 |
|---|---|
| `a014076` | V13-07：官方猫 1.0.2 无缝序列 + 罗小黑本地管线修复 |
| `…`（4 个返工提交） | 快照误报修复、个人路径清除、1.0.2 测试对齐 |
| `96551bb` | 版本收敛 1.3.0（runtime/pyproject/version resource/文档轴） |
| `d5f1715` | 热键 gate 环境对照证据 |

## V13-07 角色动画与素材

- **官方猫 Revision 1.0.2**（不覆盖 1.0.1/1.0.0）：
  - idle 为 **16 帧 sequence、3200ms**（200ms/帧），programmatic 猫的呼吸
    与尾巴摆动统一到 3.2s 公共周期，接缝不匹配率 2.3% ≈ 相邻帧 2.4%
    （无缝）；work 为 4 帧 1000ms 打字循环；
  - 帧均为 512×512 RGBA、四角透明稳定；
  - 两次构建 archive sha256 一致（确定性）；
  - 真实 App 播放验证：ACTIVE 恢复 1.0.2，runtime 渲染随时间产生不同帧；
  - contact sheet 证据：`evidence/v13/20260916-official-1.0.2-contact-sheet.png`。
- **罗小黑本地包管线**：去背景后全动作统一缩放比例与共享脚底基线
  （BASELINE_Y=488），消除头顶裁切（各动作顶部留白 ≥54px）与动作间
  体型/位置跳变；geometry 的 content_bounds/motion_bounds/hit_regions
  改为构建时实测生成；重建包过 local-import preflight（PASS ['xiaohei']），
  视觉 contact sheet 存 `.release/xiaohei-local-import/`（本地通道）。

## V13-08 公开材料与脱敏快照

- 导出器修复（修误报不改放行）：
  1. 文档四段版本号（"版本均为 1.2.0.0"）误报 IP —— 版本语境豁免；
  2. 系统通用目录（Windows 系统根目录等）不再计入导出主机敏感字面量；
  3. 盘符后接省略号的文档引用不再误判 drive-absolute 路径；
  4. 文档内联代码（`` `File::method()` ``）先剥除再扫 IP；短 hex 碎片
     （`::e`）不算 IPv6 候选。
- 真实隐私清除：`build_xiaohei_local.py` 与 `process_kitten_assets.py`
  的机器路径硬编码改为环境变量 + 仓库内默认；kitten masters 的 8 个
  PNG 用 `sanitize_png.py` 原位剥离 caBX 私有 chunk。
- **脱敏快照 ACCEPTED（零阻塞）**，且在快照内从零跑测试：
  1281 passed / 15 skipped（跳过原因：12 原生门禁、1 符号链接特权、
  2 个按"文件不存在"过滤的内部冻结包用例）/ exit 0。
- 新增 `scripts/generate_release_materials.py`：从实际 onedir 反向生成
  逐文件 SBOM（298/298 全归因，绑定 receipt commit/recipe/exe sha256）、
  LICENSES/ 六份许可文本、NOTICE 与 LGPL 对应源码/替换说明。
  已对 0fdfed30 候选生成并核对（材料在发布动作时生成，不参与验收绑定）。

## V13-09 集成候选与验收

- 版本一次性收敛为 **1.3.0**（`__init__`、pyproject、Windows 版本资源、
  文档版本轴、版本一致性测试）；官方猫版本轴推进至 1.0.2。
- 候选身份（当前 HEAD `2cc0269`）：
  - commit `2cc026994afa4fdfb4730dc7b01ac2795efe648c`
  - build_id `dba3413df3ce6c4626ee64d4ca964b794ab10ff4247fd61e491de5131b875db5`
  - run `.release/2cc026994afa-20260916-235709-bcbab125`
  - receipt：post-publish dist-manifest（file_count 298，全 inventory 匹配）
- `go_no_go.py --profile alpha` 实际结果（本机无人值守会话）：
  - PASS 8 项：receipt_schema、source_toolchain_preflight、
    static_artifact_binding、receipt_bytes_before_runtime、dist_manifest、
    pytest_regular、final_receipt_bytes（双次）、
  - **FAIL 1 项：pytest_native** —— 唯一失败用例
    `test_real_ctrl_alt_t_delivery_conflict_and_release`（SendInput 注入
    Ctrl+Alt+T 未投递）。**环境对照**：同一会话用 1.2.0 基线代码
    （worktree @1084574）跑同一用例同样失败，且 1.3 未改动热键实现与
    该测试（git log 为空）→ 结论为无人值守会话的注入投递不可达
    （环境型），证据见 `evidence/v13/20260917-hotkey-environment-control.md`。
  - 其余 gate 因 native 未过按序 SKIP（fail-closed 设计）。
- **本会话结论：ALPHA-NO-GO:ENVIRONMENT**。发布前返回项：在有人值守的
  交互桌面会话重跑 `scripts/go_no_go.py --profile alpha` 取得正式
  ALPHA-GO；工程范围（V13-01 至 V13-08）全部交付并通过各自验收。
- **回滚演练 9/9 PASS**（`evidence/v13/20260917-rollback-drill.json`）：
  1.2.0 EXE 建立日常状态 → 1.3.0 EXE 接管（Agent 协议可用、任务写入、
  schema 保持 v2）→ 回滚 1.2.0 EXE（任务保留、无损坏）。角色库差异：
  1.3.0 内嵌 1.0.2 官方包；回滚后 1.2.0 解析当前官方为 1.0.1，1.0.2
  作为 library 内记录保留可渲染（数据包向后兼容），Todo/设置完全兼容。

## V13-E1：Production 真机门禁（如实状态）

六类门禁在本会话均无对应环境/时长，全部保持 PENDING，不以合成证据改写：

| 门禁 | 状态 |
|---|---|
| 混合 DPI、多屏负坐标、主屏切换、真实拔插 | PENDING（需多屏真机） |
| 真实登录自启、重复启动、锁屏/会话切换 | PENDING（重复启动已由窗口门禁覆盖部分） |
| 睡眠/唤醒与 24h 空闲恢复 | PENDING |
| 干净 Windows 11 VM 安装/首启/升级/卸载 | PENDING（需 VM） |
| 24 小时稳定性与资源趋势 | PENDING（需长时运行） |
| 三类设备 CPU/内存/DPI/媒体联动性能 | PENDING（需多设备） |

**Production 结论维持 `PRODUCTION-NO-GO:PENDING_ENVIRONMENT`。**
