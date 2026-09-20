# PetPack 一致性闭环对照表（V12-02）

状态：**返工后自测完成待主控复验**（主控审阅 [V1_2_CONTROLLER_REVIEW_A.md](V1_2_CONTROLLER_REVIEW_A.md)
CR-P01/P-2/P-1 及 §3 口径修正已落实）。本文档回答工单 4 的要求：对本版实际
使用的字段，给出"规范要求 → validator → GUI preflight → Runtime 消费"四列
对照，并记录严格化后的逐包审计结果、engine 版本决策和新增诊断码。

## 1. 字段对照表

缩写：验证列中 `E`=拒绝（ERROR），`W`=降级警告（WARNING+用户确认进 receipt），
`—`=本版无检查。Runtime 列只记录**真实消费路径**；"格式校验"与"运行时消费"
分别陈述，后者必须有调用点证据（审阅 P-1 证据规则）。

| 字段 | 规范要求 | validator | GUI preflight | Runtime 消费 |
|---|---|---|---|---|
| `schema_version` | 仅接受 "1.0"（未知主版本拒绝） | `MAN_E003`（parse 后严格等值） | 同左（子进程内同一函数） | 只读校验一次 |
| `package.{publisher_id,id,version,display_name}` | ID/保留命名空间/semver/多语言名 | `MAN_E004/E005/E009` | 同左 | PackKey/RevisionKey 组成 |
| `package.publisher_ref` | **必填**（spec 6.1），必须解析到 `publishers[].id` | **V12-02 新增** `MAN_E004`（缺失 `publisher_ref_required`、非字符串 `publisher_ref_invalid`、悬空 `publisher_ref_dangling` 均拒绝）；**版本化旧包豁免**：恰好四个冻结 RevisionKey（见 §4）缺 ref 时以 `PPK-MAN-W002` 警告 + `publisher_ref_exempt:historical` 降级放行；任何重建变体（digest 不同）不在豁免内 | 同左 | 不消费（声明性） |
| `manifest.publishers` | 发布者声明块 | 结构 `MAN_E004`（`_validate_publishers`，仅形状与 id 提取） | 同左 | receipt 记录声明快照 |
| `compatibility.engine_min/engine_max_exclusive` | Engine API 兼容范围（spec 5） | 结构 `MAN_E004`（semver 形状、min<max）；**V12-02 新增** 值检查 `MAN_E006`：ENGINE_VERSION 不在 `[min,max)` 即拒绝 | 同左 | 不消费（安装门禁） |
| `compatibility.required_capabilities` | 列表；引擎不支持的必需能力拒绝 | **V12-02 新增** `MAN_E006`（逐项对照 engine_profile 注册表，注册表只收**有运行时消费证据**的能力，见 §2）；形状 `MAN_E004` | 同左 | 不消费 |
| `compatibility.optional_capabilities` | 列表；允许声明未知能力 | 形状 `MAN_E004`；值自由（可选即降级凭据） | 同左 | 不消费 |
| `character.geometry`（logical_canvas/content_bounds/motion_bounds/base_anchor/bubble_anchor/reference_height） | MUST 存在；有限数值、正尺寸、边界/锚点在画布内、reference_height 为正（spec 13） | **V12-02 新增** 严格校验 `MAN_E004`（画布 ≤4096 边预算、矩形完全含于画布、容差 1e-6、bool 不算数值；NaN/Inf 在 JSON 层 `MAN_E002` 拒绝） | 同左 | `CharacterGeometry.from_character`（V12-01）按同一规则解析；畸形时运行时回退旧适配——经本闭环后验证过的包不会再走到回退 |
| `character.geometry.hit_regions` | 只允许简单矩形/圆/有限多边形 | **V12-02 新增** `MAN_E004`：形状白名单、rect/circle/polygon 参数有效、圆与多边形在画布内、≤16 区域、≤32 点（超限此前被运行时静默截断） | 同左 | `pet_window` 命中测试（裁点击语义，不裁绘制） |
| Variant `geometry_override` | 必须同 logical_canvas 与 base-anchor 坐标系（spec 10） | **V12-02 新增** 同套校验 + 画布相等，否则 `MAN_E004` | 同左 | **未接入**：本版 Variant 不生效（switcher 拒绝 variant_id 请求，`PPK-LCY-E004`"variant overlays are not implemented"）；此校验只保证声明数据形状，供后续版本消费 |
| `character.thumbnail_asset` | MUST 存在且解析（spec 10） | **V12-02 新增** 缺失 `MAN_E004`、悬空 `ACT_E005`（角色与 Variant 均查） | 同左 | 角色列表缩略图 |
| `character.actions` 绑定 | 绑定值必须指向已声明动作；合并后仍须有可验证 core.idle | **V12-02 新增** 悬空绑定 `ACT_E005`；Variant 合并绑定同查；core.idle 缺失 `ACT_E001`（原有） | 同左 | PackRuntime `_profiles` |
| 动作 `semantic`（core.*） | 未知 core 语义：精确能力在 optional 才禁用降级，否则拒绝（spec 11） | **V12-02 新增** 未知且未声明 → `ACT_E002`；未知且声明 `semantic.<名>.v1` → `PPK-ACT-W001` 降级（动作禁用，`degraded=semantic_disabled:<semantic>`） | 同左 | 已知 7 语义由 context_adapter/ActionId 触发；禁用语义不进入触发集 |
| 动作 renderer 类型 | static/sequence 引擎必须支持；layered 为参数化 rig 模板；builtin_effect 非本体渲染 | **V12-02 新增**（审阅 P-2 修订）**core.idle（含经 layered 模板绑定）不可绘制 → `ACT_E004` 拒绝运行准入**（`petpack.actions.idle_undrawable`）；不可绘制的**非 idle** 绑定 → `PPK-ACT-W002` 降级（`degraded=renderer_unsupported:<action>`，仅因准入下限保证 core.idle 可用回退才允许）。结构可解析（actions 相之前无更早阶段诊断）与运行准入分层测试 | 同左 + 每角色 idle 首帧离屏验证（拒绝不可激活） | `_profile_for` 仅绘制 static/sequence；其余回退 idle |
| sequence 帧 | 数量 ≤300、duration_ms ≥33、asset 必须存在 | 原有 `RES_E004/ACT_E006/ACT_E005`；**V12-02 起按 ID 精确闭包**（原先线性扫描 id） | 同左 | 帧播放 |
| static asset | asset 必须存在 | **V12-02 新增** `ACT_E005`（原先不检查，运行时静默回退） | 同左 | 单帧绘制 |
| `rights_declarations` / `sources` / 资产 `rights_ref`/`source_ref` | 结构、basis 规则、引用闭包（spec 8） | 原有 `RGT_E001/E002/E003/RGT_W001`；**V12-02 新增** `license.legal_file_ref` 必须解析到 legal_files（`RGT_E002 kind=legal_file`） | 同左 + 确认页 warning 集 | receipt 记录确认 |
| `legal_files` | 仅 UTF-8 纯文本；路径/单文件/总量预算；未声明、缺失、MIME 不符、hash 不符均拒绝（spec 7.1） | **V12-02 新增** `MAN_E004`（id/用途）、`RES_E001`（MIME、非 UTF-8）、`MAN_E008`（byte_size/sha256 复算）；id 唯一 | 同左 | 不自动打开；许可展示 |
| `text_profiles` | 纯文本；模板 token 仅引擎 allowlist；控制符/双向符受限；必须提供默认 rights/source 引用（spec 14） | **V12-02 新增** 结构/超限 `TXT_E002`、控制符与标记 `TXT_E003`（复用 `text_profile.check_plain_text` 同一边界）、禁用 token/表达式 `TXT_E001`（仅允许 `SAFE_VARIABLES`）、引用悬空 `RGT_E002/TXT_E002` | 同左 | **未接入**：本版无运行时消费点（`text_profile` 模块当前仅被 validator 与作者表单提示引用），因此 `text.plaintext.v1` 不在支持能力注册表（见 §2）；包内文本渲染随后续版本交付 |
| `recommended_profiles` | 只能推荐引擎已知布局 ID；未知产生 WARNING 并忽略（spec 13） | **V12-02 新增** `PPK-MAN-W001`（布局白名单 pet_only/compact/standard/hover_expand/focus）；text 引用悬空 `TXT_E002` | 同左 | 布局策略仅在用户显式应用后生效 |
| 资产清单（含 wav/ogg 音频） | 逐文件精确声明、size/sha 复算、magic/MIME/像素/音频预算（spec 7/17） | 原有全量保留；**V12-02 新增** asset id 唯一（`MAN_E004`） | 同左 | 仅 PNG 经 LRU 解码缓存进入绘制；**包内音频无运行时消费点**（`audio.py` 仅本地音乐文件），故 `asset.wav.v1`/`asset.ogg.v1` 不在支持能力注册表（格式校验 ≠ 运行时消费，审阅 P-1） |
| `extensions` | 未知字段不提升能力 | 本版不解析（未声明能力不因可解析而支持） | 同左 | 不消费 |

