# 1.3.0 返工主控复验：发布线残余

> 复验日期：2026-09-17  
> 响应 HEAD：`69054db`  
> 候选身份：`4c7c3c3` / build_id `c973ee69…`  
> 结论：**CR13-01、02、03、07 关闭；CR13-04、05、06 仍有残余。**

本轮确认 Markdown 文档层拒绝资源、QLocalServer 用户范围、幂等请求指纹和外部
进程归属纪律已经按原反例修复。`4c7c3c3` 候选也确实取得 Alpha GO。公开快照、
发布材料和报告口径仍需一轮收尾，暂不批准公开同步或 GitHub Release。

## 已关闭项

- **CR13-01**：引用式本地图片像素反例归零，`SecurePreviewDocument` 是实际安装到
  `QTextBrowser` 的文档对象，资源入口 fail-closed。
- **CR13-02**：`UserAccessOption` 在 listen 前设置，实际 socket option 测试通过；
  平台不能维持该选项时不开放 Agent 服务。
- **CR13-03**：同 key 异指纹返回 `CONFLICT`，同指纹重试重建当前 request_id；
  operation、args、expected_generation 均进入规范化指纹。
- **CR13-07**：发布脚本没有终止外部进程；测试 harness 只清理自己持有的 Popen。
  原先手工终止用户桌宠的行为已被明确记录为禁止操作。

## 残余返工

### RR13-01（P1）当前 HEAD 的公开快照再次被拒绝

在干净 `69054db` 上重新执行导出，结果为 **REJECTED**：

- 文件：`docs/V1_3_REWORK_RESPONSE.md`
- 规则：`ip_address`
- 触发内容：发布材料段中的 Qt 四段版本号（`6.8.3` 后带资源修订段）

这说明 `4c7c3c3` 的历史 ACCEPTED 不能替代当前准备公开的 HEAD。修正文档或扫描器的
精确版本语境后，必须从当前 HEAD 重新导出并保存审计报告。不要再次先写响应报告、
再沿用报告写入前的导出结果。

### RR13-02（P1）SBOM 文件归因仍然错误

对最终 298 文件候选独立运行生成器成功，但结果把下列 CPython 标准扩展归为
`retirement-pet / MIT`：

- `_asyncio.pyd`、`_bz2.pyd`、`_ctypes.pyd`、`_decimal.pyd`
- `_hashlib.pyd`、`_ssl.pyd`、`_sqlite3.pyd`、`pyexpat.pyd` 等

根因是 `retirement-pet` 规则先匹配所有 `.pyd`，导致后面的 Python 扩展规则永远
接不到这些文件。`base_library.zip` 又被完整归给 PyInstaller bootloader，实际其中包含
Python 标准库；`RetirementPet.exe` 同时承载 bootloader 与应用归档，却只能落入一个
组件。当前“298/298 有标签”只证明覆盖率，不能证明标签正确。

验收要求：

1. CPython 标准扩展和标准库归 Python/PSF；项目自己的冻结模块归应用/MIT。
2. 对包含多个组件的容器文件使用多重归因或 artifact-level `contains` 关系，不能用
   “first match wins”丢掉其他组件。
3. 测试至少断言 `_ssl.pyd`、`_asyncio.pyd`、`base_library.zip` 和 EXE 的语义归因，
   不只断言所有文件都有非空 component。
4. `LGPL-SOURCES.md` 增加 FFmpeg 对应源码、构建配置与替换/重新链接说明；当前正文
   只说明 Qt，和 NOTICE 宣称的 Qt + FFmpeg 不一致。

### RR13-03（P1）独立发布材料没有产物身份与保存位置

响应报告写材料位于 `<evidence-out>/release-materials-4c7c3c3/`，但最终 run bundle、
artifact 和 dist 中均没有 `release-materials/` 或 `sbom.json`；报告也没有给出可复验的
实际路径。生成器只让 SBOM 引用 EXE receipt，未为材料目录自身生成 manifest/receipt。
因此许可文件或 SBOM 被修改、遗漏时，没有可核验的发布材料身份。

验收要求：把材料保存到最终 run bundle 的固定目录，生成材料目录自己的文件清单、
总摘要和 receipt，并绑定候选 commit/build_id/artifact_id/exe hash。最终报告给出仓库内
可访问路径。若材料单独下载，其 receipt 必须跟随下载包分发。

### RR13-04（P2）Alpha 与罗小黑报告口径过度陈述

- acceptance 实际为 **23 PASS + 1 SKIP**；SKIP 是多屏真实拓扑变化，Alpha 规则允许
  该可选项，因此 `ALPHA-GO` 有效，但“23/23 PASS”不真实。
