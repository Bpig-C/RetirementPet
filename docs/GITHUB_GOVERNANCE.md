# GitHub 协作与分支治理

> 文档关系　[文档中心](README.md) · [贡献指南](../CONTRIBUTING.md) ·
> [安全政策](../SECURITY.md) · [内容政策](CONTENT_POLICY.md)
>
> 适用仓库　`Bpig-C/RetirementPet`
> 状态　FROZEN 协作基线

仓库接受外部 Pull Request。公开协作不能削弱本地隐私、角色内容权利和冻结发布
证据的边界。

## `main` 保护要求

GitHub 服务端必须对 `main` 启用以下规则。

- 所有变更经 Pull Request 合并，包含维护者自己的普通修改；
- 合并前至少一名有写权限的维护者批准；
- 要求 Code Owner 审核，并在新提交出现后撤销过期批准；
- 要求唯一状态检查 `tests` 通过，合并分支必须与 `main` 保持最新；
- 要求所有审阅对话解决，并禁止强推、删除和绕过规则；
- 规则同样约束仓库管理员，紧急修复也应留下可审计 PR。

`.github/CODEOWNERS` 和 CI 文件只是仓库内声明。真正的阻止能力来自 GitHub branch
protection 或 ruleset，维护者必须在仓库设置中启用并通过 API 读回核对。

## Fork Actions 审批

公开 fork 的工作流使用普通 `pull_request` 事件，权限只有 `contents: read`，不读取
secrets，也不使用权限更高的 `pull_request_target`。Actions 的 fork 审批策略设置为
“Require approval for first-time contributors”。第一次参与本仓库的外部贡献者触发
工作流后，需要有写权限的维护者检查 workflow diff 和改动内容，再批准运行。

GitHub 官方文档说明，贡献者一旦有提交或 PR 被合并，之后的运行可能不再属于“首次
贡献者”。如果项目受到持续恶意 PR 或工作流资源滥用，应把策略升级为“Require
approval for all external contributors”，不要假设首次审批永久覆盖同一账号。

## 内容 PR 的附加审核

角色、图片、声音、字体和文字 PR 除通用代码审核外，还要填写并核对以下内容。

- 作者、原始来源、生成工具和可核查的 `source_ref`；
- SPDX 标识或完整许可文本、署名方式、修改权和公开再分发范围；
- AI 使用、人工修改和第三方 IP 情况；
- `rights_ref`、`legal_file_ref` 与实际包内容的闭包；
- `petpack_cli` 的构建、验证、预检结果和包体预算。

缺少授权不能用 hash、schema 通过或“仅供学习”补足。内容审核通过前，不把素材合入
官方角色库或公开发行包。

## 维护者核对清单

每次修改规则、工作流或默认分支后，维护者应读回并记录以下事实。

1. 默认分支仍为 `main`，服务端拒绝直接推送、强推和删除。
2. `tests` 是唯一且稳定的 required check 名称，最近一次主分支运行成功。
3. 需要一个批准、Code Owner 批准、过期审核撤销和对话解决均已开启。
4. Actions 默认权限为只读，工作流不能批准 PR，也没有仓库级 secret 依赖。
5. fork 审批策略仍为 first-time contributors；策略变化要更新本文件。
6. Private Vulnerability Reporting 已启用，安全报告不会被迫进入公开 Issue。

GitHub 设置属于远端可变事实，不能仅凭本文件宣称已经生效。当前读回状态应记入相应
项目更新；后续发现漂移时先区分是 GitHub 设置、工作流还是文档变化，再修复。

## 官方参考

- [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [Managing GitHub Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)
- [Approving workflow runs from public forks](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/approve-runs-from-forks)
