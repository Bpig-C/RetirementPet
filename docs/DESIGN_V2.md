# RetirementPet v2 产品与架构总规范

> 文档关系：[文档中心](README.md) · [当前实施状态](IMPLEMENTATION_STATUS.md) ·
> [决策记录](DECISIONS.md) · [PetPack 1.0](PETPACK_SPEC_1_0.md)
>
> 文档状态：v2.0 design baseline  
> 决策状态：除标为 PROVISIONAL 或 EXPLORATORY 的内容外，其余为 FROZEN  
> 决策所有者：用户  
> 目标平台：Windows 11 x64  
> 开发仓库：`<project-root>`，路径不得成为实现前提
> 实施状态：Alpha 核心已落地；当前事实与未完成门禁见
> [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)

## 1. 文档目的

本文定义 RetirementPet 从“固定退休倒计时猫”演进为“轻量通用桌宠引擎”的完整产品和架构边界。

本文不是当前功能说明，也不是把现有程序换一批图片的素材清单。它约束：

- 产品继承关系；
- 系列、角色、角色包和动作协议；
- 运行时状态与回退；
- 控制面板、文案和预定义布局；
- 配置来源和角色切换事务；
- 包安装、升级、回滚和卸载；
- 透明窗口、性能、隐私和发布验收。

机器可验证的角色包细节见 PETPACK_SPEC_1_0.md；权利和隐私政策见 CONTENT_POLICY.md；发布验收见 CONFORMANCE.md。

## 2. 产品定位

### 2.1 继承关系

采用方案 B：

    通用 RetirementPet Engine
      ├─ 内置 RetirementCountdownModule
      ├─ 内置原创退休猫 PetPack
      ├─ 用户安装的其他 PetPack
      └─ 控制面板、托盘、行为服务和 Windows 集成

原退休倒计时桌宠不是被删除的旧功能，而是通用引擎的前置产品和官方参考实现。

这里的“继承”是产品能力与模块组合关系，不使用 Python 类继承表达。核心采用组合：

- RetirementCountdownModule 拥有退休目标、阶段和倒计时数据；
- PetPack 拥有角色视觉、角色动作和可选文案；
- Engine 拥有窗口、调度、配置、安全和包生命周期；
- 切换角色不切换或复制用户退休目标。

### 2.2 产品目标

RetirementPet v2 必须同时满足：

1. 安静陪伴：不抢焦点，不频繁弹窗，不采集具体输入，不默认联网。
2. 角色可换：用户按系列选择角色，同一时间只展示一个正式角色。
3. 动作共语义：不同角色理解相同的工作、会议、休息等语义，同时允许角色专属动作。
4. 表现可配置：文案、行为方案、动作循环和显示布局可调整。
5. 长期可靠：角色包失败不能让桌宠消失，升级与卸载不能破坏用户数据。
6. 运行轻量：CPU、内存、计时器、缓存和窗口点击区域均有预算。
7. 内容边界清晰：官方内容、本地私人内容和未来社区内容分级治理。

### 2.3 非目标

v2.0 不承诺：

- 在线角色商店、云账户或跨设备同步；
- PetPack 执行 Python、QML、JavaScript、DLL 或插件代码；
- Live2D、3D、视频背景或任意网页渲染；
- 自动控制会议软件、麦克风或摄像头；
- 读取具体按键、窗口标题、剪贴板、屏幕内容或用户文件；
- 多只角色同时常驻桌面；
- 跨包依赖、跨包共享可变资源或包内网络资源；
- 自由拖拽式布局编辑器；
- 自动下载未经用户明确授权的更新。

这些候选均为 EXPLORATORY，不能被实现者提前引入 v2.0 核心。

## 3. 不可破坏原则

以下为 FROZEN：

- 同一 Windows 用户会话至多一个正式 RetirementPet 实例。
- 桌面 PetSurface 同一时刻至多一个正式 CharacterRuntime 身体可见；控制面板内的离屏或嵌入式预览不占 active slot，并与正式 Runtime 的音频、Context、持久化和系统输入隔离。
- 退休倒计时采用目标墙钟时间减当前墙钟时间，不使用持续递减计数。
- 正常启动、自启、后台恢复和自动角色切换不得抢占前台焦点。
- 透明桌宠必须保持无边框、工具窗口语义和真实透明背景。
- 鼠标穿透、隐藏和屏幕丢失不能移除托盘恢复路径。
- PetPack 是不可信纯数据，不是插件。
- 角色包不能决定系统优先级、输入监控、自启、网络、文件访问或用户全局设置。
- 角色缺少某动作时不得临时变成另一角色；回退到当前角色 idle 加引擎叠加表现。
- 切换角色默认保留退休数据、全局 Context、布局、文案、日程、音量与系统设置。
- 用户配置、个人素材和原始 petpack 文件不得因卸载托管副本而静默删除。
- 安装不等于激活，升级不覆盖旧版本，回滚不执行反向脚本。
- 隐藏或静态时停止视觉帧时钟；业务服务不得依附动画 FPS。

