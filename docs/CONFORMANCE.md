# RetirementPet v2 一致性与发布验收

> 文档关系：[文档中心](README.md) · [当前实施状态](IMPLEMENTATION_STATUS.md) ·
> [项目更新](PROJECT_UPDATES.md) · [性能预算](PERFORMANCE_BUDGET.md)
>
> 文档状态：验收结构与 P0 gate FROZEN；fixture registry/corpus 状态 PARTIAL
> 适用范围：Runtime、PetPack 1.0、Windows 桌宠窗口、控制面板、生命周期和正式发布  
> 基线日期：2026-08-28

## 1. 验收原则

RetirementPet v2 的“可运行”不等于“符合发布要求”。正式验收必须同时覆盖：

1. schema 与格式；
2. 语义和角色一致性；
3. 安全与资源预算；
4. 安装、激活、回滚和卸载；
5. Windows 原生窗口与控制入口；
6. 布局、文案和多屏 UX；
7. 性能和长期运行；
8. 隐私、联网和内容权利。

hash、schema 和 conformance 用于证明完整性，不是任务目标本身。发现漂移时先分类为
事实变化、实现缺陷、fixture 变化或文档过期，不得为了绿灯盲目更新 hash。

## 2. 结果等级

### 2.1 PetPack 验证结果

- ACCEPT：验证通过，具备发布不可变 Revision 的条件；安装后仅成为可选角色，只有独立 ACTIVATE 成功才改变 active slot。
- ACCEPT_WITH_DEGRADATION：仅缺推荐能力；按协议回退到当前角色 core.idle 和引擎 overlay/text。
- REJECT：身份、路径、资源、必需能力、兼容性或安全验证失败。

降级不得使用另一角色的身体或跨包资源。

### 2.2 发布结果

- GO：所有 P0 通过；P1 已通过或具有有效、限期、责任明确且不改变 P0 的批准豁免；P2 有记录、责任人和到期版本。
- NO-GO：任一 P0 失败，或 P1 缺少批准的短期豁免。
- INVALID RUN：环境、构建、fixture 或采样证据不完整；不得算作通过。

## 3. 稳定诊断码

PetPack 诊断码沿用 PETPACK_SPEC_1_0.md：

    PPK-ARC-*  归档与路径
    PPK-MAN-*  manifest 和 schema
    PPK-RES-*  图片、动画和音频
    PPK-ACT-*  动作与 renderer
    PPK-TXT-*  文案模板
    PPK-RGT-*  来源和权利
    PPK-LCY-*  安装、升级和卸载
    PPK-PRV-*  隐私与联网

Windows、UX 和性能 harness 采用：

    WIN-WND-*  窗口 style、透明和焦点
    WIN-TRY-*  托盘与恢复
    WIN-DPI-*  多屏与 DPI
    UX-LYT-*   布局
    UX-CFG-*   配置与控制面板
    PRF-CPU-*  CPU 与唤醒
    PRF-MEM-*  内存和句柄
    PRF-LAT-*  延迟

诊断码含义在同一 major 版本内不可重用。

## 4. 固定 fixture corpus

当前仓库只有一个固定合法 artifact 进入机器可读 registry；其余恶意、损坏与语义
场景主要由 pytest 动态构造。以下是目标 corpus，不得把动态测试数量表述成已完成的
固定 artifact registry。

所有 fixture 有稳定 ID、说明、期望结果和 SHA-256。合法与恶意 corpus 分开存放。
仓库必须提供机器可读 fixture registry，至少包含：

    {
      "fixture_id": "...",
      "path": "...",
      "sha256": "...",
      "expected_result": "REJECT",
      "expected_codes": ["PPK-ARC-E003"],
      "scenario": "...",
      "timeout_ms": 5000,
      "execution_tier": "UNIT"
    }

Markdown 清单不能替代 registry；实现变化不得静默改写期望结果或 hash。

### 4.1 合法包

- valid-minimal-static：只有 core.idle、缩略图和 geometry；
- valid-official-cat：内置安全猫，覆盖主流核心动作；
- valid-multi-character：同一系列多个角色，共享同一 rig_contract；
- valid-missing-optional：缺 work/eat/meeting 等推荐动作；
- valid-unique-action：带发布者命名空间专属动作；
- valid-unicode：中文、Emoji 和 Unicode 文件名；
- valid-wide、valid-tall：极端 content_bounds；
- valid-layered、valid-sequence：不同 renderer profile；
- valid-future-minor-optional：只在 extensions 发布者命名空间中增加 inert optional 字段；
- valid-future-minor-required：未知必需能力，期望拒绝；
- future-major：不兼容 major，期望拒绝；
- unknown-renderer：未知 renderer capability，期望拒绝。

