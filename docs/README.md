# RetirementPet 文档中心

> 2026-09-15 更新
> 当前 1.3.0 候选（4c7c3c3/c973ee69）已取得 ALPHA-GO（23/23 门禁 PASS）；V13-E1 六项真机门禁 PENDING
> 仓库可位于任意普通的 `<project-root>`；路径不是实现前提
> 返回 [项目 README](../README.md)

这里是项目文档的唯一总入口。[实施状态](IMPLEMENTATION_STATUS.md)记录当前代码与
门禁事实，[项目更新](PROJECT_UPDATES.md)记录版本变化，[未来工作](FUTURE_WORK.md)
记录会继续调整的排序。2026-08-28 的审计和 M0 至 M9 路线保留为历史基线，不再
代表当前实现。

## 第一次阅读

- 想直接试用，先读 [Alpha 初步试用说明](ALPHA_TRIAL.md)。
- 想判断现在做到了什么，读 [实施状态](IMPLEMENTATION_STATUS.md)和
  [当前问题与未来工作](FUTURE_WORK.md)。
- 想制作角色，读 [角色库扩展指南](CHARACTER_LIBRARY_GUIDE.md)，再进入
  [角色作者工作区](../character-work/README.md)。
- 想修改引擎或协议，从 [v2 总规范](DESIGN_V2.md)、[决策记录](DECISIONS.md)和
  [PetPack 1.0](PETPACK_SPEC_1_0.md)开始。

## 五条独立版本轴

| 对象 | 当前值 | 说明 |
|---|---|---|
| Windows 应用 | 1.3.0 | 当前候选已取得本机 ALPHA-GO；Production PENDING_ENVIRONMENT |
| 产品与架构规范 | v2.0 | 冻结设计基线，不随补丁版本同步递增 |
| PetPack schema | 1.0 | 角色包协议版本 |
| 官方退休猫 Revision | 1.0.2 | 当前官方角色（V13-07 序列动画）；1.0.0/1.0.1 冻结保留 |
| 半写实退休猫 | 0.1.1 | 本地内容通道的静态预览包 |

版本、包 Revision 和构建 hash 不能互相替代。精确的 1.2.0 候选身份见
[V12-09 阶段报告](V1_2_V12_09_REPORT.md)；旧版本历史见[项目更新](PROJECT_UPDATES.md)。

## 使用与发布

| 文档 | 职责 |
|---|---|
| [项目 README](../README.md) | 功能概览、运行、构建、使用、数据和回滚 |
| [Alpha 初步试用说明](ALPHA_TRIAL.md) | 试用前备份、15 分钟路径、已知限制和反馈信息 |
| [实施状态](IMPLEMENTATION_STATUS.md) | 当前源码、当前精确构建、Alpha 边界和 Production 门禁 |
| [项目更新](PROJECT_UPDATES.md) | 按时间追加的版本变化和冻结构建身份 |
| [一致性与发布验收](CONFORMANCE.md) | gate、证据层级和 GO/NO-GO 规则 |
| [证据治理说明](../evidence/README.md) | 公开脱敏摘要与私有原始 evidence 的边界 |
| [贡献指南](../CONTRIBUTING.md) | 分支、测试、文档、隐私和角色包 PR 要求 |
| [安全报告政策](../SECURITY.md) | 私密漏洞报告入口与脱敏要求 |
| [GitHub 协作与分支治理](GITHUB_GOVERNANCE.md) | `main` 保护、审核、CI 与 fork Actions 审批 |
| [许可说明](LICENSING.md) | MIT、内容资产、贡献和二进制分发边界 |
| [第三方组件声明](../THIRD_PARTY_NOTICES.md) | 运行依赖及公开二进制许可门禁 |

## 角色创作与内容

| 文档 | 职责 |
|---|---|
| [角色库扩展指南](CHARACTER_LIBRARY_GUIDE.md) | 当前作者可用范围和端到端工作流 |
| [角色作者工作区](../character-work/README.md) | 所有可编辑角色源目录的索引 |
| [半写实猫作者说明](../character-work/realistic-retirement-cat.AUTHORING.md) | 0.1.1 构建方式和下一轮素材顺序 |
| [半写实猫生成与来源](../character-work/realistic-retirement-cat.PROVENANCE.md) | 生成提示、像素验收和权利边界 |
| [随程序提供的 PetPack](../assets/petpack/README.md) | 官方包、旧 Revision 和本地示例包清单 |
| [资产许可映射](../assets/LICENSE.md) | 图标、旧资产配置与 PetPack 的逐项许可边界 |
| [角色资产发布裁定](CHARACTER_ASSET_RELEASE_DECISION.md) | 官方猫、半写实猫与第三方 IP 的公开快照、二进制和本地导入边界 |
| [内容、权利与隐私政策](CONTENT_POLICY.md) | official、local、community 的治理边界 |

