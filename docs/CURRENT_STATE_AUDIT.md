# RetirementPet 2026-08-28 状态审计（历史快照）

> 状态：`SUPERSEDED_AS_CURRENT`，原始审计事实保留
> 当前入口：[实施状态](IMPLEMENTATION_STATUS.md) · [项目更新](PROJECT_UPDATES.md) ·
> [未来工作](FUTURE_WORK.md) · [文档中心](README.md)
> 使用规则：本文只回答 2026-08-28 迁移前的仓库状态，不回答 1.1.1 当前能力
>
> 审查日期：2026-08-28  
> 审查基线：文档修改前 commit f8612ba，专用开发工作树干净
> 审查方式：只读核对代码、文档和现存构建；本轮未重新运行测试  
> 事实边界：131 passed、构建成功及原生窗口检查来自 IMPLEMENTATION_STATUS 的最近一次记录，不是本轮重跑结果

## 0. 后续处置

本文发现的固定猫、托盘恢复、视觉时钟、无界缓存、控制面板和 PetPack 缺口，已经
沿 M0–M8 路线完成 Alpha 核心实施。1.1.1 目前可以导入本地 PetPack、浏览并安全
切换角色，且有当前机器 `ALPHA-GO`。Production 的多屏、真实登录自启、睡眠唤醒、
干净 VM、24 小时和多机性能门禁仍未完成。

旧结论没有被删除或改写，因为它们解释了架构迁移的起点。需要当前判断时只读
[实施状态](IMPLEMENTATION_STATUS.md)，不要从下文的“尚不存在”推断现状。

## 1. 结论

当前仓库实现的是一只固定程序化猫的 RetirementPet v1，而不是 PetPack 或多角色平台。

已经存在的主要能力：

- PySide6 QWidget 透明、无边框、工具窗口；
- 退休倒计时及年龄阶段；
- 单主动作控制器、随机动作和叠加视觉；
- 工作、休息、三餐、健身、会议和本地音乐；
- 设置、运行状态和日志；
- 单实例、HKCU 当前用户自启和 PyInstaller onedir 构建；
- 当前发行路径实际使用程序化猫；代码另有可信内置分层 PNG 与序列帧路径，并由小型测试 fixture 覆盖，但发行素材尚未启用。

尚不存在的 v2 核心：

- 系列、角色、变体和活动角色目录；
- CharacterRuntime 与候选 Runtime；
- PetPack 身份、协议、外部导入验证和安装收据；
- 包版本共存、激活、健康检查、回滚和卸载事务；
- Context、PrimaryPerformance、Overlay、Event/Emote 的正式运行时模型；
- 文案、行为、布局和角色覆盖配置体系；
- 角色库、动作配置、文案显示和存储管理控制面板。

因此，“当前已经支持角色包，只差添加素材”是不准确的。现有实现只支持一套随程序分发、被当作可信输入的猫素材。

## 2. 可直接继承的基础

| 领域 | 当前事实 | v2 处理 |
|---|---|---|
| 窗口 | PetWindow 重算 FramelessWindowHint、Tool、可选置顶和穿透，并设置透明背景 | 作为不可破坏窗口契约保留 |
| 焦点 | 普通启动不调用 activateWindow；托盘或第二实例显式唤起才激活 | 后台切角色、恢复事务也必须遵守 |
| 时间 | 倒计时以目标时间减当前墙钟计算；测试具备 FakeClock | 保留为 RetirementCountdownModule |
| 调度 | ActionController 有优先级、打断、冷却和时长；Overlay 已与主动作部分分离 | 拆分为 Context 与表演模型 |
| 领域服务 | ActivityMonitor、RhythmController、ScheduleManager、AudioManager 已解耦于绘制 | 改为产生 Context、事件或服务状态 |
| 持久化 | SettingsStore 与 StateStore 使用临时文件加 os.replace，损坏文件备份 | 保留原子写入思想，新增作用域与 SQLite 生命周期索引 |
| 发布 | onedir、windowed、图标、版本资源和项目内虚拟环境 | 保留 onedir；收紧构建和 DLL 验收 |

## 3. 需要抽象或替换的部分

| 当前实现 | 当前限制 | v2 方向 |
|---|---|---|
| 固定 ActionId 枚举 | 包不能声明角色专属动作，长期情境与短动作混在一个 current action | core.* 语义加包命名空间；Context / Primary / Overlay / Emote 分层 |
| CatRenderer | 程序化回退始终是一只猫 | 角色无关 Renderer 接口；缺动作回到当前角色 idle，绝不变成别的角色 |
| 单 AssetBundle | 只在启动时读取一套可信内置 manifest | 每个不可变 Pack Revision 独立资源索引、验证与预算 |
| 固定窗口尺寸 | 只有倒计时展开或折叠两态 | 预定义 Layout 与 VisibilityPolicy |
| 硬编码文案 | 动作标题、气泡和倒计时文字散落 | TextProfile、语义键、安全变量和用户覆盖 |
| 扁平 settings.json | 无作用域、来源、推荐快照和孤儿设置 | 确定性的配置所有权、优先级与投影 |
| show-only 单实例 IPC | 只会转发显示命令 | 转发打开控制面板、导入包等结构化命令，包变更 fail-closed |

