# 第五轮返工响应：C06-R1、C06-R2、C05-R1 收口

> 关联：[主控审阅（第四轮独立复验）](V1_2_CONFIG_CONTROLLER_REVIEW.md) ·
> [第四轮返工响应](V1_2_REWORK_ROUND4_RESPONSE.md) ·
> [V12-06 交付报告](V1_2_V12_06_REPORT.md) · [文档中心](README.md)。
> 状态：自测完成，待主控验收。内部仓库文档，含内部提交号。

## 总览

针对第四轮独立复验的三个反例逐项修复。全量回归
**1103 passed、13 skipped**；治理与文档链接检查 68 项通过。
V12-06 报告中"当前角色序列"与"全路径"的结论已按修复后事实更新。

## C06-R1（P1）：被否决的上下文请求不再留下旧表演

- 复现：禁用 meeting → WORKING（work 在演）→ 加入 MEETING；resolver 已
  输出 core.meeting，但 bridge 忽略 request(False)，controller 仍为
  work；work 非会议安全语义，纪律失效。
- 修复：`PerformanceBridge.apply` 在 force 请求被拒时结束此前 resolve
  遗留的表演（`end_current("resolve:<reason>:vetoed")`）。真实事实
  不清除：MEETING/WORKING 均保持为真；宠物落到无动作（待机）而非
  继续上演不适用的旧表演。
- 测试：会议路径（work 在演 → 禁 meeting → 入 MEETING → work 结束、
  两事实保持、意图为 core.meeting）；勿扰路径（禁 rest → 入 DND →
  旧 work 结束）；对照用例（未禁用时入会正常切换为 meeting）。
- 位置：`src/retirement_pet/context_adapter.py`。

## C06-R2（P2）：循环控件与播放计划接当前激活 PetPack

- 复现：真实安装/切换的包（core.work 序列两帧 500/700ms）里循环框被
  误禁（"没有序列素材"）；服务路径 loop=False 后面板触发
  duration_ms=10000、Context 路径 None。
- 修复：
  - `PackCharacterRuntime.semantic_sequence_ms(semantic)` 公开当前
    角色某语义序列素材的**每帧 duration_ms**（C06-R2 的素材时序权威）。
  - `app._action_single_pass(semantic)` 读当前激活运行时（内置猫回退
    bundle+spec fps 的既有权威），返回（帧数，单遍总时长）。
  - 循环框可用性/勾选态/tooltip 全部由该查询驱动；单次播放载荷改为
    `single_pass_ms`（素材声明周期总和），控制器不再自行从帧数推算。
  - tooltip 如实标注范围：循环开关只作用于手动触发的表演；上下文/
    自动表演不受影响、循环到事实变化为止（并有测试断言 Context 路径
    在 loop=False 下仍 unbounded）。
  - 切换角色后（页面保持打开）控件可用性实时双向更新：app 新增
    `on_character_changed` 订阅（`_sync_capabilities` 发布）。
- 测试：程序化构建**通过 validator 的真实包**（非均匀 500/700ms 两
  帧），`library.install` + `_switch_character` 真实激活后验证：
  `_action_single_pass == (2, 1200)`；循环框启用且 tooltip 含范围
  标注；手动单次 `duration_ms == 1200`、反馈"1.2 秒"；**真实
  `render_body` 像素级帧边界**（0–499ms 同帧、500ms 换帧、1199ms 仍
  末帧）；FakeClock 推进 1199ms 后 tick 仍在演、再 1ms 后 tick 结束；
  自动/上下文路径 unbounded；内置猫 ↔ 序列包来回切换控件可用性
  跟随。不再以旧 bundle 帧数/FPS 的 monkeypatch 自证。
- 位置：`src/retirement_pet/petpack/runtime.py`、`app.py`、
  `action_controller.py`（`single_pass_ms`）、`ui/panel/pages.py`、
  `tests/test_config_pages.py`。

## C05-R1（P2）：被否决的上下文变化也刷新当前表演提示

- 复现：动作页保持打开、当前 core.idle，禁 rest 后设 RESTING；bridge
  意图变 core.rest、controller 为空，但标签停留 core.idle——页面只订
  阅了 controller 动作事件，被否决的上下文变化不产生该事件。
- 修复：动作页同时订阅 ContextStore 变化（以及角色切换），dispose
  解除全部三路订阅；有订阅数量的生命周期测试（建立前/后/关闭后）。
- 测试：页面持续打开时禁用 rest + 设 RESTING → 标签变为
  `core.rest…（该动作已禁用，未执行）`，无需动作事件或重开页面。
- 位置：`src/retirement_pet/ui/panel/pages.py`。

## 附带修复

- 刷新循环控件时未屏蔽信号，`setChecked(False)` 会经 toggled 处理器
  把存储偏好误写为 False（与 C04 同类）；刷新路径已 blockSignals，
  既有"控件状态不写穿"测试覆盖。

## 范围与未动项

- 主控证据目录 `.release/controller-review-cd4ab2b/` 未纳入版本库，
  不进入公开快照。
- Markdown 预览（R5-MD-01）继续后置；旧版配套数据回滚仍为独立未闭环
  项；V12-07 独立准备继续按工单推进。
