# 1.2.0 作者工具审阅响应报告（CR-T01/02/03）

> 第二轮响应（CR-T03 续：接触图可读性；CR-T04：末帧取样）见文末 §7；
> §5 的套件数字已被 §7 更新。

> 日期：2026-09-12 · 角色：执行者（本地模型） · 交付性质：按
> [作者工具主控审阅](V1_2_AUTHOR_CONTROLLER_REVIEW.md)完成 V12-03 三项补充返工
> · 状态：**自测完成待验收**
>
> 提交：`b126be8`，分支 `impl/v1.2-daily-use`。本报告含内部提交号/分支名，
> 仅存内部仓库，公开导出前须脱敏。

## 0. 范围对照

| 审阅编号 | 内容 | 状态 |
|---|---|---|
| CR-T01 / P1 | 序列帧读取层级错误，非法时长 lint clean | 已修复：帧改从 `renderer.frames` 读取，非法输入全部报错并与 validator 对齐 |
| CR-T02 / P1 | preview 未经过实际绘制与 geometry | 已修复：`compute_body_layout` + `render_body` + 真实帧时间取样，含几何报告与断言 |
| CR-T03 / P2 | 接触图未交付 | 已交付：`contact-<角色>.png`，标注角色/动作/帧序号/帧时长 |
| 口径修正 | "17 项验收测试覆盖全部验收点"表述 | 撤回并修正（见 §4） |

## 1. CR-T01：序列帧统计按协议读取

**根因**：`_action_frames()` 从 `lifecycle.loop` 段读 `frames`，而协议把帧放在
`lifecycle.loop.renderer.frames`（`validator.py:711` 与 `runtime.py` 的
`_resolve_profile` 均如此）——符合协议的 2 帧序列被当作零帧，时长检查随之
全部落空。

**修复**（`scripts/petpack_cli.py`）：帧改为从 renderer 读取，与 Runtime 单一
来源一致；并明确处理：

- 空序列 → `E: sequence renderer has no frames`；
- `duration_ms` 非整数 → `E: frame duration_ms … is not an integer`；
- 非正时长（≤0，静态动作的 0 哨兵不在此列）→ `E: non-positive frame
  duration(s)`；
- 过短时长（0<d<33）→ `E: …ms frame (min 33ms)`；
- 帧数 >300 → `E: … frames (max 300)`（原实现把该检查放在逐资产循环内，
  现移到外层只报一次）。

**测试**（独立预期值，非字符串包含断言）：

| 场景 | 断言 |
|---|---|
| 有效 2 帧 500/700 ms | `frames=2`、`total_ms=1200`、`decoded~32 KiB`（(4096+4096)×4=32768 B）、预算行 `~0.0 MiB` |
| 1 ms 帧 | lint rc=1 含 `min 33ms`，且 `validate` 同包 rc=1（两道门禁一致） |
| 0 ms 帧 | lint rc=1 含 `non-positive`，validator 同样拒绝 |
| 空序列 | lint rc=1 含 `no frames`，validator 同样拒绝 |
| 取样计划单元断言 | `preview_schedule()`：idle 行 `(sequence, 2, 1200)`，采样点 `[(0,"f0 500ms"),(500,"f1 700ms"),(1200,"seam 1200ms",seam)]`，6 个未绑定语义各一条 `fallback ->idle` 行 |

## 2. CR-T02：preview 经过真实几何与绘制入口

**方案**：preview 重写为桌宠窗口同路径——

1. 视口取 `ui.pet_window.WINDOW_WIDTH×CAT_AREA_HEIGHT`（232×236 DIP），
   与 `PetWindow._refresh_layout` 完全一致；
2. manifest geometry 经共享的 `compute_body_layout` 得到 `BodyLayout`
   （body_rect、foot/bubble 锚点、scale、clamped）；
3. 按 `PackCharacterRuntime.preview_schedule()`（本次新增的公开方法，帧
   解析与绘制同源）的时间点构造 `RenderSnapshot`，逐点调用
   `render_body(painter, viewport, snapshot, layout)`；
4. sequence 取真实帧时间：每个帧起点 + 循环接缝（t=total，经
   `_asset_for_elapsed` 取模回到首帧）；未绑定语义画 fallback 行
   （render_body 自身落到的 idle 回退）；
5. 输出几何报告行（canvas、viewport、scale、body、foot、bubble、clamped）。

无库、无 Context、无用户配置改动、不发声的约束不变；预览全部离屏。

**测试**（预期值手工推导，不读回布局代码）：

