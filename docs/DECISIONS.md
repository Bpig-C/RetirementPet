# RetirementPet v2 决策记录

> 文档关系：[文档中心](README.md) · [v2 总规范](DESIGN_V2.md) ·
> [当前实施状态](IMPLEMENTATION_STATUS.md) · [未来工作](FUTURE_WORK.md)
>
> 状态：FROZEN  
> 形成方式：ADR-V2-001–020 来自 2026-08-23 至 2026-08-28 的逐项讨论与认可；
> ADR-V2-021–022 是在“写成完整可实施设计”范围内，由交叉审查形成的规范一致性澄清；
> ADR-V2-023 来自 1.1.1 一次性角色选择的明确产品收口；ADR-V2-024 来自文档链
> 治理复核，各记录都保留自己的形成边界
> 适用范围：RetirementPet v2.0  
> 修改规则：不得静默改写；任何变更新增 ADR、说明影响和迁移，不覆盖旧记录

## 1. 决策状态

| 状态 | 含义 |
|---|---|
| FROZEN | 已确认，实施者不能自行换方案 |
| PROVISIONAL | 方向已确认，数值需实测校准 |
| SUPERSEDED | 被后续明确决策替代，旧记录仍保留 |
| EXPLORATORY | 仅候选，不进入 v2.0 完成定义 |

## 2. 已确认决策

### ADR-V2-001：项目位置固定在 E 盘

状态：FROZEN

决定：

- 开发仓库使用独立的项目目录；
- 不再以临时下载目录作为项目或正式环境；
- 正式虚拟环境位于项目内；
- 运行时大体积角色库允许用户选择 E 盘，不能硬编码所有用户都有 E 盘。

原因：Downloads 是临时目录，容易与 Anaconda、其他 Qt 和打包产物混淆；运行时路径仍需遵循可移植的 Windows 规则。

### ADR-V2-002：采用产品方案 B

状态：FROZEN

决定：

- 通用桌宠 Engine 从退休倒计时桌宠演进；
- RetirementCountdownModule 是官方内置模块；
- 原退休猫是不可删除的官方参考角色和安全回退；
- 模块与角色通过组合关联，不用类继承表达产品继承。

否决：

- 把退休倒计时从产品中移除；
- 让每个角色包自行保存一份退休目标；
- 用继承层次把角色、模块和窗口耦合。

### ADR-V2-003：同一时间只展示一个正式角色

状态：FROZEN

决定：

- 用户可以安装多个系列和多个角色；
- 正式运行时只有一个 active slot；
- 预览 Runtime 隔离且不改变 active slot；
- 系列用于 UI 分组，不代表多个角色同时显示。

重新讨论触发：只有未来明确决定支持多宠物，并同时给出窗口、调度、音频、CPU、配置和交互预算。

### ADR-V2-004：运行时拆分四类状态

状态：FROZEN

决定：

- Context 保存长期事实且可以并发；
- PrimaryPerformance 任一时刻只有一个；
- Overlay 可以多个并发；
- Event / Emote 为短暂事件。

事件结束后重新依据最新 Context 求解，不恢复旧动作栈。

否决：继续把会议、工作、音乐、随机动作和点击表情全部塞入一个 current action。

### ADR-V2-005：公共动作是语义，不是共享图片

状态：FROZEN

决定：

- Engine 定义 core.* 语义；
- 角色显式映射这些语义到自己的表现；
- 每个角色必须有 core.idle、缩略图和几何；
- 工作、会议、音乐、干饭、健身等为可选能力；
- 角色专属动作使用完整命名空间。

缺动作时保持 Context，回到当前角色 idle，并由 Engine 叠加语义提示。禁止替换为另一角色身体。

同包内显式相同 rig_contract 的角色，只能共享参数化 layered motion template 和
body_independent 效果；每个角色仍绑定自己的 body slot。static/sequence 主体帧不能跨角色复用。

### ADR-V2-006：一个 PetPack 对应一个系列

状态：FROZEN

决定：

- PetPack 1.0 是自包含 collection pack；
- 一个包包含一个 Series、一个或多个 Character 和 Variant；
- 同包内可以共享资源；
- 不允许跨包依赖；
- 同名系列通过发布者和来源并存。

