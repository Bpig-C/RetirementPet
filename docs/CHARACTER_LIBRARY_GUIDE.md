# RetirementPet 角色库扩展指南

> 适用于 RetirementPet 1.1.1/1.2.0 和 PetPack 1.0（作者工具链在 1.2.0 补齐）
> 本文解释当前已经可用的角色创作、构建、导入和更新流程
> 相关入口包括[文档中心](README.md)、[PetPack 规范](PETPACK_SPEC_1_0.md)和
> [内容与权利政策](CONTENT_POLICY.md) · [角色工作区索引](../character-work/README.md)

## 先理解角色库的四层关系

一个 PetPack 对应一个系列。包内可以有一个或多个角色，每个角色再映射自己的
动作表现。Variant 已进入协议身份，但 1.1.1 还不能作为稳定的作者交付能力。

```text
PetPack
└─ Series
   ├─ Character A
   │  ├─ core.idle → Action → static 或 sequence → PNG
   │  └─ core.work → Action → static 或 sequence → PNG
   └─ Character B
      └─ core.idle → 这个角色自己的 PNG
```

不同角色共用的是 `core.*` 动作语义。引擎说“现在是工作状态”，每个角色决定自己
怎样表现工作。它们不会因为同样支持 `core.work` 就共用另一角色的身体图片。缺少
可选动作时，Context 仍保留，画面回到当前角色自己的 `core.idle`，引擎可以继续
显示通用文字或效果。

## 1.1.1 已实现范围

| 能力 | 当前状态 | 作者应怎样处理 |
|---|---|---|
| 一个包一个系列 | 可用 | 同一系列的角色放在同一包内 |
| 多角色 | 可用，单个本地包最多 16 个 | 每个角色必须有自己的 `core.idle` 绑定、可绘制首帧、缩略图引用和 geometry；当前 static/sequence 主体资产不能跨角色共享 |
| 多版本共存 | 可用 | 内容变化后提升 `package.version`，不要覆盖旧 Revision |
| 透明 PNG `static` | 可用 | 最稳妥的第一版路径 |
| 透明 PNG `sequence` | 可用 | 每帧至少 33 ms，可见帧率不超过 30 FPS |
| `core.idle/work/rest/eat/exercise/meeting/music` | 可用 | 显式映射需要的语义，至少提供 idle |
| 缺动作回退 | 可用 | 不必为首版一次做完所有动作 |
| 本地预检、安装和安全切换 | 可用 | 导入与激活通常分开执行 |
| `layered` | 规范已有，当前 Runtime 未交付 | 暂时不要用于 1.1.1 作者包 |
| Variant | 规范已有，当前端到端行为未冻结 | 暂时用独立 Character 或等 Runtime 完成 |
| 发布者专属动作 | 规范已有，当前正式调度未接入 | 可以先设计，不要把它当可运行功能 |
| 包内音效、TextProfile、推荐配置 | 尚无完整作者流程 | 素材先分开保存，不写成交付能力 |
| geometry 驱动窗口适配 | manifest 可声明，1.1.1 绘制尚未消费 | 当前仍要靠实际 PNG 画布和透明边距控制显示比例 |
| 普通用户卸载 UI | 未完成 | 先保留已安装 Revision，不手改 LibraryRoot |

## 创建一个新角色包

### 1. 建立作者工作区

1.2.0 起，推荐直接用 `init` 生成一个**可直接 build、validate、preflight 通过**
的最小原创静态包，再在其上替换素材与身份。它自带可用透明图、geometry、
idle、缩略图和来源许可声明，不依赖任何外部下载。

```powershell
Set-Location <project-root>
.venv\Scripts\python.exe scripts\petpack_cli.py init character-work\my-character-pack
```

模板使用占位身份 `community.example/my-pack/0.1.0`、角色 `demo`，目录如下。

```text
character-work/my-character-pack/
├─ petpack.json          # 完整 manifest（含 publisher_ref、compatibility、rights）
├─ assets/
│  ├─ idle.png           # 64×64 透明背景静态帧（可绘制的圆角形体）
│  └─ thumbnail.png      # 32×32 角色缩略图
└─ legal/
   └─ license.txt        # 双语原创许可声明
```

把 `assets/idle.png` 换成自己的图（同步改 manifest 里的像素声明）、改掉
publisher/package/series/character 身份与许可声明后，即可走第 6 节的完整链路。
若要制作写实风格角色，也可以继续复制
[半写实退休猫源目录](../character-work/realistic-retirement-cat/)作参考；它带有
已记录的 `package.publisher_ref` 规范漂移，不能当作规范模板，`lint` 会把这类
漂移报出来。

