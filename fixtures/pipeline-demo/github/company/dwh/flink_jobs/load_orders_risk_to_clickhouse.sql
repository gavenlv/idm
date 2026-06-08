-- flink_jobs/load_orders_risk_to_clickhouse.sql
-- 阶段 5: Flink load model-output → ClickHouse reporting
-- 真实管道: 读 GCS company-model-output  →  转换 + 分区写入  →  ClickHouse shop.fct_orders_risk_daily
-- 会被 parse_flink_job Skill (transform_subtype=load_ch, stage=5) 解析
--
-- FQN 约定: source/sink 字段填 <service>.<database>.<schema>.<table> 形式
--   真实 CH service: clickhouse-prod (含连字符, SQL 中转写)
--   血缘映射: gcs://company-model-output/... ↔ gcs.company_model_output.default.orders_risk
--            clickhouse-prod.shop.default.fct_orders_risk_daily ↔ clickhouse_prod.shop.default.fct_orders_risk_daily
--   (ClickHouse asset (由 discover_clickhouse_assets 创建) fqn = clickhouse-prod.shop.default.fct_orders_risk_daily)

INSERT INTO clickhouse_prod.shop.default.fct_orders_risk_daily
SELECT
    order_id,
    user_id,
    order_date,
    risk_score,
    risk_label,
    fraud_prob,
    chargeback_prob,
    model_version,
    model_run_at
FROM gcs.company_model_output.default.orders_risk
WHERE order_date >= CURRENT_DATE - INTERVAL '7' DAY;
