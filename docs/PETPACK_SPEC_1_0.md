# PetPack 1.0 规范

> 文档关系：[文档中心](README.md) · [当前作者指南](CHARACTER_LIBRARY_GUIDE.md) ·
> [内容政策](CONTENT_POLICY.md) · [一致性验收](CONFORMANCE.md)
>
> 规范版本：1.0 design baseline  
> 状态：结构与安全边界 FROZEN；标为 PROVISIONAL 的资源数值待实测冻结  
> 适用引擎：RetirementPet v2  
> 安全模型：所有 PetPack 均为不可信输入  
> 规范词：MUST、MUST NOT、SHOULD、MAY 采用 docs/README.md 中定义

> 实现边界：本文描述完整协议目标。RetirementPet 1.1.1 本地作者流程只承诺透明
> PNG 的 `static/sequence`、七个 core 语义、离线预检、不可变安装和安全切换。
> `layered`、Variant 生效、专属动作调度、包内音效和 geometry 适配仍见
> [未来工作](FUTURE_WORK.md)，不能因本规范存在就写成当前功能。

## 1. 目标

PetPack 是 RetirementPet 的角色分发单元。它必须支持：

- 一个系列中的多个角色和变体；
- 多角色共享核心动作语义；
- 角色专属动作；
- 静态、序列帧和轻量分层动画；
- 文案、行为和布局建议；
- 每项素材的来源与权利声明；
- 安全导入、资源预算、版本共存和确定性诊断。

PetPack 不是插件、脚本、网页、安装程序或在线资源清单。

## 2. 安装单元与容器

### 2.1 文件形式

- 扩展名 MUST 为 .petpack；
- 容器 MUST 使用本规范限制过的 ZIP profile；
- 归档根目录 MUST 恰有一个 petpack.json；
- petpack.json MUST 为 UTF-8 严格 JSON；
- 包 MUST 自包含；
- 包 MUST NOT 包含跨包依赖或远程资源。

归档示例：

    petpack.json
    assets/
      characters/
        demo/
          thumbnail.png
          idle/
            001.png
            002.png
    legal/
      license.txt

源工程、PSD、绘图工程、构建报告和预览缓存 SHOULD NOT 进入最终 .petpack。

### 2.2 受限 ZIP profile

导入器 MUST 在解压前检查 central directory，并逐项流式解压。禁止直接调用无约束 extractall。

PetPack 1.0 的成员名规范化算法：

1. ZIP general-purpose UTF-8 flag MUST 设置，原始成员名字节 MUST 严格解码为 UTF-8；
2. 分隔符 MUST 已经是 /，不得先把反斜杠静默改成斜杠；
3. 路径不得以 / 开头，不得含空 segment、.、..、NUL、控制字符或冒号；
4. 每个 segment MUST 已处于 Unicode NFC；V1 不接受需要转换后才安全的名字；
5. Windows 碰撞比较使用 CompareStringOrdinal 的 ignoreCase 模式；任意两个成员比较相等即拒绝；
6. 任意 segment 去除尾随点或空格后发生变化即拒绝；
7. segment 第一个点之前的 basename 按 ASCII 大写后，若为 CON、PRN、AUX、NUL、
   COM1–COM9 或 LPT1–LPT9，即使带扩展名也拒绝；
8. create-new 前后检查当前 transaction staging root containment 与 no-reparse；
   最终 publish 或 delete 前再检查 managed-root containment 与 no-reparse。

规范实现和 fixture 必须冻结原始 ZIP 名称字节，不能只测试解码后的语言字符串。

成员路径 MUST：

- 使用正斜杠；
- 是规范化相对路径；
- 在 Windows 大小写不敏感和 Unicode 规范化后唯一；
- 在 canonical resolve 后仍位于本次 staging root；
- 以 create-new 方式创建，不覆盖已存在文件。

导入器 MUST 拒绝：

- 绝对路径、盘符路径和 UNC；
- 点点段、混合分隔符逃逸；
- NTFS alternate data stream 冒号；
- NUL、尾随点、尾随空格和 Windows 保留名；
- 重复 ZIP entry；
- 大小写或 NFC / NFD 规范化冲突；
- symlink、hardlink、junction 和 reparse；
- 加密、多卷、未知压缩算法和嵌套归档；
- 超过路径、文件数、单文件、实际输出或压缩比预算的内容。

central directory 中的 size、CRC 和类型声明均不可信。实际输出字节必须持续限流。

## 3. 版本轴与兼容

PetPack 不能只依赖一个版本号。至少有：

| 版本轴 | 作用 |
|---|---|
| schema_version | petpack.json 的结构 |
| engine_min / engine_max_exclusive | Engine API 兼容范围 |
| required_capabilities | 正确运行必须具备的能力 |
| optional_capabilities | 不支持时可以确定性降级的能力 |
| package.version | 发布者声明的 SemVer |
| content_digest | 安装器计算的精确内容摘要 |

