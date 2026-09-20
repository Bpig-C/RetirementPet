# 1.2.0 V12-06 交付报告：配置页面真正保存并生效

> 状态：**自测完成待验收**。关联：[版本工单](V1_2_WORK_ORDER.md) §8 ·
> [文档中心](README.md)。提交：`f33bf14`（服务层）、`5a94e1a`（引擎/
> 窗口层）、`c6c65e1`（面板接线与测试）。测试、日志与本文仅使用合成
> 数据，不含个人任务与备注。

## 1. 服务层（`config_system.py`、`settings.py`）

- **UiConfigService**：在 EffectiveConfigResolver 之上提供 UI 配置门面，
  schema 冻结每个键的合法来源与校验器：`layout_id`、`visibility_policy`
  仅接受全局覆盖/引擎默认；`countdown_text`、`greeting_text` 接受全局与
  角色两级覆盖。沿用既有优先级：角色覆盖 → 全局覆盖 → 引擎默认。
- **持久化与重启恢复**：四个 settings 键（`ui_layout_id`、
  `ui_visibility_policy`、`ui_text_templates`、`ui_character_text_templates`）
  双向映射到 resolver 层；启动时 `reload()` 从设置文件重建全部层。恢复
  默认**移除**设置键而非写入 null 残留（`SettingsStore.discard`），回滚
  恢复"改动前原样"（含键的存在与否）。
- **组合门**：布局×命名可见性按引擎允许矩阵校验（`combination_allowed`），
  非法组合被拒绝且保留上一个有效值；显示页下拉框直接按矩阵禁用不可选
  项，服务层仍二次把关。
- **保存失败不丢配置**：`set_user` 在落盘失败时回滚设置存储并 `reload()`
  恢复 resolver，返回 False 让 UI 显示"保存失败，设置未改变"。
- **包推荐不隐式生效**：`offer_recommendation` 只做校验并把合法值停放在
  `_pending_recommendation`（推荐来源不在任何键的允许来源里，结构上不
  可能被解析命中）；`apply_recommendation` 显式落为全局覆盖后才生效，
  失败自动回滚；`discard_recommendation` 丢弃。

## 2. 显示层（`pet_window.py`、`countdown_panel.py`、`overlay.py`、`overlay_renderer.py`）

- **布局视图模型**：`resolve_layout`（`layout_policy.py`）产出
  hidden/badge/standard/hover 倒计时模式 + 气泡策略 + 装饰档位；
  `apply_layout_view_model` 一次套用。hidden 模式窗口收缩到猫本体高度、
  面板输入区整体消失（含点击掩码）；badge 折叠牌只显示剩余天数不显示
  时钟；hover 模式默认折叠、悬停底部锚定展开。
- **气泡分级**：`show_bubble` 增加重要性门（normal/important/error），
  由可见性策略驱动：all 全显、important 只显重要+错误、safe_errors_only
  只显错误。错误类系统提示（角色加载失败、修复失败、音乐缺失等）已
  标记为 error，任何策略下不丢。
- **装饰档位**：渲染器 `set_decorations` 支持 full/reduced/hidden，
  reduced 只保留第一个特效，hidden 跳过特效绘制。
- **文案标题**：倒计时面板支持传入标题文本，由模板渲染结果驱动，1Hz
  刷新；引擎默认模板渲染结果与原内置标题逐像素等价。

## 3. 动作纪律（`action_registry.py`、`action_controller.py`、`random_actions.py`、`app.py`）

- **手动触发有序门**：素材能力 → 正在表演 → 模式禁用 → 会议 → 勿扰 →
  冷却 → 循环载荷 → request(force)。会议中仅音乐可手动（且需已静音），
  勿扰中仅休息/音乐；每一步都有明确的中文反馈文案。新触发按钮不绕开
  任何既有规则。
- **每动作模式**：`ui_action_modes`（自动/仅手动/禁用）约束手动按钮、
  随机调度器（`mode_allowed` 回调过滤候选），并在执行器上安装全局
  request gate（`ActionController.set_request_gate`），因此 Context 推导
  的主表演与音频等其它请求路径同样被否决——"禁用"对一切发起路径成立；
  切到禁用时已在表演的同名 unbounded 动作立即结束
  （`end_if_action(reason="mode:disabled")`）。被否决的上下文请求不会
  留下旧表演：PerformanceBridge 在 resolve 请求被拒时结束此前 resolve
  遗留的表演（真实事实不清除，纪律上下文保持为真；C06-R1）。安全纪律
  只减不增：gate 只能拒绝请求，不能放行任何被纪律/冷却拦截的动作。
- **循环开关只在有真实支持时提供**：仅当该动作是 unbounded 且**当前
  激活角色**（PackCharacterRuntime）为它声明了序列素材时才启用；部件
  绘制的动作不提供虚假的单次控制（禁用态+说明 tooltip）。loop=False
  且手动触发时，控制器按素材**自己声明的每帧 duration_ms 总和**完整
  播放一遍（如 500+700ms = 1200ms），帧边界同样按素材时序推进；无序列
  素材的回退仍是 min_duration_ms，此时 UI 不再把它称为"一遍"。循环
  开关只作用于手动触发的表演，tooltip 明确标注上下文/自动表演不受其
  影响、循环到事实变化为止；切换角色后控件可用性随当前素材实时更新。
