# 半写实退休猫 0.1.x 生成、净化与验收记录

> 返回 [角色工作区索引](README.md) · 查看
> [本角色作者说明](realistic-retirement-cat.AUTHORING.md) · 阅读
> [角色库资产清单](../docs/CHARACTER_LIBRARY_GUIDE.md) · 对照
> [内容与权利政策](../docs/CONTENT_POLICY.md)

本文件同时记录原始生成事实和后续公开净化事实。0.1.0 与 0.1.1 是两个独立的
Revision；PetPack 内的 `rights_declarations`、`sources`、资产 hash 和发布包
content digest 才是各自的冻结引用事实，不能用新结果覆盖旧结果。

## 原始生成事实（0.1.0）

- 日期：2026-09-01
- 模式：Codex 内置 `imagegen`，身份保持 + 透明背景提取
- 生成服务的内部执行 ID 保存在私有来源证据中，不进入公开仓库
- 0.1.0 接受素材字节数：2,211,697；SHA-256：
  `b59c0a8433fdccfb6c33035765cd7bfe5dec2b8a1d1e0c9556c55d34fcf8c4dd`

最终提示词组：

> 保持奶油桃色退休猫的身份、脸型和大而温暖的眼睛，保留海军蓝毕业帽和流苏；
> 绘制为更真实但仍适合作为桌宠的半写实 2.5D 形态。全身、干净轮廓、柔和毛发
> 细节，真实透明 RGBA 背景；不要文字、Logo、场景、地面或投影，不要改变角色身份。

## 公开净化 Revision（0.1.1）

- 日期：2026-09-01；
- 输入是上面的 0.1.0 原始 PNG；使用仓库内确定性工具
  `scripts/sanitize_png.py` 处理，不调用网络，也不重新编码图片；
- 只移除一个非公开 allowlist 的 ancillary `caBX` chunk：payload 21,844 bytes，连同
  length/type/CRC framing 共减少 21,856 bytes；不公开或解释该 chunk 的 payload；
- 输出是当前工作区的
  `realistic-retirement-cat/assets/characters/realistic-cat/body.png`，2,189,841 bytes，
  SHA-256：
  `1a6351e3aaaae76d0d393c2debea9e5a35751ac355f1346edc55fadb4fb2d6bf`；
- 所有保留 chunk（包括全部 IDAT）均从输入逐字节复制，顺序、payload 和 CRC 不变。
  工具会拒绝未知 critical chunk、错误 CRC、截断结构或缺少 IHDR/IDAT/IEND 的文件。

## 像素验收

- 两个 Revision 都解码为 1024 × 1536 RGBA；当前 0.1.1 PNG 为 2,189,841 bytes；
- 可见内容边界（alpha > 8）：`x=95, y=32, width=926, height=1502`；
- alpha=0 像素：629,300；部分透明像素：943,564；四角均透明；
- alpha=255 像素为 0，最大 alpha 为 254；主体大部分像素为 252–253，视觉上接近
  不透明；完整 RGBA 解码工作集约 6 MiB；
- 逐行解码后的 6,291,456 个 RGBA bytes 的 SHA-256 均为
  `d0f48290f7ea8ed4d3155fa6733fb16db768cb2440866519f72c839b514d3eee`；
- 内部冻结的 0.1.0 与新 0.1.1 经同一 QImage RGBA8888 路径解码后逐字节相等，
  包括全部 Alpha bytes；
- 只有该真实 Alpha 版本进入项目。后续生成中出现的白底或烘焙棋盘格版本全部拒绝，
  没有进入角色包。

## Revision 身份

| Revision | archive SHA-256 | content digest | 发布边界 |
|---|---|---|---|
| 0.1.0 | `2c77efbc0b99143f673acd849693d25e34fa87283a9c797621699ecc8189ce2e` | `472dfacfce41b4ca381ebfec9150f48c8531fde9c7eef0ce66484e6d527efb7b` | 内部冻结旧事实；公开快照精确排除该文件 |
| 0.1.1 | `ce80e7dd73726cdbb252f21d65538014fe10790dce7a44816ebb80bc26c2a964` | `6c4b368ad79124bf5bc48e6d8190917c7031f923b2cf00f728c4c17b9c21260c` | 当前公开源码与运行时 pin |

## 权利边界

该素材是本项目的 AI 辅助原创原型，没有以第三方影视、动漫角色或商标为目标，也不
声称与任何第三方角色有关。若后续引入用户喜爱的既有 IP，必须建立独立的来源、授权
与分发边界，不能沿用本条原创声明。

包内自定义许可声明允许 RetirementPet 项目所有者在项目范围内使用、修改和再分发
该原型，并要求素材离开本地工作区再分发时保留本 provenance notice。这些内容仍是
包作者自述，不构成发布者身份认证或第三方授权认证。
