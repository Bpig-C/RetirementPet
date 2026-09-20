# V12-08 阶段报告：可信显示证据链与运行降载

> 关联：[版本工单](V1_2_WORK_ORDER.md) · [主控审阅](V1_2_NODE_B_AND_V12_08_CONTROLLER_REVIEW.md) ·
> [节点 B 材料](V1_2_NODE_B_MATERIALS.md) · [文档中心](README.md)。
> 状态：**工程范围已签收**——显示验收链、静态视觉降载和暂定性能对比已完成；
> 真机生命周期、多设备性能冻结与 Production 认证仍开放。内部仓库文档，含内部提交号。

## D08 返工收口（针对主控六项返工）

- **D08-01 身份链路**：schema 3。成功路径产出完整身份场景；身份证据
  记录 hwnd、自报 pid、启动 pid、原生窗口属主 pid、宿主/直接归属与
  归属核验标志（EXE 另记观察到的进程映像 sha256）。自报 pid 与属主
  不符 → FAIL；属主既非启动进程也非其文档化子进程 → FAIL；缺窗 →
  INVALID。编排测试覆盖正常窗/外来窗/自报不符/缺窗四路径；本次拥有的
  进程在 finally 中清理（注入 target 不越权清理）。
- **D08-02 证据驱动**：场景结论不再信任标签——derive_* 函数按结构化
  证据重算结论，harness 标签与消费端重算共用同一推导；校验器逐场景
  对比标签与推导、要求 scenarios 条目引用与顶层一致的证据（副本篡改
  被发现）、指纹与 displays 记录互证、裁决与聚合规则重算一致（无论
  标签是否有错）。INVALID 在基线与多屏两条路径都传播，绝无降级 SKIP。
  全 PASS 无观测、伪拓扑、错类型的报告无法通过校验。
- **D08-03 面板与脚底**：IPC 改用 panel / panel-close（应用层新增
  panel-close 处理），以原生枚举观察面板真实可见性；开/关未被实际
  观察到 → INVALID（IPC 成功不是证据）。记录 before/panel_open/
  panel_closed 三个窗口位置，closed 相对 before 的最大边移超过 8px
  容差 → FAIL——窗口移动 100px 不再判稳；包含关系不再冒充稳定性。
  编排测试覆盖真开合/未开/未关/位移 100px/IPC 失败五例。
- **D08-04 拓扑**：观察期检测到的变化写入不可覆盖的 topology_events
  （before/after 指纹 + 时间戳），稳定期复验与事件关联；指纹包含
  work area；新增 per_display_landing 场景——驱动窗口逐屏落点并记录
  实际矩形/包含屏/DPI，未访问的屏不算通过。编排测试：事件保留并
  PASS、无事件如实 SKIP、恢复丢失包含 FAIL。
- **D08-05 显示事实**：GetDpiForMonitor 检查返回值，失败/超范围 DPI
  （48–768 之外）不作为逐屏实测（system_fallback 只作事实展示，含
  退化记录的 display_facts → FAIL）；工作区四边完整校验（左/上/右/
  下）；指纹纳入 work area。
- **D08-06 Alpha 语义**：finish 显式区分必需/可选项——Alpha 必需门
  全 PASS 才 GO；displays_multi_screen_exe 是唯一可选 Alpha 项，其
  真实 SKIP（带精确环境原因）不阻断 Alpha，FAIL/INVALID 依旧拒绝；
  Production 仍要求其完整 PASS。真实 finish 路径三例集成测试：单屏
  基线 PASS + 多屏 SKIP → ALPHA-GO；含 FAIL 报告/INVALID 身份 → 拒绝
  （INVALID_RUN 或 ALPHA-NO-GO）；Production 遇 SKIP → 拒绝。门禁
  记录的 report 路径指向实际生成的时间戳子目录。报告展示实测设备、
  DPI 来源、逐屏覆盖与 SKIP 原因（按主控裁定，不新增拓扑事实门）。

## R08 返工收口（针对主控五项反例）

