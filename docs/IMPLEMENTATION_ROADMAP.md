# RetirementPet v2 持续实施路线图

> 历史边界：这是 2026-08-28 从固定猫迁移到 PetPack Alpha 的已执行路线。
> M0–M8 的 Alpha 核心已落地，M9 已取得精确 1.1.1 `ALPHA-GO`；当前工作从
> [未来工作](FUTURE_WORK.md)继续，不得重新从 M0 开始。
> 文档关系：[文档中心](README.md) · [实施状态](IMPLEMENTATION_STATUS.md) ·
> [项目更新](PROJECT_UPDATES.md)
>
> 文档状态：目标、顺序和完成定义 FROZEN；每个里程碑的内部实现细节可迭代  
> 执行方式：本地模型持续推进，不把里程碑拆成等待用户逐项批准的小任务  
> 项目根目录：`<project-root>`
> 基线日期：2026-08-28

## 1. 执行目标

本路线图从当时的固定猫咪 RetirementPet v1 出发，渐进演化出轻量 PetPack 平台，
目标包括：

- 保持透明、无边框、Tool Window、不抢焦点的桌宠；
- 同一时间只展示一个角色；
- 用户可按系列、角色和 variant 切换；
- 不同角色共享 core.* 动作语义，并可声明命名空间化专属动作；
- 文案、行为、显示和动作循环方式可配置；
- 退休倒计时作为内置模块继续工作，不随角色切换改变目标；
- 支持 PetPack 验证、安装、版本共存、激活、回滚和卸载；
- 控制面板按需加载，Runtime 长期运行轻量；
- 可构建为独立 Windows onedir EXE 并可靠开机自启。

路线图不是“大重写”。现有窗口契约、领域服务、音频、原子配置和 onedir 发布应保留，
通过适配层逐步替换固定 ActionId、CatRenderer、旧 manifest、固定布局和扁平设置。

## 2. 连续执行规则

本地模型在一次持续任务中循环执行：

1. 阅读当前事实与相关规范；
2. 选择当前最前面的未通过 gate；
3. 写下小范围实施计划；
4. 实现；
5. 运行与风险相称的测试；
6. 修复失败；
7. 更新实施状态和证据索引；
8. 自动进入下一项。

里程碑用于排序、回滚和验收，不用于每完成一步就暂停询问。只有以下情况才停下请求用户：

- 需要删除不可恢复的用户数据；
- 需要凭据、签名证书或第三方授权；
- 需要改变已冻结产品语义；
- 两种方案会产生明显不同的用户体验且文档无法裁决；
- 外部环境连续阻塞，安全替代方案已穷尽。

一般的代码结构、测试修复、兼容适配和可逆实现选择由模型自行完成并记录。

## 3. 强制工作边界

- 所有项目文件、虚拟环境、构建输出和测试工件位于 `<project-root>` 或其明确子目录；
- 不在临时下载目录继续开发或构建；
- 不把个人角色包、用户配置或许可证内容提交到仓库；
- 未经授权不联网下载素材、角色或音乐；
- 不把 Harry Potter、罗小黑等第三方 IP 作为官方内置内容；
- 不覆盖旧 evidence、receipt、release report 或正式引用结果；
- 修改已有数据格式前先提供迁移与回滚；
- 不通过更新 hash、放宽安全上限或删失败样本来使 gate 变绿。

## 4. 当时的文档阅读顺序

下面是迁移阶段使用的阅读顺序。当前会话应先读[文档中心](README.md)、
[实施状态](IMPLEMENTATION_STATUS.md)和[未来工作](FUTURE_WORK.md)，再按风险进入规范。

当时的顺序如下：

1. docs/README.md；
2. docs/CURRENT_STATE_AUDIT.md；
3. docs/DESIGN_V2.md；
4. docs/DECISIONS.md；
5. docs/PETPACK_SPEC_1_0.md；
6. docs/CONTENT_POLICY.md；
7. docs/PERFORMANCE_BUDGET.md；
8. docs/CONFORMANCE.md；
9. docs/IMPLEMENTATION_STATUS.md；
10. 本路线图。

旧 DESIGN.md、ASSETS_SPEC.md 和 CONTINUOUS_IMPLEMENTATION_PROMPT.md 仅作 v1 历史参考。

## 5. 实施顺序

### M0：事实校准与 v1 P0 修复

目标：先让当前桌宠的控制入口和证据可信，再扩平台。

工作：

- 修复托盘 context menu 真实连接；
- 固定提供显示、关闭穿透、恢复主屏、打开控制面板、退出；
- 添加鼠标穿透恢复自动化和原生检查；
- 明确旧 manifest 的 canvas、anchor、parts x/y、FPS 和 effect name 实际语义；
- 修复 sound_enabled 未进入播放路径；
- 将正常退出测试与强制终止 smoke 分开；
- 建立可重复 HWND/style/exstyle 和 foreground harness；
- 统一构建入口，消除 .spec 的开发机绝对路径；
- 将“历史记录”和“本轮实测”在状态文档中分开。