| 场景 | 独立预期 | 断言 |
|---|---|---|
| 标准模板 | scale=min(230/52,224/56,230/58)=3.9655；motion 底边超视口 → 整体上移；形体像素（画布 16..48×18..56）映射到 ≈(52,61)..(179,212)，foot x=116 | 报告含 `scale 3.9655`、`foot (116.0, 228.1)`、`clamped=True`；帧恰为 232×236；alpha 包围盒四边 ±3px 内；包围盒中心 x≈116 |
| 竖图 64×128 | scale=230/128=1.7969，foot (116,234)，不钳制 | 报告逐项匹配 |
| 大留白 128×128（中置 64×64） | scale=3.5，foot (116,234) | 同上 |
| 偏置锚点 base_anchor x=60 | 整体右移钳制，foot (222.1,228.1)，clamped=True | 同上 |
| 循环接缝 | t=1200 与 t=0 同资产同变换 | 接缝帧与首帧逐像素相等（5px 网格采样）；第 2 帧形体像素与首帧不同 |

## 3. CR-T03：接触图图像产物

`preview` 现为每个角色输出 `contact-<角色>.png`：每个语义一行、每个采样一列；
行标注语义与 kind/帧数/总时长，单元格标注帧序号与帧时长（`f0 500ms`、
`seam 1200ms`、静态 `static`、回退 `->idle`），白底浅灰行分隔，样本按视口
纵横比缩放入格。静态与序列都覆盖；作者可直接比较脚底位置、角色大小与循环
首尾。lint 不写文件（审阅允许）。

**测试**：2 帧序列 + 6 条回退行的版式恰为
`132+4+3×100=436 × 4+7×114=802` 像素；行标签带与单元格标注带存在文字墨迹
（暗像素）；动作行与回退行都有绘制样本。

## 4. 口径修正

- 上轮报告中"17 项验收测试覆盖全部验收点"的表述**撤回**：当时静态模板路径
  属实，但缺少有效 sequence 的帧统计、真实几何预览与接触图验收，不能记成
  全部验收点已覆盖；本报告 §1–§3 补齐后按新证据表述。
- 此前把一次全量失败归因为"Windows 文件锁"属**未经证实的解释**，现予撤回：
  仅记录现象（该用例当轮失败、隔离运行与复跑通过），不作为根因结论；若复发
  将保留原失败输出定位。

## 5. 自测结果

- petpack CLI 套件 **26 passed**（17 项原有 + 9 项 CR-T 验收）。
- 相邻套件（realistic_cat_pack、v12_02、onboarding、runtime、layout、
  petpack）合计 **150 passed**。
- 全量 **1026 passed、13 skipped**（上轮 1017+13，净增 9 项）。
- 以上为执行者证据；主控独立复跑与签收另计。

## 6. 边界与后续

- V12-03 是否签收为四项需求全部完成，待主控按本报告复验。
- 旧版回滚演练（V12-04/09 未闭环项）仍未执行。
- 下一步按审阅指示继续 V12-05；恢复 UI 将消费 `store_available` /
  `unavailable_reason` 区分完整成功与部分成功。
- 本报告与提交号仅存内部仓库，公开导出前须脱敏。

## 7. 第二轮：CR-T03 续（接触图可读性）与 CR-T04（末帧取样）

针对[主控第二次复验](V1_2_AUTHOR_CONTROLLER_REVIEW.md)的两项 P2 收尾，
提交 `80807a2`。

### 7.1 CR-T03 续：接触图标签不可读的根因与修复

**根因**：offscreen 平台（及部分无头环境）的 Qt 字体数据库以**空集**启动
（本机实测 `QFontDatabase.families()` 返回 0），默认 `QFont` 解析不到任何
真实字体，`drawText` 画出的正是 .notdef 缺字方框——此前的"存在文字墨迹"
断言量到的就是方框墨迹，与主控"有暗像素不能证明文字正确显示"的判断一致。

修复分三层（`scripts/petpack_cli.py`）：

1. **逐码位字形验证**：`_contact_font` 对标签全集的每个非空白字符用
   `QRawFont.fromFont(font).supportsCharacter` 验证候选字体真的包含该字形，
   不再信任"字体存在"或"画出墨迹"。
2. **增量加载标准系统字体目录**：无已安装族覆盖时，从惯例系统目录
   （`QT_QPA_FONTDIR`、Windows `%WINDIR%\Fonts`、macOS 系统字体目录、
   Linux `/usr/share/fonts` 等）逐个 `QFontDatabase.addApplicationFont`
   加载并对新出现的族做同样验证。只扫标准目录，**未硬编码任何个人字体
   路径**；本机实测加载 `arial.ttf` 后 Arial 覆盖全部拉丁标签字形。