- **R08-01 身份绑定闭环**：run_harness 把**观察到的**进程映像 hash 写入
  candidate.exe_sha256（不再只复制期望值）；identity 证据必须有
  image_sha256（缺失 INVALID）；direct 归属要求 owner == 启动进程；
  candidate hash ≠ 观察映像 → FAIL；校验器再交叉核对 candidate 与
  证据、消费者核对 candidate 与 receipt——四点（启动 pid、属主 pid、
  观察映像、receipt）闭环。
- **R08-02 真落点**：per_display 每条访问记录实际矩形，推导重算包含
  屏并要求 == 目标屏（换设备名不算访问）；moved 必须 True；DPI 用
  user32!GetDpiForWindow 在移动后**实测每窗口 DPI**（声明了
  argtypes/restype），不再从 display 记录复制。反例：移动失败、留在
  原屏、DPI 未切换 → 全部 FAIL。
- **R08-03 脚底采样**：证据保存原始样本列表（每样本时间戳 + 完整矩
  形），推导从样本重算稳定性（与 stable 标志互相印证）；脚底锚取窗
  口底边（桌宠底锚定，可验证等价观测）；容差为 harness 契约常量
  FOOT_TOLERANCE_PX——报告自带 tolerance ≠ 常量即 INVALID（放大容差
  吸收 100px 位移的反例被封死）；窗口矩形跨面板阶段位移同样 FAIL。
- **R08-04 阶段隔离**：观察（采样+事件+恢复坐标）全部在任何人为搬运
  之前执行；恢复证据记录 after_rect 原始坐标，推导重算包含关系、
  核对指纹与事件日志关联（无事件日志/无坐标/指纹不匹配 → INVALID；
  坐标不被包含 → FAIL）。编排反例：事件后应用未恢复、窗口留在所有
  工作区之外——即使 harness 之后逐屏搬运可救回，恢复仍 FAIL。
- **R08-05 防御性**：derive_all_results 对每个场景 try/except，畸形
  输入返回场景级 INVALID，不抛异常；_valid_rect 拒绝非正宽高与布尔
  坐标；display 记录逐条防御校验；topology_events 非列表 → 校验错
  误。契约测试八种畸形（null 记录/负宽高/空矩形/空设备/错误嵌套/
  非列表事件/零 DPI/布尔坐标）全部返回明确错误且不抛。
- **全链路验证（主控优先要求）**：run_harness 注入编排的**原始返回
  值**直接进 validate_display_report → _displays_outcomes，无手工补
  齐：单屏正例（PASS/SKIP 且 candidate hash 来自观测）、pid 不符
  （FAIL 传播）、外来二进制（candidate=观测 hash≠receipt → 消费
  INVALID）、移动失败/DPI 卡死（multi FAIL）、未恢复+无坐标（拒绝）。

## R08 残余三项收口（第二轮返工）

- **R08-03 真脚底观测**：`PetWindow.current_foot_point()` 公开当前
  `BodyLayout.foot_point`（含脚底留白与 motion clamp 的真实锚点）；
  `--report-window` 诊断报告新增 foot_point 与 window_frame，并在该
  诊断标志激活时以 400ms 周期刷新（关机停表），harness 每个样本从
  该文件读取真实锚点。推导不再接受窗口底边：样本缺 foot_point →
  INVALID（"the window edge is not an equivalent substitute"）；
  面板阶段 before/closed 的 foot_point 差超容差 → FAIL；同拓扑内
  窗口不动而脚底变 → FAIL（主控 228.07/148.76 布局反例的等价构造）。
  冒烟：真实应用（offscreen）报告 foot_point [116.0, 232.0] 而窗口
  底边 360——窗口底边≠脚底在真实应用成立。边框稳定保留为子检查。