规则：

- 未知 schema 主版本 MUST 拒绝；
- 同一主版本中的新次版本只有在 required_capabilities 全部支持、且不存在未知 core property 时 MAY 接受；
- 未知 required capability MUST 拒绝对应角色或包；
- 未知 optional capability MUST 产生明确降级诊断；
- 未知 renderer MUST NOT 猜测为 sequence 或 layered；
- core 字段默认禁止未知属性；
- 发布者扩展只能位于 extensions 下，并使用发布者命名空间；
- 未知字段只有位于 extensions.<publisher-namespace> 且声明为 optional/inert 时才可忽略；
- 未知 extension 不得拥有文件、URL、handler、配置所有权或任何执行语义；
- 新 Engine 读取旧包时必须按旧版本定义补默认值，不得按最新含义猜测；
- 旧应用打开更高 state database schema 时必须拒绝写入。

可能结果只有：

    ACCEPT
    ACCEPT_WITH_DEGRADATION
    REJECT_WITH_STABLE_ERROR

## 4. 身份与命名空间

### 4.1 身份组成

    PackKey = (publisher_id, package_id)
    SeriesFQID = publisher.package.series
    CharacterFQID = publisher.package.series.character
    VariantKey = (CharacterFQID, variant_id)
    RevisionKey = (PackKey, package.version, content_digest)

display_name 是本地化展示字段，不参与身份。

PackKey、VariantKey 和 RevisionKey 是结构化 tuple，不得用无分隔字符串拼接。

content_digest 的 PetPack 1.0 唯一算法为：

    SHA-256(
      UTF8("RetirementPet-PetPackTree-v1") || NUL
      || raw_32_bytes(manifest_sha256)
      || for each declared non-manifest file,
           sorted by normalized path UTF-8 bytes:
           path_utf8 || NUL || uint64_be(byte_size) || raw_32_bytes(file_sha256)
    )

petpack.json 的任一字节变化，包括 source 或 rights 变化，都会改变 content_digest。
archive_sha256 仅证明导入容器字节；它不参与 RevisionKey。安装后按实际文件重新计算同一树摘要，
必须等于 content_digest。

### 4.2 ID 约束

- publisher_id SHOULD 使用反向域名风格；
- package、series、character、variant 和本地资源 ID MUST 使用小写 ASCII；
- segment MUST 以字母或数字开头，只允许字母、数字、下划线和短横线；
- 完整 ID 长度和 segment 数量 MUST 受限；
- official 与 engine 保留命名空间 MUST NOT 被外部包声明；
- 角色专属动作 MUST 以 CharacterFQID 为前缀；
- core.* 只由 Engine 定义。

同一 PackKey 和 package.version：

- 相同 content_digest：幂等，视为同一 Revision；archive 字节可以不同；
- 不同 content_digest：v2.0 MUST 以 PPK-LCY-E007 拒绝，要求提升版本或更换 package ID；
- 更高版本：不证明发布者身份相同；
- 无可信签名时，升级必须由用户明确确认。

### 4.3 受信安装上下文

TrustChannel 由 Engine 根据分发入口赋值，不属于 petpack.json，包不能声明或提升：

- BUILTIN_OFFICIAL：只读随应用发行的内容，结合发行侧权利证据注册表验证；
- LOCAL_IMPORTED：用户本地导入，一律视为发布者 UNVERIFIED；
- COMMUNITY_ONLINE：v2.0 Runtime 中不存在。

publisher_id、display_name、rights 自述或文件名包含 official 都不能提升 TrustChannel。
外部包冒用 official 或 engine 保留命名空间 MUST 以 PPK-MAN-E009 拒绝。

## 5. petpack.json 顶层结构

下面是规范骨架。字段内容在后续章节定义：

    {
      "schema_version": "1.0",
      "package": {
        "publisher_id": "community.example",
        "id": "sample-pack",
        "version": "1.0.0",
        "display_name": {"zh-CN": "示例角色包"},
        "publisher_ref": "publisher.main"
      },
      "compatibility": {
        "engine_min": "2.0.0",
        "engine_max_exclusive": "3.0.0",
        "required_capabilities": ["renderer.sequence.v1"],
        "optional_capabilities": ["asset.webp.v1"]
      },
      "publishers": [],
      "series": {},
      "rights_declarations": [],
      "sources": [],
      "legal_files": [],
      "assets": [],
      "rig_contracts": [],
      "actions": [],
      "text_profiles": [],
      "characters": [],
      "recommendations": {},
      "content_warnings": [],
      "extensions": {}
    }

根 manifest 不自列自己的 hash。manifest hash、archive hash 和 content_digest 由安装器计算并写入 receipt。

