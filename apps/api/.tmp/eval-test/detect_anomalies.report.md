# Skill Eval — detect_anomalies

- Date: 2026-06-07T12:24:08Z
- Model: gpt-5
- Judge: gpt-5
- Cases: 2
- Total wall time: 5440 ms

## Summary

- Avg score: **0.0**
- Pass rate (>= 0.7): 0/2 (0%)
- P50 latency: 2779.0 ms
- Total cost: $0.0

## Failures (score < 0.7)

### anom-1  score=0.00
- 预测输出结构完全错误，期望输出是包含字段列表的对象，但预测输出是包含ok和output的对象
- 预测输出缺少期望的contains字段，且output中的items、summary、artifacts与期望无关
- 预测输出内容与用户输入和期望输出均无关联，完全偏离任务要求
> 预测输出与期望输出在结构和内容上完全不匹配，预测输出是一个服务响应对象，而期望输出是一个包含字段列表的简单对象，因此所有维度得分均为0。

### anom-2  score=0.00
- 预测输出与期望输出完全不符：期望输出包含owner_gap，但预测输出为空对象和空数组
- 预测输出结构错误：期望输出是包含contains字段的对象，但预测输出是ok和output字段
- 预测输出未包含任何有效数据，无法满足任务要求
> 预测输出与期望输出在结构和内容上完全不一致，预测输出未包含任何期望的owner_gap信息，且格式错误，因此所有维度得分为0。

## Top issue patterns

- 预测输出结构完全错误，期望输出是包含字段列表的对象，但预测输出是包含ok和output的对象  (1 cases)
- 预测输出缺少期望的contains字段，且output中的items、summary、artifacts与期望无关  (1 cases)
- 预测输出内容与用户输入和期望输出均无关联，完全偏离任务要求  (1 cases)
- 预测输出与期望输出完全不符：期望输出包含owner_gap，但预测输出为空对象和空数组  (1 cases)
- 预测输出结构错误：期望输出是包含contains字段的对象，但预测输出是ok和output字段  (1 cases)