### 4.2 语义包

验证：

- Context 为长期事实，多个 Context 可同时存在；
- PrimaryPerformance 同时只有一个；
- Overlay 可叠加；
- Emote 结束后从当前 Context 重新决策，不恢复旧动作栈；
- 缺 core.work 时，在 work Context 下仍显示当前角色 idle；
- 专属动作不能覆盖 core.*；
- 一个包只声明一个 series；
- 跨角色共享原始动作素材必须有完全一致的 rig_contract；
- 不允许跨包依赖和资源解析。

### 4.3 布局与文案

矩阵至少覆盖：

- 所有 Engine 声明允许的 Layout 与命名 VisibilityPolicy 组合，不测试被禁止的笛卡尔积；
- DPR 1.0、1.5、2.0；
- 方形、极宽、极高角色；
- 中英文、Emoji、长文案、空文案和未知模板变量；
- 左右屏幕边缘翻转；
- 角色切换前后 base_anchor 稳定；
- 窗口透明区域按 content_bounds 收缩；
- 桌面 PetSurface 同一时间只有一个正式角色身体可见；控制面板预览可同时存在，但不得发声、
  修改 Context、active slot、长期配置或读取系统输入。

视觉验收分两层：结构 gate 硬断言 alpha bounds、anchor、slot rectangles、文本边界、
正式身体计数和 hit region；固定 Windows/font runner 再做带容差的 golden 或感知差异。
人工看图只能补充，不能替代结构 gate。

### 4.4 恶意与损坏包

至少包括：

- ../ 路径穿越；
- 绝对路径、盘符路径、UNC；
- NTFS ADS；
- CON、NUL、COM1 等保留名；
- 结尾空格或点；
- Unicode 规范化和大小写碰撞；
- 重复归档条目；
- symlink、junction 或 reparse point；
- 超文件数、超展开体积和极端压缩比；
- 超尺寸图片、解码炸弹和损坏 PNG；
- 超帧数、损坏音频和超时长音频；
- JSON 重复 key、NaN、Infinity、超深嵌套和超长字符串；
- 缺失哈希或哈希不匹配；
- Python、QML、JS、DLL、EXE、HTML、脚本或可执行表达式；
- asset、renderer、action、audio、layout 或模板中的远程 URL，任何未由用户主动操作触发的
  网络请求，以及任何包外资源引用；允许字段中的 inert metadata HTTPS URL 不属于本拒绝项，
  但必须产生零自动 DNS 和网络请求。

还必须覆盖：ZIP 非 UTF-8 名、central size 欺骗、DOS 设备名带扩展、本地包自称 official、
同版本不同 content_digest、legal_files hash/MIME、TextProfile 逐 entry rights/source，
以及 inert metadata HTTPS URL 零自动 DNS。

每个恶意 fixture 必须稳定拒绝，不在 staging 外留下文件，也不改变 active revision。

## 5. schema、语义与回退 gate

### P0

- 所有必填字段与 ID 规范可机器验证；
- 资源路径在规范化后仍位于包根；
- 包内所有文件均在 manifest 声明并校验哈希；
- package、series、character、action 和 revision ID 不冲突；
- core.idle、缩略图和 geometry 存在且可解码；
- 不支持的 protocol major 或必需 renderer capability 被拒绝；
- 缺可选动作不会崩溃、变成另一角色或丢失 Context；
- 当前旧 assets/manifest.json 不被当作不可信外部 PetPack 直接加载。

### P1

- 推荐动作、文本、布局和配置的降级理由可在 inspect 中看到；
- 同 digest 重装幂等；
- 同 version 不同 digest 在 PetPack 1.0 被拒绝；
- deterministic build 对相同输入产生相同 manifest 和 digest。

## 6. 生命周期与崩溃一致性

生命周期测试必须对真实进程执行强制终止，而不是只在函数间抛异常。
每个故障点使用仅测试构建启用的稳定 fault-point ID。子进程到达后通过受控 IPC/barrier 回 ACK，
父 harness 收到 ACK 才执行 TerminateProcess；禁止用随机 sleep 猜测时机。registry 为每个点记录
timeout、预期恢复状态和诊断码。

### 6.1 故障注入点

在以下阶段分别执行 TerminateProcess，然后重启：