Gate：

- 开启穿透后始终能从托盘关闭；
- 源码与 frozen EXE 仍是透明 Tool Window；
- 普通启动不抢焦点；
- 正常退出路径有可重复证据；
- 本阶段没有引入 PetPack 假实现。

### M1：时钟、测量和资源基础

目标：先建立低占用底座和可测量性。

工作：

- 拆分 VisualClock 与 ServiceClock；
- 静态、隐藏、锁屏和会话断开时停止绘制；
- 倒计时与日程不再按动画帧重算；
- 动作最终 FPS 只保留一个生效来源；
- 添加进程 marker、外部 CreateProcess T0 和 Windows 性能采样；
- 引入按解码字节计费的 LRU；
- 为图片、序列、音频和缩略图建立资源预算；
- 先加入 legacy/internal renderer 的静态、轻动画和复杂动画 synthetic fixture，
  只生成 v1 baseline calibration，不伪装成 PetPack 1.0 corpus；
- 建立机器可读 fixture registry schema。

Gate：

- FakeClock 降载测试通过；
- hidden VisualClock 触发为零；
- 当前 v1 猫功能无回归；
- 可生成第一份 calibration 性能报告；
- 缓存有硬上限并能回收。

### M2：角色无关运行时状态

目标：从固定单动作模型过渡到统一语义模型，但暂不开放外部包。

工作：

- 引入 Context、PrimaryPerformance、Overlay、Emote；
- 多个 Context 可并存，PrimaryPerformance 同时只有一个；
- Emote 结束后重新从 Context 决策；
- 定义 core.* 与 publisher 命名空间动作 ID；
- 用适配器把现有 ActionController、RhythmController、ScheduleManager 接入；
- RetirementCountdownModule 成为内置模块；
- 保留内置猫作为安全角色和 last-known-good。

Gate：

- work/meeting/music 等 Context 行为可预测；
- 缺动作时仍显示当前角色 idle；
- 不存在恢复旧动作栈；
- 专属动作不能覆盖 core.*；
- 退休目标不因运行时重建而改变。

### M3：PetPack 1.0 与作者工具链

目标：纯数据角色包能够离线构建、验证、检查和预览。

工作：

- 实现 manifest schema、语义验证和稳定诊断码；
- 安全归档读取、路径规范化、哈希和资源预算；
- 实现 static、sequence、layered renderer profile；
- 实现 geometry、anchors、hit regions、文本和推荐配置；
- 建立 init、validate、preview、build、inspect CLI；本阶段不得写正式 LibraryRoot；
- deterministic build；
- 将内置猫迁移为 PetPack 协议下的内置安全包；
- 建立 minimal static 和 multi-character 参考包；
- 旧 assets manifest 仅通过可信迁移适配器使用。

Gate：

- 合法和恶意 corpus 达到 CONFORMANCE.md 的包级要求；
- 相同输入产生相同 digest；
- 包中无代码、网络和任意表达式执行路径；
- 极宽、极高和 Unicode 角色可预览；
- 包缺可选动作时不变成另一角色。

### M4：不可变库与生命周期

目标：使不可变安装、版本共存、catalog 和恢复在进程崩溃后仍一致；活动 Runtime 的交换留到 M5。

工作：

- SQLite catalog、append-style journal 和引擎生成 receipt；
- RevisionKey = ((publisher_id, package_id), version, content_digest)；
- staging、revisions 和 trash 同卷；
- install 与 activate 分离；
- install-local 在本阶段接入正式生命周期库，但默认只安装不激活；
- 相同 digest 幂等安装；
- 同版本不同 digest 在 1.0 拒绝；
- uninstall、pending-delete、文件锁和默认数据保留；
- SQLite schema 版本、事务式前向迁移、迁移前备份、崩溃恢复和新 schema 下的旧应用只读/拒写；
- 从 revision/receipt 重建 catalog，以及 v1 settings/state 的幂等导入标记；
- UI 锁为 SID + logon session，LibraryRoot 写锁为 SID + canonical LibraryRoot；
- 7 天 / 256 MiB 回滚策略按 PROVISIONAL 实现为可配置常量。

Gate：

- 生命周期状态机单测和集成测试通过；
- install/catalog 故障注入点真实 TerminateProcess 后恢复一致；Runtime swap 相关点在 M5 验证；
- catalog 不会把未完成 Revision 标为 READY；
- 本阶段只验证未活动 Revision 的卸载；活动 selection 的切走、rollback 和完整 crash gate 在 M5 完成；
- receipt 不被 Runtime 日志覆盖。