身份采用 publisher.package.series.character，声明版本加内容摘要确定精确 Revision。

否决：以系列显示名作为全局唯一 ID；相同 SemVer 的不同内容静默覆盖。

### ADR-V2-007：PetPack 是统一、版本化的纯数据协议

状态：FROZEN

决定：

- 协议覆盖身份、manifest、core 语义、Context allowlist、动作能力、renderer、配置建议、几何、来源和权利；
- schema 主版本破坏性，次版本通过 required / optional capability 协商；
- 未知必需能力拒绝，未知可选动作可以降级；
- 未知 renderer 不得猜测执行；
- core 字段严格校验，扩展只能进入发布者命名空间。

否决：把当前 assets/manifest.json 直接宣布为 PetPack 1.0。

### ADR-V2-008：PetPack 不得执行代码

状态：FROZEN

允许：

- 严格 JSON；
- 白名单图片和短音频；
- 受限静态、序列帧、分层时间线；
- 纯文本和安全模板变量。

禁止：

- Python、QML、JavaScript、HTML；
- EXE、DLL、Qt 插件、快捷方式、自定义字体和视频；
- 远程资源、任意表达式、脚本迁移；
- 网络、文件、注册表、剪贴板、麦克风、摄像头和原始输入权限；
- 包定义系统优先级、自启或日志策略。

原因：本地导入的文件仍是不可信输入；无脚本不代表无风险，媒体解码和归档仍需预算。

### ADR-V2-009：配置按所有权和来源解析

状态：FROZEN

决定：

- 每个键声明 owner、scope、allowed_sources、merge_policy 和 reset_target；
- 禁止任意 JSON 深合并；
- missing、null 和 empty 不等价；
- 数组默认整体替换；
- 包建议只有用户显式应用后才成为带来源的静态快照；
- 包升级不静默重放新建议；
- 删除的动作设置进入 orphan 区。

有效值优先级：

1. session preview；
2. per-character user override；
3. global user override；
4. selected profile；
5. user-applied recommendation snapshot；
6. character-specific default；
7. module default；
8. engine default。

该顺序只适用于相应键允许的来源。

### ADR-V2-010：文案、行为和显示分别配置

状态：FROZEN

决定：

- TextProfile、BehaviorProfile 和 DisplayProfile 分离；
- 每个类别同一时间只激活一个 profile；
- 文案使用语义键、纯文本和安全变量；
- 用户可以设置全局和角色覆盖，并查看有效值来源；
- 行为配置只能在动作声明能力内选择自动、手动、禁用和循环；
- Engine 安全、隐私和资源硬约束不可由 profile 修改。

### ADR-V2-011：只提供预定义布局

状态：FROZEN

决定：

- Layout 与 VisibilityPolicy 分离；
- v2.0 提供 pet_only、compact、standard、hover_expand、focus；
- 包可以推荐布局，不能注入自由坐标布局；
- 角色通过 logical_canvas、content_bounds、motion_bounds、base_anchor、bubble_anchor 和 reference_height 适配布局；
- 窗口收缩到内容需要，避免透明区域拦截桌面点击。

否决：首版自由拖拽式布局编辑器。

### ADR-V2-012：控制面板与宠物窗口分离

状态：FROZEN

决定：

- 控制面板是按需创建的标准 Windows 窗口；
- 透明宠物窗口不承担复杂设置 UI；
- 入口包括托盘、再次启动 EXE 和宠物交互；
- 托盘固定提供显示、关闭穿透、恢复主屏、打开控制面板和退出；
- 安全模式和内置角色必须能恢复控制。

当前托盘没有真正绑定右键菜单，是迁移前 P0 缺口，不改变本决策。

后续状态：该 P0 已在 M0 修复。此句保留的是决策形成时的事实，当前实现以
[实施状态](IMPLEMENTATION_STATUS.md)为准。

### ADR-V2-013：角色切换使用两阶段提交

状态：FROZEN

决定：

- 候选 Runtime 先解析、加载最低资源、计算配置并完成离屏首帧；
- 在帧边界交换；
- active slot 只在成功提交后更新；
- 旧 Runtime 在持久化成功前保留；
- 连续选择采用 generation ID 与 latest-wins；
- 旧角色声音、Overlay 和专属队列在提交后清理；
- 预览不污染正式状态。