## 4. 术语

| 术语 | 定义 |
|---|---|
| Engine | 窗口、调度、配置、安全、角色包和 Windows 生命周期的可信核心 |
| Module | 由 Engine 托管的产品功能，如退休倒计时、日程、音乐 |
| PetPack | 自包含、纯数据、版本化的系列角色包 |
| Series | 控制面板中的系列分组；不是跨包运行时继承层 |
| Character | 用户可选择的角色身份 |
| Variant | 同一角色下的服装、年龄或视觉变体，不改变 CharacterKey |
| CharacterRuntime | 一个精确角色修订版的已解析资源、配置和渲染运行时 |
| Context | 长时间存在、可能并发的事实，如 working、meeting、music、do_not_disturb |
| PrimaryPerformance | 当前唯一的主体表演 |
| Overlay | 可同时存在的眨眼、附件、标记、气泡或粒子 |
| Event / Emote | 短暂事件或表情，结束后重新依据 Context 求解 |
| Profile | 用户选择的一组文案、行为或显示预设 |
| Recommendation | 包提供、只有用户明确应用后才产生的静态配置快照 |
| Revision | package ID、声明版本和内容摘要共同确定的不可变包内容 |

## 5. 总体架构

    RetirementPetApplication
      ├─ InstanceCoordinator
      ├─ WindowShell
      │   ├─ PetSurface
      │   ├─ ControlPanel
      │   └─ TrayController
      ├─ EngineCore
      │   ├─ ContextStore
      │   ├─ PerformanceResolver
      │   ├─ EventController
      │   ├─ OverlayController
      │   ├─ VisualClock
      │   └─ ServiceClock
      ├─ ModuleHost
      │   ├─ RetirementCountdownModule
      │   ├─ ActivityModule
      │   ├─ ScheduleModule
      │   └─ MusicModule
      ├─ CharacterSystem
      │   ├─ PetPackValidator
      │   ├─ PackageManager
      │   ├─ CharacterCatalog
      │   ├─ RuntimeFactory
      │   └─ RuntimeSwitcher
      ├─ ConfigurationSystem
      │   ├─ SourceRegistry
      │   ├─ EffectiveConfigResolver
      │   └─ ConfigRevisionStore
      └─ Persistence
          ├─ UserSettings
          ├─ LifecycleCatalog
          ├─ ImmutableReceipts
          └─ RebuildableCaches

### 5.1 可信边界

Engine、内置模块和官方构建脚本属于可信代码。PetPack、用户文案、用户音乐和导入路径属于不可信输入。

PetPackValidator 负责把不可信归档转为已经验证、不可变的内部修订版。Renderer 只能读取验证后的描述模型，禁止直接解释归档中的任意文本为代码。

### 5.2 模块职责

- WindowShell 只负责窗口样式、输入路由、位置、可见性和绘制提交。
- ContextStore 保存事实，不直接选择图片。
- PerformanceResolver 根据 Context、用户请求、事件和角色能力求出当前表现。
- CharacterRuntime 只实现角色表现，不修改全局事实。
- ModuleHost 将退休、会议、工作、音乐等产品功能转成 Context、Event 或 Overlay 请求。
- PackageManager 串行处理安装、激活、回滚和卸载。
- ConfigurationSystem 依据每个键的所有权与优先级计算有效值。

UI 不得直接修改运行时内部对象；PetPack 不得绕开上述服务。

## 6. 运行时状态模型

### 6.1 Context

Context 是长期事实集合，可以并发存在。例如：

- core.context.working
- core.context.resting
- core.context.meeting
- core.context.eating
- core.context.exercising
- core.context.music
- core.context.do_not_disturb

Context 由可信模块或用户显式命令设置。PetPack 只能声明自己如何表现某个受支持 Context，不能创建系统触发器或提高优先级。

互斥和优先关系由 Engine policy 定义。会议、勿扰等纪律性状态不能被随机动作清除。
用户显式动作先经过当前 Context 的 policy filter，再进入候选排序；允许时可以临时改变表现，
但不会伪造或删除真实 Context。

