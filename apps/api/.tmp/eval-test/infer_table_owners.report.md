# Skill Eval — infer_table_owners

- Date: 2026-06-07T12:24:20Z
- Model: gpt-5
- Judge: gpt-5
- Cases: 2
- Total wall time: 5528 ms

## Summary

- Avg score: **0.0**
- Pass rate (>= 0.7): 0/2 (0%)
- P50 latency: 2814.0 ms
- Total cost: $0.0

## Failures (score < 0.7)

### owner-shop-1  score=0.00
- 输出格式完全错误：期望输出是包含字段列表的JSON对象，但预测输出是包含ok和output的JSON对象
- 内容不相关：预测输出未包含任何关于email和owner的信息，与期望输出完全不符
- 结构不匹配：预测输出中的output字段包含items、summary、artifacts，而期望输出是简单的contains列表
> 预测输出与期望输出在结构和内容上完全不一致，预测输出是一个服务响应格式，而期望输出是字段列表，因此所有维度得分均为0。

### owner-fct-1  score=0.00
- 预测输出与期望输出完全不符，期望输出包含email和data，但预测输出为空列表和空对象
- 预测输出结构错误，缺少contains字段，且items、summary、artifacts均为空
- 预测输出未反映用户输入中的任何信息，如fqn_pattern或llm_threshold
> 预测输出与期望输出在内容和结构上均不匹配，且未包含任何有效信息，因此所有维度得分为0。

## Top issue patterns

- 输出格式完全错误：期望输出是包含字段列表的JSON对象，但预测输出是包含ok和output的JSON对象  (1 cases)
- 内容不相关：预测输出未包含任何关于email和owner的信息，与期望输出完全不符  (1 cases)
- 结构不匹配：预测输出中的output字段包含items、summary、artifacts，而期望输出是简单的conta  (1 cases)
- 预测输出与期望输出完全不符，期望输出包含email和data，但预测输出为空列表和空对象  (1 cases)
- 预测输出结构错误，缺少contains字段，且items、summary、artifacts均为空  (1 cases)
