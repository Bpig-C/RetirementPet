# RetirementPet 1.3 变更矩阵（V13-00）

> 用途：跟踪 1.3 开发期内每个工单项的 需求 → 实现提交 → 测试 → 文档 → 候选证据
> 五列对应关系。候选证据列在 V13-09 集成前保持 `—`；只有从稳定源码状态重新
> 构建的 1.3.0 候选才会填入 receipt/acceptance 标识。
>
> 版本轴说明（避免混同）：
>
> - 应用版本：开发期保持 `1.2.0` 字符串，节点 C 签收后一次性收敛为 `1.3.0`
>   （工单决定 1）。任何中途二进制只携带日志内开发标识，不冒充 1.3.0。
> - Todo schema：本版原则上仍为 **v2**（`migrations.SCHEMA_VERSION = 2`）。
> - PetPack manifest schema：仍为 **1.0**（`PETPACK_SPEC_1_0`），1.3 不升级。
> - Agent 协议：新增独立版本字符串 `retirement-pet.agent.v1`，与应用版本、
>   Todo schema、PetPack schema 均无绑定关系。

## 基线（V13-00 记录）

| 项 | 值 |
|---|---|
| 基线提交 | `1084574`（Plan RetirementPet 1.3 agent workflow release） |
| 开发分支 | `impl/v1.3-agent-workflow`（自 `impl/v1.2-daily-use` 创建） |
| 工作树 | 创建分支时干净（git status 无未提交改动） |
| Python | 3.12.7（仓库 `.venv`；系统 anaconda Python 的 QtNetwork DLL 损坏，禁用于本项目） |
| PySide6 | 6.8.3（`.venv`） |
| 全量测试基线 | 见 `evidence/v13/` 下当次 pytest 输出（数字以证据文件为准） |
| 1.2.0 冻结证据 | `.release/` 内 1.2.0 候选目录与 `evidence/` 历史目录不改动、不覆盖 |

## 变更矩阵

| 工单 | 需求摘要 | 实现提交 | 测试 | 文档 | 候选证据 |
|---|---|---|---|---|---|
| V13-00 | 基线、分支、版本治理 | `57f1e71` | 全量基线：1158 passed / 13 skipped / exit 0（`evidence/v13/20260916-baseline-pytest.txt`，1084574 干净 worktree 采集） | 本文件、`evidence/v13/README.md` | — |
| V13-01 | 版本化本地 Agent JSON 协议 | `3804fc4` | `tests/test_agent_protocol.py` 38 项（含真实子进程 socket 往返、半包/超长/坏编码/坏 JSON/错误版本/未知操作/幂等/冲突/重连/超时/退出） | `docs/AGENT_PROTOCOL.md` | — |
| V13-02 | Agent CLI MVP | `a6fea05`、`f814dda`（quadrant） | `tests/test_agent_cli.py` 14 项（真实子进程端到端：全链路/筛选/反例/退出码） | `docs/AGENT_PROTOCOL.md` | — |
| V13-03 | Todo 删除入口 + 子树完成/恢复语义 | `dfc69c7` | `tests/test_todo_subtree_semantics.py` 20 项 + `tests/test_todo_v13_ui.py` 16 项 | 本文件 | — |
| V13-04 | Markdown 预览 + 优先级可见性 | `7b21e36`、`<本次>`（图片剥除+720 布局） | `tests/test_todo_v13_04_ui.py` 16 项（含 file/data 图片剥除像素反例与 720×480 状态列可见断言） | 本文件 | — |
| V13-05 | Windows 媒体会话桥 | `ba36a00` 及返工提交 | `tests/test_media_bridge.py` 19 项（合成合约 + 真机 SMTC 集成 + 网易云实测 `evidence/v13/20260916-netease-real-session.json`） | 本文件、`docs/AGENT_PROTOCOL.md` | — |
| V13-06 | 工作活动联动可解释性 | `9270707` | `tests/test_v13_06_activity.py` 8 项（三态/门控/隐私审查/Agent 一致） | 本文件 | — |
| V13-07 | 原创猫序列动画 + 本地角色修整 | `a014076` | `tests/test_build_official_cat.py` V13-07 组 6 项（接缝/RGBA/真实播放）；罗小黑 preflight PASS | contact sheet `evidence/v13/` | 官方 1.0.2 随候选 |
| V13-08 | SBOM、许可、LGPL、脱敏快照 | 导出器修复与材料生成器各提交 | `tests/test_public_snapshot.py` 40 项；快照内 1281 passed | `THIRD_PARTY_NOTICES.md` | `scripts/generate_release_materials.py`（发布时生成） |
| V13-09 | 1.3.0 集成候选、回滚、Alpha 验收 | 版本收敛 `96551bb`；返工轮次见矩阵各行 | 最终候选 `4c7c3c3`：go_no_go alpha **23/23 PASS, ALPHA-GO**；回滚演练 9/9；全量回归 1309 passed | 本文件、返工响应报告 | commit `4c7c3c3` build_id `c973ee69…` |
| V13-E1 | Production 真机门禁 | — | — | 节点 C 报告 E1 表 | 六类门禁全部 PENDING |

（实现完成后由执行者逐行更新；提交列填短哈希，测试列填测试文件与结果数字，
文档列填更新过的文档名，候选证据列在 V13-09 之前保持 `—`。）