## 2. 引擎事实注册表（`petpack/engine_profile.py`）

- `ENGINE_VERSION = "2.0.0"`（主控裁定 P-1 接受）：spec 5 明确
  `engine_min/engine_max_exclusive` 是 **Engine API 兼容范围**，独立于应用
  版本（当前 `__version__=1.1.1`）。内部保留的四个历史/当前包均声明
  `[2.0.0, 3.0.0)`，本运行时实现的即该 API 线。2.0.0 不表示完整 v2 规范
  全部实现。该常量只在真实运行时 API 变更时移动。
- 支持能力集合（**准入凭据**，逐项有运行时消费证据）：
  renderer.static/sequence、asset.png、
  semantic.core.{idle,work,rest,eat,exercise,meeting,music}。
  **不含** renderer.layered / renderer.builtin_effect / asset.webp，也
  **不含** asset.wav/asset.ogg/text.plaintext——后三者只有 validator 格式
  校验，没有包内运行时消费点；一个 required 声明它们即被 `MAN_E006` 拒绝
  （`tests/test_petpack_v12_02.py` 固化正反两向）。
- 已知 core 语义 = context_adapter 与 PackRuntime 实际触发的 7 个。

## 3. 新增/扩展诊断码（供主控并入规范表 19.1）

| 码 | 含义 | 依据 |
|---|---|---|
| `PPK-ACT-W001` | core 语义引擎未知、但其精确能力已声明 optional → 动作禁用并降级 | spec 11 行为；spec 19.1 无对应 W 码，按 RGT_W 模式扩展 |
| `PPK-ACT-W002` | **仅限非 idle** 绑定动作的 renderer 引擎不可绘制 → 降级标注（准入下限已保证 core.idle 可用回退）；不可绘制 idle 一律 `ACT_E004` 拒绝，不得以本码放行（审阅裁定 P-2） | 工单"不因能解析写成已支持"+ 审阅 §1 P-2 |
| `PPK-MAN-W001` | 未知推荐布局 ID → 警告并忽略 | spec 13 明文要求 WARNING，表 19.1 缺码 |
| `PPK-MAN-W002` | 恰好四个钉定历史 Revision 缺规范必填 `publisher_ref`，经版本化豁免加载 → 每次加载可见警告 + `publisher_ref_exempt:historical` 降级（审阅 CR-P01） | spec 6.1 必填；19.1 无对应 W 码 |

