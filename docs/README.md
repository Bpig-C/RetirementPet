# RetirementPet 文档中心

> 2026-09-01 更新
> 当前应用版本为 1.1.1
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
| Windows 应用 | 1.1.1 | 当前可试用 EXE 的产品版本 |
| 产品与架构规范 | v2.0 | 冻结设计基线，不随补丁版本同步递增 |
| PetPack schema | 1.0 | 角色包协议版本 |
| 官方退休猫 Revision | 1.0.1 | 当前官方安全角色；旧 Revision 仍冻结保留 |
| 半写实退休猫 | 0.1.1 | 本地内容通道的静态预览包 |

版本、包 Revision 和构建 hash 不能互相替代。精确的 1.1.1 artifact 身份见
[项目更新](PROJECT_UPDATES.md)。

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
| [内容、权利与隐私政策](CONTENT_POLICY.md) | official、local、community 的治理边界 |

## 当前路线

| 文档 | 职责 |
|---|---|
| [当前问题与未来工作](FUTURE_WORK.md) | `NEXT`、`PLANNED`、环境门禁和探索项 |
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
入口。