否决：先清空旧角色再加载新角色；加载一半即写长期 active ID。

### ADR-V2-014：安装、激活和更新相互分离

状态：FROZEN

决定：

- INSTALL 只发布不可变 Revision；
- UPGRADE 是安装新 Revision 后可选 ACTIVATE；
- ROLLBACK 等同激活旧精确 Revision；
- UNINSTALL 必须先安全切走，再移入 trash；
- active 是引用，不是包目录状态；
- staging、revisions 和 trash 同卷；
- 使用 SQLite 索引、journal、receipt 和幂等启动恢复；
- 同版本不同摘要拒绝覆盖；
- 默认保留用户设置、个人素材和原始归档。

上一健康版本保护期 7 天与 256 MiB 回滚预算为 PROVISIONAL。

### ADR-V2-015：安全角色和 last-known-good

状态：FROZEN

决定：

- 内置原创退休猫不可卸载；
- active 缺失、损坏或不兼容时先尝试 last-known-good，再使用安全角色；
- 一次异常断电不直接隔离包；
- 连续两次在健康 checkpoint 前失败才自动回退；
- 缺一个可选动作只熔断该动作，不隔离整个包。

### ADR-V2-016：第三方内容采用双通道

状态：FROZEN

官方内容：

- 只允许原创、明确授权、公有领域或兼容开放许可证；
- 不内置、不抓取、不代下载未经授权的第三方角色、台词、音乐或配音。

本地私人内容：

- rights unknown 可以在醒目警示后导入；
- 标为包作者声明、发布者未验证、权利未验证；
- 不上传、不索引、不自动分享。

未来 Community / Online 通道为 EXPLORATORY，需要签名、审核、撤回、投诉和法律复核。

### ADR-V2-017：运行时资源优先于磁盘体积

状态：FROZEN，数值 PROVISIONAL

优先级：

1. 稳定和正确；
2. 静态 CPU 与唤醒；
3. 常驻内存；
4. 动画 CPU；
5. 启动和切换；
6. 磁盘体积。

决定：

- VisualClock 与 ServiceClock 分离；
- 隐藏和静态时视觉 tick 为零；
- 当前角色资源按需加载，有内存预算和 LRU；
- 可以使用安装期磁盘缓存换取运行时低 CPU；
- 用户音乐与角色短音效分离；
- QtMultimedia 可以保留在磁盘，但必须真正惰性初始化。

候选内存、CPU和响应门槛见 PERFORMANCE_BUDGET.md。

### ADR-V2-018：作者工具链与 Runtime 分离

状态：FROZEN

决定：

- Runtime 只安装、管理和运行已经验证的包；
- 作者工具提供 init、validate、preview、build、inspect、install-local；
- 预览器独立于正式 Runtime；
- source、build、report 和最终 petpack 分离；
- build 生成边界、缩略图、hash、资源估算和确定性归档；
- 内置退休猫和最小静态包都必须走相同 PetPack 协议。

否决：把完整角色编辑器塞进常驻桌宠进程。

当前 1.1.1 作者工具已提供 `init`、`build`、`validate`、`preflight` 和 `inspect`。
独立 `preview` 与 `install-local` 仍是未完成目标，不能从本 ADR 推断为现有命令。

### ADR-V2-019：透明窗口与托盘属于发布硬门槛

状态：FROZEN

决定：

- Frameless、Tool、Translucent 是基础窗口契约；
- 置顶和穿透切换不能丢失基础 flags；
- 正常启动和自启不抢焦点；
- 原生 HWND、透明合成、任务栏、Alt+Tab 和点击区域必须在真实 Windows 验证；
- offscreen 测试不能单独证明窗口正确；
- 托盘必须在穿透、隐藏、拔屏、角色失败和 Explorer 重启后恢复控制。

### ADR-V2-020：发布证据分层并冻结

状态：FROZEN

决定：

- CURRENT_STATE_AUDIT 记录当前事实；
- DESIGN_V2 与协议记录已确认设计；
- PROVISIONAL 数值先实测再冻结；
- 每个发布结果绑定 EXE hash、PetPack hash、环境和原始数据；
- 旧测试结果不冒充本轮重跑；
- 不能为了通过 gate 反复移动门槛；
- 自动化测试数量、schema、hash 和 conformance 是验收工具，不是产品目标。