## 4. 已验证的当前缺口

### 4.1 托盘恢复契约未落实

文档声称托盘菜单可以关闭鼠标穿透，但 TrayController 当前只处理左键 Trigger：

- 没有为 QSystemTrayIcon 设置 context menu；
- 没有处理 Context 激活原因；
- popup_menu 方法没有调用方。

结果是开启穿透后，宠物本体不能右键，而托盘左键只负责显示或隐藏；用户可能没有可见入口关闭穿透。该问题应作为 v2 迁移前 P0 修复，并加入真实托盘恢复测试。

### 4.2 旧 manifest 多个字段没有真正控制渲染

当前事实：

- canvas_size 被解析，但 CatRenderer 使用固定 256 逻辑画布；
- anchor 和部件 x/y 未进入渲染计算；
- SequenceVisual 保存 manifest FPS，但帧推进使用 ActionSpec.animation_fps；
- manifest 的 note 与运行时请求的 notes 不一致。

旧 assets/manifest.json 因此只能视为 v1 内置素材描述，不能升格为 PetPack 1.0。

### 4.3 隐藏状态仍执行视觉流水线

隐藏窗口时统一时钟仅降到 2 FPS，仍会执行 controller、overlay、快照合成、光标查询和 window.update。倒计时还会在每个可见动画帧重算。v2 必须拆分 VisualClock 与 ServiceClock，隐藏或静态时视觉 tick 为零。

### 4.4 资源缓存无预算

QPixmap 缓存为无界字典，没有字节统计、LRU、角色切换释放、图片像素上限或解码内存门槛。当前没有正式 PNG 素材时风险不明显；接入多角色后会直接影响内存与切换延迟。

### 4.5 当前设置面板不是 v2 控制面板

现有 SettingsDialog 是按需创建的模态表单，只编辑退休时间、日程、音量和少量开关。它没有系列/角色库、动作策略、角色专属动作、文案配置、预定义布局、包生命周期或存储管理。

### 4.6 文档中的若干验收表述过强

- 记录中的 131 passed 主要证明逻辑回归，不证明多屏 DPI、托盘恢复、性能或 PetPack 安全。
- 原生 HWND 检查仅保留文字记录，仓库没有可重复脚本和原始报告。
- scripts/smoke_test.ps1 使用 Stop-Process -Force，证明强制终止后无进程残留，不证明用户正常退出与托盘清理。
- 自启有 MemoryBackend 单元测试，但仓库没有真实注销、登录和启动竞争报告。
- “素材热替换”实际是启动前替换文件、重启后生效，没有运行时监听或 reload。

## 5. 当前构建基线

已有只读统计：

- dist\RetirementPet：255 个文件，约 131.03 MiB；
- PySide6 子目录约 108.17 MiB；
- 项目 assets 约 0.271 MiB；
- Qt Quick、Qt QML 和 Qt PDF 组件仍被带入，现有代码并未使用它们；
- QtMultimedia 与 FFmpeg 随包存在，尽管音频对象在运行时惰性初始化。

该数据是体积基线，不是 CPU 或内存证据。IMPLEMENTATION_STATUS 已明确承认尚未正式量化 CPU、内存与冷启动。

## 6. 事实状态表

| 结论 | 状态 |
|---|---|
| 窗口 flags 的 Qt 层契约 | 已有自动化测试 |
| 原生透明、任务栏和不抢焦点 | 有历史人工记录，缺可重复工件 |
| 131 项测试 | 历史记录，本轮未重跑 |
| onedir 构建约 132 MB | 已有构建与只读体积基线 |
| 可靠托盘右键恢复 | 未实现 |
| 外部 PetPack 安全导入 | 未实现 |
| 多系列、多角色、单 active slot | 未实现 |
| 原子角色切换和包回滚 | 未实现 |
| v2 配置来源与作用域 | 未实现 |
| 性能硬门槛 | 已设计，尚未实测冻结 |

## 7. 迁移优先级

v2 最先解决三个底座：

1. 固定且可实测的透明窗口和托盘恢复入口；
2. 真正生效、角色无关且有资源预算的 PetPack/Renderer 协议；
3. VisualClock 与 ServiceClock 分离，静态和隐藏时停止视觉工作。

完成这些底座前，不应优先堆叠角色库 UI 或正式第三方素材。
