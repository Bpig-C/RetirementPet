# RetirementPet 素材规格（v1）

> 历史素材提示：本文只描述 v1 旧 `assets/manifest.json` 管线。它不包含外部
> PetPack 所需的身份、版本、安全验证、权利声明和资源预算。当前角色制作请读
> [角色库扩展指南](CHARACTER_LIBRARY_GUIDE.md)，协议以
> [PETPACK_SPEC_1_0.md](PETPACK_SPEC_1_0.md)为准。返回[文档中心](README.md)。

在 v1 阶段，美术素材按本规格直接放入 `assets/`。旧渲染器通过
`assets/manifest.json` 自动加载；任何素材缺失都会回退到程序化猫咪，
这不再是 1.1.1 新角色的推荐扩展路径。

## 画布与锚点

- 统一设计画布：**256 × 256 px**，透明背景
- 每个部件 PNG 使用完整 256×256 画布，内容居中绘制
- 逻辑锚点（脚底中心）：**(128, 236)**
- 命名：部件用小写英文；序列帧目录内用 `000.png`、`001.png` 三位数字递增

## 部件清单（`sprites/common/`）

| 文件 | 内容 | 说明 |
|---|---|---|
| `shadow.png` | 地面阴影 | 半透明椭圆 |
| `tail.png` | 尾巴 | 根部在右下，渲染器绕脚底摆动 |
| `body.png` | 身体（坐姿） | 渲染器做呼吸缩放 |
| `head.png` | 头部（不含表情） | 含耳朵；渲染器做轻微浮动 |
| `face_open.png` | 睁眼表情 | 眼睛视线由渲染器叠加偏移 |
| `face_closed.png` | 闭眼表情 | 眨眼/休息用 |
| `laptop.png` | 电脑 | 工作附件 |
| `headset.png` | 耳麦 | 会议/音乐附件 |
| `bowl.png` | 饭碗 | 干饭附件 |
| `dumbbell.png` | 哑铃 | 健身附件 |
| `zzz.png` `note.png` `sweat.png` | 特效 | 休息/听歌/健身粒子 |

`required_parts`（body、head、face_open）齐备时才启用部件模式，
否则整体回退程序化绘制。附件缺失只跳过该附件。

## 序列帧动作

| 目录 | 帧数建议 | FPS | 循环 |
|---|---|---|---|
| `sprites/eat/` | 4 | 6 | 是 |
| `sprites/exercise/` | 4 | 6 | 是 |
| `sprites/stretch/` | 4 | 6 | 是 |
| `sprites/lick_paw/` | 2 | 4 | 是 |
| `sprites/look_around/` | 3 | 5 | 是 |
| `sprites/yawn/` | 3 | 5 | 否 |
| `sprites/interact/` | 2 | 8 | 是 |

序列帧以整猫为画面（含身体表情），缺帧自动跳过，全缺则回退。

## manifest 结构

```json
{
  "canvas": { "width": 256, "height": 256 },
  "parts": { "body": { "path": "sprites/common/body.png" }, ... },
  "required_parts": ["body", "head", "face_open"],
  "actions": {
    "work": { "mode": "parts", "attachments": ["laptop"] },
    "eat": { "mode": "sequence", "frames": ["sprites/eat/000.png"], "fps": 6 }
  },
  "effects": { "zzz": "sprites/common/zzz.png" }
}
```

- `mode: parts`：部件 + 附件组合，享受呼吸/摆尾/视线程序动画
- `mode: sequence`：整帧序列，按 `fps` 播放，`loop: false` 到末帧停住

## 分发时的位置

- 源码运行：`<project-root>\assets\`
- 打包后：`dist\RetirementPet\_internal\assets\`（onedir 整体拷贝即可）

## 图标

`assets/icons/retirement_pet.ico`（当前为程序化生成，可直接用正式图替换，
文件名不变即可）。
