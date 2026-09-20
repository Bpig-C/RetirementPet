# 第四轮返工响应：Todo 两项 P1 与配置/动作链路 C01–C06

> 关联：[本轮主控审阅](V1_2_CONFIG_CONTROLLER_REVIEW.md) ·
> [V12-06 交付报告](V1_2_V12_06_REPORT.md) · [文档中心](README.md)。
> 状态：自测完成，待主控验收。内部仓库文档，含内部提交号。

## 执行顺序与总览

按主控要求先修四项 P1（U07/U08/C01/C02），再处理配置与动作链路
（C03/C04/C05/C06）。全量回归 **1096 passed、13 skipped**；治理与文档
链接检查 68 项通过。报告口径按审阅要求修正（见下）。每项修复均保留
既有有效能力，未推倒重写。

## CR-U07（P1）：草稿容量满不再静默淘汰

- 复现：第 17 条失败草稿会挤掉最旧草稿，内容静默丢失。
- 修复：`_keep_draft` 在保留集满时**拒绝**新草稿（永不驱逐旧稿）；
  上限提示一次性弹出、进入下一条备注时重置；落库失败且草稿未被保留时
  如实提示"未被自动保留"。
- 测试：满集保留旧稿并拒绝新稿有反馈；上限提示自愈（后续落库成功后
  恢复收录与回收）。
- 位置：`src/retirement_pet/ui/panel/todo_page.py`、
  `tests/test_todo_rework3.py`。

## CR-U08（P1）：恢复错误按阶段如实报告

- 复现：恢复中数据库已被替换，界面仍可能声称"现有数据未改动"。
- 修复：`BackupError` 携带 `reason`；新阶段 `restore_publish_unknown`
  （os.replace 已完成、fsync 未知）如实报告"恢复已替换磁盘数据，但
  写入完成状态未知；任务列表已按重新打开的存储刷新…"；服务层任何
  恢复异常后重新打开存储并广播变更（自愈）；只有替换前的失败才报告
  "现有数据未改动"。
- 测试：publish-unknown 报告已替换 + 预置备份后第二次恢复自愈；不可用
  备份仍报告未改动。恢复点击走真实按钮路径（复现第三轮 hang 的教训，
  不用中途 `monkeypatch.undo()`）。
- 位置：`src/retirement_pet/todo/backup.py`、`service.py`、
  `ui/panel/todo_page.py`。

## CR-C01（P1）：磁盘非法配置降级，不阻塞启动

- 复现：磁盘中的非法文案配置使应用启动抛异常。
- 修复：`reload()` 对每个持久化值过 schema 校验（`_sanitize`），非法值
  丢弃并告警；布局+策略单项合法但组合被禁时两者都丢弃回引擎默认；
  `_render_text_template` 把 effective() 移入 try，模板渲染失败退回
  默认文案（第二道防线）。
- 测试：预写非法模板/非法布局/非法组合启动应用，界面降级可用且不抛
  异常。
- 位置：`src/retirement_pet/config_system.py`、`app.py`、
  `tests/test_config_pages.py`。

## CR-C02（P1）：720×480 动作页真实可点击

- 复现：最小尺寸下操作按钮被压成 0 高度，无法点击。
- 修复：动作页内容包进 QScrollArea（widgetResizable）。
- 测试：720×480 下 ≥6 个触发按钮可见且高度 ≥minimumSizeHint；视口内
  至少一个按钮中心用 `QApplication.widgetAt` 命中真实控件后 QTest
  点击，状态栏变化（程序化点击 ≠ 可点击的教训已吸收）。
- 位置：`src/retirement_pet/ui/panel/pages.py`。

## CR-C03（P2）：布局与策略成组事务

- 复现：分两次保存可能半提交；恢复默认失败仍报成功。
- 修复：`set_display(layout, policy)` 单事务成组校验候选对、一次落盘、
  失败按精确旧对回滚（存在性也还原）；`reset_display()` 三态
  （True/False/None），界面按三态如实反馈。
- 测试：全部允许组合逐对通过；注入失败后整对回滚；恢复默认三态的
  界面路径。
- 位置：`src/retirement_pet/config_system.py`、`settings.py`
  （`discard`）、`ui/panel/pages.py`。

## CR-C04（P2）：动作设置保存失败不残留内存值

- 复现：保存失败后新值仍在内存生效，后续保存还会把它落盘。
- 修复：`_set_action_setting` 快照原键的**存在性与值**，失败时精确
  回滚（原无此键则 discard，有则还原原值）；界面保存失败时
  blockSignals 回拨控件，不触发二次写。
