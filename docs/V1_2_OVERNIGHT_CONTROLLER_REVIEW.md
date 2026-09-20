# 大规模实施后主控审阅（a8981f0）

日期：2026-09-14。范围：76f760c..a8981f0，重点为 R08-03、降载、V12-09
候选消费与回滚准备。关联：[版本工单](V1_2_WORK_ORDER.md)、
[V12-08 报告](V1_2_V12_08_REPORT.md)、[V12-09 报告](V1_2_V12_09_REPORT.md)。

## 工作审视报告

### 原定目标与完成情况

核对昨夜交付，独立验证高风险变化，形成逐步返工清单。本轮不修改产品代码。

- 已核对提交及源码净差异；审阅开始时工作树干净。
- 相关回归：go_no_go、downshift、app_smoke、window **133 passed，63.40 秒**。
  未重跑执行者 1149/13 全量；通过计数不代表日志无异常，见 OVR-04。
- 读取实际 acceptance.json，确有 ALPHA-GO，候选 c48305d、版本 1.1.1。
  当前 dist EXE SHA256 为 1ed456fb49818f9064168c5d9262d09417928d1fa79405a5600a68645095afd1，
  与 acceptance 中 receipt 的 exe_sha256 一致。本轮未重新运行完整真实 EXE 门禁。
- 独立反例及日志在 `.release/controller-review-a8981f0/`，全部使用隔离合成数据。
- 未完成：V12-08/09 主控签收、发布版旧候选性能比较、真实配套回滚及生产环境门禁。

### 发现的问题

| 编号/级别 | 具体问题 | 原因 | 收口要求 |
| --- | --- | --- | --- |
| OVR-01 / P1 | go_no_go.result_postcheck_with_binding 接受失败检查及替代 source 报告 | 校验检查字段存在，却不重算成功；由报告自己决定是否进行 EXE 绑定 | 调用端指定预期 target，验证完整检查集及严格布尔结果，消费端独立裁决 |
| OVR-02 / P1 | verify_displays 的全局物理坐标直接除 DPR，破坏非零屏幕原点 | 只在原点为零的缩放模型下验证换算，未落实屏幕原点映射要求 | 分离屏幕原点、屏内偏移及尺寸单位，保存映射证据并独立重算 |
| OVR-03 / P2 | prepare_v1_drill 会覆盖已有 settings.json | 只保护 tasks.db，没有验证整个输出目录是新建演练目录 | 写入前拒绝既有数据目录或使用独立新子目录；失败保全已有文件 |
| OVR-04 / P2 | test_downshift 通过时仍有 QObject.eventFilter 的 AttributeError | 过滤器生命周期没有全部闭合；测试只看断言通过 | 所有安装路径成对解除，重复运行和销毁窗口无 Qt 回调异常 |

### OVR-01：两个独立反例

直接调用生产消费函数，传入 expected_build_id="expected" 与预期 EXE hash：

1. 报告 target="source"、checks=[{"pass":true}]、result="PASS"，无 artifact
   或 EXE hash：返回 PASS。预期 EXE 门可以由 source 报告绕过绑定。
2. 报告 target="exe" 且绑定字段正确，checks=[{"pass":false}]、result="PASS"：
   仍返回 PASS。说明不是仅缺来源约束，检查本身失败也未参与裁决。

两者均使用 exit_code=0。不是声称现有真实运行伪造，而是证明消费者的
fail-closed 声明不成立。补外来 target、缺失必需检查、失败布尔、非布尔值，
以及合法 source/EXE 正例；检查名和必需集合应来自 harness 契约。

### OVR-02：原点换算残余

_diagnostic_usable 中 native_logical=[v/dpr for v in native_rect]，后续 sample
验证重复相同算法。Qt/Windows 的全局坐标并非以所有屏幕共同零点统一缩放。
合成屏幕原点 x=1920、DPR=2，窗口物理矩形 [2120,200,400,320]，对应 Qt
逻辑矩形 [2020,100,200,160]（原点保留、屏内偏移缩放）。输入新鲜有效诊断，
该函数返回 false：window_frame contradicts the natively observed window。

