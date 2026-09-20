# RetirementPet 实施状态

> 文档关系：[文档中心](README.md) · [项目更新](PROJECT_UPDATES.md) ·
> [当前问题与未来工作](FUTURE_WORK.md) · [Alpha 试用说明](ALPHA_TRIAL.md)
>
> 更新日期：2026-09-15
>
> 本文记录当前源码能力与尚未完成的环境门禁。精确候选的 commit、build ID、
> artifact ID、EXE SHA-256 和结论只以该次 `release-receipt.json` 与
> `acceptance.json` 为准；完整身份不在会随提交变化的状态文档中重复维护。
>
> 治理原则：冻结事实，不冻结探索。原始 receipt、事件和验收证据追加保存；实现、
> 分析与说明通过新版本迭代，不覆盖旧证据。

## 当前结论

应用 **1.2.0** 已从 70c5934 的干净源码快照构建候选，并由绑定该提交、
build_id 和 EXE 哈希的新 receipt 取得 `ALPHA-GO`：23 项 PASS，1 项因未观察到
真实拓扑变化而 SKIP。当前 Production 结论仍为
`PRODUCTION-NO-GO:PENDING_ENVIRONMENT`。精确身份与主控复核见
[V12-09 阶段报告](V1_2_V12_09_REPORT.md)。以下 1.1.1 内容保留为历史冻结记录。

1.0.1 与 1.1.0 候选保留各自原有 **ALPHA-GO** 证据。应用 1.1.1 已从干净发布
快照完成可信构建，并由精确 receipt 取得 `ALPHA-GO`。同一份 acceptance 同时给出
`PRODUCTION-NO-GO:PENDING_ENVIRONMENT`，因此它可在当前机器可回滚初试，仍不是
Production 认证。

这份 1.1.1 artifact 只属于私有验收证据，目前也是
**PUBLIC-BINARY-NO-GO:THIRD_PARTY_NOTICES_MISSING**。其 onedir 目录没有随包携带
完整 LICENSE/NOTICE、实际组件清单和 LGPL 对应源码入口，不能直接上传到公开
GitHub Release。公开源码采用 MIT 不会补足二进制内 PySide6、Qt、Python、
PyInstaller 及其子组件的许可义务；可下载便携版必须从公开提交重新构建并单独验收。

本轮文档或后续源码修改不会改变已经冻结的 1.1.1 artifact。任何新提交都必须重新
构建、绑定 receipt 并运行 `scripts/go_no_go.py`，不能继承旧发布快照的结论。精确
commit、build ID、artifact ID 和 EXE SHA-256 只保存在私有追加式证据域；
[项目更新记录](PROJECT_UPDATES.md)只给出不含内部标识的公开摘要。

1.1.1 冻结发布快照的验证：

- 完整测试基线：803 passed、13 skipped（跳过项均为需显式启用的环境型/原生测试）；
- Windows 原生层：12 passed，包含真实 HWND/进程检查、中文字形、随机名称的 HKCU
  注册表沙箱，以及真实 `RegisterHotKey → WM_HOTKEY → Qt` 链路与释放；
- 在 1.1.1 冻结验收当时的检查范围内，可信验收执行边界、性能/稳定性 harness
  复核结论为 P0=0、P1=0；2026-09-11 的后续源码审计又发现 idle 序列集成缺陷和
  多屏脚本证据缺陷，见下文。旧证据不回写，新候选必须修复后重新验收；
- PySide6 6.8.3 与锁定发布工具链校验通过。

每次提交后的构建脚本都会在 Git archive 快照内重跑测试；最终数字和日志随当次
release bundle 保存。本节不会用历史 `dist` 的结果替代新候选结果。

## 里程碑