- **R08-04 阶段绑定**：每个观测（containment、samples、panel 各阶段）
  携带自身采样时的 displays 与拓扑指纹；推导用各证据自带的 displays
  重算包含，不再拿旧坐标对新工作区。恢复位移不再计入"无输入失稳"：
  stable 标志按事件前同拓扑样本重算；恢复后面板阶段重新建立基线
  （before 取恢复后位置/脚底）。正例：旧屏消失、应用自行恢复到
  x=4000 新工作区 → containment/recovery/foot/baseline 全 PASS；
  对照：应用不恢复 → recovery FAIL 且 containment 仍按初始屏诚实
  PASS。早期事件使采样 <2 时漂移子检查降级，面板阶段独立承担脚底
  对比。
- **R08-05 校验器边界**：整个校验体 try/except 兜底（任何对象 → 可
  诊断错误列表，绝不抛）；before/after 拓扑快照逐条 `_valid_display`
  预校验后才做指纹重算（`displays:[None]`、`monitor:null` → 明确错
  误）；裁决字段非 dict（如字符串 "PASS"）→ 记 malformed 且比较用
  None 结果，不再 AttributeError。契约测试覆盖主控三个原反例。

## R08-03 第三轮收口：观测链身份/新鲜度/完整坐标

主控三个反例逐项封死：

- **身份与新鲜度**：诊断报告新增 `sequence`（每次写递增）、
  `generated_at`、`window_dpi`；harness 只把"序号推进 + pid/hwnd 与
  绑定目标一致 + 带可用屏幕系脚底"的观测记为样本，陈旧/外来/无脚底
  的读取计入 `stale_diagnostic_reads` 并等待新写（不重复计数）。推导
  层再校验每样本 pid/hwnd 与 identity 绑定、序号严格递增——外来
  pid=999/固定序号的诊断记录 → INVALID。
- **完整坐标**：比较基准改为 `foot_point_screen`（Qt mapToGlobal 的
  屏幕像素，跨窗口移动与 DPI 无歧义），x/y 全量比较——横向移动
  100px → FAIL（dx=100.0）；local DIP foot_point 仍随报告提供仅作
  参照。容差注明单位为 screen px。
- **分组防绕过**：样本必须保存 displays 记录，推导重算指纹并与样本
  自报指纹核对（不符 → INVALID，"换个指纹跳比较"被封死）；**每个**
  ≥2 样本的组独立做矩形+脚底漂移检查（DISPLAY1 组稳定而 DISPLAY2 组
  漂移 → FAIL）；单样本组仅当其指纹匹配已记录事件的 after 指纹才可
  接受，否则 INVALID。

冒烟：真实应用（offscreen）诊断输出 pid/hwnd/递增 sequence/
foot_point_screen=window_origin+foot/window_dpi=96，多次读取序号推进
且屏幕系脚底与 window_frame+foot 一致。测试：go_no_go 套件显示相关
33 项，含编排级外来/停更/横移反例与真实生成链路正例。

## R08-03 第四轮收口：新鲜度/同刻窗口/阶段序号/DIP 单位

主控两处残余逐项封死：

- **生成新鲜度**：`_diagnostic_usable` 要求 `generated_at` 可解析、不
  落在未来、与读取时刻差 ≤ `DIAGNOSTIC_MAX_AGE_S`（5s 契约常量）。
  harness 采样循环与面板阶段都经过该检查；每条样本/阶段记录保存
  `generated_at` + `read_at_utc` 供消费端重算。编排反例：序号正常
  递增但 generated_at 冻结在 2000-01-01 → 无样本可录入，foot
  INVALID（stale_reasons 留 "stale" 痕迹）。
- **同刻窗口关联**：诊断 `window_frame`（Qt 逻辑）与原生
  `GetWindowRect`（物理）按记录的 `window_dpi` 换算后比对（
  `FRAME_ASSOCIATION_TOLERANCE_DIP = 2`）；矛盾映射 [9000,9000,1,1]
  在 harness 层被过滤（stale_reasons 留痕），推导层对已录样本再重算
  一次（native/dpr vs frame）。脚底还必须落在其自报窗口帧内——
  物理/逻辑混用会把脚底推到帧外 → INVALID（单位混用探测）。
- **阶段序号全序**：推导把 samples + before + panel_open +
  panel_closed 按序串成一条链，要求 sequence 严格递增；三阶段重放
  同一序号 → "derives INVALID"，消费端不可 PASS。
