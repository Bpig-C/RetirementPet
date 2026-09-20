# V12-07 交付报告：角色库版本详情、安全卸载与回滚

> 关联：[版本工单](V1_2_WORK_ORDER.md) · [V12-06 交付报告](V1_2_V12_06_REPORT.md) ·
> [文档中心](README.md)。状态：自测完成，待主控验收。
> 内部仓库文档，含内部提交号。

## 1. 范围与实现事实

角色页（`ui/panel/pages.py::build_characters_page`）从"仅列表+切换"扩展为
版本可辨、可回退、可安全卸载的角色库管理页：

- **系列筛选**：新增 `character_series_filter` 下拉（"全部系列" + 每个
  (发布者, 系列) 一项，数据驱动、blockSignals 防递归）；列表按所选系列过滤。
- **版本可辨**：每行显示 `角色名 · package ID 版本 · 摘要前 12 位
  （系列 · 发布者）`，同名/同包多版本条目天然可区分；徽标保留
  `[官方]`/`[本地内容 · 发布者/权利未验证]`，新增 `← 当前`、`← 上一健康`
  与 `[待删除]`。
- **详情**：`character_details` 按钮 + `character_details_box` 显示角色与
  character_id、package ID、版本、完整内容摘要、来源（官方内置发布 /
  本地导入 + trust_channel 与未验证说明）、安装时间、包含角色数、包体
  大小对照 SPEC 17 预算（归档 200 MiB / 解压总 500 MiB，导入校验时强制）
  与运行预算（解码缓存 48 MiB 全角色共享 LRU）。详情缩略图按 manifest
  声明的 `thumbnail_asset` 从库内包读取，边界分四层：压缩成员读取
  ≤ 8 MiB；PNG 像素尺寸在解码前解析、超 1 百万像素拒绝解码（PNG 无
  原生缩放解码，需预解码像素上限）；解码经 `QImageReader` 目标 ≤96px
  长边；页内缓存 ≤8 条，键含 revision 与角色身份。占位文案如实说明
  未读取/未解码原因。
- **切换指定 Revision**：选中任一版本行后"切换到所选角色"走既有
  REQUEST→PREPARE→SWAP→COMMIT（失败进入安全模式并如实反馈，已有测试
  覆盖失败切换）。
- **回到上一健康版本**：`character_rollback` 读取
  `last_known_good` 槽位（`checkpoint_active_health` 在健康观察后写入）；
  与当前一致或缺失时明确"没有可回退的上一健康版本"；目标包不在库时
  如实说明；回退同样走真实切换事务，失败保留当前角色。
- **安全卸载**：`character_uninstall` 调用 `PackLibrary.request_uninstall`
  （见下）。按钮按当前事实启用/禁用并给出原因 tooltip：内置安全包
  （"内置安全包不通过普通卸载移除"）、当前使用中、上一健康（恢复依据）、
  已标记待删除的版本均不可直接删；卸载成功提示"移入库内回收区；凭证与
  权利事实保留"。待删除版本再次被显式选中并成功切换时，待删状态即被
  撤销（`UNINSTALL_CANCELLED`，用户显式选择胜过先前删除请求）——不存在
  "已激活却等待自身删除"的状态。

`lifecycle.py`（服务层）：

- `request_uninstall(rk, active_guard=...) -> "uninstalled" |
  "pending_delete"`：未知 Revision、内置官方 Revision、guard 保护对象
  （活跃选择 + 恢复依据）抛 `PPK-LCY-E005`；否则复用
  `uninstall_revision` 的原子回收区移动 + 目录行删除 + `UNINSTALL_TRASHED`
  事件。
- **pending-delete（文件占用）**：移动被 OS 拒绝（Windows 文件占用抛
  OSError）时不谎报成功、也不留下半删除状态：写入 `pending_deletes` 表
  （`_migrate` 新增，幂等建表）、追加 `UNINSTALL_PENDING` 事件、目录行
  保留并标记 `[待删除]`。
- **重启继续删除（保护重检，L07-01）**：续删不由库构造期自动执行
  （彼时选择事实不可读）；应用在选择存储就绪后调用公开 API
  `recover_pending_deletes(active_guard)`，guard 按调用时刻的 active 与
  last_known_good 求值——已成为当前或恢复依据的版本不会被删除，其待删
  状态被撤销；媒体已消失的条目完成目录行与事件收尾，不遗留可选无媒体
  条目；仍占用者保留待删状态。回收区/凭证目录/日志全部在库根内，不触
  碰库外文件。
- 凭证（receipts）与权利事实在卸载后保留（回收区机制 + 独立 receipts
  目录）。

## 2. 测试证据

服务层（`tests/test_lifecycle.py`，新增 4 项）：

- 两个版本同存：minimal-static 1.0.0 + 1.0.1（同包身份、不同 Revision）
  同时在库、RevisionKey 可区分。
- 拒绝路径：内置官方、guard 保护（活跃/恢复依据）抛错且目录行不动；
  未使用版本 `request_uninstall` 返回 uninstalled 且行删除。
- 占用文件 → pending-delete → **真实重启**（新建 PackLibrary 实例，
  CONFORMANCE 6 口径）后删除完成：行消失、pending 清空、回收区有内容、
  `UNINSTALL_TRASHED` 事件、凭证保留；占用期间行仍在且
  `UNINSTALL_PENDING` 有据。
- 失败切换已有覆盖：`test_switch_failure_keeps_current_body`（安全模式
  保留原角色），回退失败路径同样保留当前角色并如实提示。

UI 层（`tests/test_control_panel.py`，新增 3 项）：

- 系列筛选 + 详情 + 版本行：过滤后 minimal-static 两行、版本号出现在
  行文本；详情框展示 package ID/版本/摘要/来源/预算；缩略图非空且
  缓存不超上限。
- 回滚：无可回退时明确提示；官方→demo→健康检查点→切回官方后，回退
  按钮可用、点击后活跃选择回到 demo 并提示"已回退到上一健康版本"。
- 卸载状态：内置行/当前行禁用且 tooltip 说明原因；占用文件的版本点击
  后状态与行标记 `[待删除]`、pending 有据；恢复钩子执行后行消失
  （重开页面不再列出）；未使用版本点击即删且状态如实。

回归：首轮交付全量 1109 passed、13 skipped；返工后全量 **1116 passed、13 skipped**（较首轮 1109+13 净增 7 项）。治理与文档链接检查
68 项通过。`docs/README.md` 等主控未提交改动未纳入本模块提交。
逐项返工证据见 [V12-07 返工响应](V1_2_V12_07_REWORK_RESPONSE.md)。

## 3. 口径与边界

- "上一健康版本"严格采用引擎既有 `last_known_good` 槽位语义：它是健康
  观察后的恢复依据；切换并健康检查后它会与当前一致，此时无可回退目标，
  页面如实显示，不虚构更早历史。
- 卸载是"移入库内回收区 + 目录除名"，不做物理粉碎；恢复依据（回收区 +
  凭证）保留，符合工单"保留必要 receipt、权利事实和恢复依据"。
- 系列筛选、详情与缩略图只读库内已验证数据；缩略图缓存为页内进程内
  缓存，上限 8 张 96px，页面从不整库解码。
- Todo、用户配置与库外文件不因卸载/回滚改动（服务层只操作库根内目录）。