### 6.2 PrimaryPerformance

任一时刻只有一个主体表演。候选来源：

1. 用户显式选择的临时表演；
2. 必须立即表现的短事件；
3. 当前 Context 解析结果；
4. 随机或环境待机；
5. core.idle。

PerformanceResolver 每次在事实变化、事件结束、角色切换或动作自然结束时重新求解，而不是维护容易失真的动作返回栈。

v2.0 内置纪律矩阵：

| Context | manual performance | random performance | emote | 角色短音频 |
|---|---|---|---|---|
| meeting | 仅静音且标记 meeting_safe 的动作 | 禁止 | 仅静音、短时 | 禁止 |
| do_not_disturb | 仅标记 dnd_safe 的动作 | 禁止 | 仅无声轻量 | 禁止 |
| working | 允许，但不清除 working | 允许低频 | 允许 | 遵循用户设置 |
| resting | 允许 | 允许 | 允许 | 遵循用户设置 |

包只能声明动作是否满足 meeting_safe、dnd_safe 和 silent 等 allowlist 属性，不能改变矩阵。

### 6.3 Overlay

Overlay 可以并发，包括：

- 眨眼、视线和表情；
- 会议勿扰、工作附件和音乐状态；
- Zzz、音符、汗滴；
- 引擎气泡和退休倒计时；
- 边缘翻转后的气泡定位。

Overlay 必须有归属、锚点、生命周期和资源预算。旧 Runtime 释放时，其 Overlay 和音效必须一并停止。

### 6.4 Event / Emote

Event / Emote 是短暂、可过期、可拒绝的表现请求，例如点击回应、打哈欠、伸懒腰。

结束规则：

    Event 结束
      → 清理事件专属 Overlay 和声音
      → 重新读取当前 Context
      → PerformanceResolver 选择下一表现

不得简单恢复事件开始前的旧动作，因为事件期间 Context 可能已经变化。

### 6.5 动作生命周期

每个动作描述必须声明：

- enter、loop、exit 三段是否存在；
- 支持的循环模式；
- 可中断点；
- 视觉 FPS 与时间轴；
- 音效策略；
- 最大持续时间和资源预算；
- 对应核心语义或角色专属命名空间。

用户只能在动作声明允许的范围内选择单次、循环、自动或手动。包不能用无限 clip 循环永久占用调度器。

## 7. 核心动作能力

### 7.1 最低要求

每个角色必须提供：

- core.idle；
- 缩略图；
- 逻辑画布、内容边界、动作边界和脚底锚点。

推荐但非强制：

- core.clicked
- core.enter
- core.exit
- core.rest

可选情境动作：

- core.work
- core.eat
- core.exercise
- core.meeting
- core.music

角色可以声明包命名空间下的专属动作。

### 7.2 回退规则

角色可以显式绑定同一包内、相同 rig_contract 的参数化 layered motion template 与
body_independent 效果，但每个角色必须提供自己的 body slot。static/sequence 主体帧不得跨角色复用。
这属于可验证能力，不是隐式继承。

某个核心动作缺失时：

1. 保留真实 Context；
2. 使用当前角色 core.idle；
3. 由 Engine 添加语义 Overlay 或纯文本提示；
4. UI 显示该角色不具备对应动画的能力标记。

禁止：

- 使用另一角色的身体；
- 因系列同名而跨包读取资源；
- 把未知角色专属动作自动解释为核心语义；
- 用程序化猫替代人形或其他动物角色。

普通安全回退使用不可卸载的 EmbeddedOfficialPack；程序化猫仅属于协议外最后防线
BootstrapSafeRuntime，不能作为人形或其他动物角色缺动作时的替身。

## 8. 系列、角色和角色包

### 8.1 用户界面层级

控制面板以：

    系列 → 角色 → 变体

进行浏览。同名系列可以来自不同作者或来源，必须同时展示发布者和包来源。

Series 只负责同一包内的 UI 分组与共享资源组织，不形成跨包全局继承树。

### 8.2 身份

稳定身份采用：

    PackKey = (publisher_id, package_id)
    CharacterFQID = publisher.package.series.character
    VariantKey = (CharacterFQID, variant_id)
    RevisionKey = (PackKey, package_version, content_digest)

这些 Key 是结构化 tuple，不得裸字符串拼接。显示名称允许本地化和修改，不参与身份判断。
content_digest 的唯一字节算法见 PETPACK_SPEC_1_0.md；archive hash 只作为导入容器证据。