这是受控坐标模型反例，不是真机混合 DPI 通过证据。补非零正原点、负坐标、
混合 DPI 映射正反例；不能只再增加原点为零的 QT_SCALE_FACTOR 测试。
同时原生 window_delta 仍与 FOOT_TOLERANCE_DIP 直接比较，需明确独立的物理
矩形容差或转换后比较。已修的新鲜度和序号全序检查保留。

### OVR-03：演练目录保全

隔离目录中预先放置 settings.json={"sentinel":"preserve"}，不创建 tasks.db；
调用 build_v1_dir 后原配置变为 {}。工具文案声称不碰日用数据，但它接受任意
已有目录，且此类无 Todo 数据库的目录并非一定空白。要求在任何写入前检查，
拒绝时不新增数据库、不覆盖配置；补失败中途的清理/保全测试。

### OVR-04：回归日志中的实际异常

本轮 tests.log 在 54% 后出现多次：
Error calling Python override of QObject::eventFilter():
AttributeError: '_PaintCounter' object has no attribute 'window'。
定位 tests/test_downshift.py:29。部分用例仍直接 installEventFilter 而未 finally
解除，已有 _counting 上下文未覆盖全部路径。133 passed 是实际计数，但必须
保留上述异常说明；不推断产品窗口本身崩溃。补窗口销毁/夹具退出的生命周期
验证，必要时将 Qt 回调错误变成测试失败。

### 做得好的地方与裁定

实际构建和 EXE 运行发现并修复了 ctypes、隔离导入等问题；候选哈希可核对；
性能数据继续标记 provisional；Production 未误报认证通过。这些成果保留。

但当前 Alpha 记录只能说明当前程序给出了 GO，OVR-01 表明消费端尚不能支撑
完整的独立签收。**节点 B 保持签收，V12-08 与 V12-09 暂不整体签收。**
R08-03 坐标关联残余归并到 OVR-02，不另起重复工单。

### 下次重点关注与返工顺序

1. 先修 OVR-01、OVR-02，交付上述独立反例以及正常链路；不用反复扩大整套测试。
2. 修 OVR-03、OVR-04；降载进一步覆盖静态角色切换、序列回退、配置变化后的
   首帧与布局刷新。本轮代码阅读发现这些边界缺少直接证据，暂不以此新增阻塞缺陷。
3. 主控复验通过后再重建候选并重跑绑定门禁，保留旧候选与新证据的明确区分。
4. 回滚演练可先用已知来源旧 EXE + 合成 v1 数据完成“开发构建兼容演练”；它
   不等于已发布稳定版认证，也无需以取得用户真实历史数据作为所有演练的前提。
5. 版本号、许可/LGPL、公开快照组装仍是工程工作；环境不足只适用于相应真机门，
   不应统称为“非执行者可闭环”。Markdown 和 Blender 继续按独立安排。

如在新任务继续返工，请以本报告作为上下文；本次不要求用户追加产品决策。

## 7174b02 返工复验（2026-09-14）

审阅 4afb13f、2fbb8d3、7174b02。主控相关回归 **82 passed，54.09 秒**
（go_no_go、downshift、文档和治理），本轮日志没有此前的 Qt 回调异常。
未重跑执行者声称的 1150/13 全量。独立探针保存在
`.release/controller-review-7174b02/probe.py` 与 `results.json`。

**OVR-04 关闭；OVR-01、02、03 部分修复，继续返工。** 节点 B 状态不变，
暂不进入以“全部关闭”为前提的候选重建。

### OVR-01 残余：实际调用漏传绑定，检查契约不完整（P1）

expected_target 与严格布尔、重复检查名检测已接入，修复方向正确。但是：