所有来自包的可见字符串，包括 display_name、description、publisher、attribution、
content warning、legal text 和诊断参数，MUST 以显式 PlainText 模式渲染，应用长度与控制字符限制，
不得依赖 Qt AutoText 或自动创建链接。

## 6. Package、Publisher 与 Series

### 6.1 Package

package 必须包含：

- publisher_id；
- id；
- version；
- 至少一个 display_name；
- publisher_ref。

包版本使用 SemVer。构建元数据不能用于规避“同版本不同内容”的冲突；正常内容变化应提升版本。

### 6.2 Publisher

publisher 记录是声明，不是认证：

    {
      "id": "publisher.main",
      "display_name": "作者声明名称",
      "contact": null,
      "homepage": null
    }

homepage 仅作为元数据展示。Runtime MUST NOT 自动探测、抓取或预览该 URL。
homepage 与 source.locator 是 inert metadata，不是资源定位器。任何 asset、renderer、action、
audio、layout 或模板中的 URL、UNC 或外部 locator 均拒绝。未来若允许用户主动在外部浏览器打开
metadata URL，必须有独立确认，只允许无 userinfo 的 https，并且仍保持默认零自动 DNS 和网络请求。

### 6.3 Series

PetPack 1.0 一个包 MUST 恰有一个 Series：

    {
      "id": "sample-series",
      "display_name": {"zh-CN": "示例系列"},
      "description": {"zh-CN": "由包作者声明的简介"}
    }

Series 是包内 UI 分组，不形成跨包继承。两个包可以拥有相同 display name。

## 7. Asset inventory

除 petpack.json 外，每个归档文件 MUST 被 assets 或 legal_files 精确声明。未声明文件和缺失文件均拒绝。

媒体资产示例：

    {
      "id": "demo.idle.001",
      "path": "assets/characters/demo/idle/001.png",
      "media_type": "image/png",
      "byte_size": 12345,
      "sha256": "64-lowercase-hex",
      "rights_ref": "rights.original",
      "source_ref": "source.original",
      "properties": {
        "width": 512,
        "height": 512
      }
    }

验证器 MUST：

- 复算 byte_size 和 sha256；
- 核对扩展名、magic、MIME、声明类型和实际解码结果；
- 验证图片像素、动画总解码量和音频属性；
- 禁止资源路径离开包根；
- 禁止把 source URL 当成资源定位器。

### 7.1 Legal file inventory

legal_files 每项 MUST 包含：

    {
      "id": "legal.license.main",
      "path": "legal/license.txt",
      "media_type": "text/plain",
      "byte_size": 1234,
      "sha256": "64-lowercase-hex",
      "purpose": "license_text"
    }

只允许 UTF-8 纯文本；同样受路径、单文件、字符串与总量预算约束。程序不得自动识别或打开其中链接。
rights.license.legal_file_ref 只能引用本表。未声明、缺失、MIME 不符或 hash 不符均拒绝。

### 7.2 格式

PetPack 1.0 core：

- Engine MUST 支持 image/png；
- image/webp 只有在声明且支持 asset.webp.v1 时 MAY 使用；
- 角色短音效 MAY 使用 Engine 白名单中的 WAV 或 OGG；
- 用户长音乐不进入 PetPack，由独立音乐库管理。

MUST NOT 包含：

- SVG；
- 字体；
- 视频；
- HTML；
- QML；
- 可执行文件；
- 播放列表；
- 未知二进制。

格式白名单的具体可选项为 PROVISIONAL；缩小白名单不改变纯数据原则。

## 8. Rights 与 Source

provenance 回答“素材从哪里来”，rights basis 回答“依据何种权利使用”，两者不能合并。

rights declaration 示例：

    {
      "id": "rights.original",
      "basis": "original",
      "claimant_ref": "publisher.main",
      "license": {
        "spdx": null,
        "legal_file_ref": null,
        "custom_name": "All rights reserved"
      },
      "scope_claimed": ["personal_use", "redistribution"],
      "attribution": "作者声明的署名文本",
      "notes": null
    }

允许的 basis：

- original；
- licensed；
- open_license；
- public_domain；
- permission_claimed；
- unknown。

source 示例：

    {
      "id": "source.original",
      "kind": "original_creation",
      "creator": "publisher.main",
      "title": "Original character art",
      "locator": null,
      "accessed_at": null
    }

规则：