同一包版本对应不同摘要时，v2.0 拒绝覆盖，要求发布者提升版本或使用新的 package ID。

### 8.3 包边界

PetPack 1.0：

- 一个包只包含一个 Series；
- 可以包含多个 Character 和 Variant；
- 同包内可以共享声明资源；
- 不允许依赖其他包；
- 不允许远程资源；
- 不允许包内代码；
- 只有明确相同 rig_contract 的角色可以复用原始动作资产。

## 9. RetirementCountdownModule

### 9.1 数据所有权

退休目标属于用户全局数据：

- 默认从现有 2060-07-07T21:32:00 迁移；
- 切换角色、升级包或卸载角色不得改变；
- 模块可显示、折叠或关闭；
- 内置默认开启；
- 配置来源只有用户、模块默认和 Engine 默认。

角色包可以提供该模块的视觉适配或推荐文案，但不能修改目标时间。

### 9.2 退休猫特性

原退休猫的青年、中年、晚年和退休外观是该角色的独特表现，不要求所有角色实现年龄变化。

其他角色可以：

- 忽略年龄阶段；
- 提供自己的阶段映射；
- 只由 Engine 显示倒计时 Overlay。

## 10. 绘制与动画

### 10.1 v2.0 渲染方案

继续采用轻量 QWidget 透明宿主与 QPainter 提交。PetPack 1.0 允许的角色渲染 profile：

- static：单张透明角色图；
- sequence：透明序列帧；
- layered：部件、附件与受限平移、旋转、缩放、透明度关键帧；
- builtin_effect：引用 Engine 内置的小型效果。

不允许 QML、SVG 脚本、HTML、视频、自定义着色器、任意表达式或自定义 renderer 代码。

Live2D、骨骼运行时和 3D 属于 EXPLORATORY；只有在形成独立、可沙箱、可预算的 renderer protocol 后才可讨论。

### 10.2 角色几何

每个角色必须声明：

- logical_canvas；
- content_bounds；
- motion_bounds；
- base_anchor；
- bubble_anchor；
- reference_height；
- 简单 hit_regions。

布局按等比例缩放和锚点对齐，不拉伸角色。切换角色时脚底锚点尽量保持原屏幕位置。

窗口透明区域必须收缩到当前内容与交互需要，不能用大面积透明窗口阻挡桌面点击。

### 10.3 时间语义

动作时间只有一个最终来源：

- PetPack 声明 clip 时序；
- Engine 验证并 clamp；
- CharacterRuntime 生成最终时间轴；
- VisualClock 只在下一帧内容会变化时请求绘制。

禁止 manifest FPS、ActionSpec FPS 和全局 repaint FPS 三套独立数字互相覆盖。

### 10.4 资源加载

- 只加载当前角色最低运行集；
- 动作资源按需预取；
- 缩略图与完整动作分离；
- 旧 Runtime 释放后主动清理 RAM 缓存；
- 缓存 key 包含包 ID、版本和内容摘要；
- 用户音乐与角色短音效使用不同管理器和预算；
- 角色音频不得伪装成长音乐。

## 11. 窗口与 Windows 契约

### 11.1 宠物窗口

宠物窗口必须：

- 无标题栏和系统边框；
- 具有 Tool 窗口语义，不作为普通应用出现在任务栏；
- 使用真实透明背景；
- 置顶和鼠标穿透为可切换状态；
- 状态切换后重新保留基础 flags；
- 宠物窗口在普通启动、自启、恢复和角色自动切换时不激活；
- 只在用户从托盘或第二实例显式召回时允许激活。

首次角色选择是一次性标准控制面板，不改变宠物窗口契约：只允许在用户手动启动时
出现；`--startup` 不创建、不激活该面板。用户关闭而未选择时不写完成状态，留到
下一次手动启动。固定随包预览的一键启用必须先钉扎完整 Revision/容器摘要和角色
身份，再按用户同一次明确授权执行 INSTALL → ACTIVATE；普通本地导入仍只安装。
ACTIVATE 的 `False` 只表示本次目标角色未获确认：PREPARE/SWAP 的可判定失败保持旧
ACTIVE；提交不确定或 CAS 竞争失败时，以读回的持久化 ACTIVE 为权威，读回或渲染
仍不可确认则进入 Bootstrap safe mode。此类失败不得写一次性选择完成标记，也不得
把候选提升为 last-known-good；已完成的 INSTALL/READY 与 receipt 作为事实保留。

