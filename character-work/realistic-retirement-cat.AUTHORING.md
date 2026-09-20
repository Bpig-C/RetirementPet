# 半写实退休猫角色工作区

> 返回 [角色工作区索引](README.md) · 阅读
> [通用角色库扩展指南](../docs/CHARACTER_LIBRARY_GUIDE.md) · 查看
> [生成与来源记录](realistic-retirement-cat.PROVENANCE.md) · 对照
> [canonical 包清单](../assets/petpack/README.md) · 跟踪
> [Blender MVP 与后续路线](../docs/FUTURE_WORK.md#r1-blend-01-更拟真退休小猫-blender-动画-mvp)

`realistic-retirement-cat/` 是可修改的 PetPack 源目录，不是运行时角色库。当前
`0.1.1` 是一个可靠的静态竖切版本：`core.idle`、`core.work` 和 `core.rest`
已接入同一张经过 Alpha 检查的透明母版。其他核心动作由运行时回退到本角色的
idle。

当前源目录只有 `petpack.json`、`body.png` 和 `legal/license.txt` 三个实体文件。
作者 manifest 中资产与 legal 项的 `byte_size: 0`、空 `sha256` 是构建占位值；
`build` 会按真实字节填入打包副本，作者不要手工维护这些摘要。

## 构建与验证

在项目根目录运行：

```powershell
$rpPackVersion = "0.2.0" # 先把 petpack.json 的 package.version 改为相同值
$rpPackOutput = ".\.release\author-packs\realistic-retirement-cat-$rpPackVersion.petpack"

.\.venv\Scripts\python.exe .\scripts\petpack_cli.py build `
  .\character-work\realistic-retirement-cat `
  $rpPackOutput

.\.venv\Scripts\python.exe .\scripts\petpack_cli.py validate `
  $rpPackOutput

.\.venv\Scripts\python.exe .\scripts\petpack_cli.py preflight `
  $rpPackOutput

.\.venv\Scripts\python.exe .\scripts\petpack_cli.py inspect `
  $rpPackOutput
```

仓库只保留 `assets/petpack/examples/` 下的一份 canonical `.petpack`；作者输出属于
可重建结果，不在 `character-work/` 重复保存。上面的 `0.2.0` 命令用于制作新版本；
修改任何包内内容后，必须同步提升 manifest 版本和输出文件名。只有逐字节复现冻结
的 0.1.0 已作为内部冻结事实保留，不再从当前工作区重建或覆盖。0.1.1 移除了图片
中的非公开 ancillary 元数据，但保持 IDAT 和解码 RGBA 逐字节一致；新版本应做两次
独立构建并互相逐字节比较，确认确定性后再发布为新的 canonical 文件。

每次修改已经安装过的包时，必须提升 `package.version`。同一个发布者、包 ID 和
版本对应的内容摘要是不可变事实；运行时会拒绝“同版本不同内容”。

1.1.1 尚未把 `layered`、Variant、专属动作调度、包内音效和 manifest geometry
接入完整作者流程。当前正文按整张 PNG 适配固定角色区域，透明边距和画布长宽比会
直接影响桌面显示大小。Blender 主工程、高分辨率纹理与草稿必须保存在本目录之外；
本目录的所有文件都会进入构建输入，不能混入 `.blend` 等 PetPack 不支持的格式。

## 下一轮素材替换顺序

1. 保留当前 `body.png` 作为身份参考，不再让它同时承担所有动作。
2. 以当前 body 作为身份参考，重新绘制或按比例排入统一 512×512 透明逻辑画布，
   不做非等比拉伸；为 idle、work、rest 分别制作独立姿态，并保持脚底锚点和角色
   比例一致。
3. 将动作从 `static` 改为 `sequence` 时，每帧至少 33 ms，桌面可见帧率不超过
   30 FPS。
4. 依次补 eat、exercise、meeting、music，再考虑点击和随机小动作。
5. 新增素材后同步声明 rights/source；图片中不要烘焙字幕或气泡。

生成图必须检查真实 Alpha 通道。视觉上看起来像棋盘格，不代表图片真的透明。
