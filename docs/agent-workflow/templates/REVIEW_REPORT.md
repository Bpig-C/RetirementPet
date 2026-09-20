# <cycle_id> 主控审阅报告

```yaml
cycle_id: <cycle_id>
role: controller
verdict: ACCEPTED | REWORK_REQUIRED | BLOCKED
reviewed_head: <full commit>
reviewed_candidate: null
started_at: <ISO 8601>
finished_at: <ISO 8601>
```

## 身份与范围

记录实际审阅的分支、提交、候选和执行报告；指出任何身份分裂。

## 独立复验

列主控实际运行的命令、观察与结果，不复制执行者结论充当证据。

## 发现

| 编号 | 严重度 | 结论 | 复现与要求 |
| --- | --- | --- | --- |

## 裁定

说明签收范围或返工范围。`ACCEPTED` 必须明确哪些环境门禁仍未执行。

## 下一状态

写明 `CURRENT_STATE.md` 应进入的状态、下一行动者以及是否允许本地合并。