- 归档复制中；
- 解包到 staging 中；
- schema/哈希验证后、revision commit 前；
- revision commit 后、catalog 更新前；
- catalog READY 后、ActiveSelection DB commit 前；
- 候选 Runtime 首帧前；
- ActiveSelection DB commit 后、旧 Runtime 释放前；
- 回滚中；
- 卸载移动到 trash 前后；
- pending-delete 等待文件锁期间。

receipt 落盘前后、PUBLISH_INTENT durable flush 前后、same-volume rename 前后、
SQLite catalog transaction commit 前后和 INSTALL_COMMITTED event 前后必须分别覆盖。

### 6.2 P0 不变量

- ActiveSelection 始终指向完整、READY 且已验证的 Revision；
- 安装不自动激活；
- 激活失败保留旧角色；
- ActiveSelection 只在候选 Runtime 通过、完成帧边界交换后提交；已确认未提交才换回旧 Runtime；
  提交结果不确定时必须重连并按 commit_sequence 与完整 tuple 读回，禁止猜测；
- staging、revisions、trash 在同一卷，提交使用可证明的原子边界；
- SQLite、journal、receipt 和文件树可恢复到一致状态；
- 恢复过程幂等，不产生幽灵 revision；
- 卸载活动包前先切换到内置安全猫；
- 默认卸载保留用户配置、个人素材和原始归档，除非用户另行确认；
- 文件被占用时记录 pending-delete，不强删或留下半安装目录；
- 至少保留上一健康 revision，符合回滚预算。
- ActiveSelection 必须包含 revision_key、character_fqid、variant_id、config_revision_id、
  generation 和 commit_sequence；多角色同包重启后恢复同一精确选择。
- 同一 PackKey 与 version 的不同 content_digest 必须 ERROR，任何用户确认都不能安装。
- UNINSTALL_REVISION 与 UNINSTALL_PACKAGE 的影响集合分别可见且可恢复。

## 7. Windows 原生窗口 gate

源码运行和 frozen EXE 必须各执行一次，且使用独立数据目录。

### 7.1 P0 窗口契约

- 真实 HWND 为无边框、无标题栏、无系统边框；
- 具有 Tool Window 语义，不进入任务栏和 Alt+Tab；
- 背景和未绘制区域真实透明；
- 普通启动、自启、包恢复和后台切换不抢前台焦点；
- 显式用户操作才可激活控制面板；
- 置顶和穿透切换后窗口契约仍成立；
- 透明留白不制造大范围桌面点击阻挡；
- 角色窗口不是普通标准 Windows 窗口。

报告保存 PID、HWND、style、exstyle、前台 HWND 前后值、截图和点击命中结果。
点击 gate 按 content_bounds 与 hit_regions 的固定采样点执行：命中点应接收，bounds 外透明点应穿透，
抗锯齿边缘使用 fixture 指定容差；全局穿透模式下所有点都必须穿透。

### 7.2 P0 托盘与恢复

托盘固定入口必须能：

- 显示桌宠；
- 关闭鼠标穿透；
- 将桌宠恢复到主屏；
- 打开控制面板；
- 正常退出。

覆盖鼠标穿透开启、桌宠完全移出屏幕、控制面板关闭和 Explorer 重启。Explorer 重启后 10 秒内
必须重建托盘入口。托盘右键菜单必须是
真实连接的 UI 路径，不能只有未调用的菜单构造函数。

### 7.3 P0 单实例与自启

- 第二实例只发送结构化 open_control_panel 等命令，不创建第二窗口或第二写事务进程；
- server 建立失败时，对包事务 fail-closed；
- HKCU Run 命令正确引用当前 frozen EXE；
- 更新后路径可修复，禁用只删除自身值；
- 真实注销/登录后自动启动；
- 自启不依赖 Python、Anaconda、当前目录或用户 PATH；
- 在 PATH 含 MiKTeX 等其他 Qt6Core.dll 时仍加载随应用发布的 Qt；
- Unicode 和带空格路径可启动。

### 7.4 正常退出

必须通过托盘退出和系统会话关闭验证：

- ServiceClock、VisualClock 和音频停止；
- 状态提交完成；
- 托盘图标移除；
- 单实例端点释放；
- 进程自行退出且无残留。

当前 `smoke_test.ps1` 通过结构化 IPC 请求退出，并要求主进程自行以 0 退出；强制
终止只允许作为失败后的清理手段，一旦使用即不能计为正常退出证据。

## 8. 多屏、DPI 与恢复

真实 Windows fixture 覆盖：