### 11.2 三条控制入口

必须始终可用：

1. 系统托盘；
2. 用户再次启动同一 EXE，由单实例 IPC 发送 open_control_panel 或 show_pet；
3. 宠物右键或双击。

托盘固定恢复命令：

- 显示桌宠；
- 关闭鼠标穿透；
- 恢复到主屏幕；
- 打开控制面板；
- 退出。

鼠标穿透、隐藏、角色损坏和窗口在已拔显示器上均不能删除这些命令。Explorer 重启后必须重新注册托盘。

### 11.3 控制面板

控制面板是独立的标准 Windows 窗口，按需创建，不与透明宠物共用窗口 flags。
单一轻量 controller 和 catalog 元数据 MAY 常驻；窗口、重型页面和缩略图 RAM 缓存按需创建。
关闭面板时必须先取消或提交 session preview，再销毁重型页面，并在性能预算规定的期限内释放
预览 Runtime 与缩略图缓存。再次打开不得依赖一个永久隐藏的完整窗口。

建议页面：

- 首页：当前角色、当前 Context、退休倒计时摘要；
- 角色库：系列、角色、变体、来源、能力和切换；
- 动作：自动、手动、禁用、权重、循环和角色专属动作；
- 文案：TextProfile、语气、逐项覆盖和来源；
- 显示：预定义布局、可见性、缩放、屏幕与置顶；
- 生活节奏：工作、休息、三餐、健身、会议和音乐；
- 角色包：导入、版本、回滚、卸载和权利声明；
- 存储与诊断：缓存、回滚版本、日志、安全模式；
- 系统：自启、隐私和退出。

后台导入、校验和缩略图生成必须异步；GUI 线程不能承担长时间解码。

## 12. 预定义布局与显示模式

Layout 与 VisibilityPolicy 分离，再按 Engine 声明的允许矩阵组合成用户可选 Profile。

v2.0 内置布局：

| ID | 角色 | 倒计时 | 适用 |
|---|---|---|---|
| pet_only | 显示 | 隐藏 | 只看桌宠 |
| compact | 显示 | 单行或徽标 | 日常低占用 |
| standard | 显示 | 标准模块 | 默认退休桌宠 |
| hover_expand | 显示 | 悬停或显式展开 | 减少常驻面积 |
| focus | 显示 | 克制摘要 | 工作、会议和勿扰 |

包只能声明推荐和兼容布局，不得注入自由坐标布局。推荐不会自动覆盖当前选择。

VisibilityPolicy 不是五个可自由组合的布尔开关。v2.0 只提供有限命名预设：

| ID | 角色 | 倒计时 | Context 标记 | 气泡 | 装饰特效 |
|---|---|---|---|---|---|
| normal | 显示 | 由 Layout 决定 | 显示 | 显示 | 显示 |
| quiet | 显示 | 由 Layout 决定 | 克制 | 仅重要提示 | 减少 |
| dnd | 显示 | 由 Layout 决定 | 仅勿扰状态 | 仅安全/错误提示 | 隐藏 |

每个 Layout 声明允许的 VisibilityPolicy；UI 只展示允许组合。禁止产生角色和所有信息均不可见、
却仍拦截桌面输入的 Profile。未来新增预设必须扩展允许组合 fixture，不能暴露任意坐标或任意布尔笛卡尔积。

同一时刻每个 profile 类别只激活一个：一个 DisplayProfile、一个 TextProfile、一个 BehaviorProfile。

## 13. 文案体系

### 13.1 TextProfile

文案通过语义键组织，例如：

- retirement.stage.young.subtitle
- action.core.work.caption
- event.clicked.reply
- system.pack.rollback_failed

来源可以是 Engine、模块、角色默认、用户应用的推荐或用户覆盖。文案必须以纯文本渲染。

安全变量采用精确 allowlist，例如 character_name、days、hours。禁止：

- eval；
- Python format 属性或索引访问；
- HTML、Markdown、QML 或自动链接；
- 读取环境变量、路径、按键、窗口、剪贴板或网络结果；
- 不受限的双向控制字符和控制符。

### 13.2 用户调整

用户可以：

- 选择预设语气；
- 设置全局文案；
- 为当前角色设置覆盖；
- 关闭特定类别；
- 预览来源和最终有效值；
- 按字段、页面、profile 或整个应用恢复。

包升级不能静默改变已经应用的推荐；推荐必须先快照，再进入配置解析。