- 每个媒体资产 MUST 有 rights_ref 和 source_ref；
- 混合来源必须逐资产声明；
- user_provided 可以是 source kind，不能作为 rights basis；
- hash、schema 和签名不能证明版权授权；
- rights unknown 对本地导入产生 WARNING；
- BUILTIN_OFFICIAL release validation 中存在 unknown 或发行侧证据注册表未核查授权时产生 ERROR；
- 升级改变 rights、license、scope 或 attribution 时必须显示 diff，并保留旧 receipt 快照。
- licensed 与 open_license MUST 提供 SPDX 标识或 legal_file_ref；
- permission_claimed MUST 提供 claimant_ref 和可发现说明，但仍标记 self-declared/unverified；
- unknown 不得同时声称 verified 或 official_distribution；
- scope_claimed 使用冻结枚举：personal_use、modification、redistribution、commercial_use、official_distribution；
- official 的合同或敏感证明保存在发行侧证据注册表，不把原件塞入 PetPack。

## 9. Rig contract

rig_contract 只用于同包内、显式兼容角色共享原始动作资产：

    {
      "id": "rig.humanoid.chibi.v1",
      "logical_canvas": {"width": 512, "height": 512},
      "required_anchors": ["base", "head", "left_hand", "right_hand"],
      "slots": [
        {"id": "body", "media_kind": "image", "required": true}
      ],
      "transform_profile": "layered.v1"
    }

规则：

- 共享动作的每个角色必须显式引用同一个 rig_contract；
- 相同 Series 不自动意味着 rig 兼容；
- 不允许跨包 rig 依赖；
- 引擎不根据角色外观猜测骨架；
- 角色可以不使用 rig_contract。
- 使用 rig_contract 的 Character MUST 以 rig_bindings 将每个 required slot 绑定到自己的资产；
- 跨 Character 共享仅允许参数化 layered motion template 和标记为 body_independent 的效果资产；
- static/sequence 主体帧不得被多个 Character 绑定；共享模板不得直接引用某一角色的 body 资产。

## 10. Character 与 Variant

角色示例：

    {
      "id": "demo",
      "display_name": {"zh-CN": "示例角色"},
      "thumbnail_asset": "demo.thumbnail",
      "rig_contract_ref": null,
      "rig_bindings": {},
      "geometry": {
        "logical_canvas": {"width": 512, "height": 512},
        "content_bounds": {"x": 70, "y": 28, "width": 372, "height": 452},
        "motion_bounds": {"x": 30, "y": 12, "width": 452, "height": 486},
        "base_anchor": {"x": 256, "y": 480},
        "bubble_anchor": {"x": 256, "y": 80},
        "reference_height": 452,
        "hit_regions": [{"shape": "rect", "x": 70, "y": 28, "width": 372, "height": 452}]
      },
      "actions": {
        "core.idle": "action.demo.idle",
        "core.work": "action.demo.work",
        "community.example.sample-pack.sample-series.demo.wave": "action.demo.wave"
      },
      "variants": [],
      "default_variant": null,
      "text_profile_refs": ["text.demo.default"],
      "recommended_profiles": {
        "display": "compact",
        "text": "text.demo.default",
        "behavior": null
      }
    }

要求：

- core.idle、thumbnail_asset 和 geometry MUST 存在；
- CharacterFQID 由包、系列和角色 ID 派生；
- CharacterKey 等同 CharacterFQID；精确运行选择还必须包含 RevisionKey；
- Variant 只能切换下述白名单差异，不改变 CharacterKey、Series、Context 或配置所有权；
- 角色专属动作 ID 必须位于 CharacterFQID 命名空间；
- 未知 core semantic 只有在对应 optional capability 中显式声明时才被禁用并产生降级诊断；
  未知 required semantic 或未声明能力的未知 core semantic 拒绝；
- unknown unique action 可以索引，但没有 Engine 触发语义；
- 推荐 profile 只有用户明确应用后才生效。

Variant 结构：

    {
      "id": "winter",
      "display_name": {"zh-CN": "冬装"},
      "thumbnail_asset": "demo.winter.thumbnail",
      "action_overrides": {
        "core.idle": "action.demo.winter.idle"
      },
      "geometry_override": null,
      "text_profile_refs": ["text.demo.winter"]
    }

Variant MAY 覆盖 thumbnail、已声明动作绑定、geometry 和 TextProfile 引用；MUST NOT 改变
rig_contract_ref、动作语义、权限、系统配置、rights/source 引用要求或资源预算。有效绑定合并后仍
必须具有可验证 core.idle。geometry_override 必须使用相同 logical_canvas 和 base-anchor 坐标系。

## 11. 动作描述

### 11.1 动作边界

PetPack 描述“如何表演”，Engine 决定：

- 何时触发；
- Context 事实；
- 系统优先级；
- 是否允许用户强制；
- 动作队列；
- 过期；
- 全局中断与安全。

动作描述 MUST NOT 包含系统 trigger、输入规则、注册表、自启、网络或任意代码。