### M5：角色目录、原子切换与缓存

目标：用户可以在已安装库中安全切换角色。

工作：

- CharacterCatalog：series → character → variant；
- CharacterRuntime 候选 prepare、validate、first frame；
- generation ID 和 latest-wins；
- 帧边界原子 commit；
- 只在候选帧边界交换成功后提交完整 ActiveSelection；
- ActiveSelection 原子保存 revision、character、variant、config revision、generation 和 commit sequence；
- 保留 Context、全局配置和退休目标；
- 清理旧角色队列、overlay、声音和缓存；
- 非活动角色只保留元数据和限额缩略图；
- 失败保持旧角色并给出可操作诊断；
- 在本阶段补齐 ACTIVATE、ROLLBACK、活动 Revision 卸载和对应真实进程 crash matrix。

Gate：

- 桌面 PetSurface 同一时间只有一个正式角色身体可见；
- 标准切换无空白或不超过硬上限；
- 连续快速选择最终只提交最后一项；
- 切换失败不改变 active；
- 旧 Runtime 在 60 秒内回收；
- 内存与切换延迟符合预算。

### M6：控制面板、布局与配置

目标：提供按需创建、可恢复且不会拖慢 Runtime 的完整面板。

工作：

- 独立标准 Windows 控制面板，单实例；
- 角色库、动作、文案、显示、系统和包管理页面；
- 预定义 Layout 与独立 VisibilityPolicy；
- geometry、base_anchor、bubble_anchor、edge flip 和 content shrink；
- TextProfile、安全变量、截断与长文案预览；
- 动作自动/手动/禁用、权重与循环方式；
- 配置所有权、scope、allowed sources 和 merge policy；
- 实现冻结的有效配置优先级；
- recommendation 只通过用户确认保存静态快照；
- session preview 可取消并原子回滚；
- 固定托盘命令和 safe mode 面板。

Gate：

- 200 角色库性能 fixture 通过；
- 打开关闭面板 100 次无泄漏；
- 所有预定义布局、多 DPI 和长文案 golden matrix 通过；
- 配置来源可解释，missing/null/empty/array 语义正确；
- 面板不改变桌宠窗口类型或抢焦点纪律。

### M7：内容治理、隐私与用户数据

目标：让本地自由使用和官方分发边界明确且可审计。

工作：

- package 与 asset 级 source_ref、rights_ref；
- 视觉、音频、文字和声音分别声明；
- official、local、community channel 分流；
- 本地未知权利内容提示，不上传、不索引、不自动获取；
- 安装确认展示来源、权利、网络和覆盖范围；
- 零 telemetry、零 PetPack 网络能力；
- 日志脱敏和隐私 canary；
- 导出、备份、卸载和清理界面；
- 官方内置猫的原创或已授权证据链。

Gate：

- 默认网络抓包为零；
- 日志和导出不泄露 canary；
- official 内容全部可追溯；
- 未知权利本地包不能进入官方或社区分发流程；
- receipt 保存用户确认过的警告快照。

### M8：发布、自启与恢复

目标：形成可在干净 Windows 环境长期使用的 onedir 发布物。

工作：

- 锁定构建环境和依赖；
- 精简未使用 Qt 模块，生成 size manifest；
- 在干净 Windows 用户下验证 DLL 来源；
- HKCU Run、自启路径修复和真实注销/登录；
- 单实例结构化命令；
- 启动事务恢复、损坏 active 回退安全猫；
- safe mode、恢复主屏和 Explorer 重启；
- 正常退出与系统会话关闭；
- 升级保留用户库与配置。

Gate：

- 不依赖 Python、Anaconda、PATH 或开发机；
- MiKTeX 等竞争 Qt 环境下仍从 onedir 加载；
- 自启不抢焦点；
- 任务栏、Alt+Tab、透明和托盘恢复通过；
- dist size 报告完整，超过理想范围有解释。

### M9：发布候选与完整 gate

目标：以证据而非口头状态完成 v2。

工作：

- 完整合法、降级、损坏和恶意 PetPack corpus；
- 完整生命周期 crash matrix；
- 源码与 frozen EXE 原生 Windows matrix；
- 多屏、DPI、锁屏、睡眠和 Explorer；
- 低配与主流参考机性能；
- 24 小时 soak；
- 文档、事实、状态和证据索引终审；
- 生成发布说明、已知问题、回滚和用户迁移说明。

Gate：

- CONFORMANCE.md 的最终 GO/NO-GO 清单全部满足；
- 性能预算已完成一次冻结，不再标为 calibration；
- 不存在未解决 P0；
- P1 豁免有责任人和到期版本；
- 发布构建、SBOM/依赖清单、hash 和证据归档完整。