- **DIP 单位统一**：容差常量更名 `FOOT_TOLERANCE_DIP`（Qt 逻辑像
  素），比较与容差同单位；`window_dpi` 全程记录供独立换算物理像素
  （Qt 全局坐标不能简单乘 DPR）。等价测试：同一逻辑几何在 96/192
  DPI（native 物理坐标随之翻倍）下裁决一致；逻辑 20 DIP 脚底位移在
  两个 DPI 下同样 FAIL；物理/逻辑混用 → INVALID。
- **2 倍缩放真实探针**：`QT_SCALE_FACTOR=2` 离屏运行真实应用——
  window_dpi=192、窗口逻辑 232×360 不变、foot_point_screen 与
  frame_origin+local_foot 全等（同一逻辑单位）；主控"局部 x+6 全局
  仍 +6"的观测与诊断单位一致。该探针不等于混合 DPI 真机认证。
- 单元与编排测试：显示相关 37 项（新增 stale 生成/矛盾帧/启动缺脚底
  重采正例/阶段重放消费反例/DPI 等价与混用反例）。

## 第一片：verify_displays 重写（schema 3）与 go_no_go 消费

### 设计

`scripts/verify_displays.py` 全量重写。五个必需场景，每个场景与两个
总裁决独立携带 PASS/FAIL/SKIP/INVALID，SKIP 永不折算成 PASS：

- **identity_binding**：harness 真实启动目标（source=venv python，
  EXE=dist 产物并绑定 build-id 与 exe sha256），应用经
  `--report-window` 自报 hwnd，harness 以句柄验证存活——身份缺失记
  INVALID（工单："身份/观测缺失记 INVALID"）。
- **display_facts**：Shcore.GetDpiForMonitor 逐屏真实 DPI（旧系统回退
  system 并如实标注 dpi_source）、显示器/工作区矩形、拓扑指纹
  （multi_screen / mixed_dpi / negative_coordinates）。
- **window_containment**：观察到的窗口矩形必须被某个可见工作区**完整
  包含**，记录落点显示器——只枚举设备不算窗口行为。
- **foot_stability**：无输入采样矩形稳定性 + 经 IPC（`panel`/`show`
  命令）自动开合控制面板后仍满足包含关系——面板开合是自动化场景，
  不再依赖操作员。
- **topology_recovery**：观察窗口内真的发生拓扑变化（指纹变化）才
  等待稳定后复验包含关系；没有真实拔插/DPI 变化事件只能是 SKIP
  （"缺少……真实拔插事件只能 SKIP"）。

**两个总裁决分开表达**（工单："单屏基线通过与多屏认证完成分别表达"）：

- `single_screen_baseline`：identity+facts+containment 全 PASS 即 PASS，
  与拓扑无关；
- `multi_screen_certification`：仅在拓扑真实提供第二屏、混合 DPI、
  负坐标屏，且面板稳定性与真实拓扑恢复都 PASS 时才 PASS；缺任一
  环境条件都是带精确原因的 SKIP；任何 FAIL/INVALID 传播为 FAIL/INVALID。

聚合逻辑是纯函数 `aggregate_display_verdicts`（单元测试全覆盖：
单屏基线、缺第二屏/缺混合 DPI/缺负坐标/缺真实事件四种 SKIP、身份
SKIP 强制 INVALID、缺场景 INVALID、FAIL 传播）。共享契约校验
`validate_display_report` 供门禁消费：schema、candidate 绑定、场景
完整性与形状、裁决与聚合规则重算一致（篡改裁决会被发现）。

### go_no_go 消费（fail-closed）

新增三个门：`displays_exe_harness`（真实运行 harness，postcheck 校验
报告契约与退出码一致性）→ `displays_single_screen_exe` /
`displays_multi_screen_exe`（消费函数 `_displays_outcomes`）。消费规则：
旧 schema、缺字段、场景缺失/畸形、裁决违背聚合规则、观察到非 receipt
绑定候选（exe sha256 不符）→ INVALID，绝不聚合为 PASS；harness PASS
但报告缺失/不可读 → 两裁决门 INVALID；单屏基线非 PASS 则后续性能/
稳定性门全部跳过。多屏认证 SKIP 不阻断 Alpha 结论，继续留在
`_production_pending` 的 multi_screen_dpi 项中。