- 测试：设置键原本缺失的回滚 ×2（内存与磁盘）、原有旧映射还原、
  控件回拨不再触发 handler。
- 位置：`src/retirement_pet/app.py`、`ui/panel/pages.py`。

## CR-C05（P2）："当前表演"跟随真实动作

- 复现：标签不随真实动作更新。
- 修复：标签优先显示 `controller.current` 的真实主表演；无运行时时
  回退上下文意图（含素材缺失/已禁用说明）；`ActionChangeEvent` 增加
  `source` 出处（引擎事实，无 provenance 字段，出处取自请求源）；页面
  活动期订阅 `controller.on_change`，`dispose` 解除订阅；模式行标签
  跟随生效值。
- 测试：手动触发/上下文切换/自然结束三种来源标签即时跟随；关闭页面
  后动作变化不触碰已销毁页面（泄漏订阅会因触碰已删控件被控制器日志
  捕获，测试以日志为证据）；重开页面显示实时状态；模式行标签随生效
  模式刷新。角色切换路径以 capability 重发布触发同一订阅刷新覆盖，
  未单独跑真实角色切换。
- 位置：`src/retirement_pet/action_controller.py`（事件 source）、
  `ui/panel/pages.py`。

## CR-C06（P2）：禁用覆盖全部请求路径；循环如实

- 复现：rest 设为 disabled 后 RESTING Context 仍使主表演为 rest；
  循环关闭只是把时长改成 10000ms，不能证明素材只循环一次。
- 修复：
  - 执行器级全局 request gate（`ActionController.set_request_gate`），
    应用把"禁用"模式注入为否决函数；Context 推导、随机调度、音频、
    面板等**一切请求路径**统一被否决（force 也不例外）。纪律只减不增：
    gate 只能拒绝，不能放行任何被会议/勿扰/冷却拦截的动作。
  - 切到禁用时已在表演的同名 unbounded 动作立即结束
    （`end_if_action("mode:disabled")`），否则无上限运行时会一直演。
  - 循环开关只在有真实支持时提供：动作是 unbounded 且当前角色 manifest
    为其声明了序列帧才启用；部件绘制的动作禁用开关并如实说明
    （"没有序列素材，无法完整单次播放"），不提供表面控制。
  - loop=False 且有序列素材时按"帧数 ÷ spec fps"完整播放素材一遍
    （12 帧 @16fps = 750ms）；无序列素材的回退仍是 min_duration_ms，
    触发反馈如实报出帧数与时长，不把有限 duration 冒充"一遍"。
  - 上下文意图与实际不符（意图被禁用）时页面标签注明
    "（该动作已禁用，未执行）"。
- 测试：引擎层 gate 全来源拒绝/可清除/不挡结束；单遍时长按帧数计算
  （750ms，非 10s）、无帧回退、1 帧下限；应用层主控原场景（rest 禁用
  + RESTING 不演 rest、意图如实标注）、禁用即时停止在演动作、音频
  路径同样被否决、循环开关需真实序列素材、单次播放全序列时长断言。
- 位置：`src/retirement_pet/action_controller.py`、`assets.py`
  （`action_frame_count`）、`app.py`、`ui/panel/pages.py`、
  `tests/test_action_controller.py`、`tests/test_config_pages.py`。

## 报告口径修正（按审阅要求）

- `tests/test_config_pages.py` 文件头：不再声称"每项都走完整循环"，
  按每类用例实际覆盖阶段分三档写明（完整循环仅显示页往返——真正重建
  PetApplication；应用→可见面变化；应用→内部状态）。
- `docs/V1_2_V12_06_REPORT.md` §5 同步改写；§3/§4/§6 的模式覆盖、
  循环语义、动作页 dispose 描述按实现后的真实行为重写。

## 附带加固：备份同秒排序（非本轮审阅项）

全量回归中 `test_owned_but_invalid_backup_is_listed_and_prunable` 偶发
失败一次（复跑通过）。根因：备份 manifest 的 `created_at` 只有秒精度，
同一秒创建的备份新旧排序依赖目录遍历顺序，而保留清理按该排序删除。
加固：manifest 写入微秒时间戳；界面显示仍截断到秒；新增同秒确定性
回归测试（后创建者在 limit=1 下存活）。

## 范围与未动项

- 未动主控审阅文档与 `docs/README.md` 的工作区改动（属主控未提交
  内容）；本响应在 README 文档中心表新增一行，随主控下次提交一并
  入库。
- 旧版回滚演练仍开放；V12-07 独立准备本轮未推进，按工单继续。
- 视觉终验（实际桌面操作）请主控执行；测试均在 offscreen 环境。