## 6. 每个里程碑的完成定义

一个里程碑只有同时满足以下条件才算完成：

- 代码实现；
- 正常、边界和失败路径测试；
- Windows 相关功能有真实原生证据；
- 对用户数据的迁移与回滚已验证；
- 性能影响有测量或明确标记为待校准；
- 文档和 IMPLEMENTATION_STATUS.md 更新；
- 未把未完成项写成已通过；
- 工作树中没有意外构建物、个人数据或秘密。

测试失败时留在当前里程碑继续修复，不以“代码已写完”进入下一阶段。

## 7. 实施状态格式

IMPLEMENTATION_STATUS.md 的每个阶段使用：

| 字段 | 内容 |
|---|---|
| 状态 | NOT STARTED / IN PROGRESS / BLOCKED / IMPLEMENTED / VERIFIED |
| 设计依据 | 文档和 ADR |
| 本次变更 | commit 与文件 |
| 自动化测试 | 命令、通过数、失败数、日期 |
| 原生验收 | 环境、报告路径、日期 |
| 性能 | calibration 或 frozen 结果 |
| 已知问题 | 优先级、责任人、到期版本 |
| 下一 gate | 可机器或人工验证的条件 |

IMPLEMENTED 只表示代码存在；VERIFIED 才表示对应 gate 的当次证据完整。

## 8. 分支与提交建议

- 每个提交只跨一个可解释的风险边界；
- schema、迁移、Runtime 和 UI 尽量分提交；
- 先提交测试和 fixture 也可以，但不能用只测 mock 的结果替代真实 Windows；
- 不重写已发布 receipt、release report 和历史 benchmark；
- 每个数据格式变化增加新版本和迁移测试；
- 重构前保留适配器，使内置猫始终可运行；
- 任何阶段都保持 safe mode 和安全猫可启动。

## 9. 给持续实施模型的主提示词

可将以下内容直接交给本地模型：

    你正在 <project-root> 持续实施 RetirementPet v2。

    首先完整阅读 docs/README.md 指定的 v2 文档顺序，并核对当前代码、
    git 状态和 IMPLEMENTATION_STATUS.md。把 CURRENT_STATE_AUDIT.md 当作
    2026-08-28 的事实基线，不把历史“131 passed”当作本轮已运行结果。

    按 IMPLEMENTATION_ROADMAP.md 从最早未通过 gate 开始，持续执行
    “检查—计划—实现—测试—修复—记录—继续”。不要在每个小任务或里程碑后
    等待用户确认；只在路线图列出的破坏性操作、凭据/授权、冻结语义冲突或
    无法安全裁决的重大产品分歧时停下。

    继承现有透明 Frameless Tool Window、不抢焦点、退休倒计时、领域服务、
    原子配置和 onedir 基础；不要把应用改回普通 Windows 窗口，也不要从头重写。
    优先修复托盘穿透恢复 P0，再分离 VisualClock/ServiceClock，然后实施
    Runtime 状态、PetPack、生命周期、原子切换和控制面板。

    所有角色包都是纯数据。禁止执行包内 Python、QML、JS、DLL、EXE、HTML、
    远程 URL 或任意表达式。缺可选动作时保留当前角色并回到 core.idle，
    不允许借用另一角色身体。退休目标和 Context 在角色切换时保留。

    所有项目操作留在 <project-root>；不要在临时下载目录
    新建环境或构建。不要联网获取第三方角色、音乐或素材。不要把个人数据、
    未授权 IP、绝对开发机路径和构建缓存提交到仓库。

    每个阶段都运行与风险相称的测试，Windows 契约必须有真实 HWND/EXE 证据，
    性能结果标清 calibration 或 frozen。不要为了通过 gate 修改 hash、
    放宽上限、删除失败样本或把历史记录冒充本轮结果。

    持续更新 IMPLEMENTATION_STATUS.md：区分 IMPLEMENTED 与 VERIFIED，
    记录命令、日期、环境、原始证据和已知问题。当前 gate 真正通过后自动进入
    下一项，直到 CONFORMANCE.md 的 v2 GO 条件全部完成，或遇到必须由用户
    决策的明确阻塞。

## 10. 当前落点

截至 2026-09-01，M0–M8 的 Alpha 底座已经完成，M9 的自动流水线已对精确
1.1.1 artifact 给出 `ALPHA-GO`。正式发布仍缺六项环境门禁，不得把 Alpha 结论
扩写为 Production。

后续内容优先级已经转向角色库。先把半写实猫从单张静态母版扩为独立 idle、work、
rest，再补生活动作、作者工具和角色库管理。详细排序、问题归属和重新讨论条件见
[未来工作](FUTURE_WORK.md)。
