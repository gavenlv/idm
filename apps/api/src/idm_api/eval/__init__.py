"""idm-eval: Skill 评估体系 (Gold Snapshot + LLM-as-judge + 用户反馈).

设计文档: docs/design/eval-harness.md

三层评估:
  1) 离线 Gold (cases/*.jsonl)        -> 拦截退化, CI 门禁
  2) 在线 LLM-judge (5% 抽样)         -> 发现 Gold 覆盖不到的问题
  3) 用户反馈 (👍/👎)                  -> 进 few-shot, 训练真实偏好

对外接口:
  - EvalCase / EvalResult / EvalReport: Pydantic model
  - SkillEvalRunner: 批量跑 case -> 打分 -> 报告
  - LLMJudge: 0~1 打分 + 原因
  - FeedbackStore: 反馈入库 + few-shot 导出
  - run_eval / gate: CLI 入口
"""
from idm_api.eval.types import (
    EvalCase,
    EvalReport,
    EvalResult,
    GateConfig,
    GateResult,
    RubricV1,
)
from idm_api.eval.feedback import (
    FeedbackRecord,
    FeedbackStore,
    FewShotExample,
    build_few_shots,
)

__all__ = [
    "EvalCase",
    "EvalResult",
    "EvalReport",
    "GateConfig",
    "GateResult",
    "RubricV1",
    "FeedbackRecord",
    "FeedbackStore",
    "FewShotExample",
    "build_few_shots",
]