作者源目录只保存可编辑输入。构建出的 `.petpack` 放到 `.release/author-packs/` 或
其他临时输出目录；要随程序提供的 canonical 示例才进入 `assets/petpack/examples/`。
`build` 会收集源目录中除 `petpack.json` 外的每个文件；PSD、生成草稿、README 或
高分辨率母版若留在源目录又未声明，`lint` 会直接报错，`build` 会拒绝执行，不会
悄悄把它们丢在包外。此类作者资料应放在包源目录旁边，而不是包源目录里面。

### 2. 先固定身份

至少确定 `publisher_id`、`package.id`、`series.id` 和每个 `character.id`。这些
机器 ID 一旦分发就应保持稳定，显示名称可以修改。一个精确 Revision 由 PackKey、
SemVer 和 canonical content digest 共同确定。

同一发布者、包 ID 和版本出现不同内容时，角色库会拒绝安装。已经安装过 0.1.0，
哪怕只改了一张图，也应发布 0.1.1 或 0.2.0。

### 3. 准备透明素材

当前本地导入的硬边界包括 64 MiB 归档、96 MiB 解压内容、最多 16 个角色，以及
角色主体合计 24 百万解码像素。当前 preflight 会对缩略图和已绑定动作的 PNG 检查
可解码性与声明尺寸；绑定动作 renderer 的主体 PNG 还必须有真实 Alpha、透明背景
和透明四角。未引用 PNG 尚不在完整检查闭包内，作者仍应保证所有图片声明与实物
一致。不要把棋盘格画进图片，也不要把字幕、气泡、Logo、场景和阴影地面烘焙进
角色正文。

同一角色的动作应使用一致的物理画布、角色比例和脚底位置。1.1.1 仍按整张 PNG
适配窗口，所以大面积透明边距或 1024×1536 的狭长画布会让角色在桌面上显得偏小。
近期角色输出建议统一为 512×512；高分辨率母版可以另外保留在作者资料中。

### 4. 声明资产、来源和权利

每个媒体资产都需要唯一 ID、路径、媒体类型、byte size、SHA-256、`rights_ref`
和 `source_ref`；图片还需声明像素尺寸。来源元数据写入顶层 `sources`，许可或法律
纯文本通过 `legal_files` 声明；包外生成过程继续保留在 provenance 文档中。原创、
授权、开放许可证、公有领域和未知权利必须按真实情况区分。

作者源 `petpack.json` 可以把 `byte_size` 写成 `0`、`sha256` 写成空字符串。
`build` 会按实际文件填写打包副本中的大小和摘要，源 manifest 保持可编辑。不要
手工把旧摘要复制给新图片。

### 5. 映射动作语义

下面只展示一个静态动作的 renderer 核心片段，不能直接替代完整 action 条目；
`policy_tags`、`user_modes`、`loop_modes`、`interrupt`、`audio` 等完整字段以
[半写实 manifest](../character-work/realistic-retirement-cat/petpack.json)和
[PetPack 规范](PETPACK_SPEC_1_0.md)为准。

```json
{
  "id": "action.my-character.idle",
  "semantic": "core.idle",
  "lifecycle": {
    "loop": {
      "renderer": {
        "type": "static",
        "asset": "asset.my-character.idle"
      }
    }
  }
}
```

序列动作把 renderer 改为 `sequence`，每帧显式给出 asset 和 duration。

```json
{
  "type": "sequence",
  "frames": [
    {"asset": "asset.my-character.rest.000", "duration_ms": 160},
    {"asset": "asset.my-character.rest.001", "duration_ms": 160}
  ]
}
```

最后在角色的 `actions` 中把 `core.idle` 等语义绑定到动作 ID。`core.idle` 是硬要求；
其他动作可以逐步增加。首版缺少 eat 或 meeting 时会继续显示这个角色的 idle，
不会突然换回官方猫。

### 6. 编辑 → lint → build → validate → preflight → preview

完整命令链（PowerShell；每一步的含义见其后的说明）：

```powershell
Set-Location <project-root>

# 1) 源树静态检查：接触图、帧数/时长/解码预算报告；不写任何文件
.venv\Scripts\python.exe scripts\petpack_cli.py lint `
  character-work\my-character-pack

# 2) 确定性构建：相同源树得到逐字节相同的包
.venv\Scripts\python.exe scripts\petpack_cli.py build `
  character-work\my-character-pack `
  .release\author-packs\my-character-pack-0.1.0.petpack

