# RetirementPet 当前问题与未来工作

> 2026-09-01 更新
> Alpha 后续以角色库和角色内容为主
> 相关入口包括[文档中心](README.md)、[实施状态](IMPLEMENTATION_STATUS.md)和
> [角色库扩展指南](CHARACTER_LIBRARY_GUIDE.md) · [发布验收](CONFORMANCE.md)

这里记录可变化的工作排序。已验证事实仍以[实施状态](IMPLEMENTATION_STATUS.md)
为准，冻结产品含义仍以[决策记录](DECISIONS.md)为准。

## 状态用语

| 状态 | 含义 |
|---|---|
| NEXT | 当前最适合继续实施的工作 |
| PLANNED | 已进入路线，但尚未承诺具体版本 |
| ENVIRONMENT GATE | 代码不能替代的真机或长期验收 |
| EXPLORATORY | 候选方向，需要新决策后才进入交付范围 |

## 当前问题台账

| 领域 | 状态 | 当前事实 | 处理方向 |
|---|---|---|---|
| 公开便携版 | NEXT | 旧 1.1.1 onedir 没有随包 LICENSE/NOTICE，实际包含 QtMultimedia、VirtualKeyboard、FFmpeg、OpenSSL 等组件 | 从公开提交重建，生成实际组件清单、完整许可目录和 LGPL 对应源码入口后再发布 |
| 角色资产授权 | NEXT | 两个冻结角色包的自定义许可没有完整定义公众修改、商业再分发和 fork 边界 | 由权利人选择 CC0、CC BY 4.0 或明确自定义许可，发布新 Revision，旧摘要不覆盖 |
| 半写实猫动作 | NEXT | 0.1.1 只有一张正文；三个动作的包自有 body 像素完全相同，最终画面差异仅可能来自 Engine overlay | 先做三个独立姿态，再补核心生活动作 |
| 画布与显示比例 | NEXT | 现有正文是 1024×1536；1.1.1 仍按整张图片适配窗口，manifest geometry 尚未参与实际缩放 | 统一方形输出画布与脚底锚，随后让 Runtime 消费 geometry |
| 作者可用 renderer | PLANNED | 本地导入当前只保证透明 PNG 的 `static` 和 `sequence` | 完成并验收 `layered` 后再写入作者承诺 |
| 变体与专属动作 | PLANNED | schema 可以描述，1.1.1 尚未把 variant、发布者专属动作接入正式调度 | 先补 Runtime、切换和回退测试，再制作对应素材 |
| 包内音效、文案和推荐配置 | PLANNED | 规范已有边界，当前角色 Runtime 没有形成端到端作者流程 | 分项接入，保持声音惰性初始化和用户显式确认 |
| 作者工具 | NEXT | `init` 只生成不完整骨架；没有独立 `preview` 或 `install-local` 命令 | 提供可直接通过校验的模板、lint、动作接触图和离线预览 |
| 规范一致性 | NEXT | `publisher_ref` 要求与当前 validator/示例有漂移；构建器创建的 ZIP 成员实际未压缩 | 先分类修正规范或实现，增加 conformance fixture，不盲目改摘要 |
| Validator conformance | NEXT | `compatibility`、`legal_files` 完整性、geometry、TextProfile/recommendations 和完整 action 引用尚未达到规范声明 | 补分层 fixture，区分纯 validator、GUI preflight 与完整规范 gate |
| 角色库界面 | PLANNED | 可以导入和切换，但没有普通用户卸载、Revision 详情、存储与回滚管理 | 增加轻量详情和安全卸载，不引入常驻负担 |
| 文案与布局 | PLANNED | 文案模板与 layout/visibility 预设当前只做诚实预览，不保存或应用；显示页的置顶与鼠标穿透已经保存并应用 | 在角色素材稳定后接入有效配置与回滚 |
| Production 认证 | ENVIRONMENT GATE | Alpha 已可试用，正式门禁仍有六项 | 按下节逐项取得同一 artifact 的原始证据 |

## 角色库已确认的技术债

1. PetPack 规范要求 `package.publisher_ref`，当前 validator 和半写实示例都没有
   落实。应先决定补实现还是修订规范，再增加 conformance fixture。
2. `petpack_cli.py init` 只生成骨架，规范目标中的独立 `preview` 与 `install-local`
   尚未实现；GUI 仍是唯一正式安装入口。