### 11.2 结构

    {
      "id": "action.demo.idle",
      "semantic": "core.idle",
      "requires_capability": null,
      "rig_contract_ref": null,
      "policy_tags": ["silent", "meeting_safe", "dnd_safe"],
      "lifecycle": {
        "enter": null,
        "loop": {
          "renderer": {
            "type": "sequence",
            "frames": [
              {"asset": "demo.idle.001", "duration_ms": 120},
              {"asset": "demo.idle.002", "duration_ms": 120}
            ]
          },
          "clip_loop": {"mode": "repeat"}
        },
        "exit": null
      },
      "user_modes": ["auto", "manual", "disabled"],
      "loop_modes": ["once", "repeat"],
      "interrupt": {
        "policy": "clip_boundary",
        "max_exit_ms": 500
      },
      "audio": null
    }

### 11.3 时间

- sequence 使用每帧 duration_ms 作为规范时序；
- 作者工具 MAY 接受 FPS 输入，但 build 必须编译为确定 duration；
- layered timeline 的 keyframe 时间使用整数毫秒；
- Engine 验证后生成唯一最终时间轴；
- duration、帧数和刷新率必须 clamp；
- clip_loop 只描述片段重复，不保证动作永久运行；
- repeat 是否持续由 PerformanceResolver 决定。

### 11.4 回退

角色缺少核心动作时：

    当前角色 core.idle
      + Engine 语义 Overlay
      + 可选 Engine 纯文本

如果角色连 core.idle 都无法验证，该角色不可激活。若 active 角色损坏，使用 last-known-good 或内置安全角色。

包不得隐式继承 Series 中另一角色的身体动作。共享描述必须被该角色显式绑定，并满足 rig_contract。
同一 action 被多个 Character 绑定时，action.rig_contract_ref MUST 非空，且必须与每个角色的
rig_contract_ref 完全相同；该 action 只能是参数化 layered motion template，并由每个 Character
通过 rig_bindings 提供自己的 body slot。static/sequence 主体帧不能跨 Character 复用；
body_independent 效果除外。违反时 PPK-ACT-E005 拒绝。Engine 不从文件路径或外观猜测共享兼容性。

未来 core semantic 与 capability 的映射是确定的：semantic core.foo.bar 对应
requires_capability = semantic.core.foo.bar.v1。旧 Engine 遇到未知 core semantic 时，
只有该精确 capability 同时出现在 compatibility.optional_capabilities 才禁用并降级；
出现在 required_capabilities 或缺少精确声明时拒绝。

## 12. Renderer profiles

### 12.1 static

static 引用一张透明图片和可选持续时间。无脚本、无滤镜表达式。

### 12.2 sequence

sequence 由 asset 与 duration_ms 列表组成。Engine：

- 只绘制内容帧变化；
- 不按全局 FPS 重复绘制相同帧；
- 非循环片段到末帧后按 lifecycle 结束；
- 超限帧数或解码预算拒绝。

### 12.3 layered

layered 允许：

- 已声明图层；
- 固定 draw order；
- 平移、旋转、等比或受限非等比缩放；
- 透明度；
- 枢轴与 anchor；
- 数值关键帧和 Engine 定义的有限 easing ID。

禁止：

- 任意表达式；
- 方法名；
- 运行时代码；
- 文件或环境引用；
- 自定义 shader；
- 包自定义 draw command；
- 未声明图层。

### 12.4 builtin_effect

只能引用 Engine allowlist，例如安全的 Zzz、音符或状态徽标。未知 effect 作为可选能力降级，不得加载包内代码。

## 13. Geometry 与布局

角色几何 MUST 使用逻辑坐标，并验证：

- logical_canvas 宽高为正且在预算内；
- content_bounds 位于画布内；
- motion_bounds 覆盖所有声明动作可见内容；
- base_anchor 与 bubble_anchor 位于允许范围；
- hit_regions 只允许简单矩形、圆形或有限多边形；
- reference_height 为正；
- 缩放保持角色比例。

包只能推荐 Engine 已知布局 ID：

- pet_only；
- compact；
- standard；
- hover_expand；
- focus。

未知推荐布局产生 WARNING 并忽略。包不得提供自由窗口坐标、屏幕编号或置顶策略。

## 14. TextProfile

    {
      "id": "text.demo.default",
      "locale": "zh-CN",
      "rights_ref": "rights.original",
      "source_ref": "source.original",
      "entries": {
        "action.core.work.caption": "一起专心一会儿",
        "event.clicked.reply": [
          {"text": "在呢"},
          {"text": "今天也陪着你", "rights_ref": "rights.original", "source_ref": "source.original"}
        ]
      }
    }

规则：