| 里程碑 | 当前状态 | 已落地事实 |
|---|---|---|
| M0 窗口与基础恢复 | **VERIFIED** | 透明无边框 PetSurface、托盘固定恢复命令、置顶/穿透切换、单实例、正常退出 |
| M1 时钟与资源基础 | **VERIFIED** | VisualClock/ServiceClock 分离、隐藏零视觉 tick、marker、按解码字节计费 LRU |
| M2 角色无关决策 | **VERIFIED** | ContextStore、PrimaryPerformance、Overlay、Emote 过期重求解、当前角色 idle 回退 |
| M3 PetPack 1.0 | **VERIFIED（Alpha 核心）** | 受限 ZIP、严格 manifest、命名空间/rig/资源验证、确定性构建、512px 官方猫与半写实本地预览包 |
| M3.5 PetPack 一致性闭环（1.2.0 V12-02） | **返工后自测完成待主控复验** | 兼容性校验（engine 范围 + 必需能力 `MAN_E006`，注册表仅收有运行时消费证据的能力）、geometry 严格校验（`MAN_E004`）、绑定/缩略图/文本/出版者/legal 引用闭包、legal 文件 size/sha 复算、TextProfile 安全边界、publisher_ref 必填 + 精确钉定的四个历史包豁免（`PPK-MAN-W002`）、未知语义与不可绘制 renderer 的显式降级（`PPK-ACT-W001/W002`，不可绘制 idle 一律 `ACT_E004` 拒绝，`PPK-MAN-W001`）、ACTIVATE_COMMITTED 追加审计事件（已确认提交在同事务落持久待记事实，审计写失败可跨切换/重启按事实补记，幂等且不反转选择）；四列对照与逐包审计见 [PETPACK_V12_CONSISTENCY.md](PETPACK_V12_CONSISTENCY.md)——内部保留的四个历史/当前包经版本化豁免可加载（ACCEPT_WITH_DEGRADATION），未改任何冻结包 |
| M4 不可变库 | **VERIFIED（核心）** | SQLite catalog、PUBLISH_INTENT、receipt、真实进程强杀恢复、同版本摘要冲突拒绝 |
| M5 角色切换 | **VERIFIED（核心）** | 本地包严格预检/导入、一次性角色选择、离屏首帧、两阶段提交、latest-wins、CAS、旧内置角色原子迁移、恢复/卸载安全切换、缓存释放 |
| M6 控制面板与配置 | **VERIFIED（Alpha）** | 八页按需创建面板、单实例路由、配置优先级、随机动作持久开关、预览边界诚实 |
| M6.5 Todo | **VERIFIED（Alpha）** | 独立 `tasks.db`、最多六层树、事务删除/移动、短中长期、截止日、单焦点、故障禁写、右键/全局快捷入口；1.2.0 起 schema v2（独立重要/紧急、可恢复归档、长备注）、v1→v2 带验证备份的自动迁移、有上限的备份保留与显式备份/恢复入口 |
| M7 内容与隐私 | **VERIFIED（核心）** | 逐资产 rights/source、信任通道、零默认联网、日志 canary、路径和异常脱敏 |
| M8 Windows 发布 | **VERIFIED（Alpha）** | 锁定工具链、Git archive 构建、编译身份、完整目录 receipt、原子发布/回滚、自启 |
| M9 完整发布验收 | **1.2.0 ALPHA-GO / PRODUCTION PENDING** | 精确 1.2.0 artifact 已通过当前机器 Alpha 自动门禁；Production 的多机、真机生命周期和正式长期预算仍待完成 |
| M10 公开分发许可 | **SOURCE READY / BINARY NO-GO** | MIT、贡献规则和脱敏公开快照已建立；旧 onedir 缺少随包第三方许可与来源材料，禁止上传 |

## 当前 Alpha 能力边界

- 桌宠是透明悬浮窗口，不是普通任务栏应用窗口；控制面板才是独立标准窗口。
- 当前自动启用的安全锚仍只有一个官方退休猫。角色页可导入本地 `.petpack`；导入
  先做 64 MiB 受限单次快照，并在可超时终止的独立进程中验证透明 PNG
  `static/sequence`、ID/引用、声明尺寸、真实 Alpha 与透明四角；关闭面板不会等待
  不可中断的图片解码。独立进程还会使用真实 offscreen `QPixmap` 验证每个角色的
  idle 首帧；GUI 只接收严格 JSON 元数据和有界原始快照，不反序列化 Python 对象、
  不解码导入图片。固定确认页显示本地信任/权利边界，确认代码进入同一快照绑定的
  不可变 receipt；receipt ID、receipt SHA 与 archive SHA 均进入 PUBLISH_INTENT，
  并在 READY 提交和崩溃恢复前复验。普通导入本身不会自动激活角色。
