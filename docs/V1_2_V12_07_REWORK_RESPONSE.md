# V12-07 返工响应：L07-01、L07-02、L07-03

> 关联：[V12-07 主控审阅](V1_2_V12_07_CONTROLLER_REVIEW.md) ·
> [V12-07 交付报告](V1_2_V12_07_REPORT.md) · [文档中心](README.md)。
> 状态：自测完成，待主控验收。内部仓库文档，含内部提交号。

## 总览

三项返工逐项收口。全量回归 **1116 passed、13 skipped**；治理与
文档链接检查 68 项通过。已保留第一版有效的版本身份、系列筛选、回滚
与未使用版本卸载能力。

## L07-01（P1）：续删重检当前与恢复依据；状态闭环

- 修复：
  - 库构造期不再自动续删（构造时选择事实尚不可读——这正是原缺口）。
    新公开 API `recover_pending_deletes(active_guard)` 由应用在
    选择存储与切换器就绪后调用；guard 按**调用时刻**的持久化事实
    （active + last_known_good）求值，成为当前/恢复依据的版本不会被
    删除，其待删状态以 `UNINSTALL_CANCELLED`（reason=
    protected_selection）撤销。
  - 状态闭环选择：**再次选中待删版本即撤销待删**。`_switch_character`
    成功提交后如目标 Revision 在待删表中，调用新增的
    `cancel_pending_delete(rk, reason="reselected_by_user")`——激活
    一个等待自身删除的版本是矛盾状态，用户的显式选择胜过先前的删除
    请求。`cancel_pending_delete` 为公开 API（目录行与媒体不动、
    事件留痕、幂等）。
  - 媒体消失/中断一致性：续删统一经 `uninstall_revision` 执行——
    媒体已不存在时同样完成目录行删除与事件记录，不再遗留
    "仍可选但没有媒体"的条目。
- 测试：
  - 服务层：guard 命中时撤销待删并保留行与媒体（UNINSTALL_CANCELLED
    事件）；重新请求后续删真实完成（回收区、UNINSTALL_TRASHED、凭证
    保留）；媒体消失后续删使目录行与 pending 行一致清空；
    `cancel_pending_delete` 幂等语义。
  - 应用层（master 情形 A 全程）：真实文件句柄占用 → pending →
    关句柄 → `_switch_character` 激活该版本 → 待删撤销 → 健康检查点
    提为 last_known_good → **关闭并重建 PetApplication** → active
    恢复、Revision 与媒体完好、pending 为空。

## L07-02（P2）：720×480 下详情与首次选择完整可达

- 修复：
  - 角色页整体包进 QScrollArea（`characters_scroll`，widgetResizable、
    纵向滚动），最小尺寸下详情、引导区与操作行都可滚动到达。
  - 根因之一：QLabel 默认不带 heightForWidth 标志，滚动壳下的布局对
    换行文本少预留高度、末行真被裁切。新增
    `_wordwrap_height_policy` 并应用到详情文本、状态栏、提示与引导
    标签；布局按实际宽度保留完整高度。
  - 列表设最小高度，防止滚动布局中塌缩。
- 测试（有/无引导两态、720×480、坐标证据，非仅 text/isHidden）：
  - 引导待定态：两个引导按钮高度 ≥ minimumSizeHint 且 `widgetAt`
    坐标命中自身。
  - 引导完成态：点开详情后，详情文本高度 ≥ heightForWidth(实际宽度)
    （此前确实少 31px）；滚动到底后文本末行的全局坐标在视口内、
    `widgetAt` 命中详情标签本身、visibleRegion 覆盖完整文本高度。

## L07-02 残余收口：横向可达性

主控复验指出纵向滚动正常，但视口宽 534px、内容宽 564px，横向滚动被
禁用导致右侧裁切。探针定位到两个撑宽源并修复：

- 五个操作按钮单行排布，最小宽约 526px——拆为两行（切换/回退/详情、
  删除/导入），行尾拉伸填充。
- 内容摘要是 64 位无断行点的十六进制 token，wordWrap 无法在词中间
  断行，把详情行撑到 564px——摘要按每 16 字符分块换行显示。

测试在 720×480、详情展开状态下断言：内容最小宽 ≤ 视口宽（widgetResizable
下内容宽随之收敛）；摘要文本右缘在视口内且坐标命中落在详情组框内；
最右按钮（导入）右缘可见且 `widgetAt` 命中按钮本身。不依赖水平滚动条。

## L07-03（P2）：缩略图解码边界与缓存身份

- 修复（按主控要求分述各上限）：
  - **读取**：压缩成员 ≤ 8 MiB，超出不读取。
  - **解码前像素预算**：PNG IHDR 尺寸在解码**之前**解析，超过
    1 百万像素（约 4 MiB RGBA 中间缓冲）直接拒绝，不解码、不分配大
    缓冲——PNG 无原生按需缩放解码，预解码像素上限是真实的资源界限。
    占位文案明确报出实际尺寸与上限。
  - **缩放**：解码经由 `QImageReader.setScaledSize`（目标 ≤96px 长
    边）；如格式内部仍需原尺寸中间缓冲，该缓冲已被上一步像素预算
    限定。
  - **缓存**：键改为 `revision_key:character_id`，同包双角色不再互串；
    总量上限保持 8 条（超出逐出最旧）。
  - 附带修复：QBuffer 临时对象被 GC 导致的原生崩溃（buffer 与 reader
    同生命周期）；缩略图异常改为记录日志（不含路径）。
- 测试：
  - 压缩小而像素大：2048×2048 纯色 PNG（压缩后数 KB、校验器合法）
    → 详情占位"未解码"、无像素缓存条目。
  - 同 Revision 双角色：demo/demo2 声明不同 thumbnail_asset，两次
    详情像素不同、缓存两条且键共享同一 revision 前缀。

## 报告口径更正

`V1_2_V12_07_REPORT.md` 中"解码上限 96px、不全尺寸解码"的原表述与
实现不符，已改为按读取/像素预算/缩放/缓存四层如实描述；并补充
L07-01 的保护重检与取消语义、pending-delete 的显式恢复 API。

## 范围与未动项

- `.release/controller-review-4318182/` 主控证据目录不入库。
- Markdown 预览、旧版配套数据回滚演练、真机与发布门禁继续按既定
  顺序后置。
