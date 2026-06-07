# IDM — LLM 路由详细设计 (GPT-5 + DeepSeek + 本地)

> 主力 GPT-5，备选 DeepSeek，兜底本地 Qwen
> 通过 LiteLLM 统一网关 + Langfuse 可观测
> 本文给出配置、调用、缓存、限流、成本控制

---

## 目录

- [1. 设计目标](#1-设计目标)
- [2. 模型矩阵](#2-模型矩阵)
- [3. 路由策略](#3-路由策略)
- [4. LiteLLM 部署](#4-litellm-部署)
- [5. 客户端 SDK](#5-客户端-sdk)
- [6. Prompt Cache 策略](#6-prompt-cache-策略)
- [7. 成本控制](#7-成本控制)
- [8. 安全与脱敏](#8-安全与脱敏)
- [9. 可观测 (Langfuse)](#9-可观测-langfuse)
- [10. 评估体系](#10-评估体系)
- [11. 降级与故障](#11-降级与故障)

---

## 1. 设计目标

| 目标 | 指标 |
| --- | --- |
| **高质量** | 主线任务 P95 输出质量分 ≥ 0.85 |
| **低成本** | 每月 ≤ $2k (1000 use case / 200k LLM call) |
| **高可用** | 任一模型不可用 → 自动 fallback，业务无感 |
| **可观测** | 每次调用 token / 延迟 / 成本 / 评分 |
| **合规** | 敏感数据只走本地 / 私有部署 |

---

## 2. 模型矩阵

| 模型 | 部署 | 用途 | 单价 (per 1M tok) | 上下文 |
| --- | --- | --- | --- | --- |
| **gpt-5** | OpenAI API | 主力 / 推理 / 代码 | $3 in / $12 out | 128k |
| **deepseek-chat (V3)** | DeepSeek API | 中文 / 长文 / 成本 | $0.14 in / $0.28 out | 64k |
| **deepseek-reasoner (R1)** | DeepSeek API | 复杂推理 / 数学 | $0.55 in / $2.19 out | 64k |
| **qwen2.5:32b** | 本地 Ollama | 合规 / 内网 / 兜底 | 0 (电费) | 32k |
| **bge-large-zh** | 本地 | Embedding | 0 | 512 |
| **text-embedding-3-large** | OpenAI API | Embedding | $0.13 | 8k |

---

## 3. 路由策略

### 3.1 决策流程

```mermaid
flowchart TD
    A[LLM Call] --> B{敏感数据?}
    B -- 是 --> C[qwen-local]
    B -- 否 --> D{长上下文 > 60k?}
    D -- 是 --> E[deepseek-v3]
    D -- 否 --> F{需要复杂推理?}
    F -- 是 --> G[gpt-5]
    F -- 否 --> H{中文为主?}
    H -- 是 --> I[deepseek-v3]
    H -- 否 --> J[gpt-5]
    C --> K[记录 trace]
    E --> K
    G --> K
    I --> K
    J --> K
```

### 3.2 路由规则 (Code)

```python
# idm/llm/router.py
def pick_model(task: dict) -> str:
    if task.get("contains_pii"):
        return "qwen-local"

    if task.get("estimated_input_tokens", 0) > 60_000:
        return "deepseek-v3"

    if task.get("requires_reasoning"):
        return "gpt-5"  # 或 gpt-5 + 后续 deepseek-reasoner

    if task.get("language", "zh") in ("zh", "zh-CN"):
        return "deepseek-v3"  # 中文性价比

    return "gpt-5"
```

### 3.3 任务 → 默认模型 速查表

| 任务 | 默认 | 备选 |
| --- | --- | --- |
| Doc Generator | gpt-5 | deepseek-v3 |
| NL2SQL | gpt-5 | deepseek-v3 |
| Entity Resolution | gpt-5 | deepseek-v3 |
| PII 分类 | gpt-5 | deepseek-v3 |
| 长文档摘要 (>30k) | deepseek-v3 | gpt-5 |
| 大批量回填 (>5k) | deepseek-v3 | qwen-local |
| 敏感字段分析 | qwen-local | - |
| Code Review (PR) | gpt-5 | deepseek-reasoner |
| Embedding | text-embedding-3-large | bge-large-zh |

---

## 4. LiteLLM 部署

### 4.1 Helm / K8s 部署

```yaml
# helm/idm-litellm/values.yaml
replicaCount: 2

image: ghcr.io/berriai/litellm:main-stable

env:
  DATABASE_URL: "postgresql://idm:***@10.0.0.5:5432/idm"  # PG 存 usage
  REDIS_HOST: "10.0.0.6"
  REDIS_PORT: "6379"
  LITELLM_MASTER_KEY: "sk-***"
  LANGFUSE_PUBLIC_KEY: "***"
  LANGFUSE_SECRET_KEY: "***"
  LANGFUSE_HOST: "http://langfuse.idm.svc:3000"

config:
  model_list:
    - model_name: gpt-5
      litellm_params:
        model: openai/gpt-5
        api_key: os.environ/OPENAI_API_KEY
    - model_name: deepseek-v3
      litellm_params:
        model: deepseek/deepseek-chat
        api_key: os.environ/DEEPSEEK_API_KEY
        api_base: https://api.deepseek.com
    - model_name: deepseek-r1
      litellm_params:
        model: deepseek/deepseek-reasoner
        api_key: os.environ/DEEPSEEK_API_KEY
        api_base: https://api.deepseek.com
    - model_name: qwen-local
      litellm_params:
        model: ollama/qwen2.5:32b
        api_base: http://ollama.idm.svc:11434
  router_settings:
    routing_strategy: simple-shuffle
    num_retries: 3
    timeout: 60
    fallbacks:
      - { gpt-5: ["deepseek-v3", "qwen-local"] }
      - { deepseek-v3: ["qwen-local", "gpt-5"] }
      - { deepseek-r1: ["gpt-5"] }
  litellm_settings:
    drop_params: true
    success_callback: ["langfuse"]
    failure_callback: ["langfuse"]
```

### 4.2 ConfigMap 完整 yaml

```yaml
apiVersion: v1
kind: ConfigMap
metadata: { name: idm-litellm-config, namespace: idm-ai }
data:
  config.yaml: |
    model_list:
      - model_name: gpt-5
        litellm_params: { model: openai/gpt-5, api_key: os.environ/OPENAI_API_KEY }
      - model_name: deepseek-v3
        litellm_params: { model: deepseek/deepseek-chat, api_key: os.environ/DEEPSEEK_API_KEY, api_base: https://api.deepseek.com }
      - model_name: qwen-local
        litellm_params: { model: ollama/qwen2.5:32b, api_base: http://ollama.idm.svc:11434 }
    router_settings:
      num_retries: 3
      timeout: 60
      fallbacks: [{ gpt-5: [deepseek-v3, qwen-local] }]
```

---

## 5. 客户端 SDK

```python
# idm/llm/client.py
from litellm import acompletion
from idm.llm.router import pick_model
from idm.llm.mask import mask_pii
from idm.llm.cache import cached
from idm.llm.trace import trace_llm

class LLM:
    def __init__(self, default_model: str = "gpt-5"):
        self.default = default_model
        self.api_base = os.environ.get("LITELLM_URL", "http://litellm.idm-ai:4000")

    @trace_llm
    async def complete(
        self,
        messages,
        model: str | None = None,
        fallback_models: list[str] | None = None,
        output_type: str = "text",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        cache_key: list[str] | None = None,
        contains_pii: bool = False,
        skill: str | None = None,
        use_case: str | None = None,
    ) -> str | dict:
        chosen = model or self.default
        if contains_pii:
            chosen = "qwen-local"
            messages = mask_pii(messages)

        kwargs = dict(
            model=chosen,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            api_base=self.api_base,
            metadata={
                "skill": skill,
                "use_case": use_case,
                "contains_pii": contains_pii,
            },
            fallbacks=fallback_models or ["deepseek-v3", "qwen-local"],
            num_retries=3,
            timeout=60,
        )

        if cache_key:
            return await cached(cache_key, acompletion, **kwargs)
        return await acompletion(**kwargs)
```

### 5.1 在 Agent / Skill 中使用

```python
# infer_table_description skill 内部
llm = LLM()
text = await llm.complete(
    model="gpt-5",
    messages=[
        {"role": "system", "content": "你是资深数据工程师"},
        {"role": "user",   "content": prompt}
    ],
    output_type="text",
    cache_key=["table_desc", table_fqn, schema_hash],
    skill="infer_table_description",
    use_case=use_case.id,
)
```

---

## 6. Prompt Cache 策略

### 6.1 缓存什么

| 缓存类型 | Key | TTL | 命中率提升 |
| --- | --- | --- | --- |
| **完全相同 prompt** | hash(prompt) | 7 天 | ~30% |
| **Schema-aware** | `["table_desc", fqn, schema_hash]` | 7 天 / schema 变失效 | ~50% |
| **Few-shot 示例** | hash(shot_ids) | 30 天 | 几乎 100% |

### 6.2 实现 (LiteLLM + Redis)

```python
# idm/llm/cache.py
import hashlib, json
import redis.asyncio as redis

r = redis.from_url(os.environ["REDIS_URL"])

async def cached(cache_key_parts: list[str], fn, **kwargs):
    key = "llm:" + hashlib.sha256(
        json.dumps(cache_key_parts, sort_keys=True).encode()
    ).hexdigest()
    cached_val = await r.get(key)
    if cached_val:
        return json.loads(cached_val)

    result = await fn(**kwargs)
    await r.set(key, json.dumps(result), ex=7 * 24 * 3600)
    return result
```

### 6.3 DeepSeek 官方 Prompt Cache

LiteLLM 已支持：开启 `cache: { mode: "default" }`

---

## 7. 成本控制

### 7.1 预算

| 级别 | 限额 | 超限动作 |
| --- | --- | --- |
| **全局 / 月** | $2,000 | 邮件告警 |
| **全局 / 月** | $3,000 | 自动降级到 deepseek 优先 |
| **Use Case / 月** | $100 | 通知 Owner |
| **User / 天** | 50k tokens | 限速 |

### 7.2 限速实现

```python
# idm/llm/ratelimit.py
import redis.asyncio as redis
r = redis.from_url(...)

async def check_quota(actor: str, cost: float):
    key = f"quota:{actor}:{date.today()}"
    used = float(await r.incrbyfloat(key, cost))
    await r.expire(key, 86400)
    if used > LIMIT:
        raise QuotaExceeded(actor, used)
```

### 7.3 模型选择降级

```python
async def auto_downgrade_if_over_budget():
    spent = await get_month_spend()
    if spent > 2000:
        # 优先 deepseek
        global DEFAULT_MODEL
        DEFAULT_MODEL = "deepseek-v3"
    if spent > 3000:
        # 全部走 deepseek
        # gpt-5 仍保留给 critical
        ...
```

---

## 8. 安全与脱敏

### 8.1 PII 检测 + Masking

```python
# idm/llm/mask.py
import re

PII_PATTERNS = {
    "email":     re.compile(r"[\w\.-]+@[\w\.-]+"),
    "phone_cn":  re.compile(r"1[3-9]\d{9}"),
    "id_card":   re.compile(r"\d{17}[\dXx]"),
    "credit":    re.compile(r"\d{16}"),
}

SENSITIVE_COL_HINTS = ["email", "phone", "mobile", "身份证", "银行卡",
                       "name_cn", "ssn", "address"]

def mask_pii(messages):
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            for k, pat in PII_PATTERNS.items():
                c = pat.sub(f"[{k}_REDACTED]", c)
        out.append({**m, "content": c})
    return out
```

### 8.2 数据隔离

- **使用 qwen-local 强制条件**:
  - `use_case.guardrails.llm.data_masking: true`
  - `task.contains_pii: true` (Skill 显式声明)
  - 表的 `pii_class != 'none'` 的列

---

## 9. 可观测 (Langfuse)

### 9.1 必抓字段

```python
# 每次 LLM 调用 → Langfuse trace
trace = langfuse.trace(
    name=skill_name or "llm",
    user_id=actor,
    session_id=use_case_id,
    tags=[model, use_case_id, skill_name],
    metadata={"contains_pii": contains_pii}
)
generation = trace.generation(
    name="llm_call",
    model=model,
    model_parameters={"temperature": temperature, "max_tokens": max_tokens},
    input=messages,
    output=response,
    usage={
        "input":  usage.prompt_tokens,
        "output": usage.completion_tokens,
        "total":  usage.total_tokens,
        "unit":   "TOKENS"
    },
    latency=latency_ms
)
```

### 9.2 关键 Dashboard

| 面板 | 指标 |
| --- | --- |
| **Cost** | 每日 $ by model, by use_case, by skill |
| **Quality** | 用户评分 / 自动评估分 / 失败率 |
| **Latency** | P50/P95/P99 by model |
| **Cache** | 命中率 by skill |
| **Fallback** | 触发次数 by from→to |
| **Quota** | Top 10 user by spend |

---

## 10. 评估体系

### 10.1 三类评估

| 类型 | 触发 | 工具 |
| --- | --- | --- |
| **离线 (Gold snapshot)** | 每周 / PR | skills/eval/harness |
| **在线 (LLM-as-judge)** | 抽样 5% 真实调用 | Langfuse Eval |
| **用户反馈** | UI 一键 👍/👎 | 写回 Langfuse score |

### 10.2 评估样例 (Doc 质量)

```python
JUDGE_PROMPT = """
你是评估员, 评估以下 Description 质量 (1-5).
- 准确性 (是否反映表实际用途)
- 具体性 (是否含业务术语)
- 简洁性 (是否 < 100 字)
- 流畅性 (中文是否自然)

表 schema: {schema}
Sample:    {sample}
Description: {description}

请输出 JSON: {"score": int, "reason": str}
"""
```

### 10.3 自动回归

```yaml
# .github/workflows/llm-eval.yml
on:
  schedule: { cron: "0 6 * * 1" }   # 每周一 06:00
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install idm[eval]
      - run: python -m idm.skills.eval --all --report
      - uses: actions/upload-artifact@v4
        with: { name: eval-report, path: reports/ }
```

---

## 11. 降级与故障

### 11.1 故障矩阵

| 故障 | 检测 | 应对 |
| --- | --- | --- |
| **OpenAI 不可用** | LiteLLM 自动 fallback | gpt-5 → deepseek-v3 → qwen-local |
| **DeepSeek 不可用** | LiteLLM 自动 fallback | deepseek-v3 → qwen-local → 缓存 |
| **LiteLLM 自身挂** | K8s liveness probe | 重启 + 客户端重试 |
| **某个 skill 持续失败** | Eval harness 报警 | 暂停该 skill, 用 default 行为 |
| **LLM 输出格式错** | JSON Schema 校验 | 自动 retry + few-shot |
| **敏感数据泄露风险** | 审计日志 | 立即切断, 告警 |

### 11.2 LiteLLM 自动重试

```yaml
router_settings:
  num_retries: 3
  retry_policy: exponential_backoff
  timeout: 60
  allowed_fails: 2
  cooldown_time: 30
```

### 11.3 客户端熔断

```python
class CircuitBreaker:
    def __init__(self, fail_threshold=5, reset_sec=60):
        self.fail_count = 0
        self.last_fail = 0
        self.threshold = fail_threshold
        self.reset = reset_sec

    def allow(self):
        if self.fail_count >= self.threshold:
            if time.time() - self.last_fail > self.reset:
                self.fail_count = 0
                return True
            return False
        return True

    def fail(self):
        self.fail_count += 1
        self.last_fail = time.time()
```

---

## 附录 A. 环境变量

```bash
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
LITELLM_URL=http://litellm.idm-ai:4000
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=http://langfuse.idm.svc:3000
REDIS_URL=redis://10.0.0.6:6379/0
OLLAMA_URL=http://ollama.idm.svc:11434
```

## 附录 B. 常用 Prompt 模板 (节选)

```text
# Doc Generator
你是一位资深数据工程师.
请基于以下信息, 用中文写一段不超过 60 字的业务描述.

【表名】{{ table_fqn }}
【Schema】{{ columns }}
【Sample 5 行】{{ sample }}
【业务术语】{{ glossary }}
【血缘上下文】{{ lineage_neighbors }}

只输出描述, 不要解释.

# NL2SQL
你是 ClickHouse SQL 专家.
用户问题: {{ question }}
候选表 (top 5): {{ candidate_tables }}
业务术语: {{ glossary }}
历史类似问题: {{ similar_qa | head(3) }}

要求:
- 仅 SELECT, 加 LIMIT 1000 默认
- 列名用反引号
- 时间字段统一 toDateTime / toDate

输出 JSON: { "sql": "...", "rationale": "..." }
```

---

> 📌 **配套阅读**：[stack-decisions.md](./stack-decisions.md) · [skills-design.md](./skills-design.md) · [walkthrough.md](./walkthrough.md)