- 100%、150%、200% DPI；
- 负坐标副屏；
- 不同 DPI 跨屏拖动；
- 更换主屏；
- 拔除桌宠所在屏幕；
- Explorer 重启；
- 锁屏、会话断开、睡眠和恢复；
- 显示拓扑变化。

P0：

- 窗口可恢复到任一可见工作区；
- 拔除所在屏幕后 2 秒内回到可见工作区；
- 脚底 base_anchor 在布局和角色切换时偏差不超过 8 logical px；
- 保存 screen identity 与相对位置，而非只保存原始 x/y；
- 透明窗口不出现黑底或整块矩形；
- DPI 变化不产生无限移动、尺寸振荡或不可点击面板。

screen identity 首选稳定 Windows display device ID；无法获得时使用 name + serial/manufacturer +
geometry fingerprint。找不到原屏时，将原工作区相对坐标映射到 primary work area。DPI 或拓扑变化
后 2 秒内稳定，之后连续 5 秒位置不得继续往返振荡。窗口必须完全位于可用工作区；若角色尺寸超过
工作区，则至少保证交互 hit region 与恢复入口可见。预定义 edge-peek 若允许少量越界，fixture
必须给出最大 logical px；不得把未声明越界当作布局特性。

## 9. 控制面板与配置

### P0

- 面板是独立标准 Windows 窗口，不改变桌宠窗口类型；
- 同一时间只有一个可交互面板实例；
- 托盘、桌宠手势和第二实例都能打开同一实例；
- 角色选择为 series → character → variant；
- 首次角色 campaign 只在手动启动显示一个选择区；`--startup` 和 quiet duplicate
  不显示、不激活面板；关闭或任一失败不写完成标记；
- 固定预览一键启用在 INSTALL 前验证 archive SHA、完整 RevisionKey、唯一角色 ID
  和 warning 集；可判定的 PREPARE/SWAP 失败保留旧 ACTIVE/LKG；提交结果不确定或
  CAS 竞争失败时，目标角色不算启用，必须按持久化 ACTIVE 恢复，权威不可判定则进入
  Bootstrap safe mode；任一失败都不完成 campaign、不提升 LKG，同时允许已提交 READY
  事实存在；
- 预览修改在取消时完全回滚；
- 有效配置遵守 DESIGN_V2.md 冻结的优先级；
- missing、null、空字符串和空数组语义不同；
- 数组默认 replace，不进行隐式深合并；
- 包 recommendation 只能由用户选择并保存为静态快照；
- 切换角色保留 Context、全局设置和退休目标，清除旧角色队列、overlay 和音频；
- safe mode 不依赖第三方包即可打开面板。

### P1

- 20 系列、200 角色下搜索和滚动响应达标；
- 连续打开关闭 100 次无对象或内存泄漏；
- 配置来源、恢复范围和被覆盖原因可解释；
- 失效角色 override 进入可管理的 orphan 状态，不悄悄应用到其他角色。

## 10. 性能 gate

按 PERFORMANCE_BUDGET.md 执行，P0 至少包括：

- 所有 Private Bytes 硬上限；
- CPU 全样本平均与 5 秒滚动 P95 的所有硬上限；
- hidden/static VisualClock 触发次数为零；
- 角色切换峰值和 60 秒回落；
- 24 小时无崩溃和无单调资源泄漏；
- 首帧、恢复和标准角色切换硬上限；
- 核心 dist size manifest 完整。

数值尚处 PROVISIONAL 时，运行结果标记为 calibration，不得写成正式 gate 已冻结。

## 11. 隐私、联网和内容权利

### P0

- 默认网络请求为零；
- 使用网络抓包或受控 DNS fixture 证明 Runtime、面板和 PetPack 不访问外网；
- PetPack 不得在资源、renderer、action、audio、layout 或模板中声明可执行 URL；
  publisher homepage 与 provenance locator 只能作为 inert metadata，未经用户主动外部打开不得产生 DNS 或网络请求；
- 日志不记录绝对导入路径、完整台词、用户个人素材内容或密钥；
- 安装收据与运行日志分离；
- 官方内容满足 CONTENT_POLICY.md 的来源和权利要求；
- TrustChannel 只由 harness 指定的 Engine 安装入口赋值；LOCAL_IMPORTED 自称 official 仍为
  UNVERIFIED，冒用保留命名空间时拒绝；
- 用户本地未知权利内容只能本地安装并显示警告，不得自动上传、索引或分享；
- 视觉、音频、文字和声音分别声明权利；
- 官方构建不得未经许可分发 Harry Potter、罗小黑等第三方角色。

