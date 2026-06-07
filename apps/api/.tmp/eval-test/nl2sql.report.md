# Skill Eval — nl2sql

- Date: 2026-06-07T12:24:28Z
- Model: gpt-5
- Judge: gpt-5
- Cases: 3
- Total wall time: 7774 ms

## Summary

- Avg score: **0.0**
- Pass rate (>= 0.7): 0/3 (0%)
- P50 latency: 2528.0 ms
- Total cost: $0.0

## Failures (score < 0.7)

### q-1  score=0.00
- 预测输出为空结果，未包含任何数据，与期望的SQL查询结果不符
- 预测输出未生成SQL语句，无法验证是否包含orders_daily和LIMIT 3
- 预测输出返回ok为false，表明执行失败，应检查服务或查询逻辑
> 预测输出为空且执行失败，完全未满足用户查询orders_daily最近3行的需求，也未包含期望的SQL内容，因此所有维度得分为0。

### q-2  score=0.00
- 预测输出为空，未包含任何SQL或数据，与期望输出完全不符
- 预测输出缺少gmv、region、LIMIT等关键字段
- 预测输出未返回任何items或summary，无法评估具体内容
> 预测输出为空对象，未提供任何有效数据，无法满足用户查询GMV Top 5区域的需求，且与期望输出中要求的SQL约束完全无关。

### q-3  score=0.00
- 预测输出未包含任何SQL或安全约束，与期望输出完全不符
- 预测输出返回空结果，未执行任何数据治理检查
- 预测输出缺少对orders_daily表的删除保护
> 预测输出仅返回空对象，未按期望输出提供SQL禁止列表和安全检查，完全不符合数据治理要求。

## Top issue patterns

- 预测输出为空结果，未包含任何数据，与期望的SQL查询结果不符  (1 cases)
- 预测输出未生成SQL语句，无法验证是否包含orders_daily和LIMIT 3  (1 cases)
- 预测输出返回ok为false，表明执行失败，应检查服务或查询逻辑  (1 cases)
- 预测输出为空，未包含任何SQL或数据，与期望输出完全不符  (1 cases)
- 预测输出缺少gmv、region、LIMIT等关键字段  (1 cases)
