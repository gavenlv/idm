-- flink_jobs/orders_preprocess.sql
-- 阶段 1: Flink 预处理 (raw → enriched)
-- 真实管道: 读 GCS company-raw  →  enrich + 计算派生列  →  写 GCS company-model-input
-- 会被 parse_flink_job Skill (transform_subtype=preprocess, stage=1) 解析
--
-- FQN 约定: source/sink 字段填 <service>.<database>.<schema>.<table> 形式
--   真实 GCS bucket: company-raw / company-model-input (含连字符, SQL 中转写)
--   service (逻辑) = gcs; database = bucket; schema = default; table = 物理表
--   GCS asset (由 discover_gcs_assets 创建) fqn = gcs://<bucket>/<key>
--   血缘映射: gcs://company-raw/... ↔ gcs.company_raw.default.orders_raw

INSERT INTO gcs.company_model_input.default.orders_enriched
SELECT
    order_id,
    user_id,
    order_date,
    total_amount,
    currency,
    status,
    CAST(item_count AS INT)        AS item_count,
    CAST(total_amount AS DECIMAL)  AS gross_amount,
    CAST(total_amount * 0.915 AS DECIMAL) AS net_amount,
    CAST(total_amount * 0.085 AS DECIMAL) AS tax_amount,
    payment_method,
    is_first_order,
    days_since_signup
FROM gcs.company_raw.default.orders_raw
WHERE order_date >= CURRENT_DATE - INTERVAL '1' DAY;