# 3) 完整引擎校验（与 GUI 导入同源）
.venv\Scripts\python.exe scripts\petpack_cli.py validate `
  .release\author-packs\my-character-pack-0.1.0.petpack

# 4) GUI 导入同款预检门禁（PNG 可解码性、声明尺寸、Alpha、预算、idle 首帧）
.venv\Scripts\python.exe scripts\petpack_cli.py preflight `
  .release\author-packs\my-character-pack-0.1.0.petpack

# 5) 离屏预览：用真实 Runtime 逐帧渲染到 PNG 目录
.venv\Scripts\python.exe scripts\petpack_cli.py preview `
  .release\author-packs\my-character-pack-0.1.0.petpack `
  .release\author-packs\preview

# 6) 身份与计数速览
.venv\Scripts\python.exe scripts\petpack_cli.py inspect `
  .release\author-packs\my-character-pack-0.1.0.petpack
```

`lint` 在构建前把问题拦在源树上：输出每个角色每个语义一行的动作接触图
（`contact demo/core.idle static action=action.idle frames=1 … decoded~16 KiB`）、
全包解码工作集预算行，以及 `E:`（必须修）/`W:`（建议处理）两类 finding；
有 finding 时退出码为 1。它检查 schema、`publisher_ref` 存在且可解析、声明与
实物文件互相吻合（缺声明文件、多未声明文件都会报）、PNG 可解码且与声明像素
一致、主体 PNG 有透明度（缩略图豁免）、geometry 锚点/边界在逻辑画布内、
`core.idle` 绑定存在，以及帧数 ≤300、单帧 ≥33 ms、解码预算 ≤48 MiB。

`build` 是确定性的：成员按名称排序、固定时间戳与权限、canonical JSON、
DEFLATE 压缩，同一源树在任意输出位置得到逐字节相同的归档。源目录里存在未
声明文件时构建直接拒绝（退出码 2）。对 `assets/petpack/` 下的官方包与
canonical 示例冻结输出，构建一律拒绝覆盖。

`validate` 检查当前已实现的归档、结构、媒体、rights 和部分动作 gate；它尚未完整
覆盖 `compatibility`、`legal_files` 完整性、geometry、TextProfile、推荐项以及
全部 action 引用闭包。`preflight` 运行与 GUI 导入相同的已绑定 static/sequence
主体 PNG、Alpha、预算和每角色 idle 首帧检查，`inspect` 显示身份、动作数、资产数
和 content digest。四步成功是进入当前应用的必要检查，不等于已经证明 PetPack 1.0
全部条款 conformance。

`preview` 用与桌宠窗口完全相同的路径离屏绘制：manifest geometry 经
`compute_body_layout` 换算到 232×236 的桌宠视口（打印 scale、foot 锚点与是否
整体平移），再由 `PackCharacterRuntime.render_body` 按 `preview_schedule`
的真实帧时间取样绘制——静态、sequence 帧起点、循环接缝（t=total 回到首帧）
和未绑定语义的 idle 回退都会画出。每个样本写成
`<角色>-<语义>-<序号>.png`，并生成一张接触图 `contact-<角色>.png`：每个语义
一行、每个样本一列，行标注语义与帧数/总时长，列标注帧序号与帧时长，便于直接
比较脚底位置、角色大小和循环首尾。预览不改变活动角色、Context、用户配置或
用户库，也不发声。

接触图采样上限是 18 列：16 帧以内的 MVP 序列（Blender 首批允许 8–16 帧）
连同接缝完整采样，最后一帧不会被丢掉。更长的序列有界抽样，但始终保留
首帧、末帧和接缝；省略数量写在行标注独立的一行 `+N frames omitted`（保证
完整可读，不会被省略号截断），CLI 每行末尾另附
`(N frame(s) not sampled)`，不会把前 15 帧静默当成整个动作。行宽超过 8 列
自动换行成带 `(cont.)` 标注的续行；续行单元的列标注继续全局帧号
（第二带是 f8 起，而不是重新从 f0 开始），接缝带保留 `seam <总时长>`。

接触图的文字对环境有硬要求：绘制前会逐码位验证所选字体真的包含这些字形
（含对标准系统字体目录的增量加载——离屏/无头平台可能以空字体库启动）；
环境里没有任何字体能覆盖标签字形时，preview **拒绝生成接触图**并以退出码 1
结束（输出 `REJECT: no installed font renders the contact-sheet caption
glyphs …`），绝不会写出一张不可读的图再声称成功。文字全部限定在各自的
省略+裁剪条带内，不会压到样本图上。