- 首次手动启动会在角色页提供一次性选择。继续官方猫只写选择 campaign，不改变
  ACTIVE/LKG 或 receipt；启用半写实猫会先钉扎 archive SHA、RevisionKey、唯一
  character ID 与 warning 集，再按明确授权顺序 INSTALL→ACTIVATE。取消、关闭、
  预检失败、安装失败或切换失败均不写完成标记。`--startup` 与重复自启实例保持
  静默；用户之后手动再次启动才会唤起同一面板。ACTIVATE 的提交结果不确定或输掉
  CAS 时不谎报“保留原角色”：目标角色保持未启用，并按持久化权威恢复；权威不可
  判定时进入 Bootstrap safe mode，已提交的 READY/receipt 事实仍保留供重试。
- 随附半写实退休猫 `0.1.1` 为 AI 辅助原创的静态竖切包，具有独立 PackKey；
  idle/work/rest 暂共用一张透明母版，其他 core semantic 明确回退 idle。它不覆盖
  或改写两个冻结的官方退休猫 Revision。
- 角色正文、动作字幕/气泡/通用效果已分层；512px 正文通过 QPainter 一次映射到
  设备像素。预定义中文仅在 Qt 确认可用字形时显示，否则使用 ASCII 后备文案。
- 精确的旧版内置 Revision 仍作为不可变事实保留；1.0.1 可用时 UI 隐藏旧条目，
  只迁移精确旧内置活动选择，失败保留旧 ACTIVE/LKG，外部角色不受影响。
- 动作页的随机动作开关会真实保存；文案页仅安全预览；布局与可见性控件明确禁用，
  不会假装已经应用。
- PetPack 校验与本版运行时能力一致：引擎不支持的必需能力或 engine 版本范围
  不匹配的包在安装前即拒绝；未知 core 语义只有声明为可选能力才安装（该动作
  禁用并在 receipt/详情可见）；引擎画不出来的 renderer 组合会显式降级标注，
  绑定到 core.idle 的不可绘制动作在校验层即被拒绝（`PPK-ACT-E004`，准入按
  运行时同规则取 loop 优先的单一 lifecycle 段判定），离屏首帧门禁仅作第二道
  防线。校验拒绝不改变已安装事实与当前活动角色。
- Todo 支持短期、中期、长期、最多六层子任务、同级上移/下移、截止日期、
  完成/恢复和单一焦点。删除父任务会事务性删除整个子树，当前没有撤销。
- Todo 存储自 1.2.0 起为 schema v2：新增独立的重要/紧急两轴（迁移后的旧任务
  保持"未分类"，不按标题或期限猜测）、逐任务可恢复归档与纯文本长备注。
  首次打开旧 v1 库时自动迁移：先以 SQLite backup API 生成经完整校验的
  pre-migration 备份（写入 `tasks.db` 同级的 `backups/`，保留数量有上限，
  且永远保留最近一份可用迁移前副本），迁移在单事务内完成并逐行比对，
  中途失败或提交失败都会回滚到原 v1 数据；备份失败则整个迁移不开始。
- 旧版本程序不能直接读写 v2。回退旧版应用前必须先用恢复入口还原一份 v1
  备份；"恢复迁移失败"（数据原样保留，可重试）与"回到旧版"（有意的
  数据回滚，不包含备份之后新增的任务）是两种不同操作。注意：本版恢复入口
  还原 v1 备份后会立即按正常迁移流程再次升级回 v2（含迁移前备份），即
  "新版恢复旧备份"不等于"旧版可直接打开"；真正的降级回滚需在应用外离线
  还原 v1 库文件后再启动旧版程序（离线演练流程见
  [V1_2_NODE_A_REPORT.md](V1_2_NODE_A_REPORT.md) 第 6 节，尚未执行）。
- Todo 的显式备份与恢复入口目前为服务层 API（手动备份、备份列表、
  验证后恢复；恢复前会先备份当前库）。恢复结果区分两种成功：
  `store_available=True` 为全部可用；重开失败时返回
  `store_available=False` 与原因（数据已恢复落盘，模块进入禁写故障态，
  后续可用恢复入口再次自修复），待办面板的可视入口随 1.2.0 面板模块
  一起交付。
