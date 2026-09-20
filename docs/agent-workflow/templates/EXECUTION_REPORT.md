# <cycle_id> 执行报告

```yaml
cycle_id: <cycle_id>
role: implementer
status: READY_FOR_REVIEW
base_commit: <full commit>
implementation_commit: <full commit>  # 交付内容链上最后一个提交
handoff_head: <full commit>           # 冻结状态的交接提交（由登记提交写入）
branch: <branch>
started_at: <ISO 8601>
finished_at: <ISO 8601>
candidate_commit: null
```

## 输入与范围

说明读取了哪些 Todo、审阅记录和项目文档，以及本轮明确做与不做的内容。

## 调查结论

区分已复现缺口、陈旧需求、产品决策和环境限制。

## 实现

按可独立验收的功能列出最终行为与提交。

## 验证

列出命令、结果、关键反例和人工证据。环境缺失如实记 SKIP。

## 数据、兼容与回滚

说明用户数据、运行实例、协议兼容、回滚方式和失败状态。

## 候选与公开边界

如有候选，列完整身份；说明公开快照、素材许可和本地排除内容。

## 遗留与主控重点

列出未关闭风险、需要主控重点构造的反例以及需要用户决定的事项。

