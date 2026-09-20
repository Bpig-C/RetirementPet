# RetirementPet 角色作者工作区

> 返回 [文档中心](../docs/README.md) · 阅读
> [角色库扩展指南](../docs/CHARACTER_LIBRARY_GUIDE.md) · 查看
> [PetPack 分发目录](../assets/petpack/README.md)

这里保存角色包的可编辑输入，不是用户运行时 LibraryRoot，也不保存重复的作者构建
输出。一个工作区通常包含源 `petpack.json`、透明素材和 legal 文件；作者说明与
来源记录放在工作区旁边，便于独立审阅。

## 当前工作区

| 工作区 | 版本 | 状态 | 相关记录 |
|---|---:|---|---|
| [半写实退休猫](realistic-retirement-cat/) | 0.1.1 | 静态竖切，可构建与预检 | [作者说明](realistic-retirement-cat.AUTHORING.md) · [生成与来源](realistic-retirement-cat.PROVENANCE.md) |
| [真实幼猫](realistic-kitten-0.1.1/) | 0.1.1 | 八场景透明静态图，已修复浅色毛发缺损 | [修复与来源](realistic-kitten-0.1.1/PROVENANCE.md) |

半写实猫的 canonical 构建位于
[assets/petpack/examples](../assets/petpack/README.md)。工作区内任一已分发内容发生
变化时必须提升包版本；同版本不同 digest 会被角色库拒绝。

新建工作区前先阅读角色库指南。当前 `init` 命令只生成骨架，最稳妥的起点仍是
复制现有半写实猫目录，再更换全部身份、来源、权利和资产声明。它能通过当前
1.1.1 Runtime，但带有已记录的 `package.publisher_ref` 规范漂移，不能当作最终
规范模板。