1. `_run_acceptance_impl` 内部的 `result_postcheck` 包装器只传 expected_target，
   丢失 expected_build_id 与 expected_exe_sha256。真实 EXE 报告即使绑定正确，
   也会与 None 比较而 INVALID。主控从源码 AST 提取实际包装器执行，完整
   合法 EXE 报告返回 `window report does not bind the expected EXE candidate`。
   直接测试模块级函数并手传正确参数没有覆盖这个断点。
2. `REQUIRED_CHECKS` 漏掉实际生产的 `B.onboarding-panel-visible` 等检查。
   主控构造完整必需集全部 True，额外加入该真实检查 False，绑定正确且顶层
   result=PASS；消费结果仍 PASS。当前只检验 required 中的失败值，不处理
   实际报告其余失败检查，仍可把失败报告提升为成功。

要求：修复包装器绑定透传，核对生产者所有实际检查（包括公共阶段、健康检查
及 target 分支），任何报告检查失败均不得 PASS。增加实际包装器/门禁路径
正例，以及真实但不在旧 required 中的失败项反例，不能仅用 REQUIRED_CHECKS
自身生成“完整报告”来证明契约完整。

### OVR-02 残余：旧换算仍在完整推导中执行（P1）

同一份非零屏原点记录：物理 [2120,200,400,320]、逻辑
[2020,100,200,160]、屏幕原点 x=1920、DPI=192，`_frames_agree` 返回 True。
但 `derive_foot_result` 紧接着仍执行 `native_logical=[v/(dpi/96) for v in rect]`，
完整推导返回 INVALID（native rect contradicts diagnostic window frame）。
所有样本/阶段均使用同一合法记录、严格递增序号和新鲜时间，反例不是缺字段。

此外，末尾 window_delta 仍直接以原生物理矩形差与 FOOT_TOLERANCE_DIP 比较；
用户转述的“位移已按 DPI 换算”未在此 HEAD 的实际路径落实。

要求：清除重复旧转换，统一使用共享映射；明确原生位移容差单位。验收必须
从完整 foot 推导到 validator/消费端验证正例，而不止测新 helper。

### OVR-03 残余：失败保全仅覆盖数据库阶段（P2）

已有 settings.json 的非空目录现在拒绝写入且原内容保留，这个原反例关闭。
但 `logs.mkdir`、`settings.json.write_text` 位于清理 try 之外；注入配置写入
OSError 后，输出目录仍留有 logs 和 tasks.db。重新执行又因非空而被拒绝，
所以“中途失败后无半成品”的承诺仅覆盖 initialize_v1 早期失败。

要求：覆盖整个演练产物生成过程的失败保全；补数据库完成后的配置写入失败
及正常重试路径。清理只能针对本次创建的演练产物，继续保留非空目录拒写。

### 收尾与继续顺序

OVR-04 本轮相关测试运行未再出现回调异常，可保持关闭。优先完成 OVR-01/02
的实际调用与完整推导测试，再补 OVR-03 晚期失败；之后主控复验，再重建候选。
审阅开始时另有未跟踪 `_patch_ovr02.py`，不是本轮主控创建，尚需执行者确认
归属并整理；主控审阅文档与 README 修改继续保留，未提交。

## 07316d7 残余复验（2026-09-14）

本轮独立探针位于 `.release/controller-review-07316d7/`。
相关回归 **86 passed，54.32 秒**（go_no_go、downshift、文档与治理），
日志无 Qt 回调异常；未重跑执行方全量。git diff --check 通过。
**关闭 OVR-01/02 上轮残余，OVR-04 保持关闭；OVR-03 仅剩部分写入失败清理。**
这是所列反例的工程复验，不等于重新运行候选门禁或真机认证。

### 已通过的独立复现

- 从源码 AST 提取实际内部 result_postcheck 包装器，以真实闭包变量
  expected_id/expected_exe 执行：完整合法 EXE 绑定报告 PASS。
- 必需集之外的真实 B.onboarding-panel-visible=False，消费端 FAIL。
- 原点 x=1920、DPI=192 的完整 foot 推导 PASS，旧平面除法残余已清除。
- 演练输出目录原有配置保持不变，非空目录继续拒写。