- 桌宠和托盘右键菜单均可直接打开 Todo；Windows 默认尝试注册 `Ctrl+Alt+T`，由
  `WM_HOTKEY` 事件驱动而不增加轮询。非 Windows 或组合键冲突时只记录日志并降级
  到菜单入口，注册会在应用关闭时显式释放。
- Todo 存储异常时页面进入不可用/禁止写入状态，可能显示空视图，但不会改写原
  `tasks.db`，也不会带崩桌宠主进程。故障态下所有读（含 `focus_task`）统一
  返回安全空结果，恢复入口仍可用作自修复；恢复数据落盘但重开失败时返回
  部分成功状态而非普通成功。
- 音乐仅使用用户选择的本地文件；会议为本地手动或时间段规则；无云同步、账号、
  协作、附件、在线曲库或第三方日历。

## 可信构建与验收不变量

1. 公开 `scripts/build_lock.json` 只含稳定、机器中立的逻辑约束；正式发布还必须
   使用 `.release/toolchain-attestation.json` 的精确本机证明。完整证明不进入 Git
   或产物，产物仅保存整份 canonical 证明（已含 public-lock 摘要）的 SHA-256，
   并由 build ID 与 receipt 绑定；缺失、漂移或摘要不一致均 fail-closed。
2. 构建输入来自固定 commit 的 Git archive；快照逐文件与 Git blob 校验，不能用
   CRLF 转换或遗留 `.pyc` 偷换源码。
3. frozen EXE 同时携带编译进 PYZ 的身份和目录可见的 `build-info.json`，二者必须
   完全一致。
4. 在执行任何候选程序之前，先静态绑定完整目录的 artifact ID、EXE SHA、文件数、
   总字节、build ID、commit、tree、必需文件和 reparse/symlink 状态。
5. 有效 receipt 与 artifact 不一致是明确的 `ALPHA-NO-GO`（退出码 1）；格式、
   工具链、超时或证据无法判定才是 `INVALID_RUN`（退出码 2）。两类均尽可能写出
   `acceptance.json`。
6. frozen smoke 必须由精确 EXE 运行本地角色包正常预检和强制超时预检，分别证明
   QPixmap 首帧可用以及子进程可回收；schema 3 或缺少上述证据的历史报告无效。
   首次 runtime attestation、EXE 窗口 harness、smoke、性能和稳定性观测前均再次
   静态绑定；失败后不再启动候选。
7. 候选先在唯一临时目录验证。公开 `dist` 的替换有事务状态和故障注入点；发布后
   再以候选 receipt 静态绑定，任何失败恢复上一完整目录。
8. Alpha 性能健康与 120 秒稳定性检查绑定同一 receipt，但明确设置
   `production_gate=false`；它们不能冒充正式性能预算或 24 小时 gate。

## 如何判断当前候选

构建成功后，使用 `scripts/build.ps1` 最后打印的精确 receipt 路径运行：

```powershell
.venv\Scripts\python.exe scripts\go_no_go.py `
  --profile alpha `
  --artifact-dir <project-root>\dist\RetirementPet `
  --receipt <本次 release-receipt.json>
