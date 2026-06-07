"""classify_pii_columns: 用 LLM 推断每列的 PII 分类, 写入 ai_suggestion (待人工审核).

Inputs:
    table_ids: list[str]  要扫描的 table_asset.id (空 = 全部表)
    column_names: list[str]  仅扫描指定列名 (空 = 所有列)
    min_confidence: float    低于此值不入建议 (默认 0.6)
    profile: str             LLM profile (pii / default)

Outputs (SkillOutput.items):
    [{column_id, table_fqn, column_name, pii_class, confidence, model, suggestion_id}, ...]

PII class 字典 (AGENT_INSTRUCTIONS §13):
    none / email / phone / id_card / address / name / card_bin / ip / dob / ...

写入:
    ai_suggestion (suggestion_type=pii_class, target_type=column,
                   payload={pii_class, masking_policy: hash|partial|full|none})

合规:
    - LLM 只看到 schema + 最多 5 个样本值, 不会发整列
    - 建议 PII class 后由人工 approve 才落到 column_asset.pii_class
    - 默认 profile="pii" (AGENT_INSTRUCTIONS §6.3 推荐本地 qwen, 但 .env 已配 deepseek)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.ai_suggestion import AISuggestion
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)

VALID_PII = {
    "none", "email", "phone", "id_card", "address", "name",
    "card_bin", "card_full", "ip", "dob", "geo", "ssn", "passport",
    "license_plate", "device_id", "uuid_pseudo", "other",
}

PII_PROMPT = """你是数据合规专家。基于以下 ClickHouse 列的元信息(列名/类型/最多 5 个样本值, 样本已脱敏或为合成),
判断该列的 PII 风险等级。仅输出严格 JSON, 不含任何解释。

PII 分类 (从下列选 1 个):
  none         - 无 PII (ID 主键 / 时间戳 / 业务度量 / 类别)
  email        - 邮箱
  phone        - 手机号
  id_card      - 身份证号
  name         - 姓名 (first/last/full)
  address      - 邮寄/账单地址 (含城市以下粒度即算)
  geo          - 经纬度 / 行政区划
  ip           - IP 地址
  dob          - 出生日期
  card_bin     - 银行卡前 6/4 位
  card_full    - 完整卡号
  ssn          - 社保号
  passport     - 护照
  license_plate - 车牌
  device_id    - 设备指纹 / IMEI
  uuid_pseudo  - 不可逆伪 ID
  other        - 其他敏感

输出 JSON:
{{
  "pii_class": "<上面之一>",
  "confidence": 0.0-1.0,         // 你对判断的确信度
  "masking_policy": "none|hash|partial|full",  // 建议脱敏策略
  "reasoning": "<20-60 字中文解释>"
}}