## 当前路线

| 文档 | 职责 |
|---|---|
| [当前问题与未来工作](FUTURE_WORK.md) | `NEXT`、`PLANNED`、环境门禁和探索项 |
| [1.3.0 版本工作工单](V1_3_WORK_ORDER.md) | Agent CLI、Todo 逻辑、Markdown、Windows 媒体、角色内容与公开发布的下一版本总工单 |
| [1.3 变更矩阵](V1_3_CHANGE_MATRIX.md) | 1.3 各工单项的需求、提交、测试、文档与候选证据对应表 |
| [本地 Agent 协议](AGENT_PROTOCOL.md) | `retirement-pet.agent.v1` JSON 行协议、结果码、幂等与乐观并发语义 |
| [异步 Agent 工作流](agent-workflow/WORKFLOW.md) | 执行者、主控、状态机、周期交接、同机互斥与发布边界 |
| [异步工作流索引](agent-workflow/README.md) | 工作流常设文档、角色 Skill、报告模板与周期记录的子索引 |
| [当前 Agent 周期](agent-workflow/CURRENT_STATE.md) | 当前周期、基线、下一行动者及交接文档入口 |
| [Agent 调度提示模板](agent-workflow/SCHEDULE_PROMPTS.md) | 首次执行、后续重复运行与主控审阅的无上下文提示 |
| [节点 A 交付报告](V1_3_NODE_A_REPORT.md) | V13-00/03/01 交付事实、测试证据与三方独立验收结论 |
| [节点 B 交付报告](V1_3_NODE_B_REPORT.md) | V13-02/04/05/06 交付事实、真机媒体验证与三方独立验收返工闭环 |
| [节点 C 交付报告](V1_3_NODE_C_REPORT.md) | V13-07/08/09 交付事实、1.3.0 候选身份、回滚演练与 V13-E1 如实状态 |
| [1.3 主控返工单](V1_3_CONTROLLER_REVIEW.md) | 节点 B/C 与候选发布的七项必须返工与复验顺序 |
| [1.3 返工响应报告](V1_3_REWORK_RESPONSE.md) | CR13-01 至 CR13-07 与 P2 项逐项修复结果；最终候选 4c7c3c3 取得 ALPHA-GO |
| [1.3 返工主控复验](V1_3_REWORK_RECHECK.md) | 返工后残余：RR13-01 至 RR13-05 三项 P1 与两项 P2 |
| [1.3 二轮返工响应](V1_3_REWORK2_RESPONSE.md) | RR13-01 至 RR13-05 逐项修复结果；最终候选 77be50e 与材料 receipt 绑定 |
| [1.3 媒体收尾报告](V1_3_MEDIA_FINAL_REPORT.md) | RR13-05 媒体失败态闭环、证据路径修正与剩余构建步骤 |
| [1.3.0 主控审阅返工单](V1_3_CONTROLLER_REVIEW.md) | ac6f1eb 独立复验：Markdown 本地读取、Agent IPC/幂等、公开快照、许可材料、候选身份与开发隔离七项返工 |
| [1.3.0 返工主控复验](V1_3_REWORK_RECHECK.md) | 69054db 复验：四项主缺口关闭；公开快照、SBOM 归因、材料 receipt 与媒体异步语义仍需收尾 |
| [1.3.0 第二轮返工主控复验](V1_3_FINAL_CONTROLLER_RECHECK.md) | d58966f 复验：快照、SBOM、材料身份签收；媒体启动失败态与测试尚需一次小修 |
| [1.2.0 版本工作工单](V1_2_WORK_ORDER.md) | 已完成的日常使用增强工单与历史验收范围 |
| [1.2.0 主控审阅反馈](V1_2_CONTROLLER_REVIEW_A.md) | 节点 A 与 V12-02 的八项裁定、独立复验和返工要求 |
| [1.2.0 进度核对与复验](V1_2_PROGRESS_RECHECK.md) | 2026-09-12 返工验证、剩余缺口与后续顺序 |
| [V12-03 作者工具主控审阅](V1_2_AUTHOR_CONTROLLER_REVIEW.md) | 四次复验后 CR-T01–04 关闭，V12-03 按工单范围签收 |
| [V12-03 作者工具审阅响应报告](V1_2_AUTHOR_REVIEW_RESPONSE.md) | CR-T01–04 修复与测试证据（含第二轮字体根因、豆腐块对照与 16 帧末帧导出）、口径修正、自测结果 |
| [V12-05 交付报告](V1_2_V12_05_REPORT.md) | 四象限视图、可恢复子树归档、懒加载长备注侧页的实现事实、口径取舍与测试证据；自测完成待验收 |
| [V12-05 主控审阅](V1_2_V12_05_CONTROLLER_REVIEW.md) | 归档取代删除的裁定、227/1 独立测试、六项 UI/服务缺口复现与返工条件 |
| [第三至五轮返工与 V12-06 主控审阅](V1_2_CONFIG_CONTROLLER_REVIEW.md) | 第五轮三个反例关闭，真实包播放边界与订阅释放复验通过；V12-05、V12-06 工程签收 |
| [V12-08 阶段报告](V1_2_V12_08_REPORT.md) | 可信显示证据链、静态视觉降载与暂定性能对比；真机及正式性能冻结仍待完成 |
| [V12-09 阶段报告](V1_2_V12_09_REPORT.md) | 1.2.0 候选（70c5934）取得本机 ALPHA-GO：23 PASS、1 个环境型 SKIP；发布材料与 Production 门禁仍开放 |
| [大规模实施后主控审阅](V1_2_OVERNIGHT_CONTROLLER_REVIEW.md) | a8981f0 独立复验：窗口消费绑定、DPI 原点映射、演练目录保全、测试回调生命周期四项返工 |
| [1.2.0 候选回滚说明](V1_2_ROLLBACK.md) | 回退上一稳定版、Todo 数据回 v1、角色库/配置差异的场景化操作与兼容边界；演练状态如实声明 |
| [节点 B 集成验收材料](V1_2_NODE_B_MATERIALS.md) | V12-02/03/05/06/07 模块清单与提交区间、Todo 全流程/配置重启/导入版本管理端到端演示、测试命令与实际结果、兼容回滚、已知开放项 |
| [节点 B 与 V12-08 主控审阅](V1_2_NODE_B_AND_V12_08_CONTROLLER_REVIEW.md) | 346 项独立回归通过，节点 B 工程签收；V12-08 第一片六项返工及 Alpha 可选认证裁定 |
| [V12-07 返工响应](V1_2_V12_07_REWORK_RESPONSE.md) | L07-01 续删保护重检与取消闭环、L07-02 最小尺寸滚动可达、L07-03 缩略图四层边界与按角色缓存；自测完成待验收 |
| [V12-07 交付报告](V1_2_V12_07_REPORT.md) | 角色库版本详情、系列筛选、回滚与安全卸载（pending-delete、凭证保留、缩略图预算）的实现事实与测试证据；自测完成待验收 |
| [V12-07 主控审阅](V1_2_V12_07_CONTROLLER_REVIEW.md) | 3fe2306 横向裁切复验关闭，V12-07 工程签收；节点 B 集成与其他门禁另验 |
| [第五轮返工响应](V1_2_REWORK_ROUND5_RESPONSE.md) | C06-R1/C06-R2/C05-R1 收口：被否决上下文请求结束旧表演、循环控件与播放计划接当前激活 PetPack、页面订阅上下文变化；自测完成待验收 |
| [第四轮返工响应](V1_2_REWORK_ROUND4_RESPONSE.md) | Todo 两项 P1（草稿容量、恢复错误阶段）与配置/动作链路 C01–C06 逐项复现、修复与测试证据、口径修正；自测完成待验收 |
| [V12-05 第三轮返工响应](V1_2_REWORK_ROUND3_RESPONSE.md) | CR-U01–U06 与 CR-T04 逐项复现、修复、测试与产物；自测完成待验收 |
| [V12-06 交付报告](V1_2_V12_06_REPORT.md) | 配置页真正保存并生效：布局/可见性/文案模板/动作模式的真实链路实现、纪律门与测试证据；自测完成待验收 |
| [1.2.0 进度核对响应报告](V1_2_RECHECK_RESPONSE.md) | CR-P03/CR-A05 修复与 V12-03 作者工具交付事实、测试证据与未闭环项 |
| [1.2.0 节点 A 返工交付报告](V1_2_NODE_A_REWORK_REPORT.md) | 审阅项逐条修复方案、提交、复现测试结果与剩余依赖 |
| [1.2.0 节点 A 交付报告](V1_2_NODE_A_REPORT.md) | V12-01/V12-04 交付事实、V12-02 设计与待主控决定事项（含审阅后修订） |
| [PetPack 一致性闭环对照表](PETPACK_V12_CONSISTENCY.md) | V12-02 的规范↔validator↔preflight↔runtime 四列对照、逐包审计与新诊断码 |
| [性能与资源预算](PERFORMANCE_BUDGET.md) | CPU、内存、延迟、缓存和包体的测量规则 |