```

从命令打印的 Evidence 目录读取 `acceptance.json`：

- `alpha_trial_verdict == "ALPHA-GO"`：该精确 artifact 可在当前机器进行可回滚初试；
- `production_release_verdict == "PRODUCTION-NO-GO:PENDING_ENVIRONMENT"`：正式发行
  仍未认证，这是当前预期的同时结论；
- `artifact.build_id`、`receipt.artifact_id`、`receipt.exe_sha256`：反馈和复核时使用
  的三项身份。

不要把一份 receipt、另一目录的 EXE 或历史 acceptance 混用。

## 证据状态

- 当前 1.1.1 的完整冻结身份、receipt 和 acceptance 位置只保存在私有追加式
  证据域；[项目更新](PROJECT_UPDATES.md)仅保留不含内部标识的公开摘要。
- 新可信构建和验收证据位于被 Git 忽略的 `.release\<run>\`；每次运行使用唯一
  目录，原始日志与 JSON 不覆盖。
- 仓库 `evidence\` 下所有 2026-08-28 至 2026-08-31 的结果统一标记为
  **LEGACY_UNBOUND / SUPERSEDED_FOR_CURRENT_CANDIDATE**。它们只说明历史实现和
  harness 演进，不能支持当前候选的 GO。
- 历史详细状态保留在 Git 历史中；本文件不重复旧 EXE SHA、旧 dist 尺寸或旧测试
  数，以免被误认成当前事实。

## Production 仍待完成

以下项目不会因 Alpha 自动检查通过而降级或消失：

1. 100%/150%/200% 混合 DPI、负坐标副屏、跨屏和拔屏恢复；
2. 真实注销/登录后的自启、不抢焦点与延迟；
3. 锁屏、会话断开、睡眠和唤醒矩阵；
4. 无 Python/Anaconda 且含竞争 Qt PATH 的干净 Windows 11 VM；
5. 绑定同一 artifact 的完整 24 小时观测和正式泄漏判据；
6. 三台参考设备的 10 分钟性能样本与预算冻结。

以上六项是 Production 环境门禁。公开 Alpha 二进制还必须先通过独立的第三方组件
清单、许可文本、LGPL 对应源码与用户替换说明门禁；两类结论不能互相替代。

因此当前版本即使得到 `ALPHA-GO`，也必须同时保持
`PRODUCTION-NO-GO:PENDING_ENVIRONMENT`。

## 2026-09-11 后续审计发现

本节是对当前源树和规划的追加审计，不更改 1.1.1 已冻结 artifact 的历史身份，也
不产生新的构建或发布结论。

1. **正常 idle 序列不会推进。** 无当前动作在控制器中代表 idle，但应用合成快照时
   把 `elapsed_ms` 与 `frame` 固定为 0；PetPack Runtime 又只按 `elapsed_ms` 选择
   sequence 帧，因此正常待机可能一直显示第一帧。现有测试覆盖了手工 elapsed 和
   首帧绘制，没有覆盖 App 的 `current=None` 路径。它是拟真猫动画前置修复项。
2. **当前多屏脚本不能作为 Production 证据。** `verify_displays.py` 只枚举显示器，
   没有启动 `--exe` 目标，也没有取得逐显示器 DPI、验证窗口落点、脚底锚或拓扑
   变化；混合 DPI 与负坐标缺失时仍会记 PASS。修正后的 gate 必须把未具备的环境
   标成 SKIP，并在真实源码/EXE 上验证。
3. **Todo 的三个用户需求此前没有进入产品路线。** 四象限优先级、完成后可恢复归档
   和长备注侧页现已纳入[未来工作](FUTURE_WORK.md) R5。当前 schema v1 没有数据
   承载型迁移路径，必须先完成 schema v2 迁移、备份和回滚设计。

所以当前更准确的结论仍是“可回滚 Alpha 试用”，不是可长期稳定使用或公开二进制
发布。角色素材工作可以开始，但批量 Blender 序列应在 idle 时间轴与 geometry/锚点
消费修复后进入正式验收。

## 已知非阻断项

- 本地角色卸载 UI、更多官方角色和高质量动画素材仍待后续版本；
- layered renderer 完整关键帧、极宽/极高固定 artifact fixture 尚未补齐；
- 被文件锁占用的 pending-delete 自动重试与多会话 LibraryRoot 写锁仍属 P2；
- 验收控制器遇到极端的外层 harness 超时时会判 `INVALID_RUN`；其子进程通常由
  harness 自身清理，但 GUI broker 异常卡死时仍可能需要人工确认并结束残留实例；
- 半写实预览已可 A/B 切换，但独立动作姿态、逐帧身份一致性与 512×512 统一画布
  仍是下一轮美术工作，不属于本次静态竖切承诺。

首次试用与成对回滚程序/数据的步骤见 [Alpha 试用说明](ALPHA_TRIAL.md)。角色库的
实装作者范围见[角色库扩展指南](CHARACTER_LIBRARY_GUIDE.md)，后续排序和问题归属
见[未来工作](FUTURE_WORK.md)。