- 只按纯文本渲染；
- 模板 token 必须来自 Engine allowlist；
- 不允许 eval、属性访问、索引访问、HTML、Markdown 或自动链接；
- 文本长度、候选数量、换行和控制字符受限；
- 双向控制符必须拒绝或经过明确安全化；
- 不得读取按键、窗口、剪贴板、环境变量、路径或网络内容；
- 原作台词仍需独立 rights/source 声明；
- TextProfile MUST 提供默认 rights_ref/source_ref；不同来源或许可的候选文本必须逐 entry 覆盖；
- LOCAL_IMPORTED 可以显式引用 rights.unknown 与 source.user_provided，但引用不能缺失；
- 纯字符串 entry 继承 profile 默认引用；对象 entry 的 text 为纯字符串，并 MAY 覆盖两项引用；
- 角色文案推荐不自动覆盖用户 TextProfile。

## 15. Recommendation

包可以提供推荐：

- DisplayProfile；
- TextProfile；
- BehaviorProfile；
- 当前角色动作启用、权重和允许循环的建议。

推荐 MUST：

- 标注来源包与版本；
- 只引用用户可配置键；
- 不包含自启、隐私、网络、安全和硬资源限制；
- 由用户显式应用；
- 应用时生成静态快照；
- 包升级后不自动变更旧快照。

## 16. Content warning

包 MAY 声明：

- flashing；
- sudden_sound；
- mature_theme；
- user_supplied_unverified_content；
- 其他 Engine allowlist 警示。

预览默认静音。警示是作者声明，不替代安全扫描或权利审核。

## 17. 资源预算

下列为工程起始上限，状态 PROVISIONAL。实现必须集中配置、导入与运行时双重执行，并覆盖边界测试。

| 项目 | 起始上限 |
|---|---:|
| 归档压缩大小 | 200 MiB |
| 实际解压总量 | 500 MiB |
| 文件数 | 2,000 |
| 单文件 | 100 MiB |
| 压缩比 | 100:1 |
| 内部路径层级 | 8 |
| manifest 大小 | 1 MiB |
| JSON 深度 | 16 |
| 单图尺寸 | 4096 × 4096 |
| 单动作帧数 | 300 |
| 动作可见刷新率 | 30 FPS |
| 单个角色短音效 | 60 秒 |
| 包内角色音效总时长 | 10 分钟 |
| idle 常驻 RAM 目标 | 24 MiB |
| 当前动作额外 RAM 目标 | 24 MiB |
| 当前角色 RAM 总目标 | 48 MiB |
| 当前角色 RAM 硬上限 | 64 MiB |

达到归档上限不等于可以突破运行时解码预算。一个大包仍必须按需加载。

## 18. 验证阶段

验证顺序：

1. 将用户源文件复制到应用 staging，同时计算 archive hash；
2. 之后不再读取用户源文件，避免 TOCTOU；
3. 归档结构预检；
4. 严格解析 petpack.json；
5. 安全流式解压并持续限流；
6. 复算所有文件 size 与 hash；
7. 验证 MIME、媒体解码和资源预算；
8. 验证 ID、引用、动作、geometry、文案、rights 和 compatibility；
9. 生成缩略图、边界和资源估算；
10. 构造候选 CharacterRuntime，离屏绘制 core.idle；
11. 展示来源、权利、体积、能力、警示和诊断；
12. 用户确认后发布不可变 Revision。

第 10 步 MUST 在隔离、静音、无状态写入的 validator runtime 中，对包内每个 Character 的
default Variant 解码并离屏渲染 core.idle 最小首帧。它不替代 ACTIVATE 对用户精确
Revision + Character + Variant 的候选 Runtime prepare。

任何 ERROR：

- 当前角色必须继续运行；
- 不得在 managed root 外产生文件；
- 不得注册 READY；
- 不得修改 active slot；
- staging 只能按受信 transaction ID 清理。

## 19. 诊断契约

每个诊断包含：

    {
      "code": "PPK-ARC-E003",
      "severity": "ERROR",
      "phase": "archive_preflight",
      "message_key": "petpack.archive.unsafe_path",
      "params": {},
      "recoverable": false,
      "user_action": "choose_another_pack",
      "cause_code": null
    }

规则：

- code 稳定且不本地化；
- params 必须安全化；
- 日志不得记录完整 manifest、用户文案、URL query 或源绝对路径；
- ERROR 拒绝或回滚；
- WARNING 需要用户确认，确认 code 写入 receipt；
- INFO 只描述状态。

### 19.1 诊断代码

Archive：

- PPK-ARC-E001：不支持的容器；
- PPK-ARC-E002：加密或多卷；
- PPK-ARC-E003：不安全路径或越界；
- PPK-ARC-E004：大小写、Unicode 或重复冲突；
- PPK-ARC-E005：link 或 reparse；
- PPK-ARC-E006：禁用压缩算法或嵌套归档；
- PPK-ARC-E007：归档资源预算超限；
- PPK-ARC-E008：截断、CRC 或流完整性失败。

Manifest：