## 冻结规范

| 文档 | 职责 |
|---|---|
| [v2 产品与架构总规范](DESIGN_V2.md) | 产品继承、状态、窗口、配置、生命周期和完成定义 |
| [v2 决策记录](DECISIONS.md) | 已确认取舍、否决方案和重新讨论条件 |
| [PetPack 1.0 规范](PETPACK_SPEC_1_0.md) | 包身份、manifest、动作、资源、安全和生命周期协议 |
| [内容、权利与隐私政策](CONTENT_POLICY.md) | 内容通道、来源、权利、隐私和 UI 措辞 |
| [性能与资源预算](PERFORMANCE_BUDGET.md) | 冻结测量结构与待校准数值 |
| [一致性与发布验收](CONFORMANCE.md) | 自动化、原生、真机和长期证据要求 |

规范词义如下。

- `FROZEN` 表示用户已经确认，实现不得静默改变含义。
- `PROVISIONAL` 表示方向已确认，数值仍需固定夹具实测后冻结。
- `EXPLORATORY` 表示候选方向，不属于当前版本承诺。
- `MUST` 和“必须”是发布硬门槛，`MUST NOT` 和“禁止”是不可绕过的边界。

规范冲突时，先看 [决策记录](DECISIONS.md)中的 FROZEN 决定，再看 PetPack、内容
政策和 conformance 的 MUST，随后看总规范和已冻结性能值。当前代码若与规范有
差异，应在实施状态或未来工作中诚实记录，不能把代码行为反向写成规范。