## 14. 行为与动作配置

BehaviorProfile 控制：

- 随机动作总开关和活跃程度；
- 每个动作自动、手动或禁用；
- 动作权重和冷却的用户允许范围；
- 支持的单次或循环模式；
- 工作、休息、会议和勿扰下的表现偏好；
- 气泡和角色短音效策略。

Engine 硬约束不可配置：

- 资源上限；
- 安全中断；
- Context 真实性；
- PetPack 权限；
- 日志和隐私边界；
- 会议、勿扰等系统纪律的最高保护规则。

角色不支持某循环方式时，UI 不展示或禁用该选项，不能靠重复触发伪造循环。

## 15. 配置所有权与优先级

每个配置键必须声明：

- owner；
- scope；
- allowed_sources；
- merge_policy；
- validation；
- reset_target。

禁止对任意 JSON 做通用深合并。

有效值优先级从高到低：

1. session preview；
2. per-character user override；
3. global user override；
4. selected profile；
5. user-applied recommendation snapshot；
6. character-specific default；
7. module default；
8. engine default。

该顺序只在键允许相应来源时适用。

规则：

- 系统、自启、隐私和安全设置只能由用户或 Engine 拥有；
- 全局体验在角色切换后继续存在；
- 角色专属设置以稳定 CharacterKey 保存；
- 包内默认不等于用户覆盖；
- missing、null 和 empty 含义不同；
- 数组默认整体替换；
- 新版本删除的动作设置进入 orphan 区，不被删除；
- UI 可以显示每个有效值来自哪里。

重置动作必须具体命名：

- 重置字段；
- 重置本页；
- 重置当前 profile；
- 恢复应用默认；
- 应用角色建议。

## 16. 原子角色切换

角色切换采用两阶段：

    Prepare candidate
      → 解析精确 Revision 与 Character
      → 计算有效配置
      → 加载最低资源
      → 离屏首帧与几何验证

    Commit at frame boundary
      → 保留全局 Context 和模块数据
      → 切换正式 Runtime
      → 在 SQLite 事务中更新完整 ActiveSelection
      → 提交成功后释放旧 Runtime

要求：

- 候选失败时旧角色保持可见；
- 只有提交成功后保存 active character；
- 旧 Runtime lease 保留到数据库提交成功；提交失败必须在下一帧边界换回旧 Runtime；
- 数据库 commit 异常且结果不确定时，必须重连并按 commit_sequence 与完整 ActiveSelection
  读回权威结果；不得把异常直接当成未提交。无法证明任一结果时进入安全恢复；
- 崩溃在 DB commit 前时重启使用旧 selection；commit 后使用新 selection；
- 连续快速选择采用 generation ID，latest-wins；
- 旧异步回调不得写入新 Runtime；
- 清除旧角色专属队列、Overlay 和声音；
- 预览使用隔离 Runtime，不修改 active slot、全局 Context 或长期配置；
- 切换时保持脚底锚点、窗口位置和当前 DisplayProfile；
- 新角色缺少 Context 动作时使用该角色 idle，不改变真实 Context。

## 17. PetPack 生命周期

完整规范见 PETPACK_SPEC_1_0.md。核心语义：

    INSTALL = 发布不可变 Revision，不激活
    UPGRADE = 安装新 Revision，再可选激活
    ACTIVATE = 预载成功后交换 Runtime，原子提交 ActiveSelection
    ROLLBACK = 激活一个旧的精确 Revision
    UNINSTALL = 先安全切走，再移入待删除区

默认：

- 当前活动版本、上一健康版本和用户固定版本不会被静默清理；
- 上一健康版本保护期 PROVISIONAL 为 7 天；
- 回滚版本全局软预算 PROVISIONAL 为 256 MiB；
- 同版本不同摘要拒绝覆盖；
- 无签名包的升级必须由用户明确确认；
- 卸载默认保留个人配置、文案和素材；
- 卸载活动 Revision 前必须先成功切换到用户选择的其他 READY selection；没有可用目标时才使用内置安全角色；
- 文件锁导致的删除失败进入 pending-delete，下次启动重试。

## 18. 存储模型

开发仓库使用独立的 `<project-root>`，不从临时下载目录运行；任何盘符和个人目录都
不得成为实现、测试或公开文档的前提。

运行时逻辑目录：

    UserConfigRoot
      settings
      global module data
      config revisions
      logs

    LibraryRoot
      state.db
      packs\revisions
      packs\staging
      packs\trash
      receipts
      cache