- **素材缺失如实回退**：能力声明、角色动作表与注册表都没有的动作返回
  "缺少该动作素材，已回退待机"，不触发任何表演。

## 4. 面板三页（`ui/panel/pages.py`）

- **显示页**：布局与命名可见性下拉按允许矩阵联动禁用；"应用并保存"
  一步完成校验→保存→实际套用，状态栏显示结果；"恢复默认"双键复位。
  保留置顶/点击穿透开关。
- **文案页**：倒计时标题与点击问候两类纯文本模板，实时预览（安全变量
  渲染，未知变量原样显示并有显式提示），全局/当前角色作用范围选择，
  状态栏常显当前生效来源；超长模板被拒绝并保留原值；"恢复默认"按所选
  范围移除覆盖，无覆盖时明说。沿用 `text_profile` 安全变量白名单，
  未知变量按既有约定原样保留。
- **动作页**：实时显示当前表演（含素材缺失回退标注，页激活时刷新）；
  每个可触发语义一行：模式下拉、循环开关（仅 unbounded 且当前角色
  有该动作序列素材时启用，否则禁用并如实说明）、手动
  触发按钮；触发结果写状态栏。随机动作总开关与纪律盒保留。

## 5. 测试证据

- 服务层：往返、优先级（角色→全局→默认）、组合门拒绝、保存失败回滚、
  推荐停放→显式应用→非法推荐不落地（`tests/test_config_system.py` 及
  新增链路测试）。
- **真实 UI+App 链路**（`tests/test_config_pages.py`，隔离数据目录 +
  headless PetApplication）。各用例覆盖阶段不同，文件头已如实标注，
  不再统称"每项都走完整循环"：
  - 完整循环（修改 → 应用 → 显示改变 → **重建 PetApplication** 验证
    重启保持 → 恢复默认）：仅显示页往返用例（hidden 模式重启保持、
    复位后设置键从文件消失、全部允许组合逐对落盘）。
  - 应用 → 可见面变化：文案模板应用到窗口标题且变量已代入、角色覆盖
    胜过全局且他角色不受影响、480+ 字合法/501 字拒绝、未知变量提示；
    动作页触发后状态栏/当前表演标签变化，含一个 720×480 最小尺寸下的
    真实命中测试点击（`QApplication.widgetAt` 命中按钮后再点击）。
  - 应用 → 内部状态（设置文件 + controller/runtime 字段）：模式与循环
    的写入/回滚、保存失败恢复、禁用对 Context 主表演与音频路径的
    否决（含否决后旧表演结束与两事实保持）、恢复错误阶段文案。这些
    用例不重建应用，也未断言像素。
  - 动作页交互事实：触发真实表演、二次触发如实拒绝、会议中触发被挡
    且无绕过、缺素材如实回退、当前表演标签跟随真实动作并在页面关闭
    后解除订阅（泄漏订阅会因触碰已销毁控件而被日志捕获）。
  - **真实安装包路径（C06-R2）**：测试程序化构建通过 validator 的
    非均匀帧时长包（500/700ms 两帧），经 `library.install` +
    `_switch_character` 真实激活后验证：循环框启用与素材信息 tooltip、
    手动单次播放 `duration_ms == 1200`（素材声明周期）、真实
    `render_body` 像素级帧边界（0–499ms 首帧、500ms 起末帧、1199ms
    仍在末帧）、1200ms 处真实结束、上下文/自动路径不属循环开关范围
    （unbounded）、开页切换角色后控件可用性双向更新。
- 既有测试按新接口更新（`render_expanded` 增加 heading 形参）。
- 第四轮返工后全量回归 **1096 passed、13 skipped**（第三轮返工时
  1069+13）；治理与文档链接检查 68 项通过。逐项证据与口径修正见
  [第四轮返工响应](V1_2_REWORK_ROUND4_RESPONSE.md)，其复验反例的收口
  见 [第五轮返工响应](V1_2_REWORK_ROUND5_RESPONSE.md)。

## 6. 口径与边界

- 布局为全局配置，角色切换不会覆盖；文本模板全局与角色两级可并存，
  角色页明确显示当前生效来源，作用范围由用户选择。
- 倒计时 hidden/badge 模式下点击/双击交互区域随之收缩；hover 展开的
  几何为底部锚定，多屏负坐标场景由既有窗口管理约束兜底。
- 包推荐 UI 入口（包详情页的"推荐配置"卡片）属 V12-C1/后续包工具链
  交付范围；服务层接口已就绪并被测试覆盖。
- 面板按需构建/释放沿用既有 lazy-build 机制；动作页活动期订阅
  controller 的动作变化流（"当前表演"实时跟随），`dispose` 解除订阅，
  关闭后的页面不会再被后续动作事件触碰（有测试以日志证据覆盖）。
- 视觉终验（实际桌面操作三页）请主控执行；测试均在 offscreen 环境。