### OVR-03：部分写入后失败仍留下 settings.json（P2）

prepare_v1_drill.py 先执行 settings.write_text，再 created.append(settings)。
因此写入内部已经创建或截断文件、随后写入/关闭失败时，清理名单还没有该文件。
独立探针先实际写入一个左花括号，再抛 OSError：数据库与 logs 清理成功，但
settings.json 残留。下一次演练因目录非空被拒绝。

要求：在可能产生文件的操作之前登记本次拥有的目标，或采用等价的完整清理
方案；保留对非空目录的保护。补“先创建/部分写入，再异常”的反例，并验证
清理后能正常重试。当前用例在 write_text 一进入就抛异常，未覆盖这一阶段。
此问题仅涉及隔离演练产物，不宣称破坏了用户日用数据。

### 测试口径修正

test_inner_wrapper_passes_binding_to_exe_postcheck 的名称和说明宣称验证实际
内部包装器，但函数体仍直接调用模块级 result_postcheck_with_binding，且
手动传入绑定参数。它不会抓住上轮包装器漏传参数的回归。请改成真正覆盖
包装器/门禁调用的测试，或如实改名并另补该路径。本轮主控用实际源码包装器
独立验证通过，因此不以此重新否定已经确认的实现修复。

### 下一步

只继续 OVR-03 上述边界与测试覆盖收尾，无需用户追加裁定。关闭后再重建候选
并复跑绑定门禁；OVR 关闭不自动关闭性能冻结、旧版配套回滚与生产环境门禁。
审阅文档和 README 仍未提交，执行者的未跟踪补丁脚本未改动。

## 1c4d597 收口复验（2026-09-14）

主控相关回归 **87 passed，55.19 秒**（go_no_go、downshift、文档与治理），
日志未见此前 Qt 回调异常；未重跑执行者 1155/13 全量。diff-check 通过。

**OVR-01～04 的已报告实现缺陷全部关闭，允许进入候选重建与绑定门禁重跑。**
此裁定不等于新候选已经通过门禁，也不关闭生产环境认证和其余发布工作。

主控独立探针：`.release/controller-review-1c4d597/probe.py`、`results.json`。
在 settings.json 真正写入部分内容后注入 OSError，输出目录残留列表为空；
撤销注入后对同一目录重新构建，SQLite 实查 tasks 数量为 3。已有配置仍拒写
并保全。实际内部包装器合法绑定 PASS、额外真实失败项 FAIL、非零屏原点完整
脚底推导 PASS，均复跑确认。

### 非阻塞的测试与报告收尾

当前提交相对 07316d7 只修改演练生成器和一个测试文件，并未新增交付说明中
所称的“门禁路径正例与反例”。模块级用例虽已改名，但 docstring 与段落标题
仍声称测试实际内部包装器，需同步更正。主控已独立抽取真实包装器执行验证，
因此实现正确性已有证据，不以文字和测试范围问题重新打开产品缺陷。

新增 retry 用例在 sqlite3.connect 处抛错；原 late-failure 用例在 write_text
进入时抛错，均未实际写入文件再异常。因此仓内仍应补本轮主控复现的“部分
写入→清理→同目录成功重试”，让将来的回归能自动抓住该缺陷。可与候选准备
一起完成；测试只覆盖已有明确边界，不扩展新功能。

### 后续执行口径

整理执行者遗留的 `_patch_ovr02.py`，并协调纳入主控文档/索引，取得自洽的
干净源码基线。所有源码与测试收尾完成后固定提交，再构建候选；构建及验收
期间不移动 HEAD 或临时 stash 文档。新 receipt、候选哈希和门禁证据必须绑定
新提交，旧 c48305d 的 ALPHA-GO 记录保留为历史，不复用作本次通过证据。
性能基线、合成数据配套旧版演练和发布材料按原工单继续推进。