其余全部复用既有稳定码（`MAN_E004/E006/E008`、`RES_E001`、`ACT_E002/E004/E005`、
`TXT_E001/E002/E003`、`RGT_E002`），未发明平行动作。

## 4. 冻结包与本地包逐包审计结果

严格化验证流水线对**内部保留的四个历史/当前包**（不称"在售包"；冻结 0.1.0
不是当前公开产品）的实测（`tests/test_petpack_v12_02.py` 固化为回归）：

| 包 | 通道 | 结果 | degraded | warnings |
|---|---|---|---|---|
| retirement-cat-official @1.0.0（冻结） | BUILTIN_OFFICIAL | ACCEPT_WITH_DEGRADATION | `publisher_ref_exempt:historical` | `PPK-MAN-W002` ×1 |
| retirement-cat-official @1.0.1（冻结） | BUILTIN_OFFICIAL | ACCEPT_WITH_DEGRADATION | `publisher_ref_exempt:historical` | `PPK-MAN-W002` ×1 |
| realistic-retirement-cat @0.1.0（本地，历史） | LOCAL_IMPORTED | ACCEPT_WITH_DEGRADATION | `publisher_ref_exempt:historical` | `PPK-MAN-W002` ×1 |
| realistic-retirement-cat @0.1.1（本地） | LOCAL_IMPORTED | ACCEPT_WITH_DEGRADATION | `publisher_ref_exempt:historical` | `PPK-MAN-W002` ×1 |