3. 构建器使用固定 ZIP 信息时没有设置成员压缩类型，半写实 0.1.0 与 0.1.1 的三个
   成员均为 `ZIP_STORED`。官方包有防覆盖保护，canonical 示例还缺同等级保护。
4. geometry 目前没有完整字段校验，Runtime 也没有用它计算正文区域、锚点和点击区。
   在修复前，作者只能靠 PNG 物理画布控制桌面比例。
5. 半写实包声明需要 `renderer.sequence.v1`，实际三个动作都是 `static`。应先审计
   它是否有意代表当前 static/sequence renderer family；在 capability 语义冻结前，
   不修改 manifest 或已钉扎摘要。
6. 规范要求激活提交有追加式生命周期事件，当前实现需要补一次针对
   `ACTIVATE_COMMITTED` 的 conformance 审计。
7. 角色页主要按系列与角色展示。多个 Revision 并存时，还需要显示 package ID、
   版本、digest、来源和当前/上一健康状态，避免出现难以区分的重复条目。

## 接下来四段工作

### R1 半写实退休猫 0.2

先锁定角色身份页、正侧面比例、配色和毕业帽细节。素材源可以高分辨率保存，桌宠
输出建议统一到 512×512 透明画布，脚底锚和可见边界保持稳定。首批交付至少包含
独立 idle、work、rest，随后补 eat、exercise、meeting、music。

每个动作先做一张稳定姿态，再决定是否扩成短序列。这样可以尽早验证画风、窗口
占比和角色一致性，不必一次制作大量返工成本很高的帧。

R1 的最低验收包括真实 Alpha、透明四角、无烘焙文字、无场景或投影、动作间身份
一致、预检通过；切换失败不能错误提交目标角色，并须按可证明权威回滚、恢复或进入
Bootstrap 安全模式。static 角色不引入额外素材解码循环或帧序列推进，并以相同
场景的 CPU 样本确认没有明显回退。

### R2 作者工具和模板

把 `petpack_cli.py init` 升级为可校验的完整静态模板，增加 manifest lint、画布与
锚点检查、动作接触图（contact sheet）、序列时长报告和离线预览。构建仍保持
确定性。ZIP 压缩方式变化会改变 archive SHA，但相同内容仍可能保持同一 content
digest；工具与发布 artifact 必须新增证据。0.1.0 canonical 是内部冻结旧事实，
0.1.1 是当前公开与运行时 pin，二者均不得原地覆盖。只有 manifest 或包内容改变时
才提升 PetPack 版本。

### R3 角色库管理

角色页增加系列筛选、角色详情、版本与来源、资产预算、当前/上一健康 Revision、
安全卸载和 pending-delete 状态。页面继续按需创建，缩略图有缓存上限，不把完整
角色图常驻内存。

### R4 多系列内容

在作者流程稳定后再加入第二个原创系列和多角色参考包，用真实内容验证系列分组、
共享语义、角色独特动作与版本升级。已有影视或动漫 IP 若没有覆盖相应用途的许可，
项目不得内置或再分发；用户本地导入仍由用户确认许可范围。

## Production 尚需的六项门禁

1. 100%、150%、200% 混合 DPI，负坐标副屏、跨屏和拔屏恢复。
2. 真实注销与登录后的自启、不抢焦点和启动延迟。
3. 锁屏、会话断开、睡眠与唤醒。
4. 无 Python、无 Anaconda 且存在竞争 Qt PATH 的干净 Windows 11 VM。
5. 绑定同一 artifact 的完整 24 小时稳定性观测。
6. 三台参考设备的 10 分钟性能样本和预算冻结。

这些项目可以与角色创作并行准备，但在取得原始证据前必须继续显示
`PRODUCTION-NO-GO:PENDING_ENVIRONMENT`。

## 暂不进入近期范围

在线角色商店、自动下载和云同步保持 `EXPLORATORY`，只有经过独立的产品、内容治理
与网络安全决策后才可能进入路线。Live2D、Spine、3D 等外部运行时同样保持
`EXPLORATORY`；只有 raster 正式案例证明表达不足，并同时给出 CPU、内存、包体与
安全预算时，才重新讨论。受限的纯数据 `rig/layered` 仍属于 `PLANNED`。PetPack 内
第三方可执行代码是冻结的禁止项，不属于探索范围。