规则：

- UserConfigRoot 默认使用 Windows 用户应用数据目录；
- LibraryRoot 可由用户选择，包括 E 盘；
- staging、revisions 与 trash 必须位于同一卷；
- 包目录只读且不可变；
- 用户配置和个人素材不写进包目录；
- cache 可重建；
- receipts 不可由包修改；
- active slot 是数据库引用，不是包目录属性；
- SQLite 事务与目录改名通过 journal 和幂等恢复达到最终一致。

ActiveSelection 是完整原子记录：

    {
      revision_key,
      character_fqid,
      variant_id | null,
      config_revision_id,
      generation,
      commit_sequence
    }

last-known-good 保存同一完整 tuple。不得仅保存 package ID、display name 或 Revision。
UI 单实例锁作用域为 SID + logon session；包生命周期写锁作用域为 SID + canonical LibraryRoot，
跨快速用户切换和 RDP 会话串行化。

state.db 必须具有显式 schema version。前向迁移使用事务、迁移前备份和幂等恢复；旧应用遇到
更高 schema 必须拒绝写入。catalog 必须能从不可变 revision、receipt 与 lifecycle event 重建。
v1 settings/state 的导入使用一次性迁移标记并可重复验证，不得在失败后重复创建覆盖。

## 19. 安全、隐私与内容

PetPack 必须是纯数据。禁止：

- Python、QML、JavaScript、HTML、EXE、DLL、快捷方式和 Qt 插件；
- 自定义字体、视频、远程图片或 URL 资源；
- 任意表达式、系统路径、网络、文件、注册表、输入、麦克风和摄像头能力；
- 跨包依赖和符号链接；
- 包修改自启、日志、优先级、退休目标或系统配置。

v2.0 核心默认：

- 零遥测；
- 零自动更新检查；
- 零 source URL 探测；
- 不保存具体按键、窗口标题、剪贴板、屏幕内容或鼠标轨迹历史；
- 键鼠动作只使用系统 idle duration 等聚合信号；
- 日志不记录用户台词全文、URL 查询串、源绝对路径或完整 manifest。

官方内容必须原创或有可核查授权；本地未知权利包可以在醒目标记后私人导入，但不得被 UI 称为官方、正版或已验证授权。

## 20. 性能与低功耗

优先级：

1. 稳定与正确；
2. 静态和隐藏时的 CPU 与唤醒；
3. 常驻内存；
4. 动画 CPU 和帧延迟；
5. 启动与切换；
6. 磁盘体积。

架构要求：

- VisualClock 与 ServiceClock 分离；
- 静态和隐藏时视觉 tick 为零；
- 倒计时和日程以低频服务时钟或事件更新；
- 光标跟随不得在无可见变化时轮询；
- 动作实际 FPS 决定内容帧，不重复绘制相同帧；
- 当前角色 RAM 缓存有预算和 LRU；
- 面板关闭后释放非必要缩略图和页面对象；
- 音乐未使用时不初始化播放后端；
- 安装期可生成边界、缩略图和解码索引，以磁盘换运行时 CPU。

候选绝对预算见 PERFORMANCE_BUDGET.md。数值在固定夹具和参考机器上测量后只允许一次有记录的校准，再转为 FROZEN。

## 21. 自启、单实例与发布

### 21.1 单实例

作用域为同一 Windows 用户会话。第二实例只转发结构化命令：

- show_pet
- open_control_panel
- import_pack
- open_character

主实例不可用时，包变更必须 fail-closed，不能让两个进程同时写 LibraryRoot。

### 21.2 自启

- 使用 HKCU 当前用户项；
- 命令引用最终 EXE 并正确引用空格路径；
- 不需要管理员权限；
- 不指向源码、虚拟环境、Downloads 或 build 临时路径；
- 登录后不展开控制面板、不抢焦点；
- 先恢复未完成包事务，再解析 active slot；
- 活动包损坏时使用上一健康版本或内置安全角色；
- 禁用和卸载只移除本应用自己的项。

### 21.3 发布

- PySide6 固定在已经验证的 6.8.3，升级需单独兼容证据；
- 正式发布继续使用 onedir；
- 在无 Python、无 Anaconda 的干净 Windows 11 VM 验证；
- PATH 中存在 MiKTeX 或其他 Qt 时仍必须加载自身 Qt；
- 中文和空格路径必须正常；
- 个人本地版代码签名可选；
- 对外公开发行时，Authenticode、正式安装器和可验证更新来源变为 MUST。