继任说明：其中“CURRENT_STATE_AUDIT 记录当前事实”的文档路由由 ADR-V2-024
显式更新；本 ADR 关于发布证据分层、绑定与冻结的其余决定继续有效。

### ADR-V2-021：协议身份、信任与提交语义唯一化

状态：FROZEN（规范一致性澄清，不新增产品取舍）

形成原因：规范交叉审查发现，同版本不同摘要、receipt state、official 自述和 active slot
若没有唯一算法，两个互不兼容的实现都可能声称合规。本 ADR 不改变产品方向，只关闭歧义。

决定：

- PackKey、RevisionKey 和 VariantKey 是结构化 tuple，禁止裸字符串拼接；
- Revision 使用 PetPack 1.0 规范的 canonical content_digest；archive hash 仅作容器证据；
- 同 PackKey、同 SemVer、不同 content_digest 是不可确认绕过的 ERROR；
- TrustChannel 由 Engine 安装入口赋值，本地包不能自称 official；
- receipt 记录不可变验证事实，不包含 committed、READY 或 active 等可变 state；
- READY 由 catalog 判定，安装结果由追加式生命周期事件记录；
- ActiveSelection 保存精确 Revision、Character、Variant、配置修订和 generation；
- 候选 Runtime 帧边界交换后，已确认未提交才换回旧 Runtime；提交结果不确定时必须重连读回
  commit_sequence 与完整 ActiveSelection，不得猜测；
- 正常安全猫是只读 EmbeddedOfficialPack；极端恢复另有最小 BootstrapSafeRuntime，
  后者不是普通动作的跨角色回退。

### ADR-V2-022：显示预设与预览边界收敛

状态：FROZEN（规范一致性澄清，不新增产品取舍）

决定：

- Layout 和 VisibilityPolicy 仍然分离，但二者只能按 Engine 允许矩阵组合；
- VisibilityPolicy 使用 normal、quiet、dnd 等有限命名预设，不向用户暴露任意布尔笛卡尔积；
- “同一时间一个角色”约束桌面 PetSurface 的正式 active Runtime；
- 控制面板内的隔离预览可以与正式角色同时可见，但不得发声、修改 Context、持久化或读取系统输入。

原因：既保留可调显示模式，也不把预定义布局重新变成自由布局系统；同时避免禁止必要的角色预览。

### ADR-V2-023：首次角色选择是版本化 campaign，不是第二份角色事实

状态：FROZEN

决定：

- ACTIVE selection 仍是当前角色的唯一事实；一次性状态只记录该选择 campaign 是否完成；
- 新用户与升级用户在下一次手动启动看到同一个角色页选择区，`--startup` 不弹窗、不抢焦点；
- 关闭、取消、预检失败、安装失败或切换失败都不写完成状态；
- 继续官方猫不重复提交 ACTIVE；固定半写实预览可在一次明确点击中顺序 INSTALL → ACTIVATE；
- 一键入口同时钉扎 archive SHA、RevisionKey、唯一 Character ID 与 warning 集；任一漂移在安装前拒绝；
- 普通本地包导入继续保持 INSTALL 与 ACTIVATE 分离。

原因：让用户第一次就能看到真实可选画风，同时不复制角色状态、不弱化本地内容权利披露，
也不让开机启动成为打扰性的标准窗口。

### ADR-V2-024：当前事实、版本事件与规划分流

状态：FROZEN（文档治理迁移澄清）

决定：

- `CURRENT_STATE_AUDIT` 在 v2 迁移完成后冻结为 2026-08-28 历史快照，不再滚动维护；
- 当前源码能力、发布结论和未完成环境门禁由 `IMPLEMENTATION_STATUS` 统一维护；
- 精确版本事件和 artifact 身份追加到 `PROJECT_UPDATES`，不覆盖旧版本记录；
- 可调整的问题排序与探索方向维护在 `FUTURE_WORK`；
- `docs/README.md` 是文档图唯一总入口，受管理 Markdown 必须从该入口可达；
- 原始 receipt、acceptance、事件与 evidence 继续采用追加式、不可覆盖治理。