【表】{fqn}
【列】{col_name}
【类型】{data_type}
【可空】{nullable}
【样本】{samples}
"""


def _format_samples(samples: list[Any]) -> str:
    if not samples:
        return "(无样本)"
    safe = []
    for s in samples[:5]:
        if s is None:
            safe.append("null")
        else:
            # 简单截断防止巨值
            text = str(s)
            safe.append(text[:40] + ("…" if len(text) > 40 else ""))
    return "[" + ", ".join(safe) + "]"


def _parse_llm_json(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(l for l in lines if not l.strip().startswith("```"))
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


@skill(name="classify_pii_columns", version=1, agent="pii")
async def classify_pii_columns(ctx: SkillContext, **inputs: Any) -> SkillResult:
    table_ids: list[str] = inputs.get("table_ids") or []
    column_names: list[str] = inputs.get("column_names") or []
    min_confidence: float = float(inputs.get("min_confidence") or 0.6)
    profile: str = inputs.get("profile") or "pii"

    if ctx.db is None:
        return SkillResult(ok=False, output=SkillOutput(), error="ctx.db is None")

    # 1) 拉候选列 (未分类的优先, 已分类但 confidence<0.5 也回滚再评)
    stmt = select(ColumnAsset).where(
        (ColumnAsset.pii_class == "none") | (ColumnAsset.pii_confidence < 0.5)
    )
    if table_ids:
        stmt = stmt.where(ColumnAsset.table_id.in_(table_ids))
    if column_names:
        stmt = stmt.where(ColumnAsset.name.in_(column_names))
    cols = list((await ctx.db.execute(stmt.limit(50))).scalars().all())
    ctx.log("columns_selected", count=len(cols))

    if not cols:
        return SkillResult(
            ok=True,
            output=SkillOutput(items=[], summary={"reason": "no columns to classify"}),
        )

    # 2) 拉表 FQN 一次性缓存
    table_ids_u = {c.table_id for c in cols}
    tables = {
        t.id: t
        for t in (await ctx.db.execute(select(TableAsset).where(TableAsset.id.in_(table_ids_u)))).scalars()
    }

    items: list[dict[str, Any]] = []
    skipped = 0

    for c in cols:
        t = tables.get(c.table_id)
        if t is None:
            skipped += 1
            continue

        prompt = PII_PROMPT.format(
            fqn=t.fqn,
            col_name=c.name,
            data_type=c.data_type,
            nullable="YES" if c.nullable else "NO",
            samples=_format_samples(c.sample_values or []),
        )
        try:
            llm_resp = await ctx.llm.complete(
                [
                    {"role": "system", "content": "你输出严格 JSON, 不含任何解释文字。"},
                    {"role": "user", "content": prompt},
                ],
                profile=profile,
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
        except Exception as e:  # noqa: BLE001
            ctx.log("llm_failed", column=c.name, error=str(e)[:120])
            skipped += 1
            continue

        parsed = _parse_llm_json(llm_resp["content"])
        pii_class = (parsed.get("pii_class") or "").strip().lower()
        if pii_class not in VALID_PII:
            pii_class = "other"
        confidence = float(parsed.get("confidence") or 0.5)
        masking_policy = parsed.get("masking_policy") or ("hash" if pii_class not in ("none", "uuid_pseudo") else "none")
        reasoning = (parsed.get("reasoning") or "").strip()[:300]

        if confidence < min_confidence:
            skipped += 1
            continue

        # 3) 写 ai_suggestion (column 级别, target_id=column.id)
        sug = AISuggestion(
            suggestion_type="pii_class",
            target_type="column",
            target_id=c.id,
            payload={
                "pii_class": pii_class,
                "masking_policy": masking_policy,
                "column_name": c.name,
                "table_fqn": t.fqn,
            },
            rationale=reasoning or f"LLM={llm_resp['model']} tier={llm_resp['tier']} conf={confidence}",
            confidence=confidence,
            model=llm_resp["model"],
            skill="classify_pii_columns",
            use_case_id=ctx.use_case_id,
            prompt_hash=llm_resp.get("prompt_hash"),
            langfuse_trace_id=None,
            status="pending",
        )
        ctx.db.add(sug)
        await ctx.db.flush()
        items.append(
            {
                "column_id": str(c.id),
                "table_fqn": t.fqn,
                "column_name": c.name,
                "pii_class": pii_class,
                "masking_policy": masking_policy,
                "confidence": confidence,
                "model": llm_resp["model"],
                "suggestion_id": str(sug.id),
            }
        )
        ctx.log("classified", column=c.name, pii_class=pii_class, conf=confidence)

    await ctx.db.commit()

    summary = {
        "columns_total": len(cols),
        "suggestions_created": len(items),
        "skipped_low_confidence_or_error": skipped,
        "model": items[0]["model"] if items else "n/a",
        "by_class": _count_by(items, "pii_class"),
    }

    return SkillResult(
        ok=True,
        output=SkillOutput(items=items, summary=summary, artifacts=[i["suggestion_id"] for i in items]),
    )


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it.get(key, "unknown")] = out.get(it.get(key, "unknown"), 0) + 1
    return out