### 证据

- 单元与编排测试：`tests/test_go_no_go.py` 显示相关 37 项——聚合规则、
  消费契约、外来候选拒绝、证据推导反例（自报 pid 不符、无关属主、
  退化 DPI、四边越界、100px 位移、面板未观察到）、run_harness 编排
  （注入 Target/Observers：正常窗/缺窗/事件保留/恢复 FAIL/面板真开合
  与未开未关/IPC 失败）、真实 finish 路径三例（Alpha SKIP 通过、
  FAIL/INVALID 拒绝、Production 拒绝），套件 54 项全过。
- 真实机器闭环探针：本机双屏（96 DPI 同值、负坐标副屏）上读取
  per-monitor DPI（dpi_source=per_monitor），报告契约校验通过，
  单屏基线 PASS、多屏认证 SKIP——SKIP 原因与本机实际拓扑一致。此
  探针只证明设备枚举与推导链路，不证明窗口身份/包含/脚底链路在真机
  通过。
- 真实多屏混合 DPI 与拔插事件、EXE 候选运行：属 V12-E1 真机门禁，
  本地无该环境，如实待真机执行。

## 未开始/已完成界定（V12-08 剩余范围，第四轮更新）

已完成（本轮新增）：

1. **静态画面降载**：`update_snapshot` 内容等值门——静态视觉
   （parts profile）且 overlay/倒计时/视线（量化后）不变时跳过重绘；
   动画序列、blink、气泡、视线跨量化步、倒计时秒进均照常重绘。
   真实 Qt paint 事件计数验证：静态角色 21 tick 仅 1 次合成重绘
   （原为 21 次）；引擎猫逐帧照常重绘；隐藏时 VisualClock 本就停表
   （既有测试覆盖）；锁屏/挂起走 set_suspended（既有测试覆盖）。
2. **新旧对比（同机/同设置/同素材）**：以静态包角色在预置数据目录上
   实测（perf_sample 新增 panel_open 场景与 --data-dir）：pre-gate
   vs 带门——visible_idle 5.027→1.630 CPU%、panel_open 5.707→2.038、
   hidden 1.223→2.038（隐藏两侧均近零，属噪声）。两次运行
   performance.json 自判均 PASS（failures=[]、provisional=true），
   harness 为已提交版本。比较与自判存
   `.release/v1208-comparison.json`（私有证据，不入公开快照）。
   数值为 PROVISIONAL，不宣布冻结。
3. **存量消费者审计**：window 报告消费要求 harness 绑定/非空
   checks/EXE 候选绑定；dist manifest 消费要求当前 schema 与精确
   source_comparison（缺失即 INVALID）；perf 聚合拒绝负值/非有限值/
   外来 pid 样本；health PASS 与 failures 列表交叉核对。均有回归
   测试。独立验收 agent 探针复现的旧行为（裸 result 伪造、缺
   source_comparison 旧格式）全部转 INVALID。

仍未开始（如实声明）：

1. **隐藏/锁屏/挂起的实际降载测量**（时钟停表已有单测；整机级
   睡眠唤醒测量需真机，属 V12-E1）。
2. **与旧版本候选的性能对比**：已定位本地 20260901 旧构建
   （v1.0.1/v1.1.0，后者经 receipt 哈希核验）；接入当前 perf
   harness 的真实尝试因旧构建缺 build-info 绑定结构、且缺现门禁
   依赖的 IPC quit/上报接口而失败（failure 记录在
   `.release/v1208-perf-oldcandidate/`）。有意义的新旧对比需以
   发布版旧候选加兼容垫片重做。

## 需要主控决定

## 需要主控决定的具体事项

- （已裁定，不再开放）Alpha 不新增拓扑事实门；实测拓扑在报告中展示。