使用 canary 数据验证日志和导出文件没有泄露。receipt 的 trust_channel、publisher verification、
脱敏 locator 和 warnings snapshot 必须与实际入口一致。

## 12. 构建与依赖 gate

- 唯一可重复构建入口；
- .spec 不含开发机绝对 E 盘路径；
- 依赖锁定，PySide6 版本与构建报告一致；
- onedir 内 Qt DLL 加载来源可证明；
- 不包含不必要的 Qt Quick、Qt PDF 等大型模块，或有书面理由；
- 发布物无源代码缓存、测试 fixture、个人 PetPack 和绝对路径；
- 生成 SBOM 或最小依赖清单、文件清单和 SHA-256；
- 在干净 Windows 用户环境中启动，不依赖已安装 Anaconda 或 VC++ 修复之外的开发环境。

## 13. 必交证据

每个发布候选保存：

- 测试运行 ID、commit 和构建 digest；
- fixture corpus 版本与 hashes；
- pytest 或等价逻辑测试原始报告；
- PetPack validator 报告；
- 生命周期 crash matrix；
- Win32 HWND 报告和截图；
- 多屏/DPI 报告；
- 性能原始采样和汇总；
- 网络零请求证据；
- dist size manifest 和依赖清单；
- 已知问题、豁免、责任人和到期版本；
- GO/NO-GO 签署结果。

任何摘要都不能覆盖原始证据。历史“131 passed”和一次人工 HWND 记录可作为 v1 参考，
不能冒充 v2 当前发布候选的验收。

## 13.1 执行层级

每个 registry scenario 必须标记：

- UNIT：无真实桌面或系统状态变更；
- WINDOWS_INTEGRATION：真实 HWND、托盘、注册表沙箱或进程；
- DESTRUCTIVE_LAB：注销、Explorer 重启、强杀、睡眠等隔离实验；
- MANUAL_ASSISTED：需要真实多屏、DPI 或硬件操作；
- 24H_SOAK：长时间稳定性。

offscreen 或 UNIT 结果不得替代更高层级 gate。需要真实用户会话的项目可以等待实验环境，
但不能被降级成“自动化已通过”。

## 14. 当前状态

截至 2026-09-01，本仓库已实现本文的大部分自动化底座：PetPack 1.0 安全
验证、动态测试 corpus 与部分固定 fixture registry、不可变角色库、活动选择与
两阶段切换、真实进程崩溃矩阵、透明窗口原生 harness、TodoModule、隐私/能力
门禁、性能与稳定性采样器，
以及带编译身份和 release receipt 的可信构建链。此前固定猫 v1、穿透后窗口消失
和伪 ActiveSelection 等状态均为历史基线，不再描述当前代码。

状态声明仍遵循以下边界：

- 源码测试 PASS 不等于 frozen artifact PASS；
- 历史 `evidence/` 不等于当前候选证据；没有 v2 编译身份、完整目录哈希和
  receipt 绑定的旧 `dist` 一律标为 `LEGACY_UNBOUND`；
- `ALPHA-GO` 只允许该精确 artifact 在当前机器进行可回滚初试；
- Production 仍需多屏混合 DPI、真实注销/登录自启、锁屏/睡眠/唤醒、干净
  Windows VM、完整 24 小时 soak 与多机性能预算冻结；
- 最终事实以 `scripts/go_no_go.py` 生成的当前 acceptance bundle 为准，本文档
  本身不构成验收通过。

## 15. 最终 GO/NO-GO 清单

发布负责人逐项确认：

- [ ] PetPack 合法、降级、损坏和恶意 corpus 全部符合预期；
- [ ] 生命周期真实进程崩溃矩阵通过；
- [ ] frozen EXE 原生窗口、透明、焦点、任务栏和 Alt+Tab 通过；
- [ ] 托盘恢复、单实例、真实自启和正常退出通过；
- [ ] 多屏、混合 DPI、锁屏、休眠和 Explorer 重启通过；
- [ ] 控制面板、配置来源和原子角色切换通过；
- [ ] 性能数值已冻结并通过硬门槛；
- [ ] 24 小时 soak 通过；
- [ ] 默认零联网、隐私 canary 和内容权利通过；
- [ ] 构建可重复，依赖和文件清单完整；
- [ ] 文档、事实状态、已知问题和证据索引一致。

当前 1.1.1 的 Alpha 与 Production 双结论见[项目更新](PROJECT_UPDATES.md)，仍待
完成的环境门禁见[未来工作](FUTURE_WORK.md)。