原因：ADR-V2-020 形成时，迁移审计仍承担当前事实入口。项目进入可试用 Alpha 后，
继续滚动改写该审计会同时混合历史快照、当前状态、版本事件与未来计划。分流让证据
冻结规则保持不变，同时让读者能找到唯一、时效明确的当前入口。

### ADR-V2-025：公开仓库采用机器中立快照与许可分层

状态：FROZEN

决定：

- 公开仓库采用 MIT，覆盖项目原创源代码和文档；角色包、内容资产和第三方组件继续
  服从各自显式许可，不能因根许可证变化而静默换证；
- 源码可位于任意普通项目目录，盘符、用户名、机器名和个人绝对路径不得成为实现、
  测试夹具、文档示例或公开发行记录的一部分；
- 公开 Git 历史从经过 fail-closed 审计的单提交快照开始，不发布含本机证据的私有
  开发历史，也不以破坏式改写私有历史冒充原始事实；
- 原始 receipt、事件和内部 evidence 仍在私有证据域追加保存；公开仓库只保留脱敏
  规范、可复现测试和不含本机身份的发布摘要；
- `main` 只能经 PR 更新，必须通过测试并取得维护者审核；角色内容还必须提交来源、
  许可、AI 使用和公开再分发权证明；
- 历史本机二进制没有随包第三方许可材料时，不得直接上传为公开 Release。公开二进制
  必须由可定位的公开提交重建，并通过许可清单与 artifact 绑定门禁。

原因：早期设计把“离开下载目录”具体化成单台机器的固定盘符，历史证据也包含执行
环境细节。这些事实需要私下保留以维持审计真实性，却不应进入公开项目。机器中立的
快照兼顾公开隐私、可复现性和不可覆盖证据治理；许可分层避免把代码 MIT 错当成角色
素材或 Qt 运行库的授权。

## 3. 被否决的总体方案

| 方案 | 否决原因 |
|---|---|
| 继续固定猫，只增加更多硬编码动作 | 无法实现自选角色、独特动作和包生命周期 |
| v2 直接迁移 QML / Live2D | 增加体积、攻击面和运行成本，当前需求可由轻量 raster 完成 |
| PetPack 允许插件代码 | 无法维持本地导入安全边界 |
| 跨包继承和依赖 | 安装、升级、冲突、卸载和版权治理复杂度过高 |
| 缺动作回退到程序化猫 | 非猫角色会突然变成猫，破坏角色一致性 |
| 包内推荐自动覆盖用户 | 升级会暗中改变长期体验 |
| 原目录覆盖更新 | Windows 文件锁、半更新、回滚和证据链均不可控 |
| 只保留 JSON active 文件 | 多事务、版本和恢复状态难以可靠表达 |
| 自由布局编辑器 | 增加 UI、测试和兼容成本，预定义布局已满足 v2 |
| v2 同时建设在线角色商店 | 内容审核、签名、撤回和网络安全不属于首版核心 |
| 用测试总数宣布完成 | 已发生窗口回归漏检，必须按发布结果验收 |

## 4. 允许重新讨论的条件

以下不是实现困难即可触发，而是产品前提真正变化时才触发：

- 用户明确要求多角色同时显示；
- raster profile 无法表达已经确定的正式美术需求，并有性能实测；
- 需要公开社区商店，并已经具备运营、审核、签名和投诉能力；
- 跨包依赖带来的收益有真实包案例，且生命周期方案通过安全评审；
- 性能候选门槛在至少三台参考机器上证明不可实现；
- Windows 或 Qt 平台能力发生破坏性变化；
- 法律、隐私或内容政策发生需要重新设计的变化。

变更记录必须包含：

- 被修改的 ADR；
- 新旧行为；
- 用户数据和包兼容影响；
- 性能、安全和权利影响；
- 迁移方案；
- 对 conformance fixtures 的修改。

## 5. 与 v1 ADR 的关系

旧 DESIGN.md 的 QWidget、onedir、隐私优先、本地规则等 ADR 继续有效。

发生冲突时：

- v2 决策优先于旧的固定猫、单 assets manifest 和固定窗口布局；
- 当前代码行为不能反向覆盖已确认 v2 决策；
- 迁移必须先保护 v1 的透明窗口、倒计时、退出、自启和用户数据，再逐步替换内部模型。
