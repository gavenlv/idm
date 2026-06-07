# Skill Eval — infer_table_description

- Date: 2026-06-07T12:22:04Z
- Model: gpt-5
- Judge: gpt-5
- Cases: 3
- Total wall time: 7951 ms

## Summary

- Avg score: **0.0**
- Pass rate (>= 0.7): 0/3 (0%)
- P50 latency: 2836.0 ms
- Total cost: $0.0

## Failures (score < 0.7)

### orders-1  score=0.00
- 预测输出为空，未包含任何订单或GMV相关信息，与期望输出完全不符
- 应生成包含'订单'和'GMV'的摘要或描述，而非空对象
> 预测输出为空对象，未满足期望输出中要求包含'订单'和'GMV'的关键词，且未提供任何有效内容，因此所有维度得分为0。

### orders-2  score=0.00
- 预测输出为空，未包含任何数据项，与期望输出完全不符
- 缺少对order_id和gmv字段的描述或示例，应补充具体内容
- 输出结构错误，应包含contains和max_length字段
> 预测输出是一个空结构，没有提供任何有效信息，而期望输出要求包含字段名称和长度限制。所有维度得分均为0，因为输出完全不符合要求。

### pii-1  score=0.00
- 预测输出为空，未包含任何数据项，与期望输出完全不符
- 期望输出要求包含'员工'和'email'，但预测输出未提供任何内容
- 预测输出结构错误，缺少必要的字段如contains和max_length
> 预测输出为空对象，未包含任何有效数据，与期望输出完全不一致，所有维度得分均为0。

## Top issue patterns

- 预测输出为空，未包含任何数据项，与期望输出完全不符  (2 cases)
- 预测输出为空，未包含任何订单或GMV相关信息，与期望输出完全不符  (1 cases)
- 应生成包含'订单'和'GMV'的摘要或描述，而非空对象  (1 cases)
- 缺少对order_id和gmv字段的描述或示例，应补充具体内容  (1 cases)
- 输出结构错误，应包含contains和max_length字段  (1 cases)