结论（CR-P01 修订）：四个包全部缺 spec 6.1 必填的 `publisher_ref`。处理方式
是**精确 RevisionKey 钉定的版本化旧包豁免**（`_HISTORICAL_PUBLISHER_REF_EXEMPT`，
含各自 content_digest）：豁免不覆盖任何重建变体，每次加载产生稳定可见的
`PPK-MAN-W002` + 降级条目；不覆盖旧包补字段、不全局跳过校验。作者新包
（含测试夹具）一律按必填校验。测试夹具（`test_petpack.py`/
`test_petpack_rig.py`）按新契约补全 publisher_ref/publishers、geometry 与
thumbnail——这是夹具升级到既已生效的规范要求，不是放宽断言。

## 5. 激活事实审计（ACTIVATE_COMMITTED，审阅 CR-P02 + 复核 CR-P03 已实现）

按主控裁定 P-3 落地：ACTIVE 行仍是**选择唯一权威**；`journal/events.jsonl`
（与 INSTALL_COMMITTED 同一追加式设施）新增 `ACTIVATE_COMMITTED` 审计行。
要点：

- 记录时机：仅**已确认提交**之后——`swap_and_commit` CAS 胜出、INDETERMINATE
  读回证实采纳、`repair_active_from_current` 胜出、legacy `commit_candidate`
  胜出（`switcher._adopt_candidate`/`_record_activation` 单一收口）。
- 失败不误报：CAS 竞争失败、提交结果不可知未证实、读回失败均**不**记录；
  journal 追加失败只记日志返回 False，绝不推翻或改变已确认的切换结果。
- 幂等：以 (revision, generation, commit_sequence) 查重，重复确认不重复
  追加。
- 持久待记事实（复核 CR-P03）：每次已确认的 ACTIVE 写入在**同一事务**内向
  `state.db` 的 `activation_audit_due` 表写入一条待记事实；事件行被证实
  （内联追加成功或已存在）后才删除该事实。审计写入失败不会丢失历史——
  事实保留在账本中，在后续每次已确认提交（先按提交顺序补记旧事实、再内联
  记当前次）与每次启动（`_reconcile_activation_journal` 全量 drain）时按
  事实补记 `recovered=true`，而不是从被覆盖后的当前 ACTIVE 槽位推断。
- 重启补记：drain 待记账本之后，旧版本数据库中"无账本事实的 ACTIVE 行"
  仍按原规则补记一条 `recovered=true`（升级兼容，幂等）。
- 事件内容仅 pack/character 标识与计数器，无路径无个人数据。

覆盖测试：`tests/test_activation_audit.py`（成功切换恰一条、CAS 失败零条、
INDETERMINATE 读回证实恰一条且幂等、重启补记恰一条且幂等、审计失败不改变
切换结果、失败一次后继续切换恢复完整历史 [1,2]、连续多次失败重启后按序补记
[1,2,3] 且重启幂等、LKG checkpoint 与 degraded bootstrap 不记、legacy 路径
记录）。

## 6. 验证

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q
# 全量 exit 0（返工后含 backup/restore 归属保护、激活审计、准入分层回归）
```