### 7. 导入和启用

打开控制面板的“角色”页，选择“导入本地角色包…”。确认页应显示本地内容、
发布者未验证、权利自述未验证、能力与警告代码。普通导入只执行 INSTALL，不会
自动改变桌面角色；随后在角色列表中手动选择并 ACTIVATE。

切换时，新 Runtime 会先完成离屏 idle 首帧，再原子提交 ActiveSelection。失败会
保留旧角色、恢复持久化权威，或在权威无法证明时进入 Bootstrap 安全模式。不要
绕过界面直接改用户数据目录中的 catalog、revision 或 active selection。

### 8. archive hash 与 content digest：什么时候需要新 Revision

一个包有两个不同层面的哈希，混用会导致错误的版本决策：

| 哈希 | 覆盖范围 | 何时变化 |
|---|---|---|
| `archive_sha256` | 整个 ZIP 容器的字节（封装框架 + 压缩方式） | 任何导致归档字节不同的重建，包括单纯重压缩 |
| `content_digest` | canonical manifest + 各资产原始字节 | 仅当 manifest 内容或资产内容实际变化 |

**只有 `content_digest` 变化才需要新 Revision。** 改了一张图、改了 manifest 里
的任何声明 → digest 变化 → 必须提升 `package.version` 发布新 Revision。仅仅
重新压缩、重打包（内容不变）→ 只有 archive hash 变化 → 不需要新 Revision，
已安装的旧 pin 继续有效。

1.2.0 修复了构建器的一个容器层缺陷：此前归档头部声明 DEFLATE，但每个成员实际
以 STORED（未压缩）写入。修复后成员真正按 DEFLATE 压缩。对同样内容重新构建会
得到不同的 archive hash，但 content digest 逐字节不变——这正是两个哈希分离的
意义。不要为了让新构建的哈希对上旧记录而改动旧 pin；旧 pin 指向的是
content digest，与容器封装无关。

## 常见错误排查

按命令链从早到晚排列；`lint` 的 `E:` 必须修复，`W:` 建议处理。

| 输出（节选） | 原因与处理 |
|---|---|
| `lint E: undeclared files would be silently left out …: scratch-notes.txt` | 源目录里有 manifest 未声明的文件。要么加入 `assets`/`legal_files` 声明，要么移出源目录 |
| `lint E: declared file is missing from the source tree: assets/idle.png` | manifest 声明了不存在的文件。补文件或删除声明 |
| `lint E: package.publisher_ref is required (PETPACK_SPEC 6.1)` | 补 `package.publisher_ref`，并在 `publishers[]` 声明该发布者 |
| `lint E: publisher_ref '…' does not resolve to any publishers[] entry` | `publisher_ref` 必须等于 `publishers[]` 中某项的 `id` |
| `lint W: declared byte_size/sha256 … is stale; build refreshes it` | 手填的摘要与实物不符。可忽略（build 会按实际文件填写），或删掉手填值保持源 manifest 可编辑 |
| `lint E: asset … is 128x128 but declared 64x64` | PNG 实际像素与声明不符。改 `properties` 或换图 |
| `lint W: asset … has no transparency; it will draw as an opaque rectangle` | 主体 PNG 缺少真实 Alpha 透明背景（角色缩略图豁免此检查） |
| `lint E: character '…' has no core.idle binding` | 每个角色必须绑定 `core.idle`，运行时不会为它另找回退 |
| `lint E: character '…' base_anchor is outside the logical canvas`（含 bounds/reference_height） | geometry 锚点或边界超出逻辑画布，修正 `geometry` 数值 |
| `lint E: action … has 320 frames (max 300)` / `has a 16ms frame (min 33ms)` | 超出动作预算：帧数 ≤300，单帧时长 ≥33 ms（可见帧率 ≤30 FPS） |
| `lint E: estimated decoded working set … exceeds the 48 MiB runtime budget` | 全包解码像素超预算。缩小画布或删帧 |
| `error: undeclared files present in the source tree …`（build，退出码 2） | 与 lint 同因；build 不允许静默丢文件 |
| `error: refusing to overwrite frozen official release media` | 输出路径命中 `assets/petpack/` 冻结输出。换一个输出文件名 |
| `refusing to overwrite existing template files`（init，退出码 1） | 目标目录已有模板文件。换目录或清理后重试 |
| `validate` 输出 `result REJECT` + 诊断行 | 看 `code [phase] message_key`，对照[PetPack 规范](PETPACK_SPEC_1_0.md)；修复源树后重新 build |
| `preflight REJECT: …` | 与 GUI 导入同一门禁：PNG 不可解码、声明尺寸不符、主体无 Alpha 或 idle 首帧不可绘制都会在此拒绝 |
| `preview REJECT: pack did not validate` | 先跑 `validate` 修完再预览 |
| `preview REJECT: no installed font renders the contact-sheet caption glyphs …` | 环境里没有任何字体能覆盖接触图标签所需字形（离屏平台连系统字体都没加载时也可能出现，工具已尝试加载标准系统字体目录）。装一个含拉丁字形的常规字体后重试；工具不会在缺字体时写出不可读的接触图 |
| `preview` 某行标注 `(+N omitted)` / `(N frame(s) not sampled)` | 该动作帧数超过 18 列采样上限，做了有界抽样：首帧、末帧和接缝一定保留，中间省略 N 帧；需要逐帧查看就拆分动作或提高 `MAX_PREVIEW_COLUMNS` |
| 安装时提示同版本内容冲突 | 同一 publisher/package/version 的不同内容不允许覆盖安装；提升 `package.version` 后重新构建 |

