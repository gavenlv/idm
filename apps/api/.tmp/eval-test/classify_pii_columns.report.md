# Skill Eval — classify_pii_columns

- Date: 2026-06-07T12:24:03Z
- Model: gpt-5
- Judge: gpt-5
- Cases: 3
- Total wall time: 8053 ms

## Summary

- Avg score: **0.0**
- Pass rate (>= 0.7): 0/3 (0%)
- P50 latency: 2612.0 ms
- Total cost: $0.0

## Failures (score < 0.7)

### email-1  score=0.00
- 预测输出完全错误，未包含任何期望的字段如email或user_email，应输出包含这些字段的JSON。
- 预测输出为空对象，与期望输出结构不符，应修正为包含contains列表的JSON。
- 预测输出未遵循任务要求，应重新生成符合期望的输出。
> 预测输出与期望输出完全不匹配，预测输出为空，而期望输出应包含email和user_email字段。所有维度得分均为0，因为输出完全错误且无任何正确内容。

### phone-1  score=0.00
- 预测输出未包含任何期望的字段名，如phone或mobile，应检查数据治理规则并补充缺失字段
- 预测输出为空对象，与期望输出完全不符，需重新生成包含正确字段的结果
- 预测输出未遵循数据治理审核要求，应确保输出包含必要的字段列表
> 预测输出为空，未包含任何期望的字段，与gold标准完全不一致，因此所有维度得分为0。

### id-card-1  score=0.00
- 预测输出未包含任何与id_card相关的信息，与期望输出完全不符
- 预测输出为空对象，未对输入列id_card_no进行任何处理或分析
- 预测输出格式错误，缺少必要的字段如contains
> 预测输出完全偏离期望输出，未识别出id_card_no字段，且输出结构错误，所有维度得分均为0。

## Top issue patterns

- 预测输出完全错误，未包含任何期望的字段如email或user_email，应输出包含这些字段的JSON。  (1 cases)
- 预测输出为空对象，与期望输出结构不符，应修正为包含contains列表的JSON。  (1 cases)
- 预测输出未遵循任务要求，应重新生成符合期望的输出。  (1 cases)
- 预测输出未包含任何期望的字段名，如phone或mobile，应检查数据治理规则并补充缺失字段  (1 cases)
- 预测输出为空对象，与期望输出完全不符，需重新生成包含正确字段的结果  (1 cases)