3. **缺字体即拒绝**：仍无任何字体覆盖时，`_write_contact_sheet` 返回
   False、**不写文件**，preview 打印缺字形清单与扫描过的目录并以退出码 1
   结束（`REJECT: no installed font renders the contact-sheet caption
   glyphs …`），绝不生成声称成功的不可读接触图。

**文本边界**：所有文字限定在各自的省略（`QFontMetrics.elidedText`）+裁剪
（`setClipRect`）条带内——行标签区 132 px、单元格标注条 96×16 px；行宽超过
8 列自动换行为带 `(cont.)` 标注的续带，接触图不再无限加宽。长说明换行/
缩写/省略，不压到样本图上。

**测试证据**（回应"补独立 CLI 进程下的字形与文字边界检查"）：

- 字形覆盖断言：`QRawFont.supportsCharacter` 覆盖标签全集每个码位。
- **豆腐块对照**：用保证无字形的私用区码位（U+E000…）以同一字体、同一
  版式渲染出真正的 .notdef 方框对照条，断言真实标签条与方框条逐像素
  **不同**——"暗墨迹"不再是可读性证据，字形与方框可区分。
- 边界检查：相邻单元格之间 4 px 间隙为纯白（`{0xFFFFFF}`），文字不越界；
  三条标注条（f0/f1/seam）互不相同，证明不同帧标注真实可辨。
- **独立 CLI 进程**：`subprocess` 以干净环境变量（`QT_QPA_PLATFORM=
  offscreen`）运行 `petpack_cli.py preview`，导出与接触图全部生成。

主控要求的"打开实际产物验收"需视觉检查，本执行环境无法渲染图像给检查
通道，视觉终验请主控执行；可复现命令与产物见 §7.3。

### 7.2 CR-T04：16 帧序列完整导出，长序列保留末帧

- `MAX_PREVIEW_COLUMNS` 16 → **18**：16 帧 MVP（Blender 首批允许 8–16 帧）
  +接缝 = 17 个采样点，**整体采样，不截断**。
- 更长序列有界抽样，但截断规则改为保留**首帧、末帧与接缝**
  （`frame_points[:head] + [frame_points[-1], seam]`），省略数写入
  `PreviewRow.omitted_frames`：CLI 每行末尾附 `(N frame(s) not sampled)`，
  接触图行副标签附 `(+N omitted)`——不把前 15 帧静默当成整个动作。
- **验收点落实**（末帧用明显不同的合成形状/颜色）：`_n_frame_variant`
  构造绿/蓝交替、**第 16 帧纯红**的合成序列。独立 CLI 进程实测：
  导出 17 张 PNG；`f15` 与 `f0` 在同一像素位置颜色不同（红 vs 绿）；
  接缝帧与首帧逐像素一致（循环回到首帧）；接触图中空闲行可扫描到红、
  绿、蓝三色墨迹，证明末帧进入导出与接触图。
- 版式选择：8 列换行而非加宽（§7.1），接触图宽度有界。

### 7.3 自测结果与复现

- petpack CLI 套件 **30 passed**（26 + 4 项新增：字形/边界/长序列取样/
  16 帧导出）。
- 全量 **1030 passed、13 skipped**（上轮 1026+13，净增 4 项）。
- 独立进程复现（隔离目录，不触日用库）：

  ```
  .venv\Scripts\python.exe scripts\petpack_cli.py init <源目录>
  .venv\Scripts\python.exe scripts\petpack_cli.py lint  <源目录>
  .venv\Scripts\python.exe scripts\petpack_cli.py build <源目录> <包>.petpack
  .venv\Scripts\python.exe scripts\petpack_cli.py preview <包>.petpack <输出目录>
  ```

  检查 `<输出目录>\contact-<角色>.png`：标签应为可读拉丁文字、限定在
  各自条带内；16 帧包的第 16 张 `<角色>-core.idle-15.png` 应与其余帧颜色
  明显不同，且出现在接触图中。

### 7.4 边界

- 接触图视觉终验待主控打开实际产物执行（§7.2/§7.3 给出验收点与复现命令）。
- V12-03 状态维持主控口径："主要实现完成，视觉工具收尾待验收"。
- 旧版回滚演练（V12-04/09 未闭环项）仍未执行。
- 下一步继续 V12-05；恢复 UI 将消费 `store_available` / `unavailable_reason`。
- 本报告与提交号仅存内部仓库，公开导出前须脱敏。
