# Skill Eval — infer_table_description

- Date: 2026-06-07T12:24:15Z
- Model: gpt-5
- Judge: gpt-5
- Cases: 3
- Total wall time: 6495 ms

## Summary

- Avg score: **0.0**
- Pass rate (>= 0.7): 0/3 (0%)
- P50 latency: 2067.0 ms
- Total cost: $0.0

## Failures (score < 0.7)

### orders-1  score=0.00
- 预测输出为空，未包含任何订单或GMV相关信息，与期望严重不符
- 预测输出结构错误，缺少items、summary等必要字段，应参考gold标准生成
- 预测输出未利用glossary中的GMV定义，导致内容缺失
> 预测输出完全为空，未包含任何与输入数据相关的订单或GMV信息，与期望输出要求包含'订单'和'GMV'严重不符，且输出结构错误，因此所有维度得分为0。

### orders-2  score=0.00
- 预测输出为空，未包含任何数据项，与期望输出完全不符
- 期望输出应包含order和GMV关键词，但预测输出未提供
- 预测输出结构错误，缺少contains和max_length字段
> 预测输出是一个空结构，没有包含任何有效信息，与期望输出完全不一致，因此所有维度得分为0。

### pii-1  score=0.00
- 预测输出为空，未包含任何数据项，与期望输出完全不符
- 期望输出应包含员工和email信息，但预测输出未提供
- 预测输出结构错误，缺少必要的contains和max_length字段
> 预测输出为空对象，未包含任何有效数据，与期望输出完全不一致，所有维度得分均为0。

## Top issue patterns

- 预测输出为空，未包含任何数据项，与期望输出完全不符  (2 cases)
- 预测输出为空，未包含任何订单或GMV相关信息，与期望严重不符  (1 cases)
- 预测输出结构错误，缺少items、summary等必要字段，应参考gold标准生成  (1 cases)
- 预测输出未利用glossary中的GMV定义，导致内容缺失  (1 cases)
- 期望输出应包含order和GMV关键词，但预测输出未提供  (1 cases)
