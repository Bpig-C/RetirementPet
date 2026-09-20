# 1.3.0 第二轮返工主控复验

> 复验日期：2026-09-18  
> 响应 HEAD：`d58966f`  
> 候选：`77be50e` / build_id `b5aa931b…`  
> 结论：**RR13-01 至 RR13-04 关闭；RR13-05 尚差媒体失败态闭环。**

公开快照、SBOM 归因和材料包身份已独立复验通过。新候选的两次 Alpha 门禁均因
原生热键注入环境失败而停在 NO-GO，报告对此表述诚实。现在不需要再做发布线的大规模
返工，只需修正媒体启动失败时的 UI/测试语义和两处证据口径。

## 已关闭

### RR13-01：公开快照

在干净 `d58966f` 上独立重跑导出：**ACCEPTED**，0 violations，罗小黑专用构建
脚本不在导出树。响应报告写入后没有再次破坏当前 HEAD 的公开性。

### RR13-02：SBOM 语义

最终候选材料抽查结果：

- `_ssl.pyd`、`_asyncio.pyd`、`_sqlite3.pyd` 均归 `python / PSF-2.0`；
- `RetirementPet.exe` 记录 bootloader 与应用的 contains 关系；
- `base_library.zip` 记录 bootloader 与 Python 标准库的 contains 关系；
- LGPL 文档包含 FFmpeg 源码入口和替换说明。

容器的 primary component 与 contains 中的同组件有少量重复，但没有再丢失应用或
Python 的许可关系，不作为本轮阻塞。

### RR13-03：材料身份

`release-materials/` 已保存到 `77be50e` run bundle。独立重算结果：11 个非 receipt
文件全部存在且 SHA-256 相符，combined SHA-256 相符，材料 receipt 绑定的
commit/recipe/artifact/exe/version 与候选一致。

### RR13-04：报告口径与本地内容

罗小黑声明已收窄为当前实际范围，测试使用原版正式 policy；当前跟踪树中的专用脚本
确实被排除。历史 `4c7c3c3` acceptance 的正确口径已经改为 23 PASS + 1 optional
SKIP。该历史 Alpha GO 仍有效，但不能替代生产代码改变后的新候选门禁。

## 唯一必须收尾：RR13-05 媒体失败态

响应报告称媒体套件 26 passed，并称环境不可用时相应测试会 SKIP。主控按报告命令复跑，
结果稳定出现 **5 failures**：

1. `availability()` 返回可用、`start()` 随后因 OSError 拒绝；原生 provider 测试仍
   直接断言 start 必须成功，没有按实际失败结果 SKIP/记录原因。
2. 设置页保存“启用”后，桥启动失败；`set_enabled()` 在失败路径更新 snapshot，
   但没有发出 `changed`，`on_apply()` 也没有调用 `refresh_media_status()`，因此页面继续
   显示“未开启”，新写的“开启失败（原因）”分支实际没有被刷新到 UI。
3. 持久化重启测试仍无条件断言 bridge 必须 enabled；在设置偏好为 true、系统层启动
   失败时，应断言偏好保留、运行态关闭、失败原因可见。
4. 真机媒体子进程不能建立会话时仍判 FAIL；若这是已识别的环境限制，应在保存子进程
   stderr 后做有理由的 SKIP，不能只检查 projections 是否安装。

修复要求很小：

- 启动失败也触发 UI 状态刷新，增加“偏好开启 + provider 拒绝”的真实页面测试；
- 将三项环境测试按“projections 存在”和“SMTC 实际可启动”分层，后者失败时保存原因并
  SKIP；真正启动成功后出现功能错误才 FAIL；
- 重启测试接受诚实降级状态，同时验证设置偏好没有被静默清除；
- 重新运行完整 `test_media_bridge.py`，必须在当前不可用环境下得到 PASS/SKIP、0 FAIL。

每命令一个 daemon 线程与 750ms 有界阻塞已经如实记录，可作为 1.3.0 的明确取舍；但它
允许连续超时命令累积多个后台线程，需留作 1.3.x 的并发上限/背压改进，不能再宣称
“非阻塞”。

## 证据口径修正

- `77be50e` run bundle 没有报告所列的根目录 `final-full-regression.txt`。两次门禁的
  `pytest_regular.stdout.log` 都存在，实际结果均为 **1311 passed / 13 skipped**，应把
  报告路径改指向真实日志，或补入声明的汇总文件。
- `77be50e` 两次 acceptance 均为 7 PASS / 1 FAIL / 16 SKIP，结论
  `ALPHA-NO-GO`；唯一 FAIL 是 `pytest_native`。这与响应报告的最终门禁结论一致。

## 签收状态

- 工程发布材料与公开快照：**签收**。
- 新候选身份与常规回归：**签收**。
- 媒体失败态与其测试：**待一次小修**。
- Alpha：**NO-GO:ENVIRONMENT**，等待有人值守交互桌面重跑。
- Production：`PRODUCTION-NO-GO:PENDING_ENVIRONMENT`。

## 工作审视报告

### 原定目标

复验 RR13-01 至 RR13-05 是否全部关闭，并检查新候选、材料 receipt 与公开快照。

### 完成情况

独立重算材料 receipt 和关键 SBOM 条目，重导出当前 HEAD，检查两次 acceptance 与常规
测试日志，并按响应报告重跑材料、媒体、快照三组专项测试。四项关闭，媒体失败态仍可
稳定复现。

### 发现的问题

| 严重程度 | 具体问题 | 根因 | 改进建议 |
|---|---|---|---|
| P1 | 媒体启动失败后页面仍显示未开启 | 失败路径不发 changed，应用按钮不主动刷新 | 无论启动成功与否都刷新真实 snapshot 状态 |
| P1 | 媒体套件在当前环境 5 failures | 测试把 projections 存在等同于 SMTC 可启动 | 分层探测，环境拒绝带原因 SKIP |
| P2 | 持久化重启测试要求运行态必然 enabled | 没区分用户偏好和系统可用性 | 验证偏好保留与诚实降级 |
| P2 | 报告引用不存在的回归汇总文件 | 证据路径在写报告时未核对 | 指向两次门禁真实 pytest 日志 |
| P2 | 超时命令可累积 daemon 线程 | 每请求新建线程且无并发上限 | 后续增加背压或真正异步状态机 |

### 做得好的地方

SBOM 从“全覆盖”提升到了语义归因，材料包也有了可以独立重算的身份；当前 HEAD 快照
确实可公开导出。新候选门禁没有用旧 Alpha GO 冒充当前候选成功。

### 下次重点关注

只处理媒体失败态，不再重做已签收的发布材料。修完后先在当前 SMTC 不可用环境跑到
0 FAIL，再在真实可用环境保留端到端 PASS；最后由有人值守桌面重跑 Alpha 热键门。