## 当前半写实退休猫有哪些资产

0.1.1 是一个画风和显示比例竖切，不是一套完整动作美术。它相对 0.1.0 只移除了
PNG 中不属于公开 allowlist 的 ancillary 元数据；IDAT 与解码后的 RGBA/Alpha 均
逐字节一致。0.1.0 作为内部冻结旧事实保留，不进入公开快照。

| 资产 | 当前事实 |
|---|---|
| 作者 `petpack.json` | 5,231 B；SHA-256 `23939684cde962dd10fd952facdae742bedc489b4a6b813538226e4a8a6f8671` |
| `body.png` | 1024×1536 RGBA，2,189,841 B，SHA-256 `1a6351e3aaaae76d0d393c2debea9e5a35751ac355f1346edc55fadb4fb2d6bf` |
| `license.txt` | 434 B，AI 辅助原创声明，SHA-256 `a72df04e3c02b9798140b43c59c62357db73ff1ef72c15baf18ae359aad7cea6` |
| canonical `.petpack` | 2,195,269 B，archive SHA-256 `ce80e7dd73726cdbb252f21d65538014fe10790dce7a44816ebb80bc26c2a964` |
| content digest | `6c4b368ad79124bf5bc48e6d8190917c7031f923b2cf00f728c4c17b9c21260c` |

包身份是 `community.retirementpet/realistic-retirement-cat/0.1.1`，系列是
`retirement-cat`，角色是 `realistic-cat`。它以 `LOCAL_IMPORTED` 身份运行，
即使文件随 EXE 分发也不会升格为官方内容。

`body.png` 同时充当缩略图、idle、work 和 rest。三个动作都是 `static`，没有
独立姿态。包内没有序列帧、音频、variant、rig、TextProfile 或推荐配置。
eat、exercise、meeting 和 music 会回退到本角色 idle。manifest 声明的可见边界
是 `x=95, y=32, width=926, height=1502`，脚底锚是 `(512,1533)`；这些几何值尚未
进入 1.1.1 的实际缩放路径。Runtime 会把整张 2 比 3 图片放进约 232×236 的区域，
整张位图的逻辑目标框约为 157×236，再扣除图片自身的透明边缘，因此桌面上会
显得偏细、左右留白较多。

canonical 归档中只有三项，分别是构建后 `petpack.json`、同一张 `body.png` 和
同一份 `license.txt`。构建后 manifest 为 4,606 B，SHA-256 是
`0f341024cb0bc24e25016fbd944a5c35b7fc6292976e440f9c7a782bc019d44a`。
当前 ZIP 三个成员均未压缩。用户看到的 Zzz、音符、字幕和气泡来自 Engine，均不
属于这个角色包的资产。

完整生成与像素验收见
[PROVENANCE](../character-work/realistic-retirement-cat.PROVENANCE.md)，下一轮素材
顺序见[作者工作区说明](../character-work/realistic-retirement-cat.AUTHORING.md)。

## 角色内容的边界

原创或有明确授权的内容才能进入官方包。Harry Potter、罗小黑等已有 IP 如果没有
覆盖相应用途的许可，项目不得内置、代下载、代打包或再分发。用户主动本地导入时，
仍需自行确认许可范围允许其使用方式；本项目的“本地内容”提示不构成授权判断。
