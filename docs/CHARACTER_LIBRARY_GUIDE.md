# RetirementPet 角色库扩展指南

> 适用于 RetirementPet 1.1.1 和 PetPack 1.0
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

当前最可靠的做法是复制
[半写实退休猫源目录](../character-work/realistic-retirement-cat/)，再整体更换身份、
来源和素材。它是最容易通过 1.1.1 Runtime 的起点，但仍带有已记录的
`package.publisher_ref` 规范漂移，不能当作最终规范模板。也可以运行下面的命令
建立骨架。

```powershell
Set-Location <project-root>
.venv\Scripts\python.exe scripts\petpack_cli.py init character-work\my-character-pack
```

`init` 目前只生成最小目录和不完整 manifest 骨架，不能直接通过 `validate`。作者
仍需参考[半写实猫 manifest](../character-work/realistic-retirement-cat/petpack.json)
补齐 `package.publisher_ref`、`compatibility`、`publishers`、
`rights_declarations`、`sources`、`legal_files`、`assets`、`actions`、
`characters` 和 geometry。

推荐目录如下。

```text
character-work/my-character-pack/
├─ petpack.json
├─ assets/
│  └─ characters/my-character/
│     ├─ idle.png
│     ├─ work.png
│     └─ rest/
│        ├─ 000.png
│        └─ 001.png
└─ legal/
   └─ license.txt
```

作者源目录只保存可编辑输入。构建出的 `.petpack` 放到 `.release/author-packs/` 或
其他临时输出目录；要随程序提供的 canonical 示例才进入 `assets/petpack/examples/`。
`build` 会收集源目录中除 `petpack.json` 外的每个文件；PSD、生成草稿、README 或
高分辨率母版若留在源目录又未声明，会作为未声明成员被 validator 拒绝。此类作者
资料应放在包源目录旁边，而不是包源目录里面。

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

### 6. 构建并做三层检查

```powershell
Set-Location <project-root>

.venv\Scripts\python.exe scripts\petpack_cli.py build `
  character-work\my-character-pack `
  .release\author-packs\my-character-pack-0.1.0.petpack

.venv\Scripts\python.exe scripts\petpack_cli.py validate `
  .release\author-packs\my-character-pack-0.1.0.petpack

.venv\Scripts\python.exe scripts\petpack_cli.py preflight `
  .release\author-packs\my-character-pack-0.1.0.petpack

.venv\Scripts\python.exe scripts\petpack_cli.py inspect `
  .release\author-packs\my-character-pack-0.1.0.petpack
```

`validate` 检查当前已实现的归档、结构、媒体、rights 和部分动作 gate；它尚未完整
覆盖 `compatibility`、`legal_files` 完整性、geometry、TextProfile、推荐项以及
全部 action 引用闭包。`preflight` 运行与 GUI 导入相同的已绑定 static/sequence
主体 PNG、Alpha、预算和每角色 idle 首帧检查，`inspect` 显示身份、动作数、资产数
和 content digest。四步成功是进入当前应用的必要检查，不等于已经证明 PetPack 1.0
全部条款 conformance。

### 7. 导入和启用

打开控制面板的“角色”页，选择“导入本地角色包…”。确认页应显示本地内容、
发布者未验证、权利自述未验证、能力与警告代码。普通导入只执行 INSTALL，不会
自动改变桌面角色；随后在角色列表中手动选择并 ACTIVATE。

切换时，新 Runtime 会先完成离屏 idle 首帧，再原子提交 ActiveSelection。失败会
保留旧角色、恢复持久化权威，或在权威无法证明时进入 Bootstrap 安全模式。不要
绕过界面直接改用户数据目录中的 catalog、revision 或 active selection。

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