## 历史快照

以下文档保留形成过程和迁移证据。它们有历史价值，但不能作为当前事实入口。

| 文档 | 历史边界 |
|---|---|
| [2026-08-28 状态审计](CURRENT_STATE_AUDIT.md) | 固定程序化猫向 PetPack 迁移前的事实快照 |
| [v2 M0 至 M9 已执行路线](IMPLEMENTATION_ROADMAP.md) | 从 v1 迁移到当前 Alpha 功能的冻结顺序 |
| [v1 设计与实施规格](DESIGN.md) | 单角色、旧 ActionId 和旧素材管线基线 |
| [v1 素材规格](ASSETS_SPEC.md) | `assets/manifest.json` 的旧分层 PNG 约定 |
| [v1 连续实施提示](CONTINUOUS_IMPLEMENTATION_PROMPT.md) | 已完成阶段使用的旧执行提示，禁止当作当前任务入口 |
| [旧 sprites 目录说明](../assets/sprites/README.md) | v1 素材占位目录的现状与迁移说明 |
| [旧 sounds 目录说明](../assets/sounds/README.md) | 预留音效目录的现状与边界 |

## 文档治理

当前事实只在实施状态中维护，版本变化追加到项目更新，探索和排序只在未来工作中
迭代。原始 receipt、acceptance、生命周期事件和历史 evidence 不覆盖；发现漂移
时先分类，再修正文档或实现。该路由由 [ADR-V2-024](DECISIONS.md#adr-v2-024当前事实版本事件与规划分流)
显式继任旧的迁移期审计路由。

每份 Markdown 都应从本索引或一个受本索引管理的子索引可达，并至少有一个入链。
`tests/test_documentation_links.py` 从本索引遍历完整文档图，并检查相对链接、越界
目标和不可达文档。新增角色工作区、历史说明或操作手册时，应在同一个提交里补上
入口；异步开发周期的 BRIEF/EXECUTION/REVIEW 逐周期登记在
[周期记录索引](agent-workflow/cycles/README.md)中。