- PPK-MAN-E001：缺失或非法 UTF-8 JSON；
- PPK-MAN-E002：重复键、非有限数、深度或大小违规；
- PPK-MAN-E003：不支持的 schema；
- PPK-MAN-E004：缺失或非法必需字段；
- PPK-MAN-E005：ID、版本或保留命名空间非法；
- PPK-MAN-E006：Engine 或必需能力不兼容；
- PPK-MAN-E007：未声明或缺失文件；
- PPK-MAN-E008：size 或 hash 不一致；
- PPK-MAN-E009：外部包冒用保留命名空间或信任通道。

Resource：

- PPK-RES-E001：禁用类型或 MIME / magic 不符；
- PPK-RES-E002：资源、renderer、action、audio、layout 或模板使用远程或外部 locator；
- PPK-RES-E003：图片解码或像素超限；
- PPK-RES-E004：帧数或解码内存超限；
- PPK-RES-E005：音频格式、时长或属性超限。

Action 与 Text：

- PPK-ACT-E001：core.idle 缺失；
- PPK-ACT-E002：未知 required 或未声明 capability 的 core 语义；
- PPK-ACT-E003：专属动作命名空间非法；
- PPK-ACT-E004：不支持 renderer 或表达式；
- PPK-ACT-E005：资产引用缺失或类型错误；
- PPK-ACT-E006：时序、循环或资源预算违规；
- PPK-ACT-E007：请求系统 trigger 或设置覆盖；
- PPK-TXT-E001：禁用模板 token 或表达式；
- PPK-TXT-E002：文本 profile 非法或超限；
- PPK-TXT-E003：控制符、双向文本或富文本违规。

Rights：

- PPK-RGT-E001：rights 结构缺失或非法；
- PPK-RGT-E002：资产缺少 rights/source 引用；
- PPK-RGT-E003：官方包存在 unknown 或不可核查权利；
- PPK-RGT-W001：权利依据 unknown；
- PPK-RGT-W002：发布者身份未验证；
- PPK-RGT-W003：逐资产声明存在冲突、缺失或 unknown，需要复核；
- PPK-RGT-W004：升级改变权利或许可。

Lifecycle 与 Privacy：

- PPK-LCY-E001：staging、复制或磁盘失败；
- PPK-LCY-E002：package ID 冲突；
- PPK-LCY-E007：同一 PackKey 和版本存在不同 content_digest，不得确认绕过；
- PPK-LCY-W002：请求降级；
- PPK-LCY-W003：声明发布者变化；
- PPK-LCY-E003：receipt 或原子提交失败；
- PPK-LCY-E004：ACTIVATE 候选 prepare 失败，当前 selection 未改变；
- PPK-LCY-E005：卸载目标不安全；
- PPK-LCY-E006：活动角色安全切换失败；
- PPK-LCY-E008：提交后健康检查失败，已激活精确 LKG；
- PPK-LCY-I001：锁文件进入 pending-delete；
- PPK-PRV-E001：请求禁用权限；
- PPK-PRV-E002：可执行资源 URL 或未经用户主动操作产生网络请求；
- PPK-PRV-E003：敏感数据将进入日志或 receipt。

## 20. 安装收据

receipt 由 Engine 生成，包不能提供或修改：

    {
      "receipt_version": 1,
      "install_id": "uuid",
      "publisher_id": "community.example",
      "package_id": "sample-pack",
      "package_version": "1.0.0",
      "content_digest": "...",
      "archive_sha256": "...",
      "manifest_sha256": "...",
      "engine_version": "...",
      "validator_version": "...",
      "validated_at_utc": "...",
      "import_mode": "local_file",
      "trust_channel": "LOCAL_IMPORTED",
      "publisher_verification_status": "UNVERIFIED",
      "declared_publisher_snapshot": {},
      "rights_snapshot": {},
      "warnings_acknowledged": ["PPK-RGT-W001"]
    }

receipt 只证明验证时看到的不可变内容和声明，不包含 READY、active、committed 等可变 state。
默认不保存源绝对路径。source/rights snapshot 禁止本地绝对路径、URI userinfo、凭据和 secret-like
query；需要 query 的公开 locator 只保存规范化脱敏形式与原声明 hash。receipt 发布后不可变；
安装提交、激活、回滚和卸载写独立追加式生命周期事件。

## 21. 生命周期

### 21.1 安装

    source snapshot
      → staging
      → validate
      → write immutable validation receipt
      → durable PUBLISH_INTENT
      → same-volume atomic rename
      → SQLite catalog commit READY
      → append INSTALL_COMMITTED lifecycle event

PUBLISH_INTENT MUST 持久化 transaction_id、staging path、target revision path、content_digest
和预期 catalog mutation。SQLite catalog 是 READY 的唯一权威；单独存在 receipt 或目录不代表已安装。
恢复器根据 intent 幂等完成 rename + catalog commit，或回收未发布目录。