- 罗小黑测试先读取真实策略，随后又在测试夹具中临时注入
  `assets/petpack/xiaohei-local-import.petpack` 和 `character-work/xiaohei-art/` 两条排除，
  所以它没有证明真实策略会排除这些类别。真实策略目前只明确排除专用构建脚本。

验收要求：把报告改为 23 PASS + 1 SKIP；罗小黑要么收窄声明为“当前跟踪树中只有专用
脚本需要排除”，要么把真实包/原图/衍生图/截图目录规则写进正式 policy，再用未修改的
真实 policy 测试。

### RR13-05（P2）媒体 P2 修复仍会阻塞，且本机不可复跑

当前实现把 provider 调用放入单线程池，但 Qt 调用线程仍执行
`future.result(timeout=0.75)`，每次最多冻结 UI 750ms；永不返回的任务还会永久占住唯一
worker，使之后每次命令继续排队并超时。`ThreadPoolExecutor` 的 worker 也不能按注释
假定为 daemon 并忽略退出等待。

独立专项复验中媒体套件出现 5 个失败；单独重跑仍稳定出现 2 个失败：

- `availability()` 返回可用，`start()` 随后因 OSError 返回失败；
- 设置已持久化为启用，但 UI 仍显示“未启用”，没有显示实际不可用原因。

这不推翻 4c 候选当时的 Alpha GO，但说明“P2 三项全部关闭、媒体套件 24 passed”无法在
当前环境复现。应改为真正异步完成/信号回调，或明确接受 750ms UI 阻塞并降低承诺；
同时让 availability/start 竞态和设置页状态如实呈现。

## 复验证据

- 响应所列六个专项测试文件重新执行：Markdown、Agent、单实例、材料与快照测试通过；
  媒体产生 5 failures。
- 单独复跑两个媒体失败：2 failures，可稳定重现。
- 最终 acceptance：profile `alpha`，verdict `GO`，统计 23 PASS / 1 SKIP。
- 最终 EXE receipt 身份与 acceptance 中的 commit/build_id/exe hash 一致。
- 当前 HEAD 公开导出：REJECTED，1 项 `ip_address`。

## 下一轮最小顺序

1. 修 RR13-02 与 RR13-03，生成正确且有自身 receipt 的材料包。
2. 修 RR13-01、RR13-04 文档/策略，重新导出当前 HEAD 公开快照。
3. 处理 RR13-05；若只作为后续优化，报告必须撤回“非阻塞/全部关闭”的表述。
4. 无需因纯文档修正重建已通过的 4c EXE；若生产代码或候选材料关系改变，则重新冻结
   唯一候选并重跑绑定门禁。

## 工作审视报告

### 原定目标

复验 CR13-01 至 CR13-07 和 P2 响应，判断 1.3.0 是否已经满足公开发布条件。

### 完成情况

核对候选 receipt 与 acceptance，重跑六组专项测试、当前 HEAD 快照导出和最终 artifact
的材料生成，并逐文件抽查 SBOM 组件归因。四项主缺口关闭，发布线发现三项 P1 残余，
另有两项报告/资源语义 P2 残余。

### 发现的问题

| 严重程度 | 具体问题 | 根因 | 改进建议 |
|---|---|---|---|
| P1 | 当前 HEAD 快照 REJECTED | 响应文档写入后未重跑导出 | 修版本语境并从最终 HEAD 重导出 |
| P1 | CPython 扩展被标成应用 MIT | 宽泛 `.pyd` 规则先于 Python 规则 | 按来源精确分类，容器文件支持多重归因 |
| P1 | 材料包无保存位置和自身 receipt | 只绑定输入 EXE receipt，未绑定输出目录 | 固定保存并生成材料 manifest/receipt |
| P2 | Alpha 和罗小黑策略证据表述过强 | 报告忽略可选 SKIP；测试注入非真实策略 | 按 acceptance 与正式 policy 如实写 |
| P2 | 媒体仍阻塞 UI 且不可稳定复跑 | Qt 线程等待 future；SMTC 可用性与启动竞态未建模 | 真异步化并显示真实不可用状态 |

### 做得好的地方

四项安全/协议缺口均按原反例落到了正确层级，幂等修复尤其完整；最终候选 receipt、
EXE hash 与 Alpha acceptance 可以互证，热键门本次也取得真实 PASS。

### 下次重点关注

发布材料必须审“归因是否正确”，不能只审“是否全覆盖”；每次新增最终报告后都要对
真正准备公开的 HEAD 重跑快照导出。测试中的临时 policy 不能作为生产 policy 的证据。