## 22. 故障恢复与安全模式

正常安全角色是只读嵌入发行物的 EmbeddedOfficialPack：它遵循同一 PetPack schema 和 CharacterRuntime，
不位于可写 LibraryRoot，用户不能卸载。若 catalog、validator 或嵌入媒体本身均不可用，
Engine 还必须提供最小 BootstrapSafeRuntime；它可以使用当前程序化 renderer，但只用于恢复托盘、
关闭穿透和打开控制面板，不参与普通角色动作回退。

启动顺序：

1. 获取生命周期互斥锁；
2. 打开并检查数据库 schema；
3. 重放未完成 install / uninstall journal；
4. 校验 active slot 指向 READY Revision；
5. 失败则尝试 last-known-good；
6. 再失败则启动内置安全角色；
7. 通过健康观察后更新 last-known-good。

一次断电不直接判定包损坏。连续两次在健康 checkpoint 前失败，才隔离候选版本并自动回退。
健康 checkpoint 至少要求该精确 ActiveSelection 跨一次进程启动成功、完成一段无 fatal renderer、
decoder 或资源违规的观察窗口；观察时长为 PROVISIONAL，必须通过稳定性测试冻结。在 checkpoint
之前不得覆盖上一 last-known-good。失败计数按 ActiveSelection + Engine build 记录，正常退出和
无法证明归因的断电不得计为包崩溃。

安全模式必须能：

- 使用内置角色；
- 禁止自动激活第三方包；
- 关闭鼠标穿透；
- 恢复主屏幕；
- 打开控制面板与诊断页；
- 保留用户配置和退休数据。

## 23. 日志与可诊断性

运行日志与安装证据分离：

- 运行日志短期轮转，默认总量目标不超过 10 MiB；
- 安装 receipt 记录摘要、验证器、声明快照和已确认警告；
- 生命周期事件追加记录安装、激活、回滚和卸载结果；
- hash 证明字节一致，不证明安全、作者身份或授权；
- 错误使用稳定 diagnostic code，本地化文本可以变化；
- 用户可以查看和删除普通日志；
- 崩溃 dump 默认关闭，若可能包含内存必须单独同意。

性能测量使用结构化 marker，但 marker 不含用户内容。

## 24. v2.0 完成定义

只有同时满足下列条件，才能宣称 v2.0 完成：

- 当前状态审计中的 P0 入口问题关闭；
- 内置退休猫迁移为通过 PetPack 1.0 的官方参考包；
- 最小静态参考包、完整参考包、多角色包和恶意包语料齐备；
- 系列、角色、变体浏览和单 active slot 可用；
- 原子角色切换、安装、升级、回滚和卸载通过真实强杀恢复测试；
- Context、PrimaryPerformance、Overlay、Event/Emote 语义符合本文；
- 文案、行为和显示 profile 按确定优先级工作；
- 五种预定义布局和多屏 DPI 通过验收；
- 托盘、再次启动和宠物交互三条控制入口可靠；
- 透明窗口、任务栏语义和不抢焦点在真实 Windows 成品中通过；
- PetPack 纯数据安全边界和资源上限通过攻击语料；
- 官方内容权利记录完整，本地内容表述符合 CONTENT_POLICY；
- CPU、内存、启动、切换和 24 小时运行满足冻结门槛；
- 干净 VM、竞争 Qt PATH、中文路径、单实例和真实登录自启通过；
- 文档、schema、fixtures、测试报告和构建摘要与实现一致。

自动化测试数量、schema 校验或 hash 全部通过，均不能单独替代上述完成定义。

## 25. 暂定与未来项

### PROVISIONAL

- PetPack 单包与单资源上限；
- 性能绝对数值；
- 健康观察时长；
- 回滚保护期与全局预算；
- WebP 与短音频的最终白名单；
- LibraryRoot 默认位置和迁移 UX。

这些项目必须使用固定夹具实测，记录环境和理由后只校准一次。

### EXPLORATORY

- 在线角色仓库；
- 发布者签名、撤回与社区审核；
- 跨设备同步；
- 日历和音乐平台适配器；
- Live2D、骨骼或 3D renderer；
- 多只角色同时显示；
- 第三方可信代码插件。

任何 EXPLORATORY 功能进入范围前，必须新增 ADR，并重新评估安全、隐私、性能和发布体积。

当前实现与本规范之间仍存在的作者工具、renderer 和 Production 差距，集中记录在
[未来工作](FUTURE_WORK.md)，不在本规范中静默降级。