安装成功不自动激活。

### 21.2 升级

- 完整安装新 Revision；
- 显示来源、能力、rights 和体积 diff；
- 生成新配置投影；
- 用户选择是否发起独立 ACTIVATE；
- ACTIVATE 必须执行候选 Runtime prepare，不是可选 smoke；
- 激活失败保留旧角色；
- 新 Revision 可以保留为“已安装但未激活”。

### 21.3 激活与回滚

ActiveSelection 为：

    {
      "revision_key": {},
      "character_fqid": "...",
      "variant_id": null,
      "config_revision_id": "...",
      "generation": 1,
      "commit_sequence": 1
    }

ACTIVATE 必须：

1. 对精确 Revision + Character + Variant 计算有效配置；
2. 建立隔离、静音候选 Runtime；
3. 完成 core.idle 首帧、geometry 与资源预算验证；
4. 在帧边界交换，但保留旧 Runtime lease；
5. 在 SQLite 事务中提交完整 ActiveSelection；
6. 已确认提交成功后释放旧 Runtime，并追加 ACTIVATE_COMMITTED；
7. 已确认 rollback 或未提交时，交换回旧 Runtime；
8. SQLite 返回异常、超时或连接丢失而提交结果不确定时，状态为 INDETERMINATE：
   保留两侧 lease，关闭并重新打开连接，按 commit_sequence 与完整 ActiveSelection 读回权威结果；
   已提交则保留新 Runtime，仍为旧 selection 才换回旧 Runtime；两者均无法证明时进入安全模式并
   交由启动恢复，禁止凭异常类型猜测。

崩溃发生在 DB commit 前，重启使用旧 selection；发生在 commit 后，重启使用新 selection，
旧 lease 和资源由恢复流程清理。连续选择使用 generation ID 与 latest-wins。

回滚等于激活已安装、仍兼容的精确旧 Revision。禁止反向迁移脚本。新字段保留但对旧版忽略。

### 21.4 卸载

- UNINSTALL_REVISION(revision_key) 是原子卸载单位；
- UNINSTALL_PACKAGE(pack_key) 是对 UI 明列 revisions 的受控批处理；
- Character 与 Variant 不是独立安装或卸载单位；
- 目标 Revision 先禁止新 lease；
- 活动 selection 先成功切到用户选择的其他 READY selection；没有可用目标时才用内置安全角色；
- pinned 或 LKG Revision 在替代 selection 通过健康 checkpoint 前不得删除；
- 原子移动到 trash；
- 写 tombstone；
- 异步物理删除；
- 文件锁进入 pending-delete；
- 默认保留个人配置、个人素材、源归档和历史 receipt。

删除路径只能来自受信 registry，并在删除前重新验证 managed-root containment 与 no-reparse。

## 22. Extensions 权限边界

V1 Runtime 对未注册 extension 只能限量保存、inspect 或忽略。不得通过动态 import、entry point、
Qt plugin path、文件名、MIME handler 或 capability 名查找执行器。Extension 不得声明额外文件、
远程 locator、配置所有权、系统 trigger 或放宽资源预算。注册 extension 仍只能组合本规范纯数据
renderer allowlist，并通过全部安全 gate。

## 23. 确定性构建

作者工具 build SHOULD 产生确定性 .petpack：

- 规范化成员顺序；
- 规范化路径分隔符；
- 固定 JSON 序列化；
- 排除源文件时间戳或使用规范时间；
- 相同输入生成相同 archive hash；
- 生成缩略图、边界、文件 hash、资源估算和验证报告；
- source、build、reports 与最终包分离。

工具链命令目标：

    petpack init
    petpack validate
    petpack preview
    petpack build
    petpack inspect
    petpack install-local

install-local 只是 M4 生命周期服务的薄客户端，不能自行复制到 LibraryRoot；在正式 catalog、
journal 和 receipt 可用前不得实现。Runtime 仍必须重新验证最终包，不能信任作者工具报告。

## 24. 参考包

PetPack 1.0 发布前必须具备：

1. minimal-static：一个角色、一张 core.idle 和缩略图；
2. retirement-cat-official：当前退休猫迁移后的官方包；
3. multi-character-shared-rig：同系列多角色、显式共享 rig；
4. missing-optional-actions：只提供 idle，验证语义回退；
5. invalid-and-hostile corpus：路径、归档、JSON、媒体和生命周期攻击夹具。

官方退休猫必须通过与第三方包相同的 schema 和 Runtime 接口；只在内容权利和可信分发渠道上具有更高等级，不能绕开协议。

从角色素材到 `.petpack`、GUI 导入和版本升级的实操步骤见
[角色库扩展指南](CHARACTER_LIBRARY_GUIDE.md)。
