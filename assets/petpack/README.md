# RetirementPet 随程序提供的 PetPack

> 返回 [文档中心](../../docs/README.md) · 阅读
> [角色库扩展指南](../../docs/CHARACTER_LIBRARY_GUIDE.md) · 查看
> [角色作者工作区](../../character-work/README.md)

本目录保存随构建分发的不可变 PetPack。`.petpack` 是构建结果，不在这里直接修改；
可编辑素材和 manifest 应放在作者工作区。

## 当前包

| 文件 | 信任通道 | 用途 |
|---|---|---|
| `retirement-cat-official-1.0.1.petpack` | `BUILTIN_OFFICIAL` | 当前官方安全退休猫 |
| `retirement-cat-official.petpack` | `BUILTIN_OFFICIAL` | 冻结的旧官方 Revision，用于精确迁移与恢复 |
| `examples/realistic-retirement-cat-0.1.1.petpack` | `LOCAL_IMPORTED` | 随附半写实画风预览与作者参考 |

半写实包虽然随 EXE 分发，仍按本地内容处理。它的发布者身份与权利自述不会因为
文件位置而自动受信；首次一键入口也必须先做固定摘要和隔离预检。

官方旧包是不得修改或删除的冻结发布基线；作者构建工具拒绝覆盖，测试和启动注册会
检测缺失或摘要漂移。制作示例时先在 `.release/author-packs/` 构建，完成
`validate`、`preflight` 和来源审查后，再决定是否增加本目录的 canonical 示例。
修改已经分发的 PackKey 时必须提升版本；全新 PackKey 可以从 0.1.0 起步。

半写实 0.1.1 canonical 被 `BUNDLED_PREVIEW_*` 常量、测试、smoke gate 和发布
artifact 钉扎。0.1.0 是含原始生成器 ancillary chunk 的冻结旧事实，只在内部仓库
保留，并由公开快照配置精确排除。后续替换仍须使用新版本和新文件名，同步更新 pin、
测试与发布证据，不能覆盖任何既有 Revision。
